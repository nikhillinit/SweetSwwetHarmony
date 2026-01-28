#!/bin/bash
#
# SweetSweetHarmony Backup Script
#
# Creates compressed backups of both databases with atomic snapshots.
# Supports local storage and optional S3/B2 upload.
#
# Usage:
#   ./scripts/backup.sh                    # Local backup only
#   ./scripts/backup.sh --upload-s3        # Local + S3 upload
#   ./scripts/backup.sh --upload-b2        # Local + Backblaze B2 upload
#
# Restore:
#   ./scripts/restore.sh /path/to/backup.tar.gz

set -euo pipefail

# Configuration
BACKUP_DIR="${BACKUP_DIR:-/backups/sweetharmony}"
DATA_DIR="${DATA_DIR:-/opt/sweetharmony/data}"
SIGNALS_DB="${SIGNALS_DB:-$DATA_DIR/signals.db}"
PRIVATE_GRAPH_DB="${PRIVATE_GRAPH_DB:-$DATA_DIR/private_graph.db}"
BLOBS_DIR="${BLOBS_DIR:-$DATA_DIR/blobs}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="sweetharmony_${DATE}"

# Parse arguments
UPLOAD_S3=false
UPLOAD_B2=false
for arg in "$@"; do
    case $arg in
        --upload-s3) UPLOAD_S3=true ;;
        --upload-b2) UPLOAD_B2=true ;;
        --help|-h)
            echo "Usage: $0 [--upload-s3] [--upload-b2]"
            exit 0
            ;;
    esac
done

# Create backup directory
mkdir -p "$BACKUP_DIR"

echo "Starting backup: $BACKUP_NAME"
echo "================================"

# Step 1: Checkpoint WAL files (ensures clean snapshot)
echo "[1/5] Checkpointing WAL files..."
if [ -f "$SIGNALS_DB" ]; then
    sqlite3 "$SIGNALS_DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
fi
if [ -f "$PRIVATE_GRAPH_DB" ]; then
    sqlite3 "$PRIVATE_GRAPH_DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
fi

# Step 2: Create atomic database backups
echo "[2/5] Creating database snapshots..."
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

if [ -f "$SIGNALS_DB" ]; then
    sqlite3 "$SIGNALS_DB" ".backup '$TEMP_DIR/signals.db'"
    echo "  - signals.db: $(du -h "$TEMP_DIR/signals.db" | cut -f1)"
fi

if [ -f "$PRIVATE_GRAPH_DB" ]; then
    sqlite3 "$PRIVATE_GRAPH_DB" ".backup '$TEMP_DIR/private_graph.db'"
    echo "  - private_graph.db: $(du -h "$TEMP_DIR/private_graph.db" | cut -f1)"
fi

# Step 3: Copy blob directory (if exists and not too large)
echo "[3/5] Copying blob storage..."
if [ -d "$BLOBS_DIR" ]; then
    BLOB_SIZE=$(du -sm "$BLOBS_DIR" 2>/dev/null | cut -f1 || echo "0")
    if [ "$BLOB_SIZE" -lt 5000 ]; then
        cp -r "$BLOBS_DIR" "$TEMP_DIR/blobs"
        echo "  - blobs: ${BLOB_SIZE}MB"
    else
        echo "  - blobs: ${BLOB_SIZE}MB (skipped - too large, use incremental)"
    fi
else
    echo "  - blobs: not found"
fi

# Step 4: Create compressed archive
echo "[4/5] Creating compressed archive..."
ARCHIVE_PATH="$BACKUP_DIR/${BACKUP_NAME}.tar.zst"
tar -C "$TEMP_DIR" -cf - . | zstd -T0 -3 > "$ARCHIVE_PATH"
ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1)
echo "  - Archive: $ARCHIVE_PATH ($ARCHIVE_SIZE)"

# Create sha256 checksum
sha256sum "$ARCHIVE_PATH" > "$ARCHIVE_PATH.sha256"

# Step 5: Upload to cloud (optional)
if [ "$UPLOAD_S3" = true ]; then
    echo "[5/5] Uploading to S3..."
    if command -v aws &> /dev/null; then
        aws s3 cp "$ARCHIVE_PATH" "s3://${S3_BUCKET:-sweetharmony-backups}/${BACKUP_NAME}.tar.zst"
        aws s3 cp "$ARCHIVE_PATH.sha256" "s3://${S3_BUCKET:-sweetharmony-backups}/${BACKUP_NAME}.tar.zst.sha256"
        echo "  - Uploaded to S3"
    else
        echo "  - aws CLI not found, skipping S3 upload"
    fi
elif [ "$UPLOAD_B2" = true ]; then
    echo "[5/5] Uploading to Backblaze B2..."
    if command -v b2 &> /dev/null; then
        b2 upload-file "${B2_BUCKET:-sweetharmony-backups}" "$ARCHIVE_PATH" "${BACKUP_NAME}.tar.zst"
        b2 upload-file "${B2_BUCKET:-sweetharmony-backups}" "$ARCHIVE_PATH.sha256" "${BACKUP_NAME}.tar.zst.sha256"
        echo "  - Uploaded to B2"
    else
        echo "  - b2 CLI not found, skipping B2 upload"
    fi
else
    echo "[5/5] Cloud upload skipped (use --upload-s3 or --upload-b2)"
fi

# Cleanup old backups
echo ""
echo "Cleaning up old backups (older than $RETENTION_DAYS days)..."
find "$BACKUP_DIR" -name "sweetharmony_*.tar.zst" -mtime +$RETENTION_DAYS -delete 2>/dev/null || true
find "$BACKUP_DIR" -name "sweetharmony_*.tar.zst.sha256" -mtime +$RETENTION_DAYS -delete 2>/dev/null || true

# Summary
echo ""
echo "================================"
echo "Backup completed successfully!"
echo "  Archive: $ARCHIVE_PATH"
echo "  Size: $ARCHIVE_SIZE"
echo "  Checksum: $ARCHIVE_PATH.sha256"
echo ""
echo "To restore: ./scripts/restore.sh $ARCHIVE_PATH"
