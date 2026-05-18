/**
 * Discovery Engine — SPA entry point.
 */
import { registerRoute, startRouter, navigate } from './router.js';
import { getUserInfo, logout, clearCache, isAuthenticated } from './api.js';
import { on, emit } from './state.js';
import { initAmbient } from './constellation.js';

// --- Import views ---
import * as loginView from './views/login.js';
import * as dashboardView from './views/dashboard.js';
import * as companiesView from './views/companies.js';
import * as inboxView from './views/inbox.js';
import * as healthView from './views/health.js';
import * as manualView from './views/manual.js';

// --- Register routes ---
registerRoute('#/login', loginView);
registerRoute('#/', dashboardView);
registerRoute('#/companies', companiesView);
registerRoute('#/inbox', inboxView);
registerRoute('#/health', healthView);
registerRoute('#/manual', manualView);

// Constellation is part of the same module — register a thin wrapper
registerRoute('#/constellation', {
  mount(container) {
    return mountConstellation(container);
  }
});

// --- Constellation full view ---
import { ConstellationRenderer } from './constellation.js';

function mountConstellation(container) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'view-header';
  const title = document.createElement('h2');
  title.className = 'view-title';
  title.textContent = 'Constellation';
  header.appendChild(title);
  const refreshBtn = document.createElement('button');
  refreshBtn.className = 'btn btn-ghost btn-sm';
  refreshBtn.textContent = 'Refresh';
  refreshBtn.addEventListener('click', () => renderer._fetchData());
  header.appendChild(refreshBtn);
  container.appendChild(header);

  const wrapper = document.createElement('div');
  wrapper.className = 'constellation-container';

  const canvas = document.createElement('canvas');
  canvas.className = 'constellation-canvas';
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Company constellation visualization');
  wrapper.appendChild(canvas);

  // Tooltip
  const tooltip = document.createElement('div');
  tooltip.className = 'constellation-tooltip';
  tooltip.style.display = 'none';
  wrapper.appendChild(tooltip);

  container.appendChild(wrapper);

  // A11y fallback
  const a11y = document.createElement('div');
  a11y.className = 'constellation-a11y';
  a11y.textContent = 'Visual representation — see company list for accessible table view.';
  container.appendChild(a11y);

  // Legend
  const legend = document.createElement('div');
  legend.className = 'constellation-legend';
  const statuses = [
    { id: 'source', label: 'Source', color: '#9BA2AA' },
    { id: 'tracking', label: 'Tracking', color: '#B9A3FB' },
    { id: 'diligence', label: 'Diligence', color: '#F7B84D' },
    { id: 'funded', label: 'Funded', color: '#34D399' },
    { id: 'passed', label: 'Passed', color: '#F87171' },
  ];
  const hiddenStatuses = new Set();
  statuses.forEach(s => {
    const item = document.createElement('div');
    item.className = 'legend-item';
    const dot = document.createElement('span');
    dot.className = 'legend-shape';
    dot.style.cssText = `width:8px;height:8px;border-radius:50%;background:${s.color};display:inline-block;`;
    item.appendChild(dot);
    const label = document.createElement('span');
    label.textContent = s.label;
    item.appendChild(label);
    item.addEventListener('click', () => {
      if (hiddenStatuses.has(s.id)) {
        hiddenStatuses.delete(s.id);
        item.classList.remove('legend-item-hidden');
      } else {
        hiddenStatuses.add(s.id);
        item.classList.add('legend-item-hidden');
      }
      renderer.setHiddenStatuses(hiddenStatuses);
    });
    legend.appendChild(item);
  });
  wrapper.appendChild(legend);

  const renderer = new ConstellationRenderer(canvas, tooltip, { mode: 'full' });
  renderer.start();

  return () => {
    renderer.stop();
  };
}

// --- Setup sidebar user info + logout ---
function updateUserInfo() {
  const info = getUserInfo();
  const avatar = document.getElementById('user-avatar');
  const name = document.getElementById('user-name');
  if (info && avatar && name) {
    avatar.textContent = (info.email || '?')[0].toUpperCase();
    name.textContent = info.email || '';
  }
}

document.getElementById('btn-logout')?.addEventListener('click', async () => {
  await logout();
  emit('auth:logout');
  navigate('#/login');
});

// Hamburger toggle
document.getElementById('hamburger')?.addEventListener('click', () => {
  document.getElementById('sidebar')?.classList.toggle('open');
});

// Close sidebar on nav click (mobile)
document.querySelectorAll('.nav-link').forEach(link => {
  link.addEventListener('click', () => {
    document.getElementById('sidebar')?.classList.remove('open');
  });
});

// --- Badge updates ---
on('triage:changed', () => {
  // Will be re-fetched on next inbox visit
});

// Update user info when route changes
window.addEventListener('hashchange', updateUserInfo);

// --- Cross-tab sync ---
window.addEventListener('storage', (e) => {
  if (e.key === 'jwt_token' && !e.newValue) {
    // Token removed in another tab — redirect to login
    if (window.location.hash !== '#/login') {
      navigate('#/login');
    }
  } else if (e.key === 'jwt_token' && e.newValue) {
    // Token set in another tab — refresh user info
    updateUserInfo();
  }
});

// --- Stale session refresh + expiry check ---
let hiddenAt = 0;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    hiddenAt = Date.now();
  } else {
    if (hiddenAt && Date.now() - hiddenAt > 5 * 60 * 1000) {
      clearCache();
    }
    if (!isAuthenticated() && window.location.hash !== '#/login') {
      navigate('#/login');
    }
  }
});

// --- Init ---
initAmbient();
updateUserInfo();
startRouter();
