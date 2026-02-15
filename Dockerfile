# =============================================================================
# Discovery Engine — Multi-stage Production Image
# =============================================================================
# Build:  docker build -t harmonic .
# Run:    docker run -p 8000:8000 -v ./data:/app/data --env-file .env harmonic
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Builder — install Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: Runtime — lean production image
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# Non-root user
RUN groupadd -r harmonic && useradd -r -g harmonic harmonic

WORKDIR /app

# Installed packages from builder
COPY --from=builder /install /usr/local

# Application source + runtime config
COPY api/ api/
COPY collectors/ collectors/
COPY config/ config/
COPY connectors/ connectors/
COPY dashboard/ dashboard/
COPY discovery_engine/ discovery_engine/
COPY distribution/ distribution/
COPY intelligence/ intelligence/
COPY integrations/ integrations/
COPY monitoring/ monitoring/
COPY ops/ ops/
COPY scripts/ scripts/
COPY storage/ storage/
COPY utils/ utils/
COPY workflows/ workflows/
COPY run_pipeline.py .

# Data directory for SQLite WAL-safe volume mount
RUN mkdir -p /app/data && chown -R harmonic:harmonic /app

USER harmonic

EXPOSE 8000

# Health probe — discover-and-poll (tries /health first, then OpenAPI discovery)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python scripts/healthcheck_startup.py --retries 1 --delay 0 || exit 1

# Default: run API server (single-worker for SQLite single-writer safety)
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
