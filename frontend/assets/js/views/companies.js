/**
 * Companies view — searchable, filterable company list with detail expansion.
 */
import * as api from '../api.js';
import { getHashParams, navigate } from '../router.js';

const STATUS_LABELS = {
  inbox: 'Source',
  tracking: 'Tracking',
  pipeline_requested: 'Diligence',
  funded: 'Funded',
  passed: 'Passed',
};

const VALID_STATUSES = ['inbox', 'tracking', 'passed', 'pipeline_requested', 'funded'];

export async function mount(container, params) {
  const ac = new AbortController();
  const signal = ac.signal;
  let currentPage = 1;
  const pageSize = 25;
  let allCompanies = [];
  let searchTerm = '';
  let activeFilters = new Set();

  // Pre-filter from URL
  const urlStatus = params?.status;
  if (urlStatus && VALID_STATUSES.includes(urlStatus)) {
    activeFilters.add(urlStatus);
  }

  container.innerHTML = '';

  // Header
  const header = document.createElement('div');
  header.className = 'view-header';
  const title = document.createElement('h2');
  title.className = 'view-title';
  title.textContent = 'Companies';
  header.appendChild(title);
  container.appendChild(header);

  // Search bar
  const searchBar = document.createElement('div');
  searchBar.className = 'search-bar';
  const searchIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  searchIcon.setAttribute('class', 'search-bar-icon');
  searchIcon.setAttribute('viewBox', '0 0 20 20');
  searchIcon.setAttribute('fill', 'currentColor');
  searchIcon.innerHTML = '<path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd"/>';
  const searchInput = document.createElement('input');
  searchInput.className = 'input';
  searchInput.type = 'text';
  searchInput.placeholder = 'Search companies...';
  searchInput.setAttribute('aria-label', 'Search companies');
  searchBar.appendChild(searchIcon);
  searchBar.appendChild(searchInput);
  container.appendChild(searchBar);

  // Filters
  const filterRow = document.createElement('div');
  filterRow.className = 'filter-row';
  VALID_STATUSES.forEach(s => {
    const chip = document.createElement('button');
    chip.className = 'filter-chip' + (activeFilters.has(s) ? ' active' : '');
    chip.textContent = STATUS_LABELS[s] || s;
    chip.setAttribute('data-status', s);
    chip.addEventListener('click', () => {
      if (activeFilters.has(s)) {
        activeFilters.delete(s);
        chip.classList.remove('active');
      } else {
        activeFilters.add(s);
        chip.classList.add('active');
      }
      renderTable();
    });
    filterRow.appendChild(chip);
  });
  container.appendChild(filterRow);

  // Table container
  const tableCard = document.createElement('div');
  tableCard.className = 'card';
  container.appendChild(tableCard);

  // Pagination
  const pagination = document.createElement('div');
  pagination.className = 'pagination';
  container.appendChild(pagination);

  // Detail panel
  let expandedKey = null;

  // Search handler
  searchInput.addEventListener('input', () => {
    searchTerm = searchInput.value.trim().toLowerCase();
    renderTable();
  });

  async function fetchCompanies() {
    try {
      const res = await api.get(
        `/api/v1/companies/inbox?page=${currentPage}&page_size=${pageSize}`,
        { signal }
      );
      if (signal.aborted) return;

      if (!res.ok) {
        showError(tableCard, res.error?.message || 'Failed to load companies.');
        return;
      }

      allCompanies = res.data?.items || res.data || [];
      renderTable();
    } catch (err) {
      if (err.name === 'AbortError') return;
      showError(tableCard, 'Failed to load companies.');
    }
  }

  function getFiltered() {
    let filtered = allCompanies;
    if (activeFilters.size > 0) {
      filtered = filtered.filter(c => activeFilters.has(c.status || 'inbox'));
    }
    if (searchTerm) {
      filtered = filtered.filter(c => {
        const name = (c.company_name || c.name || '').toLowerCase();
        const key = (c.canonical_key || '').toLowerCase();
        return name.includes(searchTerm) || key.includes(searchTerm);
      });
    }
    return filtered;
  }

  function renderTable() {
    const filtered = getFiltered();
    tableCard.innerHTML = '';

    if (filtered.length === 0 && allCompanies.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.innerHTML = '<div class="empty-state-message">No companies yet</div><div class="empty-state-hint">Run the pipeline to discover companies.</div>';
      tableCard.appendChild(empty);
      return;
    }

    if (filtered.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.innerHTML = '<div class="empty-state-message">No companies match your filters</div>';
      tableCard.appendChild(empty);
      return;
    }

    const table = document.createElement('table');
    table.className = 'table';
    table.innerHTML = `
      <thead>
        <tr>
          <th>Company</th>
          <th>Status</th>
          <th>Confidence</th>
          <th>Source</th>
          <th>Detected</th>
        </tr>
      </thead>
    `;

    const tbody = document.createElement('tbody');
    tbody.className = 'stagger-enter';

    filtered.forEach(c => {
      const key = c.canonical_key || c.company_name || '';
      const tr = document.createElement('tr');
      tr.className = 'table-row clickable';
      tr.setAttribute('data-status', c.status || 'inbox');

      const tdName = document.createElement('td');
      tdName.style.fontWeight = '500';
      tdName.textContent = c.company_name || c.name || c.canonical_key || 'Unknown';

      const tdStatus = document.createElement('td');
      tdStatus.appendChild(createChip(c.status || 'inbox'));

      const tdConf = document.createElement('td');
      tdConf.className = 'mono';
      tdConf.textContent = typeof c.confidence_score === 'number'
        ? c.confidence_score.toFixed(2) : '—';

      const tdSource = document.createElement('td');
      tdSource.style.color = 'var(--color-text-muted)';
      tdSource.style.fontSize = 'var(--text-sm)';
      tdSource.textContent = c.source_api || c.signal_types?.[0] || '—';

      const tdDate = document.createElement('td');
      tdDate.style.color = 'var(--color-text-muted)';
      tdDate.style.fontSize = 'var(--text-sm)';
      tdDate.textContent = formatDate(c.detected_at || c.created_at);

      tr.appendChild(tdName);
      tr.appendChild(tdStatus);
      tr.appendChild(tdConf);
      tr.appendChild(tdSource);
      tr.appendChild(tdDate);

      tr.addEventListener('click', () => {
        if (expandedKey === key) {
          expandedKey = null;
          renderTable();
        } else {
          expandedKey = key;
          renderTable();
        }
      });

      tbody.appendChild(tr);

      // Detail panel if expanded
      if (expandedKey === key) {
        const detailRow = document.createElement('tr');
        const detailCell = document.createElement('td');
        detailCell.colSpan = 5;
        detailCell.style.padding = '0';

        const panel = document.createElement('div');
        panel.className = 'detail-panel';

        const grid = document.createElement('div');
        grid.className = 'detail-grid';

        const fields = [
          ['Canonical Key', c.canonical_key || '—'],
          ['Status', STATUS_LABELS[c.status] || c.status || '—'],
          ['Confidence', typeof c.confidence_score === 'number' ? c.confidence_score.toFixed(3) : '—'],
          ['Sources', (c.signal_types || []).join(', ') || c.source_api || '—'],
          ['Detected', formatDate(c.detected_at || c.created_at)],
          ['Sector', c.sector || '—'],
        ];

        fields.forEach(([label, value]) => {
          const labelEl = document.createElement('div');
          labelEl.className = 'detail-label';
          labelEl.textContent = label;
          const valueEl = document.createElement('div');
          valueEl.className = 'detail-value';
          if (label === 'Confidence') valueEl.classList.add('mono');
          valueEl.textContent = value;
          grid.appendChild(labelEl);
          grid.appendChild(valueEl);
        });

        panel.appendChild(grid);

        if (c.why_now) {
          const whyLabel = document.createElement('div');
          whyLabel.className = 'detail-label';
          whyLabel.style.marginTop = 'var(--space-4)';
          whyLabel.textContent = 'Why Now';
          const whyValue = document.createElement('div');
          whyValue.className = 'detail-value';
          whyValue.textContent = c.why_now;
          panel.appendChild(whyLabel);
          panel.appendChild(whyValue);
        }

        // Signals list (G1)
        const signalTypes = c.signal_types || [];
        const signalLabel = document.createElement('div');
        signalLabel.className = 'detail-label';
        signalLabel.style.marginTop = 'var(--space-4)';
        signalLabel.textContent = 'Signals';
        panel.appendChild(signalLabel);

        if (signalTypes.length > 0) {
          const signalList = document.createElement('ul');
          signalList.style.cssText = 'list-style:none;padding:0;margin:var(--space-2) 0 0 0;display:flex;flex-direction:column;gap:var(--space-1);';
          signalTypes.forEach(st => {
            const li = document.createElement('li');
            li.style.cssText = 'font-size:var(--text-sm);color:var(--color-text-secondary);display:flex;align-items:center;gap:var(--space-2);';
            const dot = document.createElement('span');
            dot.style.cssText = 'width:4px;height:4px;border-radius:50%;background:var(--color-primary);flex-shrink:0;';
            li.appendChild(dot);
            const text = document.createElement('span');
            text.textContent = st;
            li.appendChild(text);
            signalList.appendChild(li);
          });
          panel.appendChild(signalList);
        } else {
          const noSignals = document.createElement('div');
          noSignals.className = 'detail-value';
          noSignals.style.color = 'var(--color-text-muted)';
          noSignals.textContent = c.source_api || 'No signal data';
          panel.appendChild(noSignals);
        }

        // Notion link (G2)
        const notionSection = document.createElement('div');
        notionSection.style.marginTop = 'var(--space-4)';
        const notionLabel = document.createElement('div');
        notionLabel.className = 'detail-label';
        notionLabel.textContent = 'Notion';
        notionSection.appendChild(notionLabel);

        if (c.notion_page_id || c.notion_url) {
          const notionLink = document.createElement('a');
          const url = c.notion_url || ('https://notion.so/' + c.notion_page_id);
          notionLink.href = url;
          notionLink.target = '_blank';
          notionLink.rel = 'noopener';
          notionLink.textContent = 'View in Notion';
          notionLink.style.cssText = 'font-size:var(--text-sm);';
          notionSection.appendChild(notionLink);
        } else {
          const noNotion = document.createElement('div');
          noNotion.className = 'detail-value';
          noNotion.style.color = 'var(--color-text-muted)';
          noNotion.textContent = 'Not yet pushed to Notion';
          notionSection.appendChild(noNotion);
        }
        panel.appendChild(notionSection);

        // Action buttons (G3)
        const actionsDiv = document.createElement('div');
        actionsDiv.style.cssText = 'margin-top:var(--space-4);display:flex;gap:var(--space-2);';

        const status = c.status || 'inbox';
        if (status === 'inbox' || status === 'tracking') {
          const inboxBtn = document.createElement('button');
          inboxBtn.className = 'btn btn-ghost btn-sm';
          inboxBtn.textContent = 'View in Inbox';
          inboxBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            navigate('#/inbox');
          });
          actionsDiv.appendChild(inboxBtn);
        }
        if (status === 'inbox') {
          const pushBtn = document.createElement('button');
          pushBtn.className = 'btn btn-primary btn-sm';
          pushBtn.textContent = 'Push to Notion';
          pushBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const res = await api.post(`/api/v1/companies/${encodeURIComponent(c.canonical_key || '')}/push`);
            if (res.ok) {
              api.showToast(`Pushed ${c.company_name || c.canonical_key} to Notion`, 'success');
              fetchCompanies();
            } else {
              api.showToast(res.error?.message || 'Push failed', 'error');
            }
          });
          actionsDiv.appendChild(pushBtn);
        }
        panel.appendChild(actionsDiv);

        detailCell.appendChild(panel);
        detailRow.appendChild(detailCell);
        tbody.appendChild(detailRow);
      }
    });

    table.appendChild(tbody);
    tableCard.appendChild(table);

    // Pagination controls
    renderPagination(filtered.length);
  }

  function renderPagination(total) {
    pagination.innerHTML = '';
    if (total <= pageSize && currentPage === 1) return;

    const prevBtn = document.createElement('button');
    prevBtn.className = 'btn btn-ghost btn-sm';
    prevBtn.textContent = 'Previous';
    prevBtn.disabled = currentPage <= 1;
    prevBtn.addEventListener('click', () => {
      currentPage--;
      fetchCompanies();
    });

    const pageInfo = document.createElement('span');
    pageInfo.style.cssText = 'font-size:var(--text-sm);color:var(--color-text-muted);';
    pageInfo.textContent = `Page ${currentPage}`;

    const nextBtn = document.createElement('button');
    nextBtn.className = 'btn btn-ghost btn-sm';
    nextBtn.textContent = 'Next';
    nextBtn.disabled = allCompanies.length < pageSize;
    nextBtn.addEventListener('click', () => {
      currentPage++;
      fetchCompanies();
    });

    pagination.appendChild(prevBtn);
    pagination.appendChild(pageInfo);
    pagination.appendChild(nextBtn);
  }

  await fetchCompanies();

  // Handle ?highlight= param (G4)
  const highlightKey = params?.highlight;
  if (highlightKey) {
    const rows = tableCard.querySelectorAll('.table-row');
    for (const row of rows) {
      // Find the matching company by checking the row's text or data
      const company = allCompanies.find(c =>
        (c.canonical_key || c.company_name || '') === highlightKey
      );
      if (company) {
        const key = company.canonical_key || company.company_name || '';
        // Find the row that matches
        const matchIdx = getFiltered().findIndex(c =>
          (c.canonical_key || c.company_name || '') === highlightKey
        );
        if (matchIdx >= 0) {
          const targetRow = rows[matchIdx];
          if (targetRow) {
            targetRow.classList.add('highlighted');
            targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
            // Auto-expand
            expandedKey = key;
            renderTable();
            // Re-highlight after render
            requestAnimationFrame(() => {
              const newRows = tableCard.querySelectorAll('.table-row');
              const newFiltered = getFiltered();
              const newIdx = newFiltered.findIndex(c =>
                (c.canonical_key || c.company_name || '') === highlightKey
              );
              if (newIdx >= 0 && newRows[newIdx]) {
                newRows[newIdx].classList.add('highlighted');
                newRows[newIdx].scrollIntoView({ behavior: 'smooth', block: 'center' });
              }
            });
          }
        }
        break;
      }
    }
  }

  return () => ac.abort();
}

function createChip(status) {
  const chip = document.createElement('span');
  chip.className = 'chip';
  chip.setAttribute('data-status', status);
  const dot = document.createElement('span');
  dot.className = 'chip-dot';
  chip.appendChild(dot);
  const label = document.createElement('span');
  label.textContent = STATUS_LABELS[status] || status;
  chip.appendChild(label);
  return chip;
}

function formatDate(dateStr) {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return '—';
  }
}

function showError(container, msg) {
  container.innerHTML = '';
  const errDiv = document.createElement('div');
  errDiv.className = 'error-state';
  const message = document.createElement('p');
  message.className = 'error-state-message';
  message.textContent = msg;
  errDiv.appendChild(message);
  container.appendChild(errDiv);
}
