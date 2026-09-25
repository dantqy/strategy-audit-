"""Thin, defensive wrapper around the Moomoo OpenD *quote* API.

Quote data only; there is no trading in this module. Paper orders live in
scanner/paper_execution.py.

Handles: OpenD reachability, bounded retry with exponential backoff, a
client-side rate limiter, kline pagination, snapshot batching (400/call),
empty responses, missing fields, and on-disk caching of daily history.
"""
from __future__ import annotations

import logging
import socket
import time
from collections import deque
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

from . import market_calendar as mc
from .data_cache import DailyBarCache, JsonCache
from .data_quality import normalize_daily

log = logging.getLogger(__name__)

OPEND_DOWN_MSG = ("OpenD is not reachable. Start and log into OpenD, then rerun "
                  "the sanity check:  python scripts/check_moomoo_connection.py")

_RETRYABLE = ("frequen", "too many", "timeout", "time out", "disconnect", "network",
              "not ready", "busy", "try again", "connection")
_FATAL = ("quota", "permission", "no right", "no authority", "unknown stock",
          "invalid", "not exist", "not support")


class OpenDUnavailable(RuntimeError):
    pass


class MoomooAPIError(RuntimeError):
    pass


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class RateLimiter:
    """Sliding-window limiter: at most `n` calls per `window` seconds."""

    def __init__(self, n: int, window: float = 30.0, clock=time.monotonic, sleep=time.sleep):
        self.n, self.window, self.clock, self.sleep = n, window, clock, sleep
        self.calls: deque = deque()

    def wait(self) -> None:
        now = self.clock()
        while self.calls and now - self.calls[0] >= self.window:
            self.calls.popleft()
        if len(self.calls) >= self.n:
            delay = self.window - (now - self.calls[0]) + 0.05
            log.info("rate limiter: sleeping %.1fs", delay)
            self.sleep(delay)
        self.calls.append(self.clock())


def call_with_retry(fn: Callable, *args, what: str = "api call", max_retries: int = 4,
                    base: float = 1.0, cap: float = 16.0, sleep=time.sleep,
                    limiter: RateLimiter | None = None, ok_code: Any = 0, **kwargs):
    """Call a moomoo function returning (ret, data, ...). Bounded retries only.

    Returns the full tuple on success. Raises MoomooAPIError on fatal/exhausted.
    """
    last_err = None
    for attempt in range(max_retries + 1):
        if limiter:
            limiter.wait()
        try:
            res = fn(*args, **kwargs)
        except Exception as e:  # SDK raised (e.g. socket error)
            res, last_err = None, f"{type(e).__name__}: {e}"
        if res is not None:
            if not isinstance(res, tuple) or len(res) < 2:
                raise MoomooAPIError(f"{what}: unexpected response shape {type(res)}")
            if res[0] == ok_code:
                return res
            last_err = str(res[1])
        low = (last_err or "").lower()
        if any(k in low for k in _FATAL) and not any(k in low for k in ("frequen",)):
            raise MoomooAPIError(f"{what}: {last_err}")
        if attempt < max_retries:
            delay = min(cap, base * (2 ** attempt))
            log.warning("%s failed (%s); retry %d/%d in %.1fs", what, last_err, attempt + 1, max_retries, delay)
            sleep(delay)
    raise MoomooAPIError(f"{what}: gave up after {max_retries + 1} attempts: {last_err}")


def suspected_row_cap(day_counts: dict, min_rows: int = 20, min_days: int = 3) -> int | None:
    """The max per-day row count if >= `min_days` days hit it exactly, else None.

    Real earnings counts vary day to day; several busy days landing on the same
    maximum is the signature of a truncated response.
    """
    vals = [v for v in day_counts.values() if v]
    if not vals:
        return None
    top = max(vals)
    return top if top >= min_rows and vals.count(top) >= min_days else None


