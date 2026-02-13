#!/usr/bin/env python3
"""Startup health probe for systemd ExecStartPost.

Polls the API /api/v1/health endpoint with retries. Exits 0 on success,
1 on timeout. Uses only stdlib -- no external dependencies.

Environment variables:
    HEALTHCHECK_RETRIES  Max attempts (default: 10)
    HEALTHCHECK_DELAY    Seconds between retries (default: 3)
    HEALTHCHECK_PORT     API port (default: 8000)
"""
import http.client
import os
import sys
import time

MAX_RETRIES = int(os.environ.get("HEALTHCHECK_RETRIES", "10"))
RETRY_DELAY = int(os.environ.get("HEALTHCHECK_DELAY", "3"))
PORT = int(os.environ.get("HEALTHCHECK_PORT", "8000"))
PATH = "/api/v1/health"


def check_health() -> bool:
    """Attempt a single health check against localhost."""
    try:
        conn = http.client.HTTPConnection("localhost", PORT, timeout=5)
        conn.request("GET", PATH)
        resp = conn.getresponse()
        conn.close()
        return resp.status == 200
    except (ConnectionRefusedError, OSError, http.client.HTTPException):
        return False


def main() -> int:
    """Poll health endpoint with retries. Returns 0 on success, 1 on timeout."""
    for attempt in range(1, MAX_RETRIES + 1):
        if check_health():
            print(f"API healthy after {attempt} check(s)")
            return 0
        print(f"Waiting for API... ({attempt}/{MAX_RETRIES})")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    print(f"API failed to become healthy within {MAX_RETRIES * RETRY_DELAY}s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
