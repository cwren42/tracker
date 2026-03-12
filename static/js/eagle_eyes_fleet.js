(function() {
  // ── helpers ────────────────────────────────────────────────────────────────
  function fmtSec(s) {
    if (!s || s < 1) return '<span class="text-muted">—</span>';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h > 0) return `<span class="text-warning fw-semibold">${h}h ${m}m</span>`;
    if (m > 0) return `<span class="text-info fw-semibold">${m}m</span>`;
    return `<span class="text-muted">&lt;1m</span>`;
  }
  function fmtSecPlain(s) {
    if (!s || s < 1) return '–';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }
  function timeAgo(iso) {
    if (!iso) return '<span class="text-muted">—</span>';
    const diff = (Date.now() - new Date(iso)) / 1000;
    if (diff < 60)   return `<span class="text-success">${Math.round(diff)}s ago</span>`;
    if (diff < 3600) return `<span class="text-info">${Math.round(diff/60)}m ago</span>`;
    if (diff < 86400) return `<span class="text-muted">${Math.round(diff/3600)}h ago</span>`;
    return `<span class="text-muted">${new Date(iso).toLocaleDateString()}</span>`;
  }
  function timeAgoVal(iso) {
    if (!iso) return 999999999;
    return (Date.now() - new Date(iso)) / 1000;
  }
  function appIcon(name) {
    if (!name) return '';
    const n = name.toLowerCase();
    if (n.includes('chrome'))  return '<i class="bi bi-browser-chrome text-warning me-1"></i>';
    if (n.includes('firefox')) return '<i class="bi bi-browser-firefox text-danger me-1"></i>';
    if (n.includes('slack'))   return '<i class="bi bi-slack text-purple me-1"></i>';
    if (n.includes('teams'))   return '<i class="bi bi-microsoft-teams text-primary me-1"></i>';
    if (n.includes('outlook')) return '<i class="bi bi-envelope me-1 text-info"></i>';
    if (n.includes('code') || n.includes('vscode')) return '<i class="bi bi-code-slash text-success me-1"></i>';
    if (n.includes('zoom'))    return '<i class="bi bi-camera-video text-primary me-1"></i>';
    return '<i class="bi bi-window me-1 text-muted"></i>';
  }

  // ── state ──────────────────────────────────────────────────────────────────
  let _agents = [], _sortCol = 4, _sortAsc = false, _arHandle = null;

  function fleetLoad() {
    fetch('/api/rmm/eagle-eyes/fleet')
      .then(r => r.json())
      .then(data => {
        if (!data.ok) { console.error(data.error); return; }
        _agents = data.agents;
        // summary cards
        document.getElementById('fleet-total').textContent  = data.total;
        document.getElementById('fleet-online').textContent = data.online;
        document.getElementById('fleet-today').textContent  = fmtLong(data.total_today_s);
        const avg = data.total > 0 ? Math.round(data.total_today_s / data.total) : 0;
        document.getElementById('fleet-avg').textContent    = fmtLong(avg);
        document.getElementById('fleet-today').innerHTML    = fmtSecPlain(data.total_today_s) || '–';
        document.getElementById('fleet-avg').innerHTML      = fmtSecPlain(avg) || '–';
        document.getElementById('ee-fleet-updated').textContent =
          'Updated ' + new Date().toLocaleTimeString();
        renderTable();
      })
      .catch(err => console.error('Fleet load error:', err));
  }

  function fmtLong(s) {
    if (!s || s < 1) return '–';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }

  // ── table render ───────────────────────────────────────────────────────────
  function renderTable() {
    const q = document.getElementById('fleet-search').value.toLowerCase();
    let rows = _agents.filter(a =>
      (a.hostname||'').toLowerCase().includes(q) ||
      (a.user||'').toLowerCase().includes(q)
    );

    // sort
    rows.sort((a, b) => {
      let va, vb;
      switch(_sortCol) {
        case 0: va = a.online ? 0 : 1; vb = b.online ? 0 : 1; break;
        case 1: va = a.hostname.toLowerCase(); vb = b.hostname.toLowerCase(); break;
        case 2: va = a.user.toLowerCase(); vb = b.user.toLowerCase(); break;
        case 3: va = (a.current_app||'').toLowerCase(); vb = (b.current_app||'').toLowerCase(); break;
        case 4: va = a.today_s; vb = b.today_s; break;
        case 5: va = (a.top_app||'').toLowerCase(); vb = (b.top_app||'').toLowerCase(); break;
        case 6: va = timeAgoVal(a.last_event); vb = timeAgoVal(b.last_event); break;
        default: va = 0; vb = 0;
      }
      return _sortAsc ? (va < vb ? -1 : va > vb ? 1 : 0)
                      : (va > vb ? -1 : va < vb ? 1 : 0);
    });

    const tbody = document.getElementById('fleet-tbody');
    document.getElementById('fleet-row-count').textContent =
      rows.length + ' device' + (rows.length !== 1 ? 's' : '');

    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">No monitored devices found</td></tr>';
      return;
    }

    tbody.innerHTML = rows.map(a => {
      const dot = a.online
        ? '<i class="bi bi-circle-fill text-success" style="font-size:.65rem;" title="Online"></i>'
        : '<i class="bi bi-circle text-muted" style="font-size:.65rem;" title="Offline"></i>';
      const curApp = a.current_app
        ? `${appIcon(a.current_app)}<span class="small">${escHtml(short(a.current_app))}</span>`
        : '<span class="text-muted small">—</span>';
      const topApp = a.top_app
        ? `${appIcon(a.top_app)}<span class="small">${escHtml(short(a.top_app))}</span>`
        : '<span class="text-muted small">—</span>';
      return `<tr>
        <td class="ps-3" style="width:30px;">${dot}</td>
        <td><span class="fw-semibold">${escHtml(a.hostname)}</span>
          <div class="text-muted x-small">${escHtml(a.agent_id)}</div></td>
        <td>${a.user ? `<i class="bi bi-person-fill text-secondary me-1"></i>${escHtml(a.user)}` : '<span class="text-muted small">Unknown</span>'}</td>
        <td>${curApp}</td>
        <td>${fmtSec(a.today_s)}</td>
        <td>${topApp}</td>
        <td class="small">${timeAgo(a.last_event)}</td>
        <td class="pe-3"><a href="/rmm/eagle-eyes/${encodeURIComponent(a.agent_id)}" class="btn btn-sm btn-outline-warning py-0 px-2">
          <i class="bi bi-eye me-1"></i>View
        </a></td>
      </tr>`;
    }).join('');
  }

  function short(name) {
    return (name || '').replace(/\.exe$/i,'').replace(/\.(app|dmg)$/i,'').substring(0, 32);
  }
  function escHtml(s) {
    return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function filterTable() { renderTable(); }

  // ── sort headers ───────────────────────────────────────────────────────────
  document.querySelectorAll('th.sortable').forEach(th => {
    th.addEventListener('click', function() {
      const col = parseInt(this.dataset.col);
      if (_sortCol === col) _sortAsc = !_sortAsc;
      else { _sortCol = col; _sortAsc = col === 2 || col === 1; }
      // update icons
      document.querySelectorAll('th.sortable').forEach(h => {
        const ico = h.querySelector('.bi');
        if (ico && h.dataset.col != '0') {
          ico.className = 'bi bi-chevron-expand text-secondary small';
        }
      });
      const ico = this.querySelector('.bi');
      if (ico && col !== 0) {
        ico.className = _sortAsc ? 'bi bi-chevron-up text-warning small'
                                 : 'bi bi-chevron-down text-warning small';
      }
      renderTable();
    });
  });

  // ── auto-refresh ───────────────────────────────────────────────────────────
  function toggleAutoRefresh() {
    const btn = document.getElementById('ee-ar-btn');
    if (_arHandle) {
      clearInterval(_arHandle);
      _arHandle = null;
      btn.innerHTML = '<i class="bi bi-play-circle me-1"></i>Auto-refresh';
      btn.classList.replace('btn-outline-success','btn-outline-secondary');
    } else {
      _arHandle = setInterval(fleetLoad, 30000);
      btn.innerHTML = '<i class="bi bi-stop-circle me-1"></i>Auto-refresh ON';
      btn.classList.replace('btn-outline-secondary','btn-outline-success');
    }
  }

  // ── kick off ───────────────────────────────────────────────────────────────
  window.fleetLoad = fleetLoad;
  window.toggleAutoRefresh = toggleAutoRefresh;
  window.filterTable = filterTable;
  fleetLoad();
})();
