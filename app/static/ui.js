// Minimal UI helpers for TG TXT Downloader
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const on = (el, ev, fn) => el && el.addEventListener(ev, fn);

  const api = async (url, opts = {}) => {
    const res = await fetch(url, opts);
    const ct = res.headers.get('content-type') || '';
    if (!res.ok) {
      let detail = await res.text().catch(() => '');
      try { const j = JSON.parse(detail); detail = j.detail || detail; } catch {}
      throw new Error(detail || (res.status + ' ' + res.statusText));
    }
    if (ct.includes('application/json')) return res.json();
    return res.text();
  };

  const formatBytes = (bytes) => {
    if (bytes === 0) return '0 B';
    const k = 1024, sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  // Helper to colorize Bootstrap badges by status
  const setBadge = (el, state) => {
    if (!el) return;
    el.classList.remove('text-bg-success', 'text-bg-danger', 'text-bg-secondary');
    switch (state) {
      case 'running':
        el.classList.add('text-bg-success');
        break;
      case 'stopped':
      case 'idle':
        el.classList.add('text-bg-danger');
        break;
      default:
        el.classList.add('text-bg-secondary');
    }
  };

  const UI = {
    initStats() {
      const dailyEl = $('#dailyChart');
      const originEl = $('#originChart');
      if (!dailyEl && !originEl) return; // Only on dashboard

      let dailyChart = null;
      let originChart = null;

      const palette = {
        line: 'rgba(13,110,253,0.7)',
        lineBorder: 'rgba(13,110,253,1)',
        area: 'rgba(13,110,253,0.15)',
        green: '#20c997',
        blue: '#0d6efd',
        orange: '#fd7e14',
        gray: '#6c757d',
      };

      const render = (data) => {
        const days = (data.daily || []).map(d => d.date);
        const counts = (data.daily || []).map(d => d.count || 0);
        const mbs = (data.daily || []).map(d => Number(d.mb || 0).toFixed(2));

        if (dailyEl) {
          const ctx = dailyEl.getContext('2d');
          if (dailyChart) dailyChart.destroy();
          dailyChart = new Chart(ctx, {
            type: 'line',
            data: {
              labels: days,
              datasets: [
                {
                  label: 'Files',
                  data: counts,
                  borderColor: palette.lineBorder,
                  backgroundColor: palette.area,
                  fill: true,
                  tension: 0.25,
                  yAxisID: 'y',
                },
                {
                  label: 'MB',
                  data: mbs,
                  borderColor: palette.green,
                  backgroundColor: 'rgba(32,201,151,0.15)',
                  fill: true,
                  tension: 0.25,
                  yAxisID: 'y1',
                }
              ]
            },
            options: {
              responsive: true,
              interaction: { mode: 'index', intersect: false },
              scales: {
                y: { beginAtZero: true, title: { text: 'Files', display: true } },
                y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { text: 'MB', display: true } },
              },
              plugins: { legend: { display: true } }
            }
          });
        }

        if (originEl) {
          const ctx2 = originEl.getContext('2d');
          if (originChart) originChart.destroy();
          const labels = (data.origin || []).map(o => (o.origin || 'unknown'));
          const values = (data.origin || []).map(o => Number(o.count || 0));
          const colors = [palette.blue, palette.green, palette.orange, palette.gray];
          originChart = new Chart(ctx2, {
            type: 'doughnut',
            data: {
              labels,
              datasets: [{ data: values, backgroundColor: colors.slice(0, Math.max(1, labels.length)) }]
            },
            options: { plugins: { legend: { position: 'bottom' } } }
          });
        }
      };

      const refresh = async () => {
        try {
          const stats = await api('/api/stats?days=30');
          render(stats);
        } catch (e) {
          // no-op; charts will remain empty
        }
      };

      refresh();
      // Update every 30 seconds
      setInterval(refresh, 30000);
    },

    initDashboard() {
      // Show quick status and counts
      const statusEl = $('#dl-status');
      const searchStatusEl = $('#search-status');
      const filesEl = $('#files-count');
      const resultsEl = $('#results-count');
      const refresh = async () => {
        try {
          const st = await api('/api/downloader/status');
          if (statusEl) {
            statusEl.textContent = st.running ? 'Running' : 'Stopped';
            setBadge(statusEl, st.running ? 'running' : 'stopped');
          }
        } catch (e) {
          if (statusEl) {
            statusEl.textContent = 'Unknown';
            setBadge(statusEl, 'unknown');
          }
        }
        try {
          const f = await api('/api/files');
          if (filesEl) filesEl.textContent = f.count;
        } catch (e) { if (filesEl) filesEl.textContent = '-'; }
        try {
          const rs = await api('/api/results/files');
          if (resultsEl) resultsEl.textContent = rs.count;
        } catch (e) { if (resultsEl) resultsEl.textContent = '-'; }
        try {
          const ss = await api('/api/search/status');
          if (searchStatusEl) {
            searchStatusEl.textContent = ss.running ? 'Running' : 'Idle';
            setBadge(searchStatusEl, ss.running ? 'running' : 'idle');
          }
        } catch (e) {
          if (searchStatusEl) {
            searchStatusEl.textContent = 'Unknown';
            setBadge(searchStatusEl, 'unknown');
          }
        }
      };
      refresh();
      // Keep dashboard live instead of a one-off snapshot
      setInterval(refresh, 1000);
    },

    initDownloader() {
      const startBtn = $('#btn-start');
      const stopBtn = $('#btn-stop');
      const forceStopBtn = $('#btn-stop-force');
      const statusEl = $('#status');
      const bar = $('#progress-bar');
      const label = $('#progress-label');
      const currentFileLabel = $('#current-file-label');

      const clamp = (n, a, b) => Math.max(a, Math.min(b, n));

      const refresh = async () => {
        try {
          const st = await api('/api/downloader/status');
          const running = !!st.running;
          if (statusEl) {
            statusEl.textContent = running ? 'Running' : 'Stopped';
            setBadge(statusEl, running ? 'running' : 'stopped');
          }

          // Prefer overall counters so UI shows true cumulative progress (e.g., 20/5082)
          const total = Number((st.overall_total != null ? st.overall_total : st.total_to_download) || 0);
          const downloaded = Number((st.overall_downloaded != null ? st.overall_downloaded : st.downloaded) || 0);
          const processed = Number(st.processed || (st.downloaded || 0) + (st.failed || 0) + (st.skipped || 0));
          const rawPercent = (st.overall_percent != null) ? st.overall_percent : (total ? Math.floor(100 * downloaded / total) : (downloaded > 0 ? 100 : 0));
          const percent = clamp(Number(rawPercent), 0, 100);

          if (bar) bar.style.width = percent + '%';
          if (label) label.textContent = `${downloaded}/${total} (${percent}%)`;
          if (currentFileLabel) currentFileLabel.textContent = st.current_file ? `Current: ${st.current_file}` : '';

          if (startBtn) startBtn.disabled = running;
        } catch (e) {
          if (statusEl) statusEl.textContent = 'Unknown';
          if (bar) bar.style.width = '0%';
          if (label) label.textContent = '0/0 (0%)';
          if (currentFileLabel) currentFileLabel.textContent = '';
        }
      };

      on(startBtn, 'click', async () => {
        if (startBtn) startBtn.disabled = true;
        try { await api('/api/downloader/start', { method: 'POST' }); }
        finally { if (startBtn) startBtn.disabled = false; refresh(); }
      });
      on(stopBtn, 'click', async () => {
        if (stopBtn) stopBtn.disabled = true;
        try { await api('/api/downloader/stop', { method: 'POST' }); }
        finally { if (stopBtn) stopBtn.disabled = false; refresh(); }
      });
      on(forceStopBtn, 'click', async () => {
        if (forceStopBtn) forceStopBtn.disabled = true;
        try { await api('/api/downloader/stop?force=true', { method: 'POST' }); }
        finally { if (forceStopBtn) forceStopBtn.disabled = false; refresh(); }
      });

      refresh();
      setInterval(refresh, 1000);
    },

    initFiles() {
      const tbody = $('#files-body');
      const countEl = $('#files-count');
      const refreshBtn = $('#files-refresh');
      const prevBtn = $('#files-prev');
      const nextBtn = $('#files-next');
      const pageLabel = $('#files-page-label');
      if (!tbody) return; // only run on Files page

      let page = 1;
      const perPage = 10;
      let totalPages = 1;

      const render = (files) => {
        if (!tbody) return;
        if (!files || files.length === 0) {
          tbody.innerHTML = `<tr><td colspan="4" class="text-body-secondary">No files</td></tr>`;
          return;
        }
        tbody.innerHTML = files.map(f => `
          <tr>
            <td class="text-truncate" title="${f.name}">${f.name}</td>
            <td class="text-end">${formatBytes(f.size)}</td>
            <td class="text-end">${new Date(f.modified * 1000).toLocaleString()}</td>
            <td class="text-end">
              <a class="btn btn-sm btn-outline-secondary" href="${f.path}" target="_blank"><i class="bi bi-eye"></i></a>
              <a class="btn btn-sm btn-primary" href="${f.download_url}"><i class="bi bi-download"></i></a>
            </td>
          </tr>`).join('');
      };

      const setPager = (p, tp) => {
        totalPages = Math.max(1, tp || 1);
        page = Math.min(Math.max(1, p || 1), totalPages);
        if (pageLabel) pageLabel.textContent = `Page ${page} of ${totalPages}`;
        if (prevBtn) prevBtn.disabled = page <= 1;
        if (nextBtn) nextBtn.disabled = page >= totalPages;
      };

      const refresh = async () => {
        try {
          const data = await api(`/api/files?page=${page}&per_page=${perPage}`);
          if (countEl) countEl.textContent = data.count;
          setPager(data.page, data.total_pages);
          render(data.files || []);
        } catch (e) {
          if (tbody) tbody.innerHTML = `<tr><td colspan="4" class="text-danger">${e.message}</td></tr>`;
        }
      };

      on(refreshBtn, 'click', refresh);
      on(prevBtn, 'click', () => { if (page > 1) { page -= 1; refresh(); } });
      on(nextBtn, 'click', () => { if (page < totalPages) { page += 1; refresh(); } });

      refresh();
    },

    initSearch() {
      const form = $('#search-form');
      const kw = $('#kw');
      const workers = $('#workers');
      const res = $('#search-result');
      const go = $('#go');
      const run = async () => {
        if (!kw || !go || !res) return; // not on this page
        if (!kw.value.trim()) return;
        res.innerHTML = '<div class="text-body-secondary">Searching...</div>';
        go.disabled = true;
        try {
          const q = new URLSearchParams({ keyword: kw.value.trim(), ...(workers && workers.value ? { max_workers: workers.value } : {}) });
          const data = await api('/api/search?' + q.toString(), { method: 'POST' });
          const link = data.output_url ? `<a href="${data.output_url}" target="_blank">Open results</a>` : `<code>${data.output_path}</code>`;
          res.innerHTML = `
            <div class="alert alert-success">
              Scanned files: <strong>${data.scanned_files}</strong> · Matched lines: <strong>${data.lines_found}</strong><br/>
              ${link}
            </div>`;
        } catch (e) {
          res.innerHTML = `<div class="alert alert-danger">${e.message}</div>`;
        } finally { go.disabled = false; }
      };
      on(go, 'click', run);
      on(form, 'submit', e => { e.preventDefault(); run(); });

      // Files list within Search tab
      const tbody = $('#search-files-body');
      const countEl = $('#search-files-count');
      const refreshBtn = $('#search-files-refresh');
      const filterInput = $('#search-files-filter');
      const prevBtn = $('#search-files-prev');
      const nextBtn = $('#search-files-next');
      const pageLabel = $('#search-files-page-label');
      if (tbody) {
        let page = 1;
        const perPage = 10;
        let totalPages = 1;
        let currentPageData = [];

        const render = (files) => {
          if (!files || files.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" class="text-body-secondary">No files</td></tr>`;
            return;
          }
          let rows = files;
          const q = (filterInput && filterInput.value || '').toLowerCase();
          if (q) rows = rows.filter(f => f.name.toLowerCase().includes(q));
          tbody.innerHTML = rows.map(f => `
            <tr>
              <td class="text-truncate" title="${f.name}">${f.name}</td>
              <td class="text-end">${formatBytes(f.size)}</td>
              <td class="text-end">${new Date(f.modified * 1000).toLocaleString()}</td>
              <td class="text-end">
                <a class="btn btn-sm btn-outline-secondary" href="${f.path}" target="_blank" title="Open"><i class="bi bi-eye"></i></a>
                <a class="btn btn-sm btn-primary" href="${f.download_url}" title="Download"><i class="bi bi-download"></i></a>
                <button class="btn btn-sm btn-outline-danger" data-action="delete" data-name="${f.name}" title="Delete"><i class="bi bi-trash"></i></button>
              </td>
            </tr>`).join('');
        };

        const setPager = (p, tp) => {
          totalPages = Math.max(1, tp || 1);
          page = Math.min(Math.max(1, p || 1), totalPages);
          if (pageLabel) pageLabel.textContent = `Page ${page} of ${totalPages}`;
          if (prevBtn) prevBtn.disabled = page <= 1;
          if (nextBtn) nextBtn.disabled = page >= totalPages;
        };

        const refresh = async () => {
          try {
            const data = await api(`/api/results/files?page=${page}&per_page=${perPage}`);
            if (countEl) countEl.textContent = data.count;
            setPager(data.page, data.total_pages);
            currentPageData = data.files || [];
            render(currentPageData);
          } catch (e) {
            tbody.innerHTML = `<tr><td colspan="4" class="text-danger">${e.message}</td></tr>`;
          }
        };

        on(refreshBtn, 'click', (e) => { e.preventDefault(); refresh(); });
        on(prevBtn, 'click', (e) => { e.preventDefault(); if (page > 1) { page -= 1; refresh(); } });
        on(nextBtn, 'click', (e) => { e.preventDefault(); if (page < totalPages) { page += 1; refresh(); } });
        on(filterInput, 'input', () => render(currentPageData));

        // Delete handler (delegated)
        on(tbody, 'click', async (ev) => {
          const btn = ev.target.closest('button[data-action="delete"]');
          if (!btn) return;
          ev.preventDefault();
          const name = btn.getAttribute('data-name');
          if (!name) return;
          const ok = confirm(`Delete \"${name}\"?`);
          if (!ok) return;
          try {
            await api(`/api/results/${encodeURIComponent(name)}`, { method: 'DELETE' });
            // If this was the last item on the current page and not the first page, step back
            if (currentPageData.length <= 1 && page > 1) page -= 1;
            await refresh();
          } catch (e) {
            alert('Delete failed: ' + e.message);
          }
        });

        refresh();
      }
    },

    initLogs() {
      const pre = $('#logs');
      const btn = $('#logs-refresh');
      const tailInput = $('#logs-tail');
      const refresh = async () => {
        const tail = parseInt(tailInput.value || '2000', 10);
        const text = await api('/api/logs?tail=' + (isFinite(tail) ? tail : 2000));
        pre.textContent = text;
        pre.scrollTop = pre.scrollHeight;
      };
      on(btn, 'click', refresh);
      refresh();
    }
  };

  // Theme toggle
  UI.initThemeToggle = function() {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    const icon = btn.querySelector('i');
    const label = btn.querySelector('span');
    const metaTheme = document.querySelector('meta[name="theme-color"]');

    const apply = (t) => {
      document.documentElement.setAttribute('data-bs-theme', t);
      try { localStorage.setItem('theme', t); } catch {}
      if (icon && label) {
        if (t === 'dark') { icon.className = 'bi bi-moon-stars'; label.textContent = 'Dark'; metaTheme && (metaTheme.content = '#0f1115'); }
        else { icon.className = 'bi bi-sun'; label.textContent = 'Light'; metaTheme && (metaTheme.content = '#ffffff'); }
      }
    };

    let cur = 'light';
    try { cur = localStorage.getItem('theme') || (document.documentElement.getAttribute('data-bs-theme') || 'light'); } catch {}
    apply(cur);

    btn.addEventListener('click', () => {
      cur = (document.documentElement.getAttribute('data-bs-theme') === 'dark') ? 'light' : 'dark';
      apply(cur);
    });
  };

  // Auto-init on load (safe if button not present)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => UI.initThemeToggle());
  } else {
    UI.initThemeToggle();
  }

  window.UI = UI;
})();
