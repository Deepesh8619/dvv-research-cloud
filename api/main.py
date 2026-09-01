"""FastAPI application."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.config import CORS_ORIGINS, LOG_LEVEL
from api.db import init_db

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

# CORS
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
    Render injects RENDER_GIT_COMMIT automatically; locally it is absent and
    the field reads "unknown".
    """
    import os

    from src.ai.router import llm_key_presence

    commit = os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT") or ""
    return {
        "status": "ok",
        "service": "dvv-research-api",
        "commit": commit[:12] or "unknown",
        "llm_keys": llm_key_presence(),
    }


# Root redirect
@app.get("/")
async def root():
    """Redirect to API docs."""
    return JSONResponse(
        {"message": "DVV Research Cloud API", "docs": "/docs"},
        status_code=200,
    )


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
except Exception as e:
    logger.warning(f"Routes not yet available: {e}")


# Static files (React build - optional)
web_build_path = Path(__file__).parent.parent / "web" / "dist"
if web_build_path.exists():
    app.mount("/", StaticFiles(directory=str(web_build_path), html=True), name="static")
    logger.info("✅ React static files mounted")


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
