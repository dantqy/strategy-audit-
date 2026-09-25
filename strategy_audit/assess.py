"""Audit table, evidence assessment and plain-English explanation.

Deterministic: rules are fixed below. The explanation is assembled from computed results only; each
sentence lists the result fields it quotes ("facts"), so every number is traceable. No LLM is used
for any number (an LLM layer, if ever added, may only rephrase these sentences).
"""
from __future__ import annotations

NO_EDGE = "NO EVIDENCE OF HISTORICAL EDGE"
WEAK = "WEAK / INCONCLUSIVE HISTORICAL EVIDENCE"
PROMISING = "PROMISING BUT UNSTABLE HISTORICAL EVIDENCE"
ROBUST = "RELATIVELY ROBUST HISTORICAL EVIDENCE"

TESTS = [("oos", "Out-of-sample"), ("sensitivity", "Parameter stability"), ("costs", "Transaction costs"),
         ("regimes", "Market regimes"), ("years", "Year-by-year"), ("outliers", "Outlier dependence"),
         ("concentration", "Stock concentration"), ("random", "Random-entry baseline"),
         ("stats", "Statistical confidence"), ("multiple_testing", "Multiple testing"),
         ("survivorship", "Survivorship bias"), ("lookahead", "Lookahead audit")]
CORE = ["sensitivity", "costs", "regimes", "years", "outliers", "concentration", "random", "stats"]

LIMITATIONS = [
    "Historical performance does not predict future returns.",
    "Backtests are sensitive to data quality and assumptions.",
    "Out-of-sample testing reduces but does not eliminate hindsight bias: you may already know how these years went.",
    "Research lineage cannot perfectly detect related strategies across users or accounts.",
    "The current dataset contains survivorship bias (today's large stocks only; no delisted companies).",
    "Transaction-cost estimates may differ from real execution.",
    "This platform provides historical research tools, not investment recommendations.",
]


def pct(x, d=2) -> str:
    return "n/a" if x is None else f"{x * 100:+.{d}f}%"


def multiple_testing(n_versions: int) -> dict:
    lvl = "LOW" if n_versions <= 1 else "MODERATE" if n_versions <= 5 else "HIGH"
    msg = (f"You have evaluated {n_versions} related version(s) of this strategy against overlapping historical data."
           + (" Selecting the strongest-performing version increases the probability of overfitting." if n_versions > 1 else ""))
    return {"status": lvl, "summary": msg, "versions": n_versions,
            "note": "Research warning only: renaming a strategy or using another account is not detected."}


def survivorship(biased: bool, synthetic: bool) -> dict:
    if synthetic:
        return {"status": "N/A", "summary": "Synthetic demo data (no real companies)."}
    if biased:
        return {"status": "DATA LIMITATION",
                "summary": "This universe may contain survivorship bias because historical membership is reconstructed "
                           "using currently available securities. Results may therefore overstate historical performance."}
    return {"status": "PASS", "summary": "Point-in-time universe including delisted securities."}


def evidence(audit: dict, revealed: bool) -> dict:
    st = {k: audit[k]["status"] for k, _ in TESTS if k in audit}
    mean = audit["stats"].get("mean")
    n = audit["stats"].get("n", 0)
    core = CORE + (["oos"] if revealed else [])
    fails = [k for k in core if st.get(k) == "FAIL"]
    mixed = [k for k in core if st.get(k) == "MIXED"]
    reasons = []
    if mean is None or mean <= 0 or n < 10:
        level = NO_EDGE
        reasons.append("the average trade after costs is not positive" if (mean or 0) <= 0 else f"only {n} trades")
    elif st.get("random") == "FAIL":
        level = NO_EDGE
        reasons.append("it did no better than entering the same stocks on random days")
    elif (revealed and st.get("oos") == "PASS" and not fails and len(mixed) <= 1 and st.get("stats") == "PASS"
          and st.get("random") == "PASS" and st.get("lookahead") == "PASS"):
        level = ROBUST
        reasons.append("it held up out-of-sample and passed every stress test with at most one mixed result")
    elif not fails and (st.get("stats") == "PASS" or st.get("random") == "PASS") and st.get("lookahead") == "PASS":
        level = PROMISING
        reasons.append("no stress test failed, but " + (f"{len(mixed)} gave mixed results" if mixed else
                                                          "the validation period has not been revealed"))
    else:
        level = WEAK
        if fails:
            reasons.append("it failed: " + ", ".join(dict(TESTS)[k].lower() for k in fails))
        if st.get("stats") != "PASS" and st.get("random") != "PASS":
            reasons.append("neither its average trade nor its edge over random entries is statistically distinguishable from zero")
        if st.get("lookahead") != "PASS":
            reasons.append("the automated lookahead audit raised a warning")
    if not revealed:
        reasons.append("the sealed validation period has not been revealed, so no unseen-data test has been run yet")
    return {"level": level, "reasons": reasons, "failed": fails, "mixed": mixed}


def explanation(bt: dict, audit: dict, ev: dict) -> list[dict]:
    """Sentences built only from computed fields; `facts` lists exactly which fields each one quotes."""
    s, b, m = bt["strategy"], bt["benchmark"], bt["meta"]
    out = []
    out.append({"text": (f"From {m['start']} to {m['end']} the rules produced {audit['stats']['n']} trades averaging "
                         f"{pct(audit['stats']['mean'])} each after costs. The portfolio returned "
                         f"{pct(s.get('total_return'), 1)} while SPY returned {pct(b.get('total_return'), 1)} over the "
                         f"same period, and it was invested {pct(s.get('exposure'), 0).lstrip('+')} of the time on average."),
                "facts": ["meta.start", "meta.end", "stats.n", "stats.mean", "strategy.total_return",
                          "benchmark.total_return", "strategy.exposure"]})
    r = audit["random"]
    if r.get("random_mean") is not None:
        out.append({"text": (f"Entering the same stocks on comparable random days averaged {pct(r['random_mean'])} per trade, "
                             f"versus {pct(r['strategy_mean'])} for the strategy: a difference of "
                             f"{pct(r['difference']['mean'])} (95% CI {pct(r['difference']['ci_low'])} to "
                             f"{pct(r['difference']['ci_high'])})."),
                    "facts": ["random.random_mean", "random.strategy_mean", "random.difference.mean",
                              "random.difference.ci_low", "random.difference.ci_high"]})
    for k, label in TESTS:
        t = audit.get(k)
        if t and t.get("status") in ("FAIL", "MIXED") and k not in ("random",):
            out.append({"text": f"{label}: {t['summary']}", "facts": [f"{k}.summary"]})
    o = audit.get("oos", {})
    if o.get("status") not in (None, "SEALED") and o.get("validation"):
        out.append({"text": (f"In the sealed validation period the average trade was {pct(o['validation']['mean'])} "
                             f"({o['validation']['n']} trades) versus {pct(o['development']['mean'])} in development."),
                    "facts": ["oos.validation.mean", "oos.validation.n", "oos.development.mean"]})
    out.append({"text": f"Assessment: {ev['level']}, because " + "; ".join(ev["reasons"]) + ".",
                "facts": ["evidence.level", "evidence.reasons"]})
    return out
