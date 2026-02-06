#!/bin/bash
# Quick setup for internal team

set -e

echo "🚀 Setting up Ops Memory System..."

python_version=$(python3 --version 2>&1 | awk '{print $2}')

if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 8) else 1)"; then
    echo "❌ Python 3.8+ required (found $python_version)"
    exit 1
fi

echo "✓ Python $python_version"

mkdir -p ops/{memory,trends,artifacts}
mkdir -p consumer
echo "✓ Directory structure created"

touch ops/__init__.py ops/memory/__init__.py ops/trends/__init__.py consumer/__init__.py
echo "✓ Python packages initialized"

echo "📦 Installing dependencies..."
python3 -m pip install -r requirements.txt

echo "🔎 Checking SQLite FTS5 support..."
python3 - <<'PY'
import sqlite3, sys
conn = sqlite3.connect(":memory:")
try:
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
except sqlite3.OperationalError as e:
    print("❌ SQLite FTS5 not available in this environment:", e)
    print("   You need a Python/SQLite build compiled with FTS5 enabled.")
    sys.exit(1)
print("✓ SQLite FTS5 available")
PY

echo "🗄️  Initializing database..."
python3 - <<'PY'
from ops.storage import OpsStorage
OpsStorage("signals.db")
print("✓ Database initialized with FTS5 support")
PY

if [ -z "$GEMINI_API_KEY" ] && [ -z "$GOOGLE_API_KEY" ]; then
    echo ""
    echo "⚠️  Warning: GEMINI_API_KEY or GOOGLE_API_KEY not set"
    echo "   Set one before running extraction:"
    echo "   export GEMINI_API_KEY='your-key-here'"
fi

read -p "Test YouTube fetcher? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🎬 Testing YouTube fetcher..."
    python3 ops/trends/youtube.py
fi

echo "📋 Generating initial briefing..."
python3 -m ops.memory.briefing

echo ""
echo "✅ Setup complete!"
echo ""
echo "🎯 NEW: Dynamic context retrieval via FTS5"
echo "   The classifier searches for relevant facts instead of using static briefing"
echo
