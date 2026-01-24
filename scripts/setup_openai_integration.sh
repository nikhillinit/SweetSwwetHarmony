#!/bin/bash
# Setup script for OpenAI/Codex integration
# This enables multi-LLM strategy iteration using your ChatGPT Pro subscription

set -e

echo "=== OpenAI Integration Setup ==="
echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ok() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }

# --- Check Python dependencies ---
echo "=== Python Dependencies ==="

# Check for openai package
if python3 -c "import openai" 2>/dev/null; then
    ok "openai package installed"
else
    warn "openai package not installed"
    echo "    Installing: pip install openai"
    pip install openai || fail "Could not install openai"
fi

# Check for mcp package
if python3 -c "from mcp.server import Server" 2>/dev/null; then
    ok "mcp package installed"
else
    warn "mcp package not installed"
    echo "    Installing: pip install mcp"
    pip install mcp || fail "Could not install mcp"
fi

echo ""

# --- Check Codex CLI ---
echo "=== Codex CLI ==="

if command -v codex &> /dev/null; then
    ok "Codex CLI installed: $(which codex)"

    # Check version
    CODEX_VERSION=$(codex --version 2>/dev/null || echo "unknown")
    ok "Version: $CODEX_VERSION"

    # Check auth status
    AUTH_STATUS=$(codex login status 2>&1 || echo "not authenticated")
    if echo "$AUTH_STATUS" | grep -qi "logged in"; then
        ok "Authenticated: $AUTH_STATUS"
    else
        warn "Not authenticated with Codex"
        echo "    Run: codex login"
    fi
else
    warn "Codex CLI not installed"
    echo "    Install with: npm install -g @openai/codex"
    echo "    Then run: codex login"
fi

echo ""

# --- Check Environment Variables ---
echo "=== Environment Variables ==="

if [ -n "$OPENAI_API_KEY" ]; then
    # Mask the key for display
    MASKED_KEY="${OPENAI_API_KEY:0:8}...${OPENAI_API_KEY: -4}"
    ok "OPENAI_API_KEY: $MASKED_KEY"
else
    warn "OPENAI_API_KEY not set"
    echo "    Set with: export OPENAI_API_KEY=sk-..."
    echo "    Get your key at: https://platform.openai.com/api-keys"
fi

echo ""

# --- Test OpenAI Connection ---
echo "=== OpenAI API Test ==="

if [ -n "$OPENAI_API_KEY" ]; then
    # Quick API test
    TEST_RESULT=$(python3 -c "
import asyncio
from openai import AsyncOpenAI

async def test():
    client = AsyncOpenAI()
    try:
        response = await client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{'role': 'user', 'content': 'Say OK'}],
            max_tokens=5
        )
        print('API connection successful')
        return True
    except Exception as e:
        print(f'API error: {e}')
        return False

asyncio.run(test())
" 2>&1)

    if echo "$TEST_RESULT" | grep -q "successful"; then
        ok "OpenAI API connection verified"
    else
        fail "OpenAI API connection failed"
        echo "    $TEST_RESULT"
    fi
else
    warn "Skipping API test (no API key)"
fi

echo ""

# --- Summary ---
echo "=== Quick Commands ==="
echo ""
echo "# Test OpenAI integration"
echo "python -c \"from integrations import OpenAIMCPServer; print('OK')\""
echo ""
echo "# Test Codex wrapper"
echo "python -m integrations.codex_wrapper check"
echo ""
echo "# Run OpenAI MCP server"
echo "python -m integrations.openai_mcp"
echo ""
echo "# Strategy iteration example"
echo "python -m integrations.strategy_iterator thesis --question 'How to reduce false positives?'"
echo ""
echo "=== Setup Complete ==="