def _sdk():
    try:
        import moomoo  # noqa: WPS433 (lazy import: SDK prints logs on import)
    except ImportError as e:
        raise RuntimeError("The 'moomoo' package is not installed. Run: pip install moomoo-api") from e
    return moomoo


class MoomooClient:
    def __init__(self, cfg: dict, quote_ctx: Any = None, sdk: Any = None):
        self.cfg = cfg
        o, a = cfg["opend"], cfg["api"]
        self.host, self.port = o["host"], int(o["port"])
        self.retry = dict(max_retries=a["max_retries"], base=a["backoff_base_seconds"], cap=a["backoff_max_seconds"])
        self.limiter = RateLimiter(int(a["requests_per_30s"]))
        self.page_size = int(a["kline_page_size"])
        self.autype = cfg["history"]["autype"]
        self.daily_cache = DailyBarCache("data/cache/daily", self.autype)
        # Unadjusted (as-traded) bars: needed to know what a share really cost on a past
        # date (forward-adjusted prices rewrite pre-split history). Re-requesting a stock
        # already fetched this week does not use history quota (verified live 2026-09-24).
        self.raw_cache = DailyBarCache("data/cache/daily", "NONE")
        self.misc_cache = JsonCache("data/cache/misc")
        self.earn_cache = JsonCache("data/cache/earnings")
        self._sdk = sdk
        self.ctx = quote_ctx
        self.cache_hits = 0
        self.api_fetches = 0

    # ---------------------------------------------------------------- connect
    @property
    def sdk(self):
        if self._sdk is None:
            self._sdk = _sdk()
        return self._sdk

    def connect(self) -> "MoomooClient":
        if self.ctx is not None:
            return self
        if not port_open(self.host, self.port):
            raise OpenDUnavailable(OPEND_DOWN_MSG)
        self.ctx = self.sdk.OpenQuoteContext(host=self.host, port=self.port)
        ret, state = self.ctx.get_global_state()
        if ret != self.sdk.RET_OK:
            self.close()
            raise OpenDUnavailable(f"{OPEND_DOWN_MSG}\n(get_global_state failed: {state})")
        if isinstance(state, dict) and not state.get("qot_logined", True):
            self.close()
            raise OpenDUnavailable("OpenD is running but NOT logged in to quotes. Log into OpenD, then rerun.")
        return self

    def close(self) -> None:
        if self.ctx is not None:
            try:
                self.ctx.close()
            finally:
                self.ctx = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def _call(self, name: str, *args, what: str | None = None, **kwargs):
        fn = getattr(self.ctx, name, None)
        if fn is None:
            raise MoomooAPIError(f"This moomoo SDK has no '{name}'. Upgrade: pip install -U moomoo-api")
        return call_with_retry(fn, *args, what=what or name, limiter=self.limiter,
                               ok_code=self.sdk.RET_OK, **self.retry, **kwargs)

    # ----------------------------------------------------------- introspection
    def global_state(self) -> dict:
        return self._call("get_global_state")[1]

    def history_quota(self) -> dict:
        used, remain, detail = self._call("get_history_kl_quota", get_detail=True)[1]
        return {"used": used, "remaining": remain, "detail": detail}

    def probe_features(self) -> dict:
        names = ["request_history_kline", "get_market_snapshot", "get_history_kl_quota",
                 "get_earnings_calendar", "get_financials_earnings_price_move",
                 "get_financials_earnings_price_history", "get_stock_basicinfo", "get_global_state"]
        return {n: hasattr(self.ctx, n) for n in names}

    # ------------------------------------------------------------ daily bars
    def _fetch_daily(self, code: str, start: date, end: date, autype: str | None = None) -> pd.DataFrame:
        sdk = self.sdk
        frames, key, pages = [], None, 0
        while True:
            ret, data, key = self._call(
                "request_history_kline", code, what=f"history_kline {code}",
                start=start.isoformat(), end=end.isoformat(), ktype=sdk.KLType.K_DAY,
                autype=getattr(sdk.AuType, autype or self.autype), max_count=self.page_size, page_req_key=key)
            pages += 1
            if isinstance(data, pd.DataFrame) and not data.empty:
                frames.append(data)
            if key is None or pages > 100:
                break
        self.api_fetches += 1
        if not frames:
            log.warning("%s: empty kline response for %s..%s", code, start, end)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df, rep = normalize_daily(pd.concat(frames, ignore_index=True), code)
        return df

    def daily_bars(self, code: str, start: date, end: date | None = None,
                   use_cache: bool = True) -> pd.DataFrame:
        """Completed daily bars [start, end]; end defaults to last completed session.

        Uses cache when it covers the range; otherwise fetches only the missing
        tail. If overlapping cached bars disagree (a new split/dividend changed
        forward-adjusted prices), the full range is refetched.
        """
        end = end or mc.last_completed_session(mc.now_utc())
        cached, meta = self.daily_cache.load(code) if use_cache else (None, {})
        if cached is not None and len(cached):
            req_start = date.fromisoformat(meta.get("requested_start", str(cached.index[0])))
            head_ok = req_start <= start
            tail_ok = cached.index[-1] >= end
            if head_ok and tail_ok:
                self.cache_hits += 1
                log.info("cache hit: %s daily %s..%s", code, start, end)
                return cached.loc[start:end]
            if head_ok:
                tail_start = cached.index[-1] - timedelta(days=10)
                new = self._fetch_daily(code, tail_start, end)
                overlap = cached.index.intersection(new.index)
                if len(overlap):
                    drift = (cached.loc[overlap, "close"] / new.loc[overlap, "close"] - 1).abs().max()
                    if drift > 1e-4:
                        log.warning("%s: cached prices differ from fresh (adj. change?) -> full refetch", code)
                        full = self._fetch_daily(code, start, end)
                        self.daily_cache.save(code, full, start)
                        return full.loc[start:end]
                merged = pd.concat([cached[~cached.index.isin(new.index)], new]).sort_index()
                self.daily_cache.save(code, merged, req_start)
                log.info("cache extended: %s (+%d bars from API)", code, len(new.index.difference(cached.index)))
                return merged.loc[start:end]
        df = self._fetch_daily(code, start, end)
        if len(df):
            self.daily_cache.save(code, df, start)
        return df.loc[start:end] if len(df) else df

    def raw_daily_bars(self, code: str, start: date, end: date | None = None) -> pd.DataFrame:
        """As-traded (unadjusted) daily bars. Past bars never change, so the cache only extends."""
        end = end or mc.last_completed_session(mc.now_utc())
        cached, meta = self.raw_cache.load(code)
        if cached is not None and len(cached):
            req_start = date.fromisoformat(meta.get("requested_start", str(cached.index[0])))
            if req_start <= start and cached.index[-1] >= end:
                self.cache_hits += 1
                return cached.loc[start:end]
            if req_start <= start:
                new = self._fetch_daily(code, cached.index[-1] + timedelta(days=1), end, autype="NONE")
                merged = pd.concat([cached, new[~new.index.isin(cached.index)]]).sort_index() if len(new) else cached
                self.raw_cache.save(code, merged, req_start)
                return merged.loc[start:end]
        df = self._fetch_daily(code, start, end, autype="NONE")
        if len(df):
            self.raw_cache.save(code, df, start)
        return df.loc[start:end] if len(df) else df

    def intraday_bars(self, code: str, days: int = 10, ktype: str = "K_1M") -> pd.DataFrame:
        """Recent 1-minute bars (regular session). Not cached (short-lived use)."""
        sdk = self.sdk
        end = mc.now_utc().astimezone(mc.ET).date()
        start = end - timedelta(days=days * 2)
        frames, key = [], None
        for _ in range(50):
            ret, data, key = self._call("request_history_kline", code, what=f"intraday {code}",
                                        start=start.isoformat(), end=end.isoformat(),
                                        ktype=getattr(sdk.KLType, ktype), autype=sdk.AuType.QFQ,
                                        max_count=self.page_size, page_req_key=key)
            if isinstance(data, pd.DataFrame) and not data.empty:
                frames.append(data)
            if key is None:
                break
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames).drop_duplicates("time_key")
        df["ts_et"] = pd.to_datetime(df["time_key"]).dt.tz_localize(mc.ET)
        return df.sort_values("ts_et").reset_index(drop=True)

    # ------------------------------------------------------------- snapshots
    def snapshot(self, codes: list[str]) -> pd.DataFrame:
        frames = []
        for i in range(0, len(codes), 400):
            batch = codes[i:i + 400]
            _, data = self._call("get_market_snapshot", batch, what=f"snapshot[{len(batch)}]")[:2]
            if isinstance(data, pd.DataFrame) and not data.empty:
                frames.append(data)
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        # update_time for US stocks is documented as US Eastern.
        if "update_time" in df.columns:
            ts = pd.to_datetime(df["update_time"], errors="coerce")
            df["update_time_et"] = ts.dt.tz_localize(mc.ET, ambiguous="NaT", nonexistent="NaT")
        return df

    # ------------------------------------------------------------ static info
    def us_stock_names(self, max_age_days: float = 7) -> pd.DataFrame:
        key = "us_basicinfo"
        if self.misc_cache.fresh(key, max_age_days * 86400):
            cached = self.misc_cache.read_df(key)
            if "exchange_type" in cached.columns:
                return cached
        sdk = self.sdk
        _, data = self._call("get_stock_basicinfo", sdk.Market.US, sdk.SecurityType.STOCK)[:2]
        keep = [c for c in ("code", "name", "listing_date", "delisting", "exchange_type") if c in data.columns]
        df = data[keep].copy()
        self.misc_cache.write_df(key, df)
        return df

    # --------------------------------------------------------------- earnings
    def earnings_calendar(self, begin: date, end: date) -> pd.DataFrame:
        """Moomoo get_earnings_calendar for [begin, end], ONE DAY PER REQUEST.

        The response has no paging and no total count, so a server-side row cap
        would be silent. Querying single days keeps each response as small as
        possible, and sorting by market cap means that if a cap exists the
        largest companies (this universe) survive it. Per-day row counts are
        checked for a suspicious plateau that would suggest a cap.

        Days more than 3 days in the past are cached permanently; recent ones for 1h.
        First backtest run from 2019 is ~2,800 requests (~28 min at 50/30s); cached after.
        """
        sdk = self.sdk
        sort_enum = getattr(sdk, "EarningsCalendarSortType", None)
        sort_type = getattr(sort_enum, "MARKET_CAP", None) if sort_enum is not None else None
        out, counts = [], {}
        today_et = mc.now_utc().astimezone(mc.ET).date()
        d = begin
        while d <= end:
            key = f"earn_day_{d.isoformat()}"
            max_age = None if d < today_et - timedelta(days=3) else 3600
            if self.earn_cache.fresh(key, max_age):
                df = self.earn_cache.read_df(key)
            else:
                _, df = self._call("get_earnings_calendar", what=f"earnings_calendar {d}",
                                   market=sdk.Market.US, sort_type=sort_type,
                                   begin_date=d.isoformat(), end_date=d.isoformat())[:2]
                df = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
                self.earn_cache.write_df(key, df)
            counts[d] = len(df)
            if len(df):
                out.append(df)
            d += timedelta(days=1)
        self.last_earnings_day_counts = counts
        cap = suspected_row_cap(counts)
        if cap:
            log.warning("earnings_calendar: %d days returned exactly %d rows (the max). This looks like a "
                        "server-side row cap: smaller companies may be missing on those days.",
                        sum(1 for v in counts.values() if v == cap), cap)
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()
