"""Orchestration: versions/lineage, deterministic backtests, audits, sealed validation, reports.

Lineage model (a research WARNING, not enforcement):
* A strategy FAMILY groups related versions. Editing a strategy from its results page creates version N+1
  in the same family. Separately, a new submission from the same anonymous browser whose rule SIGNATURE
  (the set of indicators used) matches an existing family is attached to that family automatically.
* Renaming, other browsers or other accounts are NOT detected; the UI says so.
Sealed validation: the last 30% of sessions. Revealed once per family, recorded permanently; later
versions see the result marked as "previously revealed" (no longer an unseen test).
"""
from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict
from datetime import date, datetime

import numpy as np
import pandas as pd

from . import ENGINE_VERSION, METHODOLOGY_VERSION
from . import assess as A
from .db import DB, new_id, now
from .parser import apply_patches, interpret, validate_draft
from .quant import robustness as R
from .quant.data import MarketDataProvider, default_provider
from .quant.engine import Market, run_backtest
from .quant.lookahead import lookahead_audit
from .schema import StrategySpec

DEMO_TEXT = ("Buy S&P 500 stocks when RSI(14) is below 30, the stock remains above its 200-day moving average, "
             "and it has fallen at least 5% over the previous five trading days. Enter at the next trading day's open. "
             "Hold for 10 trading days.")


