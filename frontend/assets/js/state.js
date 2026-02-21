/**
 * Event bus for cross-view communication.
 * Events: triage:changed, companies:loaded, auth:logout, theme:toggle
 */
const listeners = new Map();

export function on(event, callback) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(callback);
  return () => listeners.get(event)?.delete(callback);
}

export function emit(event, data) {
  const cbs = listeners.get(event);
  if (cbs) cbs.forEach(cb => cb(data));
}

export function off(event, callback) {
  listeners.get(event)?.delete(callback);
}
