"""FastAPI application."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.config import CORS_ORIGINS, DATABASE_URL, LOG_LEVEL
from api.db import init_db
from api.licence import ALWAYS_OPEN
from api.licence import load as load_licence

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize database
init_db()
logger.info("✅ Database initialized")

# Create FastAPI app
app = FastAPI(
    title="DVV Research Cloud API",
    version="0.1.0",
    description="End-to-end survey data processing pipeline",
)

# Refuses to boot on a real database without a valid licence (api/licence.py).
LICENCE = load_licence(DATABASE_URL)
if LICENCE:
    logger.info(f"✅ Licensed to {LICENCE.client} until {LICENCE.expires}")


@app.middleware("http")
async def licence_gate(request, call_next):
    """After the grace period, the management API stops; survey links do not."""
    path = request.url.path
    if (
        LICENCE is not None
        and path.startswith("/api/")
        and not path.startswith(ALWAYS_OPEN)
        and LICENCE.state() == "expired"
    ):
        return JSONResponse(
            {
                "detail": f"This DVV Research Cloud licence expired on "
                f"{LICENCE.expires:%d %b %Y}. Contact DVV to renew. "
                "Live survey links keep working."
            },
            status_code=402,
        )
    return await call_next(request)


# CORS — registered after the licence gate so it wraps it (outermost).
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    """Turn unhandled crashes into diagnosable JSON 500s WITH CORS headers.

    Unhandled exceptions bypass CORSMiddleware (Starlette's ServerErrorMiddleware
    sits outside it), so the cross-origin frontend saw them as an opaque
    "Network Error" / CORS block instead of the real failure — a production 500
    on question-save was undiagnosable from the browser. This handler logs the
    full traceback server-side, returns the exception type + message (enough to
    act on, no stack trace leaked), and re-attaches the CORS header manually
    since the middleware won't run for this response path.
    """
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}",
                 exc_info=True)
    origin = request.headers.get("origin", "")
    headers = {}
    if origin in CORS_ORIGINS or "*" in CORS_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=500,
        content={"detail": f"Server error: {type(exc).__name__}: {exc}"},
        headers=headers,
    )


# Health check
@app.get("/api/health")
async def health():
    """Health check endpoint.

    Reports which LLM API keys are present in the environment (booleans only,
    never the values/lengths/prefixes) so a missing key — the root cause of the
    production incident where all 80 questions failed extraction — is caught at
    deploy via the health check, not at the first client upload (T16 preflight).

    Also reports the commit the running instance was built from. "Is my fix
    live yet?" was previously unanswerable without opening the Render dashboard
    — and on a free plan that redeploys on every push, it is the first thing
    worth checking when behaviour does not match the code in front of you.
    Railway injects RAILWAY_GIT_COMMIT_SHA (Render: RENDER_GIT_COMMIT); locally it is absent and
    the field reads "unknown".
    """
    import os

    from src.ai.router import llm_key_presence

    commit = (
        os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or os.environ.get("RENDER_GIT_COMMIT")
        or os.environ.get("GIT_COMMIT")
        or ""
    )
    return {
        "status": "ok",
        "service": "dvv-research-api",
        "commit": commit[:12] or "unknown",
        # State + date only: the client name is not for a public endpoint.
        "licence": (
            {"state": LICENCE.state(), "expires": LICENCE.expires.isoformat()}
            if LICENCE
            else None
        ),
        "llm_keys": llm_key_presence(),
    }


# Import routes
try:
    from api.routes import (
        auth,
        chat,
        codeframes,
        corrections,
        fielding,
        materialize,
        public,
        questionnaire,
        studies,
        ui_overrides,
    )

    app.include_router(auth.router, prefix="/api")
    logger.info("✅ Auth routes loaded")

    app.include_router(studies.router, prefix="/api")
    logger.info("✅ Studies routes loaded")

    app.include_router(chat.router, prefix="/api")
    logger.info("✅ Chat routes loaded")

    app.include_router(questionnaire.router, prefix="/api")
    logger.info("✅ Questionnaire routes loaded")

    app.include_router(public.router, prefix="/api")
    logger.info("✅ Public routes loaded")

    app.include_router(fielding.router, prefix="/api")
    logger.info("✅ Fielding routes loaded")

    app.include_router(materialize.router, prefix="/api")
    logger.info("✅ Materialize routes loaded")

    app.include_router(corrections.router, prefix="/api")
    logger.info("✅ Correction-loop routes loaded")

    app.include_router(ui_overrides.router, prefix="/api")
    logger.info("✅ UI-override routes loaded")

    app.include_router(codeframes.router, prefix="/api")
    logger.info("✅ Codeframe-library routes loaded")
    from api.routes import copilot

    app.include_router(copilot.router, prefix="/api")
    logger.info("✅ Copilot routes loaded")
except Exception as e:
    logger.warning(f"Routes not yet available: {e}")


def mount_web(target: FastAPI, dist: Path) -> None:
    """Serve the React build from this same origin.

    One origin means no CORS, no API URL baked into a bundle, and one deploy for
    the whole product. `StaticFiles(html=True)` alone would 404 a deep link like
    /r/<token> or /projects/x/fielding on refresh — for a respondent link a panel
    hands out, that is the whole product — so unknown paths fall back to
    index.html. Unknown /api paths stay a JSON 404, never the app shell.
    """
    root = dist.resolve()
    target.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @target.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        file = (root / path).resolve()
        if path and file.is_file() and root in file.parents:  # no ../ escapes
            return FileResponse(file)
        return FileResponse(root / "index.html")


web_build_path = Path(__file__).parent.parent / "web" / "dist"
if (web_build_path / "index.html").exists():
    mount_web(app, web_build_path)
    logger.info("✅ React static files mounted")
else:

    @app.get("/")
    async def root():
        """API-only run (no web build): point at the docs."""
        return JSONResponse({"message": "DVV Research Cloud API", "docs": "/docs"})


@app.on_event("startup")
async def startup():
    """Run on startup."""
    logger.info("🚀 DVV Research Cloud API started")
    # Preflight: log which LLM keys are configured (booleans only, never values)
    # so a missing key is visible in the deploy log immediately (T16).
    from src.ai.router import llm_key_presence

    presence = llm_key_presence()
    missing = [name for name, present in presence.items() if not present]
    if missing:
        logger.warning(f"⚠️  LLM key preflight — MISSING: {', '.join(missing)}")
    logger.info(f"LLM key preflight: {presence}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