def clean(o):
    """JSON-safe: numpy -> python, NaN/inf -> None, dates -> ISO strings."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (date, datetime, pd.Timestamp)):
        return o.isoformat() if not isinstance(o, pd.Timestamp) else o.date().isoformat()
    return o


class NotFound(Exception):
    pass


class AuditService:
    def __init__(self, db: DB | None = None, provider: MarketDataProvider | None = None):
        self.db = db or DB()
        self.provider = provider or default_provider()
        self._mkt: Market | None = None
        self._lock = threading.Lock()

    @property
    def mkt(self) -> Market:
        with self._lock:
            if self._mkt is None:
                self._mkt = Market(self.provider, "LARGE_CAP_93")
            return self._mkt

    def dataset(self) -> dict:
        return clean(asdict(self.provider.info("LARGE_CAP_93")))

    # ------------------------------------------------------------ interpret
    def interpret(self, text: str) -> dict:
        return clean(interpret(text))

    def resolve(self, draft: dict, patches: list[dict], name: str | None) -> dict:
        d = apply_patches(draft, patches)
        if name:
            d["name"] = name
        spec, errors = validate_draft(d)
        out = {"spec": spec, "errors": errors, "draft": d}
        if spec:
            s = StrategySpec.model_validate(spec)
            out.update(rules=[c.describe() for c in s.entry_conditions], exit_text=s.exit.describe())
        return out

    # --------------------------------------------------------------- lineage
    @staticmethod
    def _signature(spec: StrategySpec) -> str:
        inds = sorted({c.indicator for c in spec.entry_conditions} |
                      {c.comparison_indicator for c in spec.entry_conditions if c.comparison_indicator})
        return spec.universe.type + ":" + ",".join(inds)

    @staticmethod
    def _diff(old: dict, new: dict, prefix: str = "") -> list[str]:
        out = []
        for k in sorted(set(old) | set(new)):
            a, b = old.get(k), new.get(k)
            if isinstance(a, dict) and isinstance(b, dict):
                out += AuditService._diff(a, b, f"{prefix}{k}.")
            elif isinstance(a, list) and isinstance(b, list) and k == "entry_conditions":
                sa = [StrategySpec.model_validate({**_STUB, "entry_conditions": [c]}).entry_conditions[0].describe() for c in a]
                sb = [StrategySpec.model_validate({**_STUB, "entry_conditions": [c]}).entry_conditions[0].describe() for c in b]
                out += [f"removed: {x}" for x in sa if x not in sb] + [f"added: {x}" for x in sb if x not in sa]
            elif a != b and k != "name":
                out.append(f"{prefix}{k}: {a} -> {b}")
        return out

    def create_version(self, spec_dict: dict, anon: str | None, family_id: str | None = None) -> dict:
        spec = StrategySpec.model_validate(spec_dict)
        sig = self._signature(spec)
        related = False
        if family_id:
            fam = self.db.one("SELECT * FROM families WHERE id=?", (family_id,))
            if not fam:
                raise NotFound("family not found")
        else:
            fam = self.db.one("SELECT * FROM families WHERE anonymous_user_id=? AND signature=? ORDER BY created_at DESC",
                              (anon, sig)) if anon else None
            related = fam is not None
            if not fam:
                fid = new_id("fam")
                self.db.x("INSERT INTO families(id, created_at, anonymous_user_id, signature) VALUES (?,?,?,?)",
                          (fid, now(), anon, sig))
                fam = self.db.one("SELECT * FROM families WHERE id=?", (fid,))
        last = self.db.one("SELECT * FROM versions WHERE family_id=? ORDER BY version_no DESC", (fam["id"],))
        if last and last["spec_hash"] == spec.spec_hash():
            return {"family_id": fam["id"], "version_id": last["id"], "version_no": last["version_no"],
                    "related_detected": related, "reused": True}
        changes = self._diff(json.loads(last["spec_json"]), spec.model_dump(mode="json")) if last else ["initial version"]
        vid, no = new_id("ver"), (last["version_no"] + 1 if last else 1)
        self.db.x("INSERT INTO versions(id, family_id, version_no, created_at, name, spec_json, spec_hash, rules_hash, "
                  "changes_json) VALUES (?,?,?,?,?,?,?,?,?)",
                  (vid, fam["id"], no, now(), spec.name, spec.canonical(), spec.spec_hash(), spec.rules_hash(),
                   json.dumps(changes)))
        return {"family_id": fam["id"], "version_id": vid, "version_no": no, "related_detected": related, "reused": False}

    def _version(self, version_id: str) -> tuple[dict, StrategySpec]:
        v = self.db.one("SELECT * FROM versions WHERE id=?", (version_id,))
        if not v:
            raise NotFound("version not found")
        return v, StrategySpec.model_validate_json(v["spec_json"])

    def lineage(self, family_id: str) -> dict:
        fam = self.db.one("SELECT * FROM families WHERE id=?", (family_id,))
        if not fam:
            raise NotFound("family not found")
        vs = self.db.all("SELECT id, version_no, created_at, name, spec_hash, changes_json FROM versions "
                         "WHERE family_id=? ORDER BY version_no", (family_id,))
        rows = []
        for v in vs:
            bts = self.db.all("SELECT id, period, created_at FROM backtests WHERE version_id=?", (v["id"],))
            aus = self.db.all("SELECT id, evidence, created_at FROM audits WHERE version_id=?", (v["id"],))
            rows.append({"version_id": v["id"], "version_no": v["version_no"], "created_at": v["created_at"],
                         "name": v["name"], "changes": json.loads(v["changes_json"] or "[]"),
                         "results_viewed": any(b["period"] == "development" for b in bts),
                         "validation_viewed": any(b["period"] == "validation" for b in bts),
                         "audits": [{"id": a["id"], "evidence": a["evidence"], "at": a["created_at"]} for a in aus]})
        evaluated = sum(1 for r in rows if r["results_viewed"])
        return {"family_id": family_id, "created_at": fam["created_at"],
                "validation_revealed": bool(fam["validation_revealed"]), "revealed_at": fam["revealed_at"],
                "revealed_version_id": fam["revealed_version_id"], "versions": rows,
                "multiple_testing": A.multiple_testing(max(1, evaluated))}

    # -------------------------------------------------------------- backtest
    def backtest(self, version_id: str, period: str = "development") -> dict:
        v, spec = self._version(version_id)
        ds = self.provider.dataset_version()
        prev = self.db.one("SELECT * FROM backtests WHERE version_id=? AND period=? AND engine_version=? AND "
                           "dataset_version=?", (version_id, period, ENGINE_VERSION, ds))
        if prev:
            return {"backtest_id": prev["id"], "version_id": version_id, **json.loads(prev["results_json"])}
        res = clean(run_backtest(spec, self.mkt, period))
        bid = new_id("bt")
        self.db.x("INSERT INTO backtests(id, version_id, period, created_at, engine_version, methodology_version, "
                  "dataset_version, settings_json, results_json) VALUES (?,?,?,?,?,?,?,?,?)",
                  (bid, version_id, period, now(), ENGINE_VERSION, METHODOLOGY_VERSION, ds,
                   json.dumps(res["meta"]["settings"]), json.dumps(res)))
        return {"backtest_id": bid, "version_id": version_id, **res}

    def get_backtest(self, backtest_id: str) -> dict:
        b = self.db.one("SELECT * FROM backtests WHERE id=?", (backtest_id,))
        if not b:
            raise NotFound("backtest not found")
        v, spec = self._version(b["version_id"])
        fam = self.db.one("SELECT validation_revealed FROM families WHERE id=?", (v["family_id"],))
        return {"backtest_id": b["id"], "version_id": v["id"], "family_id": v["family_id"], "version_no": v["version_no"],
                "name": spec.name, "rules": [c.describe() for c in spec.entry_conditions], "exit": spec.exit.describe(),
                "validation_revealed": bool(fam["validation_revealed"]), **json.loads(b["results_json"])}

    # ----------------------------------------------------------------- audit
    def audit(self, backtest_id: str) -> dict:
        b = self.db.one("SELECT * FROM backtests WHERE id=?", (backtest_id,))
        if not b:
            raise NotFound("backtest not found")
        v, spec = self._version(b["version_id"])
        fam = self.db.one("SELECT * FROM families WHERE id=?", (v["family_id"],))
        mkt = self.mkt
        s, e = mkt.period("development")
        dev = run_backtest(spec, mkt, "development")
        led = dev["_ledger"]
        revealed = bool(fam["validation_revealed"])
        val_led, val_bt = None, None
        if revealed:
            vb = run_backtest(spec, mkt, "validation")
            val_led, val_bt = vb["_ledger"], vb
            self.backtest(v["id"], "validation")      # RECORD that this version's validation results were shown
        lin = self.lineage(fam["id"])
        audit = {
            "oos": R.out_of_sample(led, val_led, revealed),
            "sensitivity": R.sensitivity(spec, mkt, s, e, led),
            "costs": R.cost_stress(led, spec),
            "regimes": R.regime_test(led),
            "years": R.year_test(led),
            "outliers": R.outlier_test(led),
            "concentration": R.concentration_test(led),
            "random": R.random_baseline(led, spec, mkt, s, e),
            "stats": R.statistical_confidence(led),
            "multiple_testing": lin["multiple_testing"],
            "survivorship": A.survivorship(self.provider.survivorship_biased, self.provider.synthetic),
            "lookahead": lookahead_audit(spec, mkt, led),
        }
        if revealed and val_bt is not None:
            audit["oos"]["validation_portfolio"] = {"strategy": clean(val_bt["strategy"]),
                                                    "benchmark": clean(val_bt["benchmark"]), "meta": clean(val_bt["meta"])}
            if fam["revealed_version_id"] != v["id"]:
                audit["oos"]["contaminated"] = True
                audit["oos"]["summary"] += (" NOTE: validation was already revealed for an earlier version of this "
                                            "family, so this is no longer an unseen test.")
        ev = A.evidence(audit, revealed)
        bt = json.loads(b["results_json"])
        out = clean({"audit_id": None, "backtest_id": backtest_id, "version_id": v["id"], "family_id": fam["id"],
                     "version_no": v["version_no"], "created_at": now(), "engine_version": ENGINE_VERSION,
                     "methodology_version": METHODOLOGY_VERSION, "dataset_version": self.provider.dataset_version(),
                     "tests": audit, "evidence": ev,
                     "table": [{"key": k, "label": lbl, "status": audit[k]["status"], "summary": audit[k].get("summary", "")}
                               for k, lbl in A.TESTS],
                     "explanation": A.explanation(bt, clean(audit), ev), "validation_revealed": revealed})
        aid = new_id("aud")
        out["audit_id"] = aid
        self.db.x("INSERT INTO audits(id, backtest_id, version_id, created_at, evidence, audit_json) VALUES (?,?,?,?,?,?)",
                  (aid, backtest_id, v["id"], out["created_at"], ev["level"], json.dumps(out)))
        return out

    # ------------------------------------------------------- sealed validation
    def reveal(self, family_id: str, version_id: str, confirm: bool) -> dict:
        if not confirm:
            raise ValueError("confirmation required")
        fam = self.db.one("SELECT * FROM families WHERE id=?", (family_id,))
        if not fam:
            raise NotFound("family not found")
        v, _ = self._version(version_id)
        if v["family_id"] != family_id:
            raise ValueError("version does not belong to this family")
        first = not fam["validation_revealed"]
        if first:
            t = now()
            self.db.x("UPDATE families SET validation_revealed=1, revealed_at=?, revealed_version_id=? WHERE id=?",
                      (t, version_id, family_id))
            self.db.x("INSERT INTO validation_reveals(id, family_id, version_id, revealed_at) VALUES (?,?,?,?)",
                      (new_id("rev"), family_id, version_id, t))
        self.backtest(version_id, "validation")
        dev_bt = self.backtest(version_id, "development")
        au = self.audit(dev_bt["backtest_id"])
        return {"first_reveal": first, "audit": au}

    # ---------------------------------------------------------------- report
    def get_audit(self, audit_id: str) -> dict:
        a = self.db.one("SELECT audit_json FROM audits WHERE id=?", (audit_id,))
        if not a:
            raise NotFound("audit not found")
        return json.loads(a["audit_json"])

    def report(self, audit_id: str) -> dict:
        au = self.get_audit(audit_id)
        b = self.db.one("SELECT results_json FROM backtests WHERE id=?", (au["backtest_id"],))
        v, spec = self._version(au["version_id"])
        return {"audit": au, "backtest": json.loads(b["results_json"]), "lineage": self.lineage(au["family_id"]),
                "strategy": {"spec": spec.model_dump(mode="json"), "rules": [c.describe() for c in spec.entry_conditions],
                             "exit": spec.exit.describe(), "version_no": v["version_no"]},
                "dataset": self.dataset(), "limitations": A.LIMITATIONS,
                "methodology": {"engine_version": au["engine_version"], "methodology_version": au["methodology_version"],
                                "dataset_version": au["dataset_version"], "random_seed": R.SEED,
                                "bootstrap_resamples": R.N_BOOT, "random_samples_per_trade": R.N_RANDOM}}

    # ----------------------------------------------------------------- sample
    def sample_audit(self) -> dict:
        """The demo strategy, fully audited on the real dataset, with validation revealed (kept separate)."""
        r = interpret(DEMO_TEXT)
        patches = [p for i in r["issues"] for p in i["options"][0]["patch"]]      # SMA; 93 large caps
        spec, err = validate_draft({**apply_patches(r["draft"], patches), "name": "Oversold Uptrend Rebound (sample)"})
        if err:
            raise RuntimeError(f"demo strategy invalid: {err}")
        ver = self.create_version(spec, "sample-audit")
        bt = self.backtest(ver["version_id"])
        fam = self.db.one("SELECT * FROM families WHERE id=?", (ver["family_id"],))
        if not fam["validation_revealed"]:
            return self.reveal(ver["family_id"], ver["version_id"], True)["audit"]
        last = self.db.one("SELECT id FROM audits WHERE version_id=? ORDER BY created_at DESC", (ver["version_id"],))
        return self.get_audit(last["id"]) if last else self.audit(bt["backtest_id"])


_STUB = {"name": "x", "universe": {"type": "LARGE_CAP_93"}, "direction": "LONG", "entry_execution": "NEXT_OPEN",
         "exit": {"type": "FIXED_HOLD", "trading_days": 10}}
