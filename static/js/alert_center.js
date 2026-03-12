let _currentCat = 'agent';
let _ruleModal;

const CAT_LABELS  = {agent:'Agent / RMM', asset:'Asset Lifecycle', vulnerability:'Vulnerability', eagle_eyes:'Eagle Eyes'};
const CAT_ICONS   = {agent:'bi-pc-display', asset:'bi-hdd', vulnerability:'bi-shield-exclamation', eagle_eyes:'bi-eye'};
const CAT_COLORS  = {agent:'#4dabf7', asset:'#69db7c', vulnerability:'#ff6b6b', eagle_eyes:'#da77f2'};
const PRIO_COLORS = {Urgent:'#ff6b6b', High:'#ff8c42', Normal:'#fcc419', Low:'#74c0fc'};

function switchCat(cat, btn) {
  document.querySelectorAll('#alertTabs .nav-link').forEach(b => {
    b.classList.remove('active');
    b.style.borderBottomColor = 'transparent';
  });
  btn.classList.add('active');
  btn.style.borderBottomColor = CAT_COLORS[cat] || '#4dabf7';
  _currentCat = cat;
  if (cat === '__log') {
    document.getElementById('rules-panel').style.display = 'none';
    document.getElementById('log-panel').style.display   = '';
    loadLog();
  } else {
    document.getElementById('rules-panel').style.display = '';
    document.getElementById('log-panel').style.display   = 'none';
    loadRules(cat);
  }
}

