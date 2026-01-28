#!/bin/bash
#
# SweetSweetHarmony Restore Script
#
# Restores databases and blobs from a backup archive.
#
# Usage:
#   ./scripts/restore.sh /path/to/sweetharmony_YYYYMMDD_HHMMSS.tar.zst
#
# Safety:
#   - Stops services before restore
#   - Creates a pre-restore backup
#   - Verifies checksum before restore
#   - Restarts services after restore

set -euo pipefail

# Configuration
DATA_DIR="${DATA_DIR:-/opt/sweetharmony/data}"
SIGNALS_DB="$DATA_DIR/signals.db"
PRIVATE_GRAPH_DB="$DATA_DIR/private_graph.db"
BLOBS_DIR="$DATA_DIR/blobs"

# Check arguments
if [ $# -lt 1 ]; then
    echo "Usage: $0 /path/to/backup.tar.zst"
    exit 1
fi

ARCHIVE_PATH="$1"
if [ ! -f "$ARCHIVE_PATH" ]; then
    echo "Error: Backup file not found: $ARCHIVE_PATH"
    exit 1
fi

echo "SweetSweetHarmony Restore"
echo "========================="
echo "Archive: $ARCHIVE_PATH"
echo ""

# Step 1: Verify checksum (if available)
echo "[1/5] Verifying checksum..."
if [ -f "$ARCHIVE_PATH.sha256" ]; then
    if sha256sum -c "$ARCHIVE_PATH.sha256" --status; then
        echo "  - Checksum verified"
    else
        echo "  - ERROR: Checksum mismatch!"
        exit 1
    fi
else
    echo "  - Checksum file not found, skipping verification"
fi

# Step 2: Stop services
echo "[2/5] Stopping services..."
if systemctl is-active --quiet sweetharmony 2>/dev/null; then
    sudo systemctl stop sweetharmony-dashboard 2>/dev/null || true
    sudo systemctl stop sweetharmony
    echo "  - Services stopped"
else
    echo "  - Services not running (or not using systemd)"
fi

# Step 3: Create pre-restore backup
echo "[3/5] Creating pre-restore backup..."
PRE_RESTORE_DIR="/tmp/sweetharmony_pre_restore_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$PRE_RESTORE_DIR"
if [ -f "$SIGNALS_DB" ]; then
    cp "$SIGNALS_DB" "$PRE_RESTORE_DIR/"
fi
if [ -f "$PRIVATE_GRAPH_DB" ]; then
    cp "$PRIVATE_GRAPH_DB" "$PRE_RESTORE_DIR/"
fi
echo "  - Pre-restore backup: $PRE_RESTORE_DIR"

# Step 4: Extract and restore
echo "[4/5] Restoring from backup..."
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Extract archive
zstd -d "$ARCHIVE_PATH" -c | tar -C "$TEMP_DIR" -xf -

# Restore databases
mkdir -p "$DATA_DIR"

if [ -f "$TEMP_DIR/signals.db" ]; then
    cp "$TEMP_DIR/signals.db" "$SIGNALS_DB"
    echo "  - Restored signals.db"
fi

if [ -f "$TEMP_DIR/private_graph.db" ]; then
    cp "$TEMP_DIR/private_graph.db" "$PRIVATE_GRAPH_DB"
    echo "  - Restored private_graph.db"
fi

# Restore blobs (if present)
if [ -d "$TEMP_DIR/blobs" ]; then
    rm -rf "$BLOBS_DIR"
    cp -r "$TEMP_DIR/blobs" "$BLOBS_DIR"
    echo "  - Restored blobs directory"
fi

# Set permissions
if id sweetharmony &>/dev/null; then
    chown -R sweetharmony:sweetharmony "$DATA_DIR"
fi

# Step 5: Restart services and verify
echo "[5/5] Restarting services..."
if systemctl list-unit-files | grep -q sweetharmony; then
    sudo systemctl start sweetharmony
    sleep 2
    sudo systemctl start sweetharmony-dashboard 2>/dev/null || true

    # Verify health
    sleep 3
    if curl -s http://localhost:8000/health | grep -q "healthy"; then
        echo "  - Services restarted and healthy"
    else
        echo "  - WARNING: Health check failed, please verify manually"
    fi
else
    echo "  - Systemd services not configured, start manually"
fi

echo ""
echo "========================="
echo "Restore completed!"
echo ""
echo "Restored files:"
[ -f "$SIGNALS_DB" ] && echo "  - $SIGNALS_DB ($(du -h "$SIGNALS_DB" | cut -f1))"
[ -f "$PRIVATE_GRAPH_DB" ] && echo "  - $PRIVATE_GRAPH_DB ($(du -h "$PRIVATE_GRAPH_DB" | cut -f1))"
[ -d "$BLOBS_DIR" ] && echo "  - $BLOBS_DIR"
echo ""
echo "Pre-restore backup saved to: $PRE_RESTORE_DIR"
echo "  (Delete manually after verifying restore)"
