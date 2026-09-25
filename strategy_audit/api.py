"""FastAPI app: JSON API + the static single-page frontend.

Run:  .venv\\Scripts\\python -m uvicorn strategy_audit.api:app --port 8000
Security: every body is a pydantic model with length limits; strategies only ever become validated
StrategySpec data (no eval, no user code); the frontend renders user text with textContent only;
admin endpoints need X-Admin-Token == env ADMIN_TOKEN (and are disabled when it is unset).
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import ENGINE_VERSION, METHODOLOGY_VERSION
from .db import ADMIN_FIELDS, SUBMISSION_FIELDS, new_id, now
from .service import DEMO_TEXT, AuditService, NotFound

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Strategy Audit Platform", version=ENGINE_VERSION, docs_url="/api/docs", redoc_url=None)
_svc: AuditService | None = None


def svc() -> AuditService:
    global _svc
    if _svc is None:
        _svc = AuditService()
    return _svc


Anon = Optional[str]


class InterpretIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    anonymous_user_id: Anon = Field(None, max_length=64)


class ResolveIn(BaseModel):
    draft: dict
    patches: list[dict] = Field(default_factory=list, max_length=40)
    name: Optional[str] = Field(None, max_length=80)


class VersionIn(BaseModel):
    spec: dict
    family_id: Optional[str] = Field(None, max_length=40)
    anonymous_user_id: Anon = Field(None, max_length=64)


class BacktestIn(BaseModel):
    version_id: str = Field(..., max_length=40)


class AuditIn(BaseModel):
    backtest_id: str = Field(..., max_length=40)


class RevealIn(BaseModel):
    version_id: str = Field(..., max_length=40)
    confirm: bool


class SubmissionIn(BaseModel):
    anonymous_user_id: Anon = Field(None, max_length=64)
    strategy_name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=4000)
    market: str = Field("", max_length=120)
    holding_period: str = Field("", max_length=120)
    entry_conditions: str = Field("", max_length=2000)
    exit_conditions: str = Field("", max_length=2000)
    traded_yn: Literal["Y", "N", ""] = ""
    belief_1_5: Optional[int] = Field(None, ge=1, le=5)
    notes: str = Field("", max_length=2000)
    referral_source: str = Field("", max_length=120)


class AdminPatch(BaseModel):
    strategy_type: Optional[str] = Field(None, max_length=120)
    testable_yn: Optional[Literal["Y", "N", ""]] = None
    reason_if_not_testable: Optional[str] = Field(None, max_length=1000)
    audit_minutes: Optional[float] = Field(None, ge=0, le=10000)
    report_delivered: Optional[Literal["Y", "N", ""]] = None
    returned_without_prompt: Optional[Literal["Y", "N", ""]] = None
    days_until_return: Optional[int] = Field(None, ge=0, le=10000)
    referred_someone: Optional[Literal["Y", "N", ""]] = None
    willing_to_pay: Optional[Literal["Y", "N", "MAYBE", ""]] = None
    actual_payment_test: Optional[str] = Field(None, max_length=200)
    admin_notes: Optional[str] = Field(None, max_length=4000)


class EventIn(BaseModel):
    name: Literal["strategy_submitted", "strategy_testable", "interpretation_confirmed", "backtest_completed",
                  "audit_launched", "report_viewed", "second_strategy_submitted", "referral_source",
                  "validation_revealed", "sample_viewed", "submission_form_sent", "plan_run"]
    anonymous_user_id: Anon = Field(None, max_length=64)
    props: dict[str, Any] = Field(default_factory=dict)


@app.exception_handler(NotFound)
async def _nf(_: Request, e: NotFound):
    return JSONResponse({"detail": str(e)}, status_code=404)


@app.exception_handler(ValidationError)
async def _ve(_: Request, e: ValidationError):
    return JSONResponse({"detail": [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]}, status_code=422)


def _admin(token: str | None):
    want = os.environ.get("ADMIN_TOKEN")
    if not want:
        raise HTTPException(403, "Admin disabled: set the ADMIN_TOKEN environment variable to enable it.")
    if not token or not hmac.compare_digest(token, want):
        raise HTTPException(401, "Wrong admin token.")


# ------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    return {"ok": True, "engine_version": ENGINE_VERSION, "methodology_version": METHODOLOGY_VERSION}


@app.get("/api/dataset")
def dataset():
    return svc().dataset()


@app.get("/api/demo-text")
def demo_text():
    return {"text": DEMO_TEXT}


@app.post("/api/interpret")
def interpret(body: InterpretIn):
    out = svc().interpret(body.text)
    svc().db.event("strategy_submitted", body.anonymous_user_id, {"status": out["status"]})
    if out["status"] != "unsupported":
        svc().db.event("strategy_testable", body.anonymous_user_id, {})
    return out


@app.post("/api/resolve")
def resolve(body: ResolveIn):
    try:
        return svc().resolve(body.draft, body.patches, body.name)
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise HTTPException(422, f"Could not apply the chosen options: {e}")


@app.post("/api/versions")
def create_version(body: VersionIn):
    try:
        out = svc().create_version(body.spec, body.anonymous_user_id, body.family_id)
    except ValidationError as e:
        raise HTTPException(422, [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()])
    svc().db.event("interpretation_confirmed", body.anonymous_user_id, {"version_no": out["version_no"]})
    if out["version_no"] >= 2 or out["related_detected"]:
        svc().db.event("second_strategy_submitted", body.anonymous_user_id, {})
    return out


@app.post("/api/backtests")
def backtest(body: BacktestIn):
    try:
        out = svc().backtest(body.version_id, "development")
    except NotFound:
        raise
    except Exception as e:                     # shown to the user, never replaced with fake numbers
        raise HTTPException(500, f"Backtest failed: {type(e).__name__}: {e}")
    svc().db.event("backtest_completed", None, {"trades": out["counts"]["trades"]})
    return out


@app.get("/api/backtests/{backtest_id}")
def get_backtest(backtest_id: str):
    return svc().get_backtest(backtest_id[:40])


@app.post("/api/audits")
def audit(body: AuditIn):
    try:
        out = svc().audit(body.backtest_id)
    except NotFound:
        raise
    except Exception as e:
        raise HTTPException(500, f"Audit failed: {type(e).__name__}: {e}")
    svc().db.event("audit_launched", None, {"evidence": out["evidence"]["level"]})
    return out


@app.get("/api/audits/{audit_id}")
def get_audit(audit_id: str):
    return svc().get_audit(audit_id[:40])


@app.post("/api/families/{family_id}/reveal")
def reveal(family_id: str, body: RevealIn):
    try:
        out = svc().reveal(family_id[:40], body.version_id, body.confirm)
    except ValueError as e:
        raise HTTPException(422, str(e))
    svc().db.event("validation_revealed", None, {"first": out["first_reveal"]})
    return out


@app.get("/api/families/{family_id}")
def lineage(family_id: str):
    return svc().lineage(family_id[:40])


@app.get("/api/versions/{version_id}")
def get_version(version_id: str):
    v, spec = svc()._version(version_id[:40])
    return {"version_id": v["id"], "family_id": v["family_id"], "version_no": v["version_no"],
            "spec": spec.model_dump(mode="json"), "rules": [c.describe() for c in spec.entry_conditions],
            "exit": spec.exit.describe()}


@app.get("/api/reports/{audit_id}")
def report(audit_id: str):
    out = svc().report(audit_id[:40])
    svc().db.event("report_viewed", None, {})
    return out


@app.get("/api/sample-audit")
def sample_audit():
    au = svc().sample_audit()
    svc().db.event("sample_viewed", None, {})
    return {"audit_id": au["audit_id"]}


class PlanIn(BaseModel):
    spec: dict
    anonymous_user_id: Anon = Field(None, max_length=64)


@app.get("/api/plan-options")
def plan_options():
    return svc().plan_options()


@app.post("/api/plans")
def run_plan(body: PlanIn):
    try:
        out = svc().run_plan(body.spec, body.anonymous_user_id)
    except ValidationError as e:
        raise HTTPException(422, [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()])
    except ValueError as e:
        raise HTTPException(422, str(e))
    svc().db.event("plan_run", body.anonymous_user_id, {"dip": str(bool(out["spec"].get("dip")))})
    return out


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    return svc().get_plan(plan_id)


@app.post("/api/events")
def event(body: EventIn):
    svc().db.event(body.name, body.anonymous_user_id, {k: str(v)[:100] for k, v in list(body.props.items())[:5]})
    return {"ok": True}


@app.post("/api/submissions")
def submit(body: SubmissionIn):
    sid = new_id("sub")
    vals = [getattr(body, f) for f in SUBMISSION_FIELDS]
    svc().db.x(f"INSERT INTO submissions(id, created_at, anonymous_user_id, {', '.join(SUBMISSION_FIELDS)}) "
               f"VALUES (?,?,?,{','.join('?' * len(SUBMISSION_FIELDS))})", (sid, now(), body.anonymous_user_id, *vals))
    svc().db.event("submission_form_sent", body.anonymous_user_id, {"referral": body.referral_source[:40]})
    return {"submission_id": sid}


@app.get("/api/admin/submissions")
def admin_list(x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    return svc().db.all("SELECT * FROM submissions ORDER BY created_at DESC")


@app.patch("/api/admin/submissions/{sid}")
def admin_patch(sid: str, body: AdminPatch, x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    upd = {k: v for k, v in body.model_dump().items() if v is not None and k in ADMIN_FIELDS}
    if not upd:
        return {"updated": 0}
    cur = svc().db.x(f"UPDATE submissions SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?", (*upd.values(), sid[:40]))
    if cur.rowcount == 0:
        raise HTTPException(404, "submission not found")
    return {"updated": cur.rowcount}


@app.get("/api/admin/funnel")
def admin_funnel(x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    rows = svc().db.all("SELECT name, COUNT(*) AS n, COUNT(DISTINCT anonymous_user_id) AS users FROM events GROUP BY name")
    subs = svc().db.all("SELECT testable_yn, report_delivered, returned_without_prompt, referred_someone, willing_to_pay "
                        "FROM submissions")
    n = len(subs)

    def share(field, val="Y"):
        return (sum(1 for s in subs if s[field] == val) / n) if n else None
    return {"events": rows, "submissions": n,
            "rates": {"testable": share("testable_yn"), "report_delivered": share("report_delivered"),
                      "returned_without_prompt": share("returned_without_prompt"),
                      "referred": share("referred_someone"), "willing_to_pay": share("willing_to_pay")}}


# ---------------------------------------------------------------- frontend
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def _revalidate_static(request: Request, call_next):
    """Browsers must re-check the page and its scripts (a cheap 304 when unchanged), so updates show on a plain reload."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
