/**
 * Dashboard view — pipeline overview with metric cards and recent signals.
 */
import * as api from '../api.js';
import { emit } from '../state.js';
import { navigate } from '../router.js';

export async function mount(container) {
  const ac = new AbortController();
  const signal = ac.signal;

  container.innerHTML = '';

  // Header
  const header = document.createElement('div');
  header.className = 'view-header';
  const title = document.createElement('h2');
  title.className = 'view-title';
  title.textContent = 'Pipeline Dashboard';
  header.appendChild(title);
  container.appendChild(header);

  // Metric cards placeholder
  const metricGrid = document.createElement('div');
  metricGrid.className = 'metric-grid stagger-enter';
  container.appendChild(metricGrid);

  // Status breakdown placeholder
  const statusSection = document.createElement('div');
  statusSection.style.marginBottom = 'var(--space-6)';
  container.appendChild(statusSection);

  // Recent signals section
  const recentSection = document.createElement('div');
  recentSection.className = 'card';
  container.appendChild(recentSection);

  // Fetch data in parallel
  try {
    const [healthRes, entitiesRes, triageRes] = await Promise.all([
      api.get('/health', { signal }),
      api.get('/api/v1/companies/inbox?page=1&page_size=10', { signal }),
      api.get('/api/v1/triage?status=pending&limit=1', { signal }),
    ]);

    if (signal.aborted) return;

    // --- Metric cards ---
    const totalSignals = healthRes.ok ? (healthRes.data?.total_signals || 0) : '?';
    const dbStatus = healthRes.ok ? (healthRes.data?.status || 'unknown') : 'unknown';

    let totalCompanies = '?';
    let companies = [];
    if (entitiesRes.ok) {
      companies = entitiesRes.data?.items || entitiesRes.data || [];
      totalCompanies = entitiesRes.data?.total || companies.length || 0;
    }

    const pendingTriage = triageRes.ok
      ? (triageRes.data?.length > 0 || triageRes.meta?.has_more ? '1+' : '0')
      : '?';

    const metrics = [
      { label: 'Total Signals', value: totalSignals },
      { label: 'Companies', value: totalCompanies },
      { label: 'Pending Triage', value: pendingTriage },
      { label: 'System', value: dbStatus === 'healthy' ? 'Healthy' : dbStatus },
    ];

    metricGrid.innerHTML = '';
    metrics.forEach(m => {
      const card = document.createElement('div');
      card.className = 'metric-card';
      const label = document.createElement('div');
      label.className = 'metric-label';
      label.textContent = m.label;
      const value = document.createElement('div');
      value.className = 'metric-value';
      value.textContent = '0';
      card.appendChild(label);
      card.appendChild(value);
      metricGrid.appendChild(card);

      // Count-up animation
      animateCount(value, m.value);
    });

    // --- Status breakdown ---
    if (companies.length > 0) {
      const statusCounts = {};
      companies.forEach(c => {
        const s = c.status || 'inbox';
        statusCounts[s] = (statusCounts[s] || 0) + 1;
      });

      const statusMap = {
        inbox: { label: 'Source', color: 'var(--status-source)', bg: 'var(--status-source-bg)' },
        tracking: { label: 'Tracking', color: 'var(--status-tracking)', bg: 'var(--status-tracking-bg)' },
        pipeline_requested: { label: 'Diligence', color: 'var(--status-diligence)', bg: 'var(--status-diligence-bg)' },
        funded: { label: 'Funded', color: 'var(--status-funded)', bg: 'var(--status-funded-bg)' },
        passed: { label: 'Passed', color: 'var(--status-passed)', bg: 'var(--status-passed-bg)' },
      };

      const sTitle = document.createElement('h3');
      sTitle.style.cssText = 'font-size:var(--text-lg);margin-bottom:var(--space-3);';
      sTitle.textContent = 'Pipeline Status';
      statusSection.appendChild(sTitle);

      const grid = document.createElement('div');
      grid.className = 'status-grid stagger-enter';

      for (const [id, count] of Object.entries(statusCounts)) {
        const meta = statusMap[id] || { label: id, color: 'var(--color-text-muted)', bg: 'var(--color-surface)' };
        const card = document.createElement('div');
        card.className = 'status-card card-hoverable';
        card.style.background = meta.bg;
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => navigate(`#/companies?status=${id}`));

        const dot = document.createElement('div');
        dot.style.cssText = `width:12px;height:12px;border-radius:50%;background:${meta.color};flex-shrink:0;`;

        const info = document.createElement('div');
        const countEl = document.createElement('div');
        countEl.className = 'status-card-count';
        countEl.textContent = count;
        const labelEl = document.createElement('div');
        labelEl.className = 'status-card-label';
        labelEl.textContent = meta.label;
        info.appendChild(countEl);
        info.appendChild(labelEl);

        card.appendChild(dot);
        card.appendChild(info);
        grid.appendChild(card);
      }
      statusSection.appendChild(grid);
    }

    // --- Recent signals table ---
    recentSection.innerHTML = '';
    const recentHeader = document.createElement('div');
    recentHeader.className = 'card-header';
    const recentTitle = document.createElement('h3');
    recentTitle.className = 'card-title';
    recentTitle.textContent = 'Recent Companies';
    recentHeader.appendChild(recentTitle);
    recentSection.appendChild(recentHeader);

    if (companies.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.innerHTML = '<div class="empty-state-message">Run the pipeline to discover companies</div>';
      recentSection.appendChild(empty);
    } else {
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

      companies.slice(0, 10).forEach(c => {
        const tr = document.createElement('tr');
        tr.className = 'table-row clickable';
        tr.setAttribute('data-status', c.status || 'inbox');
        tr.addEventListener('click', () => navigate(`#/companies?highlight=${encodeURIComponent(c.canonical_key || '')}`));

        const tdName = document.createElement('td');
        tdName.textContent = c.company_name || c.name || c.canonical_key || 'Unknown';

        const tdStatus = document.createElement('td');
        const chip = createChip(c.status || 'inbox');
        tdStatus.appendChild(chip);

        const tdConf = document.createElement('td');
        tdConf.className = 'mono';
        tdConf.textContent = typeof c.confidence_score === 'number'
          ? c.confidence_score.toFixed(2) : '—';

        const tdSource = document.createElement('td');
        tdSource.style.color = 'var(--color-text-muted)';
        tdSource.textContent = c.source_api || c.signal_types?.[0] || '—';

        const tdDate = document.createElement('td');
        tdDate.style.cssText = 'color:var(--color-text-muted);font-size:var(--text-sm);';
        tdDate.textContent = formatDate(c.detected_at || c.created_at);

        tr.appendChild(tdName);
        tr.appendChild(tdStatus);
        tr.appendChild(tdConf);
        tr.appendChild(tdSource);
        tr.appendChild(tdDate);
        tbody.appendChild(tr);
      });

      table.appendChild(tbody);
      recentSection.appendChild(table);
    }

    emit('companies:loaded', companies);

  } catch (err) {
    if (err.name === 'AbortError') return;
    container.innerHTML = '';
    const errDiv = document.createElement('div');
    errDiv.className = 'error-state';
    const msg = document.createElement('p');
    msg.className = 'error-state-message';
    msg.textContent = 'Failed to load dashboard data.';
    const btn = document.createElement('button');
    btn.className = 'btn btn-ghost';
    btn.textContent = 'Retry';
    btn.onclick = () => mount(container);
    errDiv.appendChild(msg);
    errDiv.appendChild(btn);
    container.appendChild(errDiv);
  }

  return () => ac.abort();
}

function createChip(status) {
  const statusLabels = {
    inbox: 'Source',
    tracking: 'Tracking',
    pipeline_requested: 'Diligence',
    funded: 'Funded',
    passed: 'Passed',
  };
  const chip = document.createElement('span');
  chip.className = 'chip';
  chip.setAttribute('data-status', status);
  const dot = document.createElement('span');
  dot.className = 'chip-dot';
  chip.appendChild(dot);
  const label = document.createElement('span');
  label.textContent = statusLabels[status] || status;
  chip.appendChild(label);
  return chip;
}

function formatDate(dateStr) {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch { return '—'; }
}

function animateCount(el, target) {
  if (typeof target !== 'number') {
    el.textContent = target;
    return;
  }
  const duration = 800;
  const start = performance.now();
  function step(now) {
    const progress = Math.min((now - start) / duration, 1);
    // Ease out
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.round(eased * target);
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
