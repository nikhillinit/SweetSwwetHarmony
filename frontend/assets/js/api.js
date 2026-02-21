/**
 * API client with cache, error normalization, and envelope detection.
 *
 * INVARIANT: Never innerHTML with API data — use textContent or escapeHTML().
 */

// --- Endpoint contract registry ---
const ENVELOPE_ENDPOINTS = new Set([
  '/api/v1/triage',
  '/api/v1/entities',
  '/api/v1/batches',
]);

function isEnvelopeEndpoint(path) {
  return [...ENVELOPE_ENDPOINTS].some(prefix => path.startsWith(prefix));
}

// --- Error normalization (5 backend shapes → 1) ---
function normalizeError(status, json) {
  if (!json || typeof json !== 'object') {
    return { code: 'UNKNOWN', message: 'Request failed' };
  }
  // Shape 1: flat middleware (429, 500) — {error: string, ...}
  if (typeof json.error === 'string' && !json.detail) {
    return { code: json.code || 'UNKNOWN', message: json.error || json.message };
  }
  // Shape 2: jwt_auth 401 — {detail: "string"}
  if (typeof json.detail === 'string') {
    return { code: 'UNAUTHORIZED', message: json.detail };
  }
  // Shape 3: contracts/rbac structured — {detail: {error, code, message, ...}}
  if (json.detail && typeof json.detail === 'object') {
    return {
      code: json.detail.code || 'UNKNOWN',
      message: json.detail.message || 'Request failed',
      detail: json.detail.detail,
    };
  }
  return { code: 'UNKNOWN', message: 'Request failed' };
}

// --- Promise cache ---
const cache = new Map();
const SUCCESS_TTL = 30000;
const ERROR_TTL = 3000;

function getCached(path) {
  const entry = cache.get(path);
  if (!entry) return null;
  if (Date.now() > entry.expiry) {
    cache.delete(path);
    return null;
  }
  return entry.promise;
}

function setCached(path, promise, ttl) {
  cache.set(path, { promise, expiry: Date.now() + ttl });
}

export function clearCache() {
  cache.clear();
}

// --- Token management ---
function getToken() {
  return sessionStorage.getItem('jwt_token');
}

function setToken(token, expiresAt) {
  sessionStorage.setItem('jwt_token', token);
  if (expiresAt) sessionStorage.setItem('jwt_expires_at', expiresAt);
}

function clearToken() {
  sessionStorage.removeItem('jwt_token');
  sessionStorage.removeItem('jwt_expires_at');
}

export function isAuthenticated() {
  const token = getToken();
  if (!token) return false;
  const exp = sessionStorage.getItem('jwt_expires_at');
  if (exp && new Date(exp) < new Date()) {
    clearToken();
    return false;
  }
  return true;
}

// --- Core fetch ---
async function doFetch(path, opts = {}) {
  const { method = 'GET', body, signal } = opts;
  const headers = { 'Content-Type': 'application/json' };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const fetchOpts = { method, headers, signal };
  if (body !== undefined) fetchOpts.body = JSON.stringify(body);

  const res = await fetch(path, fetchOpts);

  let json;
  try {
    json = await res.json();
  } catch {
    json = null;
  }

  if (!res.ok) {
    const error = normalizeError(res.status, json);

    // 401 → redirect to login
    if (res.status === 401) {
      clearToken();
      if (window.location.hash !== '#/login') {
        window.location.hash = '#/login';
      }
      return { ok: false, status: res.status, error };
    }

    // 429 → note Retry-After
    if (res.status === 429) {
      error.code = 'RATE_LIMITED';
      error.retryAfter = res.headers.get('Retry-After');
    }

    // 403
    if (res.status === 403) {
      error.code = error.code || 'INSUFFICIENT_PERMISSION';
    }

    // 409
    if (res.status === 409) {
      error.code = error.code || 'VERSION_MISMATCH';
    }

    // 423
    if (res.status === 423) {
      error.code = error.code || 'FEATURE_DISABLED';
    }

    return { ok: false, status: res.status, error };
  }

  // Unwrap envelope if applicable
  if (isEnvelopeEndpoint(path) && json && 'data' in json) {
    return { ok: true, data: json.data, meta: json.meta || null };
  }

  return { ok: true, data: json };
}

// --- Public API ---
export async function get(path, opts = {}) {
  const { signal, noCache } = opts;

  if (!noCache) {
    const cached = getCached(path);
    if (cached) {
      if (signal) {
        // Race cached promise against consumer abort
        return Promise.race([
          cached,
          new Promise((_, reject) => {
            if (signal.aborted) reject(new DOMException('Aborted', 'AbortError'));
            signal.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError'))
            );
          }),
        ]);
      }
      return cached;
    }
  }

  // Create and cache the promise (cache the Promise, not the result)
  const promise = doFetch(path, { method: 'GET' }).then(result => {
    // Set appropriate TTL based on success/failure
    if (!result.ok) {
      setCached(path, Promise.resolve(result), ERROR_TTL);
    }
    return result;
  });

  setCached(path, promise, SUCCESS_TTL);

  if (signal) {
    return Promise.race([
      promise,
      new Promise((_, reject) => {
        if (signal.aborted) reject(new DOMException('Aborted', 'AbortError'));
        signal.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError'))
        );
      }),
    ]);
  }

  return promise;
}

export async function post(path, body, opts = {}) {
  const result = await doFetch(path, { method: 'POST', body, signal: opts.signal });
  if (result.ok) clearCache();
  return result;
}

export async function put(path, body, opts = {}) {
  const result = await doFetch(path, { method: 'PUT', body, signal: opts.signal });
  if (result.ok) clearCache();
  return result;
}

export async function del(path, opts = {}) {
  const result = await doFetch(path, { method: 'DELETE', signal: opts.signal });
  if (result.ok) clearCache();
  return result;
}

export async function login(email, password) {
  const result = await doFetch('/api/v1/auth/login', {
    method: 'POST',
    body: { email, password },
  });
  if (result.ok && result.data?.access_token) {
    setToken(result.data.access_token, result.data.expires_at);
  }
  return result;
}

export async function logout() {
  try {
    await doFetch('/api/v1/auth/logout', { method: 'POST' });
  } catch { /* ignore */ }
  clearToken();
  clearCache();
}

export function getUserInfo() {
  const token = getToken();
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return { email: payload.sub, role: payload.role };
  } catch {
    return null;
  }
}

// --- XSS-safe helpers ---
const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHTML(strings, ...values) {
  if (typeof strings === 'string') {
    return strings.replace(/[&<>"']/g, c => ESC_MAP[c]);
  }
  let result = strings[0];
  for (let i = 0; i < values.length; i++) {
    result += String(values[i]).replace(/[&<>"']/g, c => ESC_MAP[c]);
    result += strings[i + 1];
  }
  return result;
}

// --- Toast ---
let toastTimeout;

export function showToast(message, type = 'info', duration = 4000) {
  const region = document.getElementById('toast-region');
  if (!region) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  region.appendChild(toast);
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    toast.remove();
  }, duration);
}
