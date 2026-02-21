/**
 * Health view — system status with traffic light indicators.
 */
import * as api from '../api.js';

export async function mount(container) {
  const ac = new AbortController();
  const signal = ac.signal;

  container.innerHTML = '';

  // Header
  const header = document.createElement('div');
  header.className = 'view-header';
  const title = document.createElement('h2');
  title.className = 'view-title';
  title.textContent = 'System Health';
  header.appendChild(title);
  const refreshBtn = document.createElement('button');
  refreshBtn.className = 'btn btn-ghost btn-sm';
  refreshBtn.textContent = 'Refresh';
  refreshBtn.addEventListener('click', () => fetchHealth());
  header.appendChild(refreshBtn);
  container.appendChild(header);

  // Content area
  const content = document.createElement('div');
  container.appendChild(content);

  async function fetchHealth() {
    content.innerHTML = '<div class="skeleton skeleton-card" style="margin-bottom:16px;"></div><div class="skeleton skeleton-card"></div>';

    try {
      const [basicRes, detailedRes] = await Promise.all([
        api.get('/health', { signal }),
        api.get('/api/v1/health/detailed', { signal }).catch(() => ({ ok: false })),
      ]);

      if (signal.aborted) return;
      content.innerHTML = '';

      // Basic health card
      const basicCard = document.createElement('div');
      basicCard.className = 'card';
      basicCard.style.marginBottom = 'var(--space-4)';

      const basicHeader = document.createElement('div');
      basicHeader.className = 'card-header';
      const basicTitle = document.createElement('h3');
      basicTitle.className = 'card-title';
      basicTitle.textContent = 'Overview';
      basicHeader.appendChild(basicTitle);
      basicCard.appendChild(basicHeader);

      if (basicRes.ok) {
        const data = basicRes.data;
        const statusClass = data.status === 'healthy' ? 'healthy'
          : data.status === 'degraded' ? 'warning' : 'unhealthy';

        const indicator = document.createElement('div');
        indicator.className = 'health-indicator';
        indicator.style.marginBottom = 'var(--space-4)';

        const dot = document.createElement('div');
        dot.className = `health-dot ${statusClass}`;
        indicator.appendChild(dot);

        const label = document.createElement('span');
        label.style.cssText = 'font-size:var(--text-lg);font-weight:500;';
        label.textContent = data.status === 'healthy' ? 'All Systems Healthy'
          : data.status === 'degraded' ? 'Degraded Performance' : 'System Unhealthy';
        indicator.appendChild(label);
        basicCard.appendChild(indicator);

        // Metrics
        const metricsGrid = document.createElement('div');
        metricsGrid.className = 'metric-grid stagger-enter';

        const metrics = [
          { label: 'Database', value: data.database || '—' },
          { label: 'Total Signals', value: data.total_signals ?? '—' },
        ];

        if (data.total_companies != null) {
          metrics.push({ label: 'Companies', value: data.total_companies });
        }

        metrics.forEach(m => {
          const card = document.createElement('div');
          card.className = 'metric-card';
          const mLabel = document.createElement('div');
          mLabel.className = 'metric-label';
          mLabel.textContent = m.label;
          const mValue = document.createElement('div');
          mValue.className = 'metric-value';
          mValue.textContent = m.value;
          card.appendChild(mLabel);
          card.appendChild(mValue);
          metricsGrid.appendChild(card);
        });

        basicCard.appendChild(metricsGrid);
      } else {
        const err = document.createElement('div');
        err.className = 'health-indicator';
        const dot = document.createElement('div');
        dot.className = 'health-dot unhealthy';
        err.appendChild(dot);
        const label = document.createElement('span');
        label.textContent = 'Health check failed';
        label.style.color = 'var(--status-passed)';
        err.appendChild(label);
        basicCard.appendChild(err);
      }

      content.appendChild(basicCard);

      // Detailed health
      if (detailedRes.ok && detailedRes.data) {
        const detailed = detailedRes.data;

        // Components
        if (detailed.components && detailed.components.length > 0) {
          const compCard = document.createElement('div');
          compCard.className = 'card';
          compCard.style.marginBottom = 'var(--space-4)';

          const compHeader = document.createElement('div');
          compHeader.className = 'card-header';
          const compTitle = document.createElement('h3');
          compTitle.className = 'card-title';
          compTitle.textContent = 'Components';
          compHeader.appendChild(compTitle);
          compCard.appendChild(compHeader);

          const grid = document.createElement('div');
          grid.className = 'health-grid stagger-enter';

          detailed.components.forEach(comp => {
            const item = document.createElement('div');
            item.className = 'card';
            item.style.padding = 'var(--space-4)';

            const indicator = document.createElement('div');
            indicator.className = 'health-indicator';
            indicator.style.marginBottom = 'var(--space-2)';

            const dot = document.createElement('div');
            const status = comp.status || comp.health || 'unknown';
            dot.className = `health-dot ${status === 'healthy' || status === 'ok' ? 'healthy' : status === 'degraded' ? 'warning' : 'unhealthy'}`;
            indicator.appendChild(dot);

            const name = document.createElement('span');
            name.style.fontWeight = '500';
            name.textContent = comp.name || comp.component || 'Unknown';
            indicator.appendChild(name);
            item.appendChild(indicator);

            if (comp.message || comp.description) {
              const msg = document.createElement('div');
              msg.style.cssText = 'font-size:var(--text-sm);color:var(--color-text-muted);';
              msg.textContent = comp.message || comp.description;
              item.appendChild(msg);
            }

            grid.appendChild(item);
          });

          compCard.appendChild(grid);
          content.appendChild(compCard);
        }

        // Collectors
        if (detailed.collectors && detailed.collectors.length > 0) {
          const collCard = document.createElement('div');
          collCard.className = 'card';
          collCard.style.marginBottom = 'var(--space-4)';

          const collHeader = document.createElement('div');
          collHeader.className = 'card-header';
          const collTitle = document.createElement('h3');
          collTitle.className = 'card-title';
          collTitle.textContent = 'Collectors';
          collHeader.appendChild(collTitle);
          collCard.appendChild(collHeader);

          const table = document.createElement('table');
          table.className = 'table';
          table.innerHTML = '<thead><tr><th>Collector</th><th>Status</th><th>Last Run</th></tr></thead>';
          const tbody = document.createElement('tbody');

          detailed.collectors.forEach(coll => {
            const tr = document.createElement('tr');
            tr.className = 'table-row';

            const tdName = document.createElement('td');
            tdName.textContent = coll.name || coll.collector || '—';

            const tdStatus = document.createElement('td');
            const statusText = coll.status || coll.health || '—';
            tdStatus.style.color = statusText === 'ok' || statusText === 'healthy'
              ? 'var(--color-success)' : statusText === 'degraded'
              ? 'var(--color-warning)' : 'var(--color-text-muted)';
            tdStatus.textContent = statusText;

            const tdLast = document.createElement('td');
            tdLast.className = 'mono';
            tdLast.style.color = 'var(--color-text-muted)';
            tdLast.textContent = coll.last_run ? new Date(coll.last_run).toLocaleString() : '—';

            tr.appendChild(tdName);
            tr.appendChild(tdStatus);
            tr.appendChild(tdLast);
            tbody.appendChild(tr);
          });

          table.appendChild(tbody);
          collCard.appendChild(table);
          content.appendChild(collCard);
        }

        // Alerts
        if (detailed.alerts && detailed.alerts.length > 0) {
          const alertCard = document.createElement('div');
          alertCard.className = 'card';

          const alertHeader = document.createElement('div');
          alertHeader.className = 'card-header';
          const alertTitle = document.createElement('h3');
          alertTitle.className = 'card-title';
          alertTitle.textContent = 'Alerts';
          alertHeader.appendChild(alertTitle);
          alertCard.appendChild(alertHeader);

          detailed.alerts.forEach(alert => {
            const item = document.createElement('div');
            item.style.cssText = 'padding:var(--space-3);border-left:3px solid var(--color-warning);margin-bottom:var(--space-2);';
            const msg = document.createElement('div');
            msg.style.cssText = 'font-size:var(--text-sm);';
            msg.textContent = alert.message || alert.description || alert;
            item.appendChild(msg);
            if (alert.severity) {
              const sev = document.createElement('span');
              sev.style.cssText = 'font-size:var(--text-xs);color:var(--color-text-muted);';
              sev.textContent = alert.severity;
              item.appendChild(sev);
            }
            alertCard.appendChild(item);
          });

          content.appendChild(alertCard);
        }
      }

    } catch (err) {
      if (err.name === 'AbortError') return;
      content.innerHTML = '';
      const errDiv = document.createElement('div');
      errDiv.className = 'error-state';
      const msg = document.createElement('p');
      msg.className = 'error-state-message';
      msg.textContent = 'Failed to load health data.';
      const btn = document.createElement('button');
      btn.className = 'btn btn-ghost';
      btn.textContent = 'Retry';
      btn.addEventListener('click', () => fetchHealth());
      errDiv.appendChild(msg);
      errDiv.appendChild(btn);
      content.appendChild(errDiv);
    }
  }

  await fetchHealth();

  return () => ac.abort();
}