async function loadRules(cat) {
  const grid    = document.getElementById('rules-grid');
  const empty   = document.getElementById('rules-empty');
  const spinner = document.getElementById('rules-spinner');
  grid.innerHTML = ''; empty.style.display='none'; spinner.style.display='';
  try {
    const r = await fetch(`/api/alerts/rules?category=${cat}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    spinner.style.display = 'none';
    if (!d.ok || !d.rules.length) { empty.style.display=''; return; }

  grid.innerHTML = d.rules.map(rule => {
    const col   = CAT_COLORS[rule.category] || '#888';
    const pCol  = PRIO_COLORS[rule.ticket_priority] || '#aaa';
    const thresh = rule.threshold_value > 0 ? `${rule.threshold_value} ${rule.threshold_unit||''}` : '—';
    const badges = [
      rule.email_notify  ? `<span class="badge bg-secondary"><i class="bi bi-envelope-fill me-1"></i>Email</span>` : '',
      rule.teams_notify  ? `<span class="badge" style="background:#5865f2;"><i class="bi bi-microsoft me-1"></i>Teams</span>` : '',
      rule.auto_ticket   ? `<span class="badge" style="background:#2a4a6a;"><i class="bi bi-ticket-perforated me-1"></i>Ticket</span>` : '',
    ].filter(Boolean).join(' ');
    return `<div class="col-lg-4 col-md-6">
      <div class="rule-card p-3 h-100" id="rcard-${rule.id}">
        <div class="d-flex align-items-start justify-content-between gap-2 mb-2">
          <div class="flex-grow-1 overflow-hidden">
            <div class="d-flex align-items-center gap-2">
              <div style="width:6px;height:6px;border-radius:50%;background:${col};flex-shrink:0;"></div>
              <span class="fw-semibold text-white text-truncate" style="font-size:.85rem;" title="${rule.label||rule.alert_type}">${rule.label||rule.alert_type}</span>
            </div>
            <div class="text-muted mt-1" style="font-size:.7rem;">
              Threshold: <span class="text-white">${thresh}</span>
              &nbsp;·&nbsp;Cooldown: <span class="text-white">${rule.cooldown_minutes}m</span>
              &nbsp;·&nbsp;Priority: <span style="color:${pCol};">${rule.ticket_priority}</span>
            </div>
          </div>
          <div class="form-check form-switch mb-0 flex-shrink-0">
            <input class="form-check-input" type="checkbox" ${rule.enabled?'checked':''} onchange="toggleRule(${rule.id},this)">
          </div>
        </div>
        <div class="mb-2" style="min-height:22px;">${badges}</div>
        <div class="d-flex gap-2 mt-auto">
          <button class="btn btn-xs btn-outline-secondary py-0 px-2" style="font-size:.72rem;" onclick="editRule(${JSON.stringify(rule).replace(/"/g,'&quot;')})">
            <i class="bi bi-pencil"></i> Edit
          </button>
          <button class="btn btn-xs btn-outline-danger py-0 px-2 ms-auto" style="font-size:.72rem;" onclick="deleteRule(${rule.id})">
            <i class="bi bi-trash"></i>
          </button>
        </div>
      </div>
    </div>`;
  }).join('');
  } catch(e) {
    spinner.style.display = 'none';
    grid.innerHTML = `<div class="col-12"><div class="alert alert-danger py-2 small">Failed to load rules: ${e.message}</div></div>`;
  }
}

async function toggleRule(id, cb) {
  const r = await fetch(`/api/alerts/rules/${id}/toggle`, {method:'POST'});
  const d = await r.json();
  if (!d.ok) { cb.checked = !cb.checked; }
  // Visual pulse
  const card = document.getElementById(`rcard-${id}`);
  if (card) {
    card.style.transition='opacity .2s';
    card.style.opacity = d.enabled ? '1' : '0.45';
  }
}

async function deleteRule(id) {
  if (!confirm('Delete this alert rule?')) return;
  await fetch(`/api/alerts/rules/${id}`, {method:'DELETE'});
  loadRules(_currentCat);
}

function showAddRule() {
  document.getElementById('ruleModal-title').textContent = 'Add Alert Rule';
  document.getElementById('r-id').value        = '';
  document.getElementById('r-label').value     = '';
  document.getElementById('r-type').value      = '';
  document.getElementById('r-threshold').value = 0;
  document.getElementById('r-cooldown').value  = 60;
  document.getElementById('r-enabled').checked = true;
  document.getElementById('r-email').checked   = true;
  document.getElementById('r-teams').checked   = false;
  document.getElementById('r-ticket').checked  = false;
  document.getElementById('r-priority').value  = 'Normal';
  document.getElementById('r-category').value  = _currentCat !== '__log' ? _currentCat : 'agent';
  document.getElementById('r-teams-wh').value  = '';
  document.getElementById('r-assign').value    = '';
  _ruleModal.show();
}

function editRule(r) {
  document.getElementById('ruleModal-title').textContent = 'Edit Alert Rule';
  document.getElementById('r-id').value        = r.id;
  document.getElementById('r-label').value     = r.label || '';
  document.getElementById('r-type').value      = r.alert_type || '';
  document.getElementById('r-threshold').value = r.threshold_value || 0;
  document.getElementById('r-unit').value      = r.threshold_unit || '';
  document.getElementById('r-cooldown').value  = r.cooldown_minutes || 60;
  document.getElementById('r-enabled').checked = !!r.enabled;
  document.getElementById('r-email').checked   = !!r.email_notify;
  document.getElementById('r-teams').checked   = !!r.teams_notify;
  document.getElementById('r-ticket').checked  = !!r.auto_ticket;
  document.getElementById('r-priority').value  = r.ticket_priority || 'Normal';
  document.getElementById('r-category').value  = r.category || 'agent';
  document.getElementById('r-teams-wh').value  = r.teams_webhook_url || '';
  document.getElementById('r-assign').value    = r.assigned_to_user_id || '';
  _ruleModal.show();
}

async function saveRule() {
  const id = document.getElementById('r-id').value;
  const payload = {
    label:               document.getElementById('r-label').value,
    alert_type:          document.getElementById('r-type').value,
    category:            document.getElementById('r-category').value,
    threshold_value:     parseFloat(document.getElementById('r-threshold').value)||0,
    threshold_unit:      document.getElementById('r-unit').value,
    cooldown_minutes:    parseInt(document.getElementById('r-cooldown').value)||60,
    enabled:             document.getElementById('r-enabled').checked,
    email_notify:        document.getElementById('r-email').checked,
    teams_notify:        document.getElementById('r-teams').checked,
    auto_ticket:         document.getElementById('r-ticket').checked,
    ticket_priority:     document.getElementById('r-priority').value,
    teams_webhook_url:   document.getElementById('r-teams-wh').value,
    assigned_to_user_id: document.getElementById('r-assign').value || null,
  };
  const url    = id ? `/api/alerts/rules/${id}` : '/api/alerts/rules';
  const method = id ? 'PUT' : 'POST';
  await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  _ruleModal.hide();
  if (_currentCat !== '__log') loadRules(_currentCat);
}

async function loadLog() {
  const tbody = document.getElementById('log-tbody');
  tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3"><span class="spinner-border spinner-border-sm"></span></td></tr>';
  const r = await fetch('/api/alerts/log?limit=200');
  const d = await r.json();
  if (!d.ok || !d.log.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">No alerts logged yet.</td></tr>';
    return;
  }
  tbody.innerHTML = d.log.map(l => {
    const catIcon = CAT_ICONS[l.category] || 'bi-bell';
    const catCol  = CAT_COLORS[l.category] || '#888';
    const ts = (l.fired_at||'').slice(0,16).replace('T',' ');
    const ticket = l.ticket_id
      ? `<a href="/tickets/${l.ticket_id}" class="badge bg-primary text-decoration-none">#${l.ticket_id}</a>` : '';
    return `<tr>
      <td class="text-muted" style="white-space:nowrap;">${ts}</td>
      <td><i class="bi ${catIcon} me-1" style="color:${catCol};"></i><span class="text-muted" style="font-size:.75rem;">${l.category||''}</span></td>
      <td><code style="font-size:.72rem;">${l.alert_type||''}</code></td>
      <td style="max-width:400px;" class="text-truncate" title="${l.message||''}">${l.message||''}</td>
      <td>${ticket}</td>
    </tr>`;
  }).join('');
}

async function saveTeamsWebhook() {
  const val = document.getElementById('global-teams-wh').value.trim();
  await fetch('/api/settings', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({key:'teams_webhook_url', value:val})
  });
}

// Load global Teams webhook on page load
async function loadTeamsWebhook() {
  try {
    const r = await fetch('/api/settings/teams_webhook_url');
    const d = await r.json();
    if (d.value) document.getElementById('global-teams-wh').value = d.value;
  } catch(_) {}
}

document.addEventListener('DOMContentLoaded', () => {
  _ruleModal = new bootstrap.Modal(document.getElementById('ruleModal'));
  // Default to Alert Log so users see fired alerts, not rules
  const logBtn = document.querySelector('#alertTabs .nav-link[data-cat="__log"]');
  if (logBtn) {
    document.querySelectorAll('#alertTabs .nav-link').forEach(b => b.classList.remove('active'));
    logBtn.classList.add('active');
    switchCat('__log', logBtn);
  } else {
    const firstTab = document.querySelector('#alertTabs .nav-link.active');
    if (firstTab) firstTab.style.borderBottomColor = CAT_COLORS[_currentCat] || '#4dabf7';
    loadRules('agent');
  }
  loadTeamsWebhook();
});
