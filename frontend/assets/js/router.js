/**
 * Hash-based SPA router with auth guard and view lifecycle.
 */
import { isAuthenticated } from './api.js';

let currentView = null;
let currentCleanup = null;
const routes = {};

export function registerRoute(hash, viewModule) {
  routes[hash] = viewModule;
}

export function navigate(hash) {
  window.location.hash = hash;
}

function getHash() {
  const raw = window.location.hash || '#/';
  // Strip query params for routing
  return raw.split('?')[0];
}

function getHashParams() {
  const raw = window.location.hash || '';
  const qIdx = raw.indexOf('?');
  if (qIdx === -1) return {};
  const params = {};
  new URLSearchParams(raw.slice(qIdx + 1)).forEach((v, k) => { params[k] = v; });
  return params;
}

export { getHashParams };

async function handleRoute() {
  const hash = getHash();
  const appLayout = document.getElementById('app-layout');
  const container = document.getElementById('app');

  // Auth guard
  if (hash !== '#/login' && !isAuthenticated()) {
    navigate('#/login');
    return;
  }

  // If authenticated and hitting login, redirect to dashboard
  if (hash === '#/login' && isAuthenticated()) {
    navigate('#/');
    return;
  }

  // Show/hide app layout (login doesn't use sidebar)
  if (hash === '#/login') {
    appLayout.style.display = 'none';
  } else {
    appLayout.style.display = 'flex';
  }

  // Unmount previous view
  if (currentCleanup) {
    try { currentCleanup(); } catch { /* ignore */ }
    currentCleanup = null;
  }

  const view = routes[hash];
  if (!view) {
    // Fallback to dashboard
    if (hash !== '#/') navigate('#/');
    return;
  }

  // Update active nav link
  document.querySelectorAll('.nav-link').forEach(link => {
    const isActive = link.getAttribute('href') === hash;
    link.classList.toggle('active', isActive);
  });

  // Update slide indicator
  updateIndicator(hash);

  // Show skeleton
  if (hash !== '#/login') {
    container.innerHTML = '';
    const skeleton = document.createElement('div');
    skeleton.innerHTML =
      '<div class="skeleton skeleton-metric" style="margin-bottom:16px;"></div>' +
      '<div class="skeleton skeleton-card" style="margin-bottom:16px;"></div>' +
      '<div class="skeleton skeleton-card"></div>';
    container.appendChild(skeleton);
  }

  // Mount new view
  try {
    const target = hash === '#/login' ? document.body : container;
    currentCleanup = await view.mount(target, getHashParams());
    currentView = hash;
  } catch (err) {
    if (err.name === 'AbortError') return;
    console.error('View mount error:', err);
    if (container) {
      container.innerHTML = '';
      const errDiv = document.createElement('div');
      errDiv.className = 'error-state';
      const msg = document.createElement('p');
      msg.className = 'error-state-message';
      msg.textContent = 'Something went wrong loading this view.';
      const btn = document.createElement('button');
      btn.className = 'btn btn-ghost';
      btn.textContent = 'Retry';
      btn.onclick = () => handleRoute();
      errDiv.appendChild(msg);
      errDiv.appendChild(btn);
      container.appendChild(errDiv);
    }
  }
}

function updateIndicator(hash) {
  const indicator = document.getElementById('nav-indicator');
  const activeLink = document.querySelector(`.nav-link[href="${hash}"]`);
  if (indicator && activeLink) {
    const nav = document.getElementById('sidebar-nav');
    const navRect = nav.getBoundingClientRect();
    const linkRect = activeLink.getBoundingClientRect();
    indicator.style.top = (linkRect.top - navRect.top) + 'px';
    indicator.style.display = 'block';
  } else if (indicator) {
    indicator.style.display = 'none';
  }
}

export function startRouter() {
  window.addEventListener('hashchange', handleRoute);
  handleRoute();
}
