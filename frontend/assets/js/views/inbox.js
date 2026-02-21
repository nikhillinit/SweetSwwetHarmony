/**
 * Inbox / Triage view — card list with approve/defer/reject actions.
 *
 * - Approve/defer: optimistic UI
 * - Reject: pessimistic with inline confirmation (rejected is TERMINAL)
 * - 409 VERSION_MISMATCH: re-fetch item, allow retry
 * - 409 INVALID_TRANSITION: remove item, toast
 * - Cursor dirty flag: mutation → discard cursor → full refresh
 * - Visibility change: hidden > 5 min → full refresh
 */
import * as api from '../api.js';
import { emit } from '../state.js';

const APPROVE_REASONS = ['Consumer CPG', 'Health tech', 'Travel & hospitality', 'Marketplace'];
const REJECT_REASONS = ['B2B/Enterprise', 'Dev tools', 'Crypto', 'Series B+'];

export async function mount(container) {
  const ac = new AbortController();
  const signal = ac.signal;
  let items = [];
  let cursor = null;
  let hasMore = false;
  let cursorDirty = false;
  let lastVisibleTime = Date.now();

  container.innerHTML = '';

  // Header
  const header = document.createElement('div');
  header.className = 'view-header';
  const title = document.createElement('h2');
  title.className = 'view-title';
  title.textContent = 'Triage Inbox';
  header.appendChild(title);
  const refreshBtn = document.createElement('button');
  refreshBtn.className = 'btn btn-ghost btn-sm';
  refreshBtn.textContent = 'Refresh';
  refreshBtn.addEventListener('click', () => fullRefresh());
  header.appendChild(refreshBtn);
  container.appendChild(header);

  // Item list
  const listEl = document.createElement('div');
  listEl.className = 'inbox-list';
  container.appendChild(listEl);

  // Load more
  const loadMoreBtn = document.createElement('button');
  loadMoreBtn.className = 'btn btn-ghost';
  loadMoreBtn.textContent = 'Load more';
  loadMoreBtn.style.display = 'none';
  loadMoreBtn.style.margin = 'var(--space-4) auto';
  loadMoreBtn.addEventListener('click', () => {
    if (cursorDirty) {
      fullRefresh();
    } else {
      fetchItems(false);
    }
  });
  container.appendChild(loadMoreBtn);

  // Visibility handler for stale sessions
  function onVisibilityChange() {
    if (document.hidden) {
      lastVisibleTime = Date.now();
    } else if (Date.now() - lastVisibleTime > 5 * 60 * 1000) {
      fullRefresh();
    }
  }
  document.addEventListener('visibilitychange', onVisibilityChange);

  async function fullRefresh() {
    cursor = null;
    cursorDirty = false;
    items = [];
    await fetchItems(true);
  }

  async function fetchItems(replace = true) {
    try {
      let url = '/api/v1/triage?status=pending&limit=20';
      if (!replace && cursor) url += `&cursor=${encodeURIComponent(cursor)}`;

      const res = await api.get(url, { signal, noCache: true });
      if (signal.aborted) return;

      if (!res.ok) {
        showError('Failed to load inbox items.');
        return;
      }

      const newItems = res.data || [];
      cursor = res.meta?.next_cursor || null;
      hasMore = res.meta?.has_more || false;

      if (replace) {
        items = newItems;
      } else {
        // Merge by id, avoiding duplicates
        const existing = new Set(items.map(i => i.id || i.review_id));
        for (const item of newItems) {
          if (!existing.has(item.id || item.review_id)) {
            items.push(item);
          }
        }
      }

      renderList();
    } catch (err) {
      if (err.name === 'AbortError') return;
      showError('Failed to load inbox items.');
    }
  }

  function renderList() {
    listEl.innerHTML = '';

    if (items.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.innerHTML = '<div class="empty-state-message">No items pending review</div><div class="empty-state-hint">You\'re all caught up.</div>';
      listEl.appendChild(empty);
      loadMoreBtn.style.display = 'none';
      updateBadge(0);
      return;
    }

    listEl.classList.add('stagger-enter');

    items.forEach(item => {
      const card = createCard(item);
      listEl.appendChild(card);
    });

    loadMoreBtn.style.display = hasMore ? 'block' : 'none';
    updateBadge(items.length);
  }

  function createCard(item) {
    const id = item.id || item.review_id;
    const card = document.createElement('div');
    card.className = 'inbox-card';
    card.setAttribute('data-review-id', id);
    card.setAttribute('data-updated-at', item.updated_at || '');
    card.setAttribute('tabindex', '0');

    // Header
    const cardHeader = document.createElement('div');
    cardHeader.className = 'inbox-card-header';
    const name = document.createElement('div');
    name.className = 'inbox-card-name';
    name.textContent = item.company_name || item.canonical_key || 'Unknown';
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.setAttribute('data-status', 'tracking');
    const chipDot = document.createElement('span');
    chipDot.className = 'chip-dot';
    chip.appendChild(chipDot);
    const chipLabel = document.createElement('span');
    chipLabel.textContent = 'Pending';
    chip.appendChild(chipLabel);
    cardHeader.appendChild(name);
    cardHeader.appendChild(chip);
    card.appendChild(cardHeader);

    // Meta
    const meta = document.createElement('div');
    meta.className = 'inbox-card-meta';
    const confSpan = document.createElement('span');
    confSpan.innerHTML = `Confidence: <span class="mono">${
      typeof item.confidence_score === 'number' ? item.confidence_score.toFixed(2) : '—'
    }</span>`;
    meta.appendChild(confSpan);
    if (item.source_api || item.canonical_key) {
      const srcSpan = document.createElement('span');
      srcSpan.textContent = item.source_api || item.canonical_key || '';
      srcSpan.style.color = 'var(--color-text-muted)';
      meta.appendChild(srcSpan);
    }
    card.appendChild(meta);

    // Rationale
    if (item.thesis_rationale || item.reason) {
      const rationale = document.createElement('div');
      rationale.style.cssText = 'font-size:var(--text-sm);color:var(--color-text-secondary);margin-bottom:var(--space-4);';
      rationale.textContent = item.thesis_rationale || item.reason || '';
      card.appendChild(rationale);
    }

    // Actions
    const actions = document.createElement('div');
    actions.className = 'inbox-actions';

    // Approve button
    const approveBtn = document.createElement('button');
    approveBtn.className = 'btn btn-primary btn-sm';
    approveBtn.textContent = 'Approve';
    approveBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      handleApprove(card, item, actions);
    });

    // Defer button
    const deferBtn = document.createElement('button');
    deferBtn.className = 'btn btn-muted btn-sm';
    deferBtn.textContent = 'Defer';
    deferBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      handleDefer(card, item);
    });

    // Reject button
    const rejectBtn = document.createElement('button');
    rejectBtn.className = 'btn btn-danger btn-sm';
    rejectBtn.textContent = 'Reject';
    rejectBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      showRejectConfirm(card, item, actions, rejectBtn);
    });

    actions.appendChild(approveBtn);
    actions.appendChild(deferBtn);
    actions.appendChild(rejectBtn);
    card.appendChild(actions);

    // Keyboard shortcuts
    card.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key >= '1' && e.key <= '4') {
        e.preventDefault();
        const reason = APPROVE_REASONS[parseInt(e.key) - 1];
        doApprove(card, item, reason);
      }
    });

    return card;
  }

  function handleApprove(card, item, actionsEl) {
    // Show quick-reason buttons
    actionsEl.innerHTML = '';
    APPROVE_REASONS.forEach((reason, i) => {
      const btn = document.createElement('button');
      btn.className = 'btn btn-primary btn-sm';
      btn.textContent = `[${i + 1}] ${reason}`;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        doApprove(card, item, reason);
      });
      actionsEl.appendChild(btn);
    });
    const customBtn = document.createElement('button');
    customBtn.className = 'btn btn-ghost btn-sm';
    customBtn.textContent = '[C] Custom';
    customBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const reason = prompt('Enter reason:');
      if (reason) doApprove(card, item, reason);
    });
    actionsEl.appendChild(customBtn);
  }

  async function doApprove(card, item, reason) {
    const id = item.id || item.review_id;
    // Optimistic: animate card out
    card.classList.add('removing');
    const removed = removeItem(id);

    try {
      const res = await api.post(`/api/v1/triage/${id}/approve`, {
        reason,
        updated_at: item.updated_at,
      });

      if (!res.ok) {
        // Restore card
        card.classList.remove('removing');
        restoreItem(removed, id);
        handle409(res, item);
        return;
      }

      cursorDirty = true;
      emit('triage:changed');
      api.showToast(`Approved: ${item.company_name || item.canonical_key || id}`, 'success');
    } catch (err) {
      card.classList.remove('removing');
      restoreItem(removed, id);
      api.showToast('Action failed. Please try again.', 'error');
    }
  }

  async function handleDefer(card, item) {
    const id = item.id || item.review_id;
    card.classList.add('removing');
    const removed = removeItem(id);

    try {
      const res = await api.post(`/api/v1/triage/${id}/defer`, {
        reason: 'deferred',
        updated_at: item.updated_at,
      });

      if (!res.ok) {
        card.classList.remove('removing');
        restoreItem(removed, id);
        handle409(res, item);
        return;
      }

      cursorDirty = true;
      emit('triage:changed');
      api.showToast(`Deferred: ${item.company_name || item.canonical_key || id}`, 'info');
    } catch (err) {
      card.classList.remove('removing');
      restoreItem(removed, id);
      api.showToast('Action failed. Please try again.', 'error');
    }
  }

  function showRejectConfirm(card, item, actionsEl, rejectBtn) {
    // Inline confirmation (pessimistic — reject is TERMINAL)
    const confirm = document.createElement('div');
    confirm.className = 'inline-confirm';
    const msg = document.createElement('span');
    msg.textContent = 'Are you sure?';
    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn btn-danger btn-sm';
    confirmBtn.textContent = 'Confirm';
    confirmBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      showRejectReasons(card, item, actionsEl);
    });
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-ghost btn-sm';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      confirm.remove();
      rejectBtn.style.display = '';
    });
    confirm.appendChild(msg);
    confirm.appendChild(confirmBtn);
    confirm.appendChild(cancelBtn);
    rejectBtn.style.display = 'none';
    actionsEl.appendChild(confirm);
  }

  function showRejectReasons(card, item, actionsEl) {
    actionsEl.innerHTML = '';
    REJECT_REASONS.forEach((reason, i) => {
      const btn = document.createElement('button');
      btn.className = 'btn btn-danger btn-sm';
      btn.textContent = `[${i + 1}] ${reason}`;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        doReject(card, item, reason);
      });
      actionsEl.appendChild(btn);
    });
    const customBtn = document.createElement('button');
    customBtn.className = 'btn btn-ghost btn-sm';
    customBtn.textContent = '[C] Custom';
    customBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const reason = prompt('Enter rejection reason:');
      if (reason) doReject(card, item, reason);
    });
    actionsEl.appendChild(customBtn);
  }

  async function doReject(card, item, reason) {
    const id = item.id || item.review_id;
    try {
      const res = await api.post(`/api/v1/triage/${id}/reject`, {
        reason,
        updated_at: item.updated_at,
      });

      if (!res.ok) {
        handle409(res, item);
        return;
      }

      card.classList.add('removing');
      setTimeout(() => {
        removeItem(id);
        renderList();
      }, 300);

      cursorDirty = true;
      emit('triage:changed');
      api.showToast(`Rejected: ${item.company_name || item.canonical_key || id}`, 'info');
    } catch (err) {
      api.showToast('Rejection failed. Please try again.', 'error');
    }
  }

  function handle409(res, item) {
    if (!res.error) return;

    if (res.error.code === 'INVALID_TRANSITION') {
      const id = item.id || item.review_id;
      removeItem(id);
      renderList();
      api.showToast(
        `${item.company_name || 'Item'} was already ${res.error.detail?.current_status || 'processed'}.`,
        'info'
      );
    } else if (res.error.code === 'VERSION_MISMATCH') {
      api.showToast('Item was modified. Refreshing...', 'info');
      fullRefresh();
    } else {
      api.showToast(res.error.message || 'Action failed.', 'error');
    }
  }

  function removeItem(id) {
    const idx = items.findIndex(i => (i.id || i.review_id) === id);
    if (idx !== -1) {
      const [removed] = items.splice(idx, 1);
      return { item: removed, index: idx };
    }
    return null;
  }

  function restoreItem(removed, id) {
    if (removed) {
      items.splice(removed.index, 0, removed.item);
      renderList();
    }
  }

  function updateBadge(count) {
    const badge = document.getElementById('inbox-badge');
    if (badge) {
      if (count > 0) {
        badge.textContent = count;
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }
    }
  }

  function showError(msg) {
    listEl.innerHTML = '';
    const errDiv = document.createElement('div');
    errDiv.className = 'error-state';
    const message = document.createElement('p');
    message.className = 'error-state-message';
    message.textContent = msg;
    const btn = document.createElement('button');
    btn.className = 'btn btn-ghost';
    btn.textContent = 'Retry';
    btn.addEventListener('click', () => fullRefresh());
    errDiv.appendChild(message);
    errDiv.appendChild(btn);
    listEl.appendChild(errDiv);
    loadMoreBtn.style.display = 'none';
  }

  await fetchItems(true);

  return () => {
    ac.abort();
    document.removeEventListener('visibilitychange', onVisibilityChange);
  };
}
