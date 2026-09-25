"""SQLite persistence (stdlib sqlite3, parameterised queries only).

Tables: families, versions, backtests, audits, validation_reveals, submissions, plans, events.
There is no user/login table: people are identified only by a random anonymous id kept in their browser.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "outputs" / "strategy_audit.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS families (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, anonymous_user_id TEXT, signature TEXT,
    validation_revealed INTEGER NOT NULL DEFAULT 0, revealed_at TEXT, revealed_version_id TEXT);
CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id), version_no INTEGER NOT NULL,
    created_at TEXT NOT NULL, name TEXT, spec_json TEXT NOT NULL, spec_hash TEXT NOT NULL, rules_hash TEXT NOT NULL,
    changes_json TEXT, UNIQUE(family_id, version_no));
CREATE TABLE IF NOT EXISTS backtests (
    id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id), period TEXT NOT NULL,
    created_at TEXT NOT NULL, engine_version TEXT, methodology_version TEXT, dataset_version TEXT,
    settings_json TEXT, results_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY, backtest_id TEXT NOT NULL REFERENCES backtests(id), version_id TEXT NOT NULL,
    created_at TEXT NOT NULL, evidence TEXT, audit_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS validation_reveals (
    id TEXT PRIMARY KEY, family_id TEXT NOT NULL, version_id TEXT NOT NULL, revealed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, anonymous_user_id TEXT, strategy_name TEXT, description TEXT,
    market TEXT, holding_period TEXT, entry_conditions TEXT, exit_conditions TEXT, traded_yn TEXT, belief_1_5 INTEGER,
    notes TEXT, referral_source TEXT,
    strategy_type TEXT, testable_yn TEXT, reason_if_not_testable TEXT, audit_minutes REAL, report_delivered TEXT,
    returned_without_prompt TEXT, days_until_return INTEGER, referred_someone TEXT, willing_to_pay TEXT,
    actual_payment_test TEXT, admin_notes TEXT);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, anonymous_user_id TEXT, dataset_version TEXT, engine_version TEXT,
    spec_json TEXT NOT NULL, results_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, anonymous_user_id TEXT, name TEXT NOT NULL, props_json TEXT);
"""

ADMIN_FIELDS = ["strategy_type", "testable_yn", "reason_if_not_testable", "audit_minutes", "report_delivered",
                "returned_without_prompt", "days_until_return", "referred_someone", "willing_to_pay",
                "actual_payment_test", "admin_notes"]
SUBMISSION_FIELDS = ["strategy_name", "description", "market", "holding_period", "entry_conditions", "exit_conditions",
                     "traded_yn", "belief_1_5", "notes", "referral_source"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DB:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("AUDIT_DB_PATH") or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self._lock:
            r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # --- events (privacy: event name + small property dict only)
    def event(self, name: str, anon: str | None, props: dict | None = None) -> None:
        self.x("INSERT INTO events(at, anonymous_user_id, name, props_json) VALUES (?,?,?,?)",
               (now(), anon, name, json.dumps(props or {})[:2000]))
