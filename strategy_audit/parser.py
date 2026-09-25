"""Plain English -> strategy DRAFT + explicit issues. Deterministic pattern matching; no code execution.

Rules:
* Text is only ever mapped onto approved primitives (schema.INDICATORS). Nothing is eval'd.
* Anything vague ("strong stock", "moving average" without simple/exponential, missing periods,
  S&P 500 when that universe is not in the dataset) becomes an ISSUE with explicit OPTIONS.
  Nothing is silently assumed; the user must pick.
* Unsupported requests (short selling, options, crypto, intraday, fundamentals...) are rejected with
  a reason, never forced into the engine.
* Phrases that match nothing are reported back verbatim as "not understood".
* Options carry PATCHES (data, from a closed set of operations) that the server re-applies and
  re-validates with the schema.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import ValidationError

from .schema import StrategySpec

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "sixty": 60}
NUM = r"(\d+(?:\.\d+)?|" + "|".join(NUM_WORDS) + r")"
UNIT = r"(trading\s+days?|days?|sessions?|weeks?|months?)"
BELOW = {"below", "under", "less than", "lower than", "<", "<=", "beneath"}


def _n(s: str) -> float:
    s = s.strip().lower()
    return float(NUM_WORDS[s]) if s in NUM_WORDS else float(s)


def _op(word: str) -> str:
    w = word.strip()
    if w in ("<=", ">="):
        return w
    return "<" if w in BELOW else ">"


class _Ctx:
    def __init__(self):
        self.conditions: list[dict] = []
        self.universe = None
        self.execution = None
        self.exit: dict | None = None
        self.direction = None
        self.issues: list[dict] = []
        self.matched: list[dict] = []
        self.unmatched: list[str] = []

    def issue(self, kind: str, message: str, options: list[tuple[str, list[dict]]] | None = None, phrase: str = ""):
        self.issues.append({"id": f"i{len(self.issues) + 1}", "kind": kind, "phrase": phrase, "message": message,
                            "options": [{"label": lbl, "patch": p} for lbl, p in (options or [])]})

    def cond(self, c: dict, phrase: str, meaning: str) -> int:
        self.conditions.append(c)
        self.matched.append({"phrase": phrase, "meaning": meaning})
        return len(self.conditions) - 1


def _add(c: dict) -> dict:
    return {"op": "add_condition", "value": c}


def _setf(i: int, field: str, value: Any) -> dict:
    return {"op": "set_condition_field", "index": i, "field": field, "value": value}


# ------------------------------------------------------------------ patterns
UNSUPPORTED = [
    (r"\b(sell\s+short|short[- ]?sell\w*|shorting|go\s+short|short\s+the)\b|\bshort\b(?![- ]term)",
     "Short selling is not supported in V0 (long-only)."),
    (r"\b(options?|calls?|puts?|straddles?|strangles?)\b", "Options are not supported in V0 (US stocks only)."),
    (r"\b(crypto\w*|bitcoin|btc|ethereum|eth|coins?)\b", "Crypto is not supported in V0 (US stocks only)."),
    (r"\b(forex|fx|currenc(y|ies)|eur/?usd)\b", "Forex is not supported in V0 (US stocks only)."),
    (r"\b(futures?)\b", "Futures are not supported in V0."),
    (r"\b(intraday|minute|hourly|scalp\w*|5[- ]?min|1[- ]?min|day[- ]?trad\w*)\b",
     "Intraday strategies are not supported in V0 (daily candles only)."),
    (r"\b(leverage\w*|margin|2x|3x)\b", "Leverage is not supported in V0."),
    (r"\b(earnings|p/?e\b|pe ratio|revenue|eps|fundamental\w*|valuation|undervalued|cheap|dividend\w*|news|sentiment|twitter|reddit|analyst\w*)\b",
     "Fundamental, news and sentiment data are not in the V0 dataset (price and volume only)."),
    (r"\btrailing\s+stop\b", "Trailing stops are not supported in V0 (fixed stop-loss / take-profit only)."),
    (r"\b(machine learning|neural|ai model|predict\w*)\b", "Model-based predictions are not supported; describe explicit rules."),
]

VAGUE = [
    (r"\boversold\b", "\"Oversold\" is ambiguous.",
     [("RSI(14) below 30", [_add({"indicator": "RSI", "period": 14, "operator": "<", "value": 30})]),
      ("RSI(14) below 20", [_add({"indicator": "RSI", "period": 14, "operator": "<", "value": 20})])]),
    (r"\boverbought\b", "\"Overbought\" is ambiguous.",
     [("RSI(14) above 70", [_add({"indicator": "RSI", "period": 14, "operator": ">", "value": 70})]),
      ("RSI(14) above 80", [_add({"indicator": "RSI", "period": 14, "operator": ">", "value": 80})])]),
    (r"\b(strong|strength|leaders?|leading)\b", "\"Strong stock\" is ambiguous.",
     [("Outperforming SPY over 60 days", [_add({"indicator": "REL_STRENGTH", "period": 60, "operator": ">", "value": 0})]),
      ("Above its 50-day SMA", [_add({"indicator": "CLOSE", "operator": ">", "comparison_indicator": "SMA", "comparison_period": 50})]),
      ("Both", [_add({"indicator": "REL_STRENGTH", "period": 60, "operator": ">", "value": 0}),
                _add({"indicator": "CLOSE", "operator": ">", "comparison_indicator": "SMA", "comparison_period": 50})])]),
    (r"\buptrend\b", "\"Uptrend\" is ambiguous.",
     [("Above its 200-day SMA", [_add({"indicator": "CLOSE", "operator": ">", "comparison_indicator": "SMA", "comparison_period": 200})]),
      ("Above its 50-day SMA", [_add({"indicator": "CLOSE", "operator": ">", "comparison_indicator": "SMA", "comparison_period": 50})])]),
    (r"\bdowntrend\b", "\"Downtrend\" is ambiguous.",
     [("Below its 200-day SMA", [_add({"indicator": "CLOSE", "operator": "<", "comparison_indicator": "SMA", "comparison_period": 200})]),
      ("Below its 50-day SMA", [_add({"indicator": "CLOSE", "operator": "<", "comparison_indicator": "SMA", "comparison_period": 50})])]),
    (r"\b(breakout|breaks? out)\b", "\"Breakout\" is ambiguous.",
     [("Close above the prior 20-session high", [_add({"indicator": "CLOSE", "operator": ">", "comparison_indicator": "HIGH", "comparison_period": 20})]),
      ("Close above the prior 50-session high", [_add({"indicator": "CLOSE", "operator": ">", "comparison_indicator": "HIGH", "comparison_period": 50})])]),
    (r"\bmomentum\b", "\"Momentum\" is ambiguous.",
     [("Up over the last 60 sessions", [_add({"indicator": "RETURN", "period": 60, "operator": ">", "value": 0})]),
      ("Outperforming SPY over 60 sessions", [_add({"indicator": "REL_STRENGTH", "period": 60, "operator": ">", "value": 0})])]),
    (r"\b(high|heavy|big|unusual|huge|strong)\s+volume\b", "\"High volume\" is ambiguous.",
     [("Volume at least 1.5x its 20-session average", [_add({"indicator": "REL_VOLUME", "period": 20, "operator": ">=", "value": 1.5})]),
      ("Volume at least 2x its 20-session average", [_add({"indicator": "REL_VOLUME", "period": 20, "operator": ">=", "value": 2.0})])]),
    (r"\bbull\s+market\b", "\"Bull market\" is ambiguous.",
     [("SPY above its 200-day SMA", [_add({"indicator": "SPY_CLOSE", "operator": ">", "comparison_indicator": "SPY_SMA", "comparison_period": 200})])]),
    (r"\bbear\s+market\b", "\"Bear market\" is ambiguous.",
     [("SPY at or below its 200-day SMA", [_add({"indicator": "SPY_CLOSE", "operator": "<=", "comparison_indicator": "SPY_SMA", "comparison_period": 200})])]),
]

FILLER = re.compile(r"\b(buy|buying|purchase|go|long|enter|entry|stocks?|shares?|the|a|an|it|its|they|them|when|if|i|we|"
                    r"you|my|strategy|and|then|also|only|is|are|has|have|been|remains?|stays?|still|trade|trading|of|"
                    r"to|at|on|for|with|position|positions|that|this|which|should|will|would|us|all|any|every|some|"
                    r"just|simply|please|condition|conditions|rule|rules|signal|signals|following|day|days)\b")

RSI_RE = re.compile(r"\brsi\s*(?:\(\s*(\d+)\s*\)|(\d+)(?=\s))?\s*(?:is\s+|falls?\s+|fell\s+|drops?\s+|dropped\s+|goes\s+|gets\s+|"
                    r"rises?\s+|rose\s+|moves?\s+|stays?\s+|remains?\s+|crosses\s+|crossed\s+)*"
                    r"(below|under|less than|lower than|beneath|<=|<|above|over|greater than|higher than|>=|>)\s*"
                    r"(\d+(?:\.\d+)?)")
MA_RE = re.compile(r"\b(above|below|over|under)\s+(?:its|the|their|a)?\s*(\d+)\s*[- ]?\s*(?:day|d|session|period)?s?\s*"
                   r"(simple\s+|exponential\s+)?(moving\s+average|sma|ema|ma)\b")
MA2_RE = re.compile(r"\b(?:close|price)\s*(>=|<=|>|<)\s*(sma|ema)\s*\(?\s*(\d+)\s*\)?")
MKT_RE = re.compile(r"\b(s&p\s*500|s&p|spy|spx|market|index)\s+(?:is\s+|remains?\s+|stays?\s+|trades?\s+|trading\s+)?"
                    r"(above|below|over|under)\s+(?:its|the)?\s*(\d+)\s*[- ]?\s*(?:day|d|session)?s?\s*"
                    r"(simple\s+|exponential\s+)?(moving\s+average|sma|ema|ma)\b")
RET_DOWN_RE = re.compile(r"\b(?:fallen|fell|falls|dropped|drops|declined|declines|down|lost|loses|decreased|sold\s+off)\s+"
                         r"(?:by\s+)?(at\s+least|more\s+than|over|by|)\s*" + NUM + r"\s*(?:%|percent)"
                         r"(?:\s*(?:over|in|during|within|across)\s+(?:the\s+)?(?:previous|past|last|prior)?\s*" + NUM + r"\s*" + UNIT + r")?")
RET_UP_RE = re.compile(r"\b(?:risen|rose|rises|gained|gains|up|rallied|rallies|increased|climbed)\s+"
                       r"(?:by\s+)?(at\s+least|more\s+than|over|by|)\s*" + NUM + r"\s*(?:%|percent)"
                       r"(?:\s*(?:over|in|during|within|across)\s+(?:the\s+)?(?:previous|past|last|prior)?\s*" + NUM + r"\s*" + UNIT + r")?")
VOL_RE = re.compile(r"\bvolume\s+(?:is\s+)?(?:at\s+least\s+|more\s+than\s+|above\s+|over\s+|>=?\s*)?" + NUM +
                    r"\s*(?:x|times)\s*(?:its\s+|the\s+)?(?:average|normal|avg|usual)"
                    r"(?:\s+(?:of\s+|over\s+)?(?:the\s+)?(?:previous|past|last|prior)?\s*" + NUM + r"\s*" + UNIT + r")?")
HIGH_RE = re.compile(r"\b(?:new|makes?\s+a|hits?\s+a|closes?\s+at\s+a|breaks?\s+(?:above|out\s+(?:above|to|of))?\s*(?:a|the)?)\s*"
                     + NUM + r"\s*[- ]?\s*(day|session|week)s?\s+(high|low)")
PRICE_RE = re.compile(r"\b(?:price|trading|trades|stock|close|closes)\s+(?:is\s+)?(above|below|over|under)\s+\$\s*(\d+(?:\.\d+)?)")
RS_RE = re.compile(r"\b(?:outperform\w*|stronger\s+than|beat\w*|better\s+than)\s+(?:the\s+)?(spy|s&p\s*500|market)\s+"
                   r"(?:over|in|during)\s+(?:the\s+)?(?:past|last|previous|prior)?\s*" + NUM + r"\s*" + UNIT)
EXEC_NEXT_RE = re.compile(r"\bnext\s+(?:trading\s+)?(?:day'?s?|session'?s?)?\s*open\b|\bfollowing\s+(?:day'?s?\s+|session'?s?\s+)?open\b|"
                          r"\bopen\s+(?:of\s+)?the\s+next\s+(?:trading\s+)?(?:day|session)\b|\bnext\s+open\b")
EXEC_SAME_RE = re.compile(r"\b(?:same\s+day|at\s+the\s+close|on\s+the\s+close|market\s+on\s+close|immediately|right\s+away)\b")
HOLD_RE = re.compile(r"\b(?:hold(?:ing)?|keep|sell\s+after|exit\s+after|close\s+after|sell\s+it\s+after)\s+(?:it\s+|them\s+|the\s+position\s+)?"
                     r"(?:for\s+)?" + NUM + r"\s*" + UNIT)
STOP_RE = re.compile(r"\bstop[- ]?loss\s+(?:of\s+|at\s+)?" + NUM + r"\s*%|\b(?:sell|exit|cut)\s+(?:early\s+)?(?:if|when)\s+(?:it\s+|the\s+stock\s+)?"
                     r"(?:falls|drops|loses|declines)\s+(?:by\s+)?" + NUM + r"\s*%")
TP_RE = re.compile(r"\btake[- ]?profit\s+(?:of\s+|at\s+)?" + NUM + r"\s*%|\b(?:sell|exit)\s+(?:early\s+)?(?:if|when)\s+(?:it\s+|the\s+stock\s+)?"
                   r"(?:rises|gains|is\s+up|goes\s+up)\s+(?:by\s+)?" + NUM + r"\s*%")
UNIVERSE_SP_RE = re.compile(r"\b(s&p\s*500|s&p|sp\s*500|spx|sp500)\b")
UNIVERSE_OK_RE = re.compile(r"\b(large[- ]?caps?|big\s+(?:us\s+)?stocks|mega[- ]?caps?|93\s+stocks|large\s+us\s+stocks)\b")
TICKER_RE = re.compile(r"\b(?:only|just)\s+(?:buy\s+)?([A-Z]{1,5})\b")


def _unit_sessions(ctx: _Ctx, n: float, unit: str, phrase: str, make) -> None:
    """Convert N units to sessions; weeks/months are ambiguous -> ask."""
    unit = unit.lower()
    if unit.startswith(("trading", "day", "session")):
        make(int(round(n)))
    elif unit.startswith("week"):
        ctx.issue("ambiguous", f"\"{int(n)} week(s)\" must be expressed in trading sessions.",
                  [(f"{int(n * 5)} trading sessions (5 per week)", make(int(n * 5), as_patch=True))], phrase)
    else:
        ctx.issue("ambiguous", f"\"{int(n)} month(s)\" must be expressed in trading sessions.",
                  [(f"{int(n * 21)} trading sessions (about 21 per month)", make(int(n * 21), as_patch=True))], phrase)


def interpret(text: str) -> dict:
    """Return {status, draft, spec, issues, matched, unmatched}. status: ok | needs_clarification | unsupported."""
    raw = (text or "").strip()
    if not raw:
        return {"status": "unsupported", "draft": None, "spec": None, "matched": [], "unmatched": [],
                "issues": [{"id": "i1", "kind": "unsupported", "phrase": "", "message": "Describe a strategy first.",
                            "options": []}]}
    if len(raw) > 2000:
        raw = raw[:2000]
    t = raw.lower().replace("’", "'").replace("≤", "<=").replace("≥", ">=")
    ctx = _Ctx()
    consumed: list[tuple[int, int]] = []

    def eat(m):
        consumed.append(m.span())

    # 1 unsupported (hard stop, but keep scanning so all reasons are shown)
    for rx, why in UNSUPPORTED:
        for m in re.finditer(rx, t):
            ctx.issue("unsupported", why, None, raw[m.start():m.end()])
            eat(m)
            break

    # 2 market filter before universe (so "S&P 500 above its 200-day" is a filter, not a universe)
    for m in MKT_RE.finditer(t):
        n, kind = int(m.group(3)), (m.group(4) or "").strip()
        op = _op(m.group(2))
        base = {"indicator": "SPY_CLOSE", "operator": op, "comparison_indicator": "SPY_SMA", "comparison_period": n}
        if kind == "exponential" or m.group(5) == "ema":
            ctx.issue("unsupported", "An exponential moving average of SPY is not supported; use a simple average.",
                      None, m.group(0))
        elif kind == "simple" or m.group(5) == "sma":
            ctx.cond(base, m.group(0), f"SPY close {op} SPY SMA({n})")
        else:
            ctx.issue("ambiguous", f"\"{n}-day moving average\" of the market: simple (SMA) is the supported type.",
                      [(f"SPY close {op} SPY 200-day SIMPLE average" if n == 200 else f"SPY close {op} SPY {n}-day SIMPLE average",
                        [_add(base)])], m.group(0))
        eat(m)

    # 3 conditions
    for m in RSI_RE.finditer(t):
        per = m.group(1) or m.group(2)
        op, val = _op(m.group(3)), float(m.group(4))
        if "cross" in m.group(0):
            ctx.issue("ambiguous", "\"Crosses\" describes a change between two days; V0 tests the level on the signal day.",
                      [(f"RSI {op} {val:g} on the signal day", [])], m.group(0))
        c = {"indicator": "RSI", "period": int(per) if per else None, "operator": op, "value": val}
        if per:
            ctx.cond(c, m.group(0), f"RSI({per}) {op} {val:g}")
        else:
            ctx.issue("missing", "RSI period not specified.",
                      [("RSI(14), the standard period", [_add({**c, "period": 14})]),
                       ("RSI(7)", [_add({**c, "period": 7})]), ("RSI(21)", [_add({**c, "period": 21})])], m.group(0))
        eat(m)
    for m in MA_RE.finditer(t):
        if any(a <= m.start() < b for a, b in consumed):
            continue
        op, n, kind, word = _op(m.group(1)), int(m.group(2)), (m.group(3) or "").strip(), m.group(4)
        base = {"indicator": "CLOSE", "operator": op, "comparison_period": n}
        if kind == "exponential" or word == "ema":
            ctx.cond({**base, "comparison_indicator": "EMA"}, m.group(0), f"Close {op} EMA({n})")
        elif kind == "simple" or word == "sma":
            ctx.cond({**base, "comparison_indicator": "SMA"}, m.group(0), f"Close {op} SMA({n})")
        else:
            ctx.issue("ambiguous", f"\"{n}-day moving average\" could be simple or exponential.",
                      [(f"Simple (SMA {n})", [_add({**base, "comparison_indicator": "SMA"})]),
                       (f"Exponential (EMA {n})", [_add({**base, "comparison_indicator": "EMA"})])], m.group(0))
        eat(m)
    for m in MA2_RE.finditer(t):
        ind = m.group(2).upper()
        ctx.cond({"indicator": "CLOSE", "operator": m.group(1), "comparison_indicator": ind,
                  "comparison_period": int(m.group(3))}, m.group(0), f"Close {m.group(1)} {ind}({m.group(3)})")
        eat(m)
    for rx, sign in ((RET_DOWN_RE, -1), (RET_UP_RE, 1)):
        for m in rx.finditer(t):
            if any(a <= m.start() < b for a, b in consumed):
                continue
            pct = _n(m.group(2)) / 100 * sign
            op = "<=" if sign < 0 else ">="
            if m.group(3):
                n, unit = _n(m.group(3)), m.group(4)

                def make(k, as_patch=False, pct=pct, op=op):
                    c = {"indicator": "RETURN", "period": k, "operator": op, "value": round(pct, 4)}
                    if as_patch:
                        return [_add(c)]
                    ctx.cond(c, m.group(0), f"{k}-session return {op} {pct * 100:+.1f}%")
                _unit_sessions(ctx, n, unit, m.group(0), make)
            else:
                ctx.issue("missing", f"Over how many trading sessions is the {abs(pct) * 100:g}% move measured?",
                          [(f"{k} sessions", [_add({"indicator": "RETURN", "period": k, "operator": op, "value": round(pct, 4)})])
                           for k in (1, 5, 20)], m.group(0))
            eat(m)
    for m in VOL_RE.finditer(t):
        x = _n(m.group(1))
        if m.group(2):
            n = int(_n(m.group(2)))
            ctx.cond({"indicator": "REL_VOLUME", "period": n, "operator": ">=", "value": x}, m.group(0),
                     f"Volume >= {x:g}x prior {n}-session average")
        else:
            ctx.issue("missing", "Average volume over how many sessions?",
                      [(f"{k} sessions", [_add({"indicator": "REL_VOLUME", "period": k, "operator": ">=", "value": x})])
                       for k in (20, 50)], m.group(0))
        eat(m)
    for m in HIGH_RE.finditer(t):
        n, unit, hl = _n(m.group(1)), m.group(2), m.group(3)
        sessions = int(n * 5) if unit == "week" else int(n)
        if unit == "week" and int(n) == 52:
            sessions = 252
        ind, op = ("HIGH", ">") if hl == "high" else ("LOW", "<")
        c = {"indicator": "CLOSE", "operator": op, "comparison_indicator": ind, "comparison_period": min(sessions, 252)}
        ctx.cond(c, m.group(0), f"Close {op} prior {c['comparison_period']}-session {hl}")
        eat(m)
    for m in PRICE_RE.finditer(t):
        op, v = _op(m.group(1)), float(m.group(2))
        ctx.cond({"indicator": "CLOSE", "operator": op, "value": v}, m.group(0), f"Close {op} ${v:,.2f}")
        eat(m)
    for m in RS_RE.finditer(t):
        n, unit = _n(m.group(2)), m.group(3)

        def make(k, as_patch=False):
            c = {"indicator": "REL_STRENGTH", "period": k, "operator": ">", "value": 0}
            if as_patch:
                return [_add(c)]
            ctx.cond(c, m.group(0), f"{k}-session return above SPY's")
        _unit_sessions(ctx, n, unit, m.group(0), make)
        eat(m)

    # 4 vague words (only where not already consumed by a precise pattern)
    for rx, msg, opts in VAGUE:
        for m in re.finditer(rx, t):
            if any(a <= m.start() < b for a, b in consumed):
                continue
            ctx.issue("ambiguous", msg + " Choose a precise definition:", opts, raw[m.start():m.end()])
            eat(m)
            break

    # 5 execution
    m = EXEC_NEXT_RE.search(t)
    if m:
        ctx.execution = "NEXT_OPEN"
        ctx.matched.append({"phrase": m.group(0), "meaning": "Enter at the next session's open"})
        eat(m)
    else:
        m2 = EXEC_SAME_RE.search(t)
        ctx.issue("ambiguous" if m2 else "missing",
                  ("Buying at the same close the signal is measured on would use information you don't have yet "
                   "(lookahead). The earliest realistic entry is the next session's open." if m2 else
                   "Entry timing not specified."),
                  [("Enter at the next trading session's open", [{"op": "set_execution", "value": "NEXT_OPEN"}])],
                  m2.group(0) if m2 else "")
        if m2:
            eat(m2)

    # 6 exit
    m = HOLD_RE.search(t)
    if m:
        n, unit = _n(m.group(1)), m.group(2)

        def make(k, as_patch=False):
            if as_patch:
                return [{"op": "set_exit_days", "value": k}]
            ctx.exit = {"type": "FIXED_HOLD", "trading_days": k}
            ctx.matched.append({"phrase": m.group(0), "meaning": f"Hold {k} trading sessions"})
        _unit_sessions(ctx, n, unit, m.group(0), make)
        eat(m)
    else:
        ctx.issue("missing", "No exit rule found. How long should each trade be held?",
                  [(f"{k} trading sessions", [{"op": "set_exit_days", "value": k}]) for k in (5, 10, 20)])
    m = STOP_RE.search(t)
    if m:
        v = _n(m.group(1) or m.group(2)) / 100
        ctx.exit = {**(ctx.exit or {"type": "FIXED_HOLD"}), "stop_loss_pct": v}
        ctx.matched.append({"phrase": m.group(0), "meaning": f"Stop-loss {v * 100:g}% (checked on closes)"})
        eat(m)
    m = TP_RE.search(t)
    if m:
        v = _n(m.group(1) or m.group(2)) / 100
        ctx.exit = {**(ctx.exit or {"type": "FIXED_HOLD"}), "take_profit_pct": v}
        ctx.matched.append({"phrase": m.group(0), "meaning": f"Take-profit {v * 100:g}% (checked on closes)"})
        eat(m)

    # 7 universe (after filters; only one universe exists in the current dataset)
    tickers = TICKER_RE.search(raw)
    if tickers:
        ctx.issue("unsupported", "Single-stock or custom ticker lists are not supported in V0.", None, tickers.group(0))
    rest = "".join(ch if not any(a <= i < b for a, b in consumed) else " " for i, ch in enumerate(t))
    mu = UNIVERSE_SP_RE.search(rest)
    if mu:
        ctx.issue("ambiguous", "S&P 500 membership history is not in the current dataset.",
                  [("Use the 93 large-cap US stocks available (today's large caps: survivorship-biased)",
                    [{"op": "set_universe", "value": "LARGE_CAP_93"}])], raw[mu.start():mu.end()])
        consumed.append(mu.span())
    elif UNIVERSE_OK_RE.search(rest):
        m = UNIVERSE_OK_RE.search(rest)
        ctx.universe = "LARGE_CAP_93"
        ctx.matched.append({"phrase": m.group(0), "meaning": "93 large-cap US stocks (current dataset)"})
        consumed.append(m.span())
    else:
        ctx.issue("missing", "Which stocks? Only one universe is available in the current dataset.",
                  [("93 large-cap US stocks (survivorship-biased)", [{"op": "set_universe", "value": "LARGE_CAP_93"}])])

    # 8 direction
    ctx.direction = "LONG"

    # 9 anything left unexplained is reported, never dropped
    rest = "".join(ch if not any(a <= i < b for a, b in consumed) else " " for i, ch in enumerate(t))
    for piece in re.split(r"[,;\n]|\.(?=\s|$)|\band\b|\bthen\b|\bwhen\b|\bwhile\b|\bif\b", rest):
        left = FILLER.sub(" ", piece)
        left = re.sub(r"[^a-z0-9%$]+", " ", left).strip()
        if len(left) >= 3 and re.search(r"[a-z]{3,}", left):
            ctx.unmatched.append(piece.strip())
            ctx.issue("not_understood", f"Not understood: \"{piece.strip()}\". It will NOT be part of the test "
                      "unless you rephrase it using supported rules.",
                      [("Leave it out of the test", [])], piece.strip())

    if not ctx.conditions and not any(o for i in ctx.issues for o in i["options"] if any(p["op"] == "add_condition" for p in o["patch"])):
        ctx.issue("missing", "No entry condition was recognised. Describe at least one rule (e.g. RSI(14) below 30).")

    draft = {"name": _auto_name(ctx.conditions), "universe": {"type": ctx.universe} if ctx.universe else None,
             "direction": ctx.direction, "entry_conditions": ctx.conditions, "entry_execution": ctx.execution,
             "exit": ctx.exit, "benchmark": "SPY"}
    status = ("unsupported" if any(i["kind"] == "unsupported" for i in ctx.issues)
              else "needs_clarification" if ctx.issues else "ok")
    spec, errors = (validate_draft(draft) if status == "ok" else (None, []))
    if status == "ok" and spec is None:
        status = "needs_clarification"
        for e in errors:
            ctx.issue("invalid", e)
    return {"status": status, "draft": draft, "spec": spec, "issues": ctx.issues, "matched": ctx.matched,
            "unmatched": ctx.unmatched}


def _auto_name(conds: list[dict]) -> str:
    if not conds:
        return "My strategy"
    bits = []
    for c in conds[:3]:
        bits.append(c["indicator"].replace("_", " ").title().replace("Rsi", "RSI").replace("Spy", "SPY"))
    return (" + ".join(bits))[:80]


# ------------------------------------------------------------- resolution
ALLOWED_OPS = {"add_condition", "set_condition_field", "set_universe", "set_execution", "set_exit_days"}


def apply_patches(draft: dict, patches: list[dict]) -> dict:
    """Apply option patches from a closed set of operations. Output is still just data (validated next)."""
    d = copy.deepcopy(draft or {})
    d.setdefault("entry_conditions", [])
    for p in patches or []:
        op = p.get("op")
        if op not in ALLOWED_OPS:
            raise ValueError(f"operation {op!r} not allowed")
        if op == "add_condition":
            if not isinstance(p.get("value"), dict):
                raise ValueError("condition must be an object")
            d["entry_conditions"].append(dict(p["value"]))
        elif op == "set_condition_field":
            i, f = int(p["index"]), p["field"]
            if f not in ("period", "comparison_period", "comparison_indicator", "value", "operator"):
                raise ValueError("field not allowed")
            d["entry_conditions"][i][f] = p["value"]
        elif op == "set_universe":
            d["universe"] = {"type": p["value"]}
        elif op == "set_execution":
            d["entry_execution"] = p["value"]
        elif op == "set_exit_days":
            d["exit"] = {**(d.get("exit") or {"type": "FIXED_HOLD"}), "type": "FIXED_HOLD", "trading_days": int(p["value"])}
    return d


def validate_draft(draft: dict) -> tuple[dict | None, list[str]]:
    """Strict schema validation. Returns (spec as JSON dict, errors)."""
    try:
        spec = StrategySpec.model_validate(draft)
        return spec.model_dump(mode="json"), []
    except ValidationError as e:
        return None, [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
    except Exception as e:           # malformed input structure
        return None, [str(e)]
