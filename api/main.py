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


# Health check
@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "dvv-research-api"}


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
    from api.routes import auth, chat, public, studies

    app.include_router(auth.router, prefix="/api")
    logger.info("✅ Auth routes loaded")

    app.include_router(studies.router, prefix="/api")
    logger.info("✅ Studies routes loaded")

    app.include_router(chat.router, prefix="/api")
    logger.info("✅ Chat routes loaded")

    app.include_router(public.router, prefix="/api")
    logger.info("✅ Public routes loaded")
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
