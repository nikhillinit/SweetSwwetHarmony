#!/usr/bin/env python3
"""Discover-and-poll healthcheck for API startup.

1) Fetches /openapi.json to discover a health-like endpoint.
2) Falls back to a ranked candidate list if discovery fails.
3) Polls the best endpoint until healthy or timeout.

Exit codes:
    0 = healthy
    1 = timeout / unhealthy
    2 = no reachable endpoint found

Uses only stdlib — no external dependencies.

Environment variables:
    HEALTHCHECK_RETRIES  Max poll attempts (default: 10)
    HEALTHCHECK_DELAY    Seconds between retries (default: 3)
    HEALTHCHECK_PORT     API port (default: 8000)
    HEALTHCHECK_HOST     API host (default: 127.0.0.1)
"""
import argparse
import http.client
import json
import os
import sys
import time

# ── Defaults ────────────────────────────────────────────────────────────
HOST = os.environ.get("HEALTHCHECK_HOST", "127.0.0.1")
PORT = int(os.environ.get("HEALTHCHECK_PORT", "8000"))
MAX_RETRIES = int(os.environ.get("HEALTHCHECK_RETRIES", "10"))
RETRY_DELAY = int(os.environ.get("HEALTHCHECK_DELAY", "3"))

# Preferred candidates in priority order (checked before keyword search).
DEFAULT_CANDIDATES = [
    "/health",
    "/api/v1/health",
    "/healthz",
    "/readyz",
    "/livez",
    "/status",
]

# Keywords scored when scanning OpenAPI paths.
_HEALTH_KEYWORDS = ("health", "ready", "live", "status", "ping")

# Exported for smoke-test contract assertions.
PATH: str = "/health"  # updated at runtime by discover()


# ── HTTP helpers (stdlib only) ──────────────────────────────────────────

def _http_get(host: str, port: int, path: str, timeout: float = 3.0) -> tuple[int, bytes]:
    """Issue a GET and return (status_code, body). Raises on network error."""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


# ── OpenAPI discovery ───────────────────────────────────────────────────

def fetch_openapi(host: str, port: int, timeout: float = 3.0) -> dict | None:
    """Fetch and parse /openapi.json. Returns None on any failure."""
    try:
        status, body = _http_get(host, port, "/openapi.json", timeout)
        if 200 <= status < 300:
            return json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        pass
    return None


def discover_health_path(spec: dict, preferred: list[str]) -> str | None:
    """Find the best health-like GET endpoint in an OpenAPI spec.

    Strategy:
    1. Check preferred candidates in order — first match wins.
    2. Fall back to keyword scoring across all GET-capable paths.
    """
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return None

    # Normalize trailing slashes for comparison.
    normalized: dict[str, dict] = {}
    for p, methods in paths.items():
        if isinstance(methods, dict):
            normalized[p.rstrip("/") or "/"] = methods

    # 1) Exact match on preferred candidates.
    for cand in preferred:
        key = cand.rstrip("/") or "/"
        methods = normalized.get(key)
        if methods and isinstance(methods, dict):
            if any(m in methods for m in ("get", "head")):
                return cand

    # 2) Keyword scoring.
    scored: list[tuple[int, str]] = []
    for p, methods in normalized.items():
        if not isinstance(methods, dict):
            continue
        if not any(m in methods for m in ("get", "head")):
            continue
        low = p.lower()
        score = sum(1 for kw in _HEALTH_KEYWORDS if kw in low)
        if score:
            scored.append((score, p))

    if scored:
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][1]

    return None


# ── Probing ─────────────────────────────────────────────────────────────

def check_health(host: str, port: int, path: str, timeout: float = 3.0) -> bool:
    """Single health probe. Returns True on any 2xx status."""
    try:
        status, _ = _http_get(host, port, path, timeout)
        return 200 <= status < 300
    except Exception:
        return False


def poll(host: str, port: int, path: str,
         retries: int, delay: float, timeout: float = 3.0) -> bool:
    """Poll a single path. Returns True if healthy within retries."""
    for attempt in range(1, retries + 1):
        if check_health(host, port, path, timeout):
            print(f"[OK] {path} -> healthy (attempt {attempt})")
            return True
        print(f"[WAIT] {path} (attempt {attempt}/{retries})")
        if attempt < retries:
            time.sleep(delay)
    return False


# ── Orchestration ───────────────────────────────────────────────────────

def discover(host: str, port: int, candidates: list[str],
             request_timeout: float = 3.0) -> list[str]:
    """Return an ordered list of paths to try (discovered + candidates + fallbacks)."""
    probe_paths: list[str] = []

    # Try OpenAPI discovery.
    spec = fetch_openapi(host, port, request_timeout)
    if spec:
        discovered = discover_health_path(spec, candidates)
        if discovered:
            print(f"[DISCOVERED] {discovered} (from /openapi.json)")
            probe_paths.append(discovered)
        else:
            print("[DISCOVERY] /openapi.json fetched but no health endpoint found")
    else:
        print("[DISCOVERY] /openapi.json not available, using candidates")

    # Append static candidates + fallbacks.
    probe_paths.extend(candidates)
    probe_paths.extend(["/docs", "/"])

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for p in probe_paths:
        key = p.rstrip("/") or "/"
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique


def main() -> int:
    """Discover health endpoint, then poll until healthy or timeout."""
    global PATH

    p = argparse.ArgumentParser(
        description="Discover-and-poll healthcheck for API startup",
    )
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--retries", type=int, default=MAX_RETRIES)
    p.add_argument("--delay", type=float, default=RETRY_DELAY)
    p.add_argument("--request-timeout", type=float, default=3.0)
    p.add_argument(
        "--candidates",
        help="Comma-separated preferred health paths",
    )
    args = p.parse_args()

    candidates = (
        [c.strip() for c in args.candidates.split(",") if c.strip()]
        if args.candidates
        else list(DEFAULT_CANDIDATES)
    )

    paths = discover(args.host, args.port, candidates, args.request_timeout)

    for path in paths:
        print(f"[PROBE] Trying {path}")
        if poll(args.host, args.port, path, args.retries, args.delay, args.request_timeout):
            PATH = path
            return 0

    print(f"[FAIL] API failed to become healthy within {args.retries * args.delay}s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
