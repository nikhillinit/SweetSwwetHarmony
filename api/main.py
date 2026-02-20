"""
Discovery Engine API - Command Center

FastAPI service layer providing REST endpoints for:
- Authentication (JWT-based)
- Company inbox management
- Entity management with stage tracking
- Background jobs and health monitoring
- Action execution (Track, Pass, Pipeline)

Usage:
    # Development
    uvicorn api.main:app --reload --port 8000

    # Production
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv(override=False)  # Load .env without overwriting real env vars

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.middleware import ExceptionHandlerMiddleware, RateLimitMiddleware, RequestIdMiddleware
from utils.logging_config import configure_logging, startup_check
from utils.config_validator import validate_config

from api.routers import actions, batch, companies, public, auth, health, jobs, entities, scheduler, triage
from api.routers import merge_review, canary, hunter
from api.auth.jwt_auth import seed_default_users
from storage.signal_store import SignalStore

# Configure structured logging at import time (before any loggers are used)
configure_logging()
logger = logging.getLogger(__name__)


# =============================================================================
# LIFESPAN MANAGEMENT
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifespan.

    - Initialize database on startup
    - Seed default users for development
    - Clean up resources on shutdown
    """
    # Startup checks
    issues = startup_check()
    for issue in issues:
        logger.warning("Startup check: %s", issue)

    # Config validation
    config_issues = validate_config()
    log_levels = {"error": logging.ERROR, "warning": logging.WARNING, "info": logging.INFO}
    for ci in config_issues:
        logger.log(log_levels.get(ci.level, logging.INFO), "Config %s: %s", ci.key, ci.message)

    if os.getenv("STRICT_CONFIG_VALIDATION", "false").lower() == "true":
        if any(ci.level == "error" for ci in config_issues):
            raise RuntimeError("Config validation failed -- see logs above")

    # Initialize store
    store = SignalStore()
    await store.initialize()
    app.state.store = store
    logger.info("SignalStore initialized")

    # Write lock for single-writer pattern (SQLite concurrency)
    app.state.write_lock = asyncio.Lock()

    # Initialize Notion connector (app-scoped, lifecycle-managed)
    from connectors.notion_transport import NotionTransport
    from connectors.notion_connector_v2 import NotionConnector
    try:
        notion_api_key = os.environ["NOTION_API_KEY"]
        notion_db_id = os.environ["NOTION_DATABASE_ID"]
        transport = NotionTransport(api_key=notion_api_key)
        await transport.start()
        connector = NotionConnector(
            api_key=notion_api_key,
            database_id=notion_db_id,
            transport=transport,
        )
        app.state.notion_transport = transport
        app.state.notion_connector = connector
        logger.info("NotionConnector initialized (app-scoped)")
    except (KeyError, ValueError) as e:
        app.state.notion_transport = None
        app.state.notion_connector = None
        logger.warning("Notion not configured: %s -- batch commit unavailable", e)
    except Exception as e:
        app.state.notion_transport = None
        app.state.notion_connector = None
        logger.warning("Notion transport failed to start: %s -- batch commit unavailable", e)

    # Seed default users (always seed unless PRODUCTION=true)
    if os.getenv("PRODUCTION", "false").lower() != "true":
        seed_default_users()

    logger.info("Discovery Engine API started")
    yield

    # Graceful shutdown
    logger.info("Shutting down — closing store")
    await store.close()
    if getattr(app.state, "notion_transport", None):
        await app.state.notion_transport.shutdown()
        logger.info("NotionTransport shut down")
    logger.info("Shutdown complete")


# =============================================================================
# APPLICATION SETUP
# =============================================================================

app = FastAPI(
    title="Discovery Engine API",
    description="Deal sourcing and inbox management for Press On Ventures",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Production middleware (order matters: outermost executes first)
# 1. RequestId — assigns X-Request-ID to every request
# 2. RateLimit — per-IP rate limiting with path-based tiers
# 3. ExceptionHandler — catches unhandled exceptions, returns clean JSON 500
app.add_middleware(ExceptionHandlerMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIdMiddleware)

# CORS configuration for Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",  # Streamlit default
        "http://127.0.0.1:8501",
        os.getenv("FRONTEND_URL", "http://localhost:8501"),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# ROUTERS
# =============================================================================

app.include_router(auth.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(entities.router, prefix="/api/v1")
app.include_router(actions.router, prefix="/api/v1")
app.include_router(companies.router, prefix="/api/v1")
app.include_router(public.router, prefix="/api/v1")
app.include_router(scheduler.router, prefix="/api/v1")
app.include_router(triage.router, prefix="/api/v1")
app.include_router(batch.router, prefix="/api/v1")
app.include_router(merge_review.router, prefix="/api/v1")
app.include_router(canary.router, prefix="/api/v1")
app.include_router(hunter.router, prefix="/api/v1")


# =============================================================================
# HEALTH CHECK
# =============================================================================

@app.get("/health", tags=["health"])
async def health_check():
    """
    Health check endpoint.

    Returns basic service status and database connectivity.
    """
    try:
        store = app.state.store
        stats = await store.get_stats()
        return {
            "status": "healthy",
            "database": "connected",
            "total_signals": stats.get("total_signals", 0),
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e),
        }


@app.get("/", tags=["root"])
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Discovery Engine API",
        "version": "1.0.0",
        "docs": "/api/docs",
        "health": "/health",
    }


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("API_DEBUG", "false").lower() == "true",
    )
