# Docker Deployment Runbook

## Overview

The Discovery Engine runs as a single-container FastAPI service backed by SQLite
(WAL mode). It supports two deployment models:

1. **Docker Compose** (development / staging)
2. **Systemd + Docker** (production VM)

SQLite WAL mode requires **single-writer** access. Do not run multiple containers
sharing the same database file.

---

## Prerequisites

- Docker 24+ and Docker Compose v2+
- `.env` file with required API keys (see `.env.example`)
- `data/` directory for persistent SQLite storage

## Building the Image

```bash
docker build -t harmonic:latest .
```

The multi-stage build installs Python dependencies in a builder stage and copies
only the runtime into a slim image with a non-root `harmonic` user.

## Development (Docker Compose)

```bash
# Create data directory (first time)
mkdir -p data

# Copy production DB or start fresh
cp signals.db data/signals.db  # or skip for empty DB

# Start
docker compose up

# Start detached
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

The compose file mounts `./data/` to `/app/data/` (not the single `.db` file)
to ensure SQLite WAL and SHM files stay on the same filesystem.

**Health check:** The container exposes `http://localhost:8000/api/v1/health`.
Docker's built-in HEALTHCHECK runs `scripts/healthcheck_startup.py` every 30s.

## Production VM (Systemd + Docker)

### 1. Create systemd unit

```ini
# /etc/systemd/system/harmonic.service
[Unit]
Description=Harmonic Discovery Engine
After=docker.service
Requires=docker.service

[Service]
Type=simple
Restart=always
RestartSec=10
ExecStartPre=-/usr/bin/docker stop harmonic
ExecStartPre=-/usr/bin/docker rm harmonic
ExecStart=/usr/bin/docker run --name harmonic \
  -p 8000:8000 \
  -v /opt/harmonic/data:/app/data \
  -v /opt/harmonic/.env:/app/.env:ro \
  -e DISCOVERY_DB_PATH=/app/data/signals.db \
  harmonic:latest
ExecStop=/usr/bin/docker stop harmonic

[Install]
WantedBy=multi-user.target
```

### 2. Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable harmonic
sudo systemctl start harmonic
sudo systemctl status harmonic
```

### 3. Verify

```bash
curl -sf http://localhost:8000/api/v1/health | python3 -m json.tool
```

## Volume Management

SQLite in WAL mode creates three files:
- `signals.db` — main database
- `signals.db-wal` — write-ahead log
- `signals.db-shm` — shared memory index

All three must reside on the same filesystem. The `data/` volume mount ensures
this. **Never mount a single `.db` file** — WAL/SHM siblings would be created
inside the container and lost on restart.

### Backup

```bash
# While container is running (uses SQLite online backup API)
docker exec harmonic python -c "
import sqlite3, shutil
src = sqlite3.connect('/app/data/signals.db')
dst = sqlite3.connect('/app/data/signals.db.backup')
src.backup(dst)
dst.close()
src.close()
"

# Copy backup out
cp data/signals.db.backup ~/backups/signals-$(date +%Y%m%d).db
```

## Upgrade Procedure

```bash
# 1. Build new image
docker build -t harmonic:$(git rev-parse --short HEAD) .
docker tag harmonic:$(git rev-parse --short HEAD) harmonic:latest

# 2. Backup database
cp data/signals.db data/signals.db.pre-upgrade

# 3. Stop current container
docker compose down  # or: sudo systemctl stop harmonic

# 4. Start with new image (migrations run automatically on startup)
docker compose up -d  # or: sudo systemctl start harmonic

# 5. Verify health
curl -sf http://localhost:8000/api/v1/health
```

## Rollback

```bash
# 1. Stop current container
docker compose down

# 2. Restore database backup
cp data/signals.db.pre-upgrade data/signals.db

# 3. Run previous image
docker run -d --name harmonic \
  -p 8000:8000 \
  -v ./data:/app/data \
  --env-file .env \
  -e DISCOVERY_DB_PATH=/app/data/signals.db \
  harmonic:<previous-sha>

# 4. Verify
curl -sf http://localhost:8000/api/v1/health
```

## Environment Variables

Key Docker-specific variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DISCOVERY_DB_PATH` | `signals.db` | Path to SQLite database inside container |
| `API_PORT` | `8000` | Host port mapping (compose only) |
| `HEALTHCHECK_RETRIES` | `10` | Health probe max attempts |
| `HEALTHCHECK_DELAY` | `3` | Seconds between probe retries |
| `STRICT_CONFIG_VALIDATION` | `false` | Abort on config errors if `true` |

## Troubleshooting

**Container exits immediately:**
```bash
docker logs harmonic  # check startup errors
```

**Health check failing:**
```bash
docker exec harmonic python scripts/healthcheck_startup.py
```

**Database locked errors:**
Ensure only one container mounts the data directory. SQLite WAL requires
single-writer access.

**Permission denied on data volume:**
The container runs as user `harmonic` (non-root). Ensure the host `data/`
directory is writable:
```bash
chmod 777 data/  # or match container UID
```
