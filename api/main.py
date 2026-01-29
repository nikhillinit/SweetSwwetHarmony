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
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.routers import actions, companies, public, auth, health, jobs, entities
from api.auth.jwt_auth import seed_default_users
from storage.signal_store import SignalStore


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
    # Startup
    store = SignalStore()
    await store.initialize()
    app.state.store = store

    # Write lock for single-writer pattern (SQLite concurrency)
    app.state.write_lock = asyncio.Lock()

    # Seed default users (always seed unless PRODUCTION=true)
    if os.getenv("PRODUCTION", "false").lower() != "true":
        seed_default_users()

    yield

    # Shutdown
    await store.close()


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
