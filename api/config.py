"""API configuration."""

import os
import secrets
from pathlib import Path

from src.env import load_env

# Before ANY os.getenv below — a local .env is otherwise silently ignored.
# Real environment variables still win (load_env uses setdefault).
load_env()

PROJECT_ROOT = Path(__file__).parent.parent

# Database
def _normalize_db_url(url: str) -> str:
    """Accept the `postgres://` form some providers still hand out.

    Neon, Supabase and Heroku-style dashboards copy connection strings starting
    `postgres://`, but SQLAlchemy 2.x only registers the `postgresql://`
    dialect and raises `Can't load plugin: sqlalchemy.dialects:postgres` — an
    error that reads like a missing driver, not a one-word URL difference.
    Normalising here costs nothing and removes a genuinely confusing failure
    from the very first minute of a deploy.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    # Name the installed driver. A bare `postgresql://` means "SQLAlchemy's
    # default driver", and SQLAlchemy 2.1 changed that default from psycopg2 to
    # psycopg 3 — which is not installed. A fresh image build picked 2.1 up and
    # the app crashed on its first import; the upgrade's rollback rebuilt the
    # OLD code with the same new library and crashed the same way.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", "sqlite:///./dvv_research.db"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", PROJECT_ROOT / "dvv_research.db"))

# Storage
STUDIES_DIR = Path(os.getenv("STUDIES_DIR", PROJECT_ROOT / "studies"))
STUDIES_DIR.mkdir(parents=True, exist_ok=True)

# API
API_TITLE = "DVV Research Cloud"
API_VERSION = "0.1.0"
_DEFAULT_CORS = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000"
# Comma-separated so the deployed frontend's origin (e.g. a Vercel URL) can be
# added via one env var without a code change or redeploy of this service.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", _DEFAULT_CORS).split(",") if o.strip()]

# Orchestration
MIN_CODING_CONFIDENCE = float(os.getenv("MIN_CODING_CONFIDENCE", "0.8"))
AUTO_APPROVE_CLASSIFY = os.getenv("AUTO_APPROVE_CLASSIFY", "true").lower() == "true"
AUTO_APPROVE_QC = os.getenv("AUTO_APPROVE_QC", "true").lower() == "true"
REQUIRE_DELIVERY_APPROVAL = os.getenv("REQUIRE_DELIVERY_APPROVAL", "true").lower() == "true"

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# JWT Authentication
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "168"))  # 7 days

# JWT_SECRET: read from env if set, otherwise persist to .jwt_secret file
_jwt_secret_file = PROJECT_ROOT / ".jwt_secret"
if env_secret := os.getenv("JWT_SECRET"):
    JWT_SECRET = env_secret
elif _jwt_secret_file.exists():
    JWT_SECRET = _jwt_secret_file.read_text().strip()
else:
    # Generate once and persist — so reload doesn't invalidate sessions
    JWT_SECRET = secrets.token_urlsafe(32)
    _jwt_secret_file.write_text(JWT_SECRET)
    print(f"Generated JWT_SECRET and saved to {_jwt_secret_file}")

# Admin bootstrap (every startup — see api/db.py:seed_admin_user)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "deepsharma616@gmail.com")
# A default password is only tolerable on a local SQLite file. On a real
# database it would be a public owner login — and seed_admin_user() re-applies
# it on EVERY cold start, so it can't even be changed away. Refuse to boot.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
if not ADMIN_PASSWORD:
    if not DATABASE_URL.startswith("sqlite"):
        raise RuntimeError(
            "ADMIN_PASSWORD is not set. Set it in the host's environment variables — "
            "without it the owner account would get a known default password."
        )
    ADMIN_PASSWORD = "changeme123"  # local development only
ADMIN_NAME = os.getenv("ADMIN_NAME", "Deepesh Sharma")

# Emergency admin password reset (api/routes/auth.py: POST /auth/emergency-reset).
# Unset by default — the endpoint 404s if this is empty, so it's not an
# always-on backdoor. Set this alongside ADMIN_EMAIL/ADMIN_PASSWORD once you
# actually need it: it lets you reset the admin password immediately via the
# UI, without waiting on a redeploy cycle (seed_admin_user only re-syncs the
# password from ADMIN_PASSWORD at container startup).
ADMIN_RESET_TOKEN = os.getenv("ADMIN_RESET_TOKEN", "")

# Public share links base URL
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:3000")

print(f"DVV API Config: DB={DATABASE_PATH}, Studies={STUDIES_DIR}")
