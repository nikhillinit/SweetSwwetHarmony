/**
 * Login view — centered glassmorphism card over ambient constellation.
 */
import { login, escapeHTML } from '../api.js';
import { navigate } from '../router.js';

export async function mount(target) {
  // Login renders outside the app layout, directly in body
  const existing = document.getElementById('login-view');
  if (existing) existing.remove();

  const view = document.createElement('div');
  view.id = 'login-view';
  view.className = 'login-container stagger-enter';
  view.innerHTML = `
    <div class="login-card">
      <h1 class="login-title">Discovery Engine</h1>
      <p class="login-subtitle">Press On Ventures deal sourcing</p>
      <div id="login-error" class="login-error" style="display:none;"></div>
      <form id="login-form">
        <div class="input-group" style="margin-bottom:var(--space-4);">
          <label class="input-label" for="login-email">Email</label>
          <input class="input" type="email" id="login-email" placeholder="you@example.com" autocomplete="email" required>
        </div>
        <div class="input-group" style="margin-bottom:var(--space-6);">
          <label class="input-label" for="login-password">Password</label>
          <input class="input" type="password" id="login-password" placeholder="Password" autocomplete="current-password" required>
        </div>
        <button type="submit" class="btn btn-primary" style="width:100%;" id="login-btn">Sign in</button>
      </form>
      <div class="login-hint">
        Dev: gp@example.com / password
      </div>
    </div>
  `;
  target.appendChild(view);

  const form = document.getElementById('login-form');
  const errorEl = document.getElementById('login-error');
  const btn = document.getElementById('login-btn');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;

    if (!email || !password) {
      errorEl.textContent = 'Please enter email and password.';
      errorEl.style.display = 'block';
      return;
    }

    btn.disabled = true;
    btn.textContent = 'Signing in...';
    errorEl.style.display = 'none';

    try {
      const result = await login(email, password);
      if (result.ok) {
        view.remove();
        navigate('#/');
      } else {
        errorEl.textContent = result.error?.message || 'Login failed.';
        errorEl.style.display = 'block';
      }
    } catch (err) {
      errorEl.textContent = 'Network error. Please try again.';
      errorEl.style.display = 'block';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Sign in';
    }
  });

  // Focus email input
  document.getElementById('login-email')?.focus();

  return () => {
    view.remove();
  };
}
