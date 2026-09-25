"""Start the Strategy Audit Platform locally.

    .venv\\Scripts\\python -m strategy_audit.run            # http://127.0.0.1:8765

* Binds to 127.0.0.1 only: this is a local prototype (data-licensing questions are unresolved).
* Settings come from environment variables, or config/audit_platform.local.env (git-ignored).
  On first run an ADMIN_TOKEN is generated there and printed once.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:          # allow `python strategy_audit/run.py` from any working directory
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
ENV_FILE = ROOT / "config" / "audit_platform.local.env"


def load_env() -> None:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    if not os.environ.get("ADMIN_TOKEN"):
        tok = secrets.token_urlsafe(18)
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ENV_FILE.open("a", encoding="utf-8") as f:
            f.write(f"ADMIN_TOKEN={tok}\n")
        os.environ["ADMIN_TOKEN"] = tok
        print(f"Generated an admin token (saved in {ENV_FILE.name}): {tok}")


def main() -> None:
    load_env()
    port = int(os.environ.get("PORT", "8765"))
    print(f"Strategy Audit Platform: http://127.0.0.1:{port}   (admin page: http://127.0.0.1:{port}/#/admin)")
    uvicorn.run("strategy_audit.api:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
