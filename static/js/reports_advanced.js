const TYPE_ICONS = {
  vulnerability:'shield-exclamation', patch_compliance:'patch-check-fill',
  asset_inventory:'pc-display', tickets:'ticket-fill', alerts:'bell-fill',
  user_activity:'person-lines-fill', rmm_status:'server'
};
const TYPE_COL = {
  vulnerability:'#f87171', patch_compliance:'#5fcf8b', asset_inventory:'#6aacf8',
  tickets:'#f8a84a', alerts:'#e879f9', user_activity:'#34d399', rmm_status:'#60a5fa'
};

let templates = [];

async function loadTemplates() {
  const resp = await fetch('/api/reports/templates');
  templates  = await resp.json();
  document.getElementById('tmpl-count').textContent = templates.length;

  const list = document.getElementById('tmpl-list');
  if (!templates.length) { list.innerHTML='<div class="text-center text-secondary py-4 small">No templates</div>'; return; }
  list.innerHTML = templates.map(t => {
    const ic  = TYPE_ICONS[t.report_type] || 'file-earmark-bar-graph';
    const col = TYPE_COL[t.report_type]   || '#8899aa';
    return `<div class="tmpl-row">
      <div class="tmpl-icon" style="background:${col}22;color:${col}"><i class="bi bi-${ic}"></i></div>
      <div class="flex-grow-1">
        <div class="tmpl-name">${escHtml(t.name)}</div>
        <div class="tmpl-desc">${escHtml(t.description||'')}</div>
      </div>
      <button class="btn btn-sm btn-outline-primary py-0 px-2" onclick='openRunModalWith(${JSON.stringify(t)})'>
        <i class="bi bi-play-fill"></i>
      </button>
    </div>`;
  }).join('');

  // populate modal select
  const sel = document.getElementById('modal-tmpl');
  sel.innerHTML = '<option value="">— select template —</option>' +
    templates.map(t=>`<option value="${t.id}" data-type="${t.report_type}">${escHtml(t.name)}</option>`).join('');
}

async function loadRuns() {
  const resp = await fetch('/api/reports/runs');
  const runs = await resp.json();
  const tbody = document.getElementById('run-tbody');
  if (!runs.length) { tbody.innerHTML='<tr><td colspan="6" class="text-center text-secondary py-3 small">No reports generated yet</td></tr>'; return; }
  const cols = { success:'#5fcf8b',failed:'#f87171',generating:'#f8a84a',pending:'#8899aa',ready:'#5fcf8b' };
  tbody.innerHTML = runs.map(r => {
    const col = cols[r.status]||'#8899aa';
    let actions = '';
    if (r.status==='ready' || r.status==='success') {
      actions += `<button class="btn btn-xs btn-sm py-0 px-1 btn-outline-info me-1" title="Preview" onclick="previewReport(${r.id},'${escHtml(r.name)}')"><i class="bi bi-eye-fill"></i></button>`;
      if (r.file_csv) actions += `<a class="btn btn-xs btn-sm py-0 px-1 btn-outline-secondary me-1" title="CSV" href="/api/reports/download/${encodeURIComponent(r.file_csv.split('/').pop())}"><i class="bi bi-filetype-csv"></i></a>`;
      if (r.file_pdf) actions += `<a class="btn btn-xs btn-sm py-0 px-1 btn-outline-secondary" title="PDF" href="/api/reports/download/${encodeURIComponent(r.file_pdf.split('/').pop())}"><i class="bi bi-filetype-pdf"></i></a>`;
    }
    return `<tr>
      <td>${escHtml(r.name)}</td>
      <td><span style="color:${TYPE_COL[r.report_type]||'#8899aa'};font-size:.75rem">${r.report_type}</span></td>
      <td><span class="status-badge" style="color:${col}">${r.status}</span></td>
      <td>${r.row_count??'—'}</td>
      <td style="font-size:.75rem;color:#8899aa">${(r.completed_at||r.generated_at||'').slice(0,16)}</td>
      <td>${actions||'<span class="text-secondary small">—</span>'}</td>
    </tr>`;
  }).join('');
}

async function previewReport(runId, name) {
  const resp = await fetch(`/api/reports/runs/${runId}/data`);
  if (resp.status === 202) { alert('Report is still generating, try again in a moment.'); return; }
  const d = await resp.json();
  if (!resp.ok) { alert(d.error||'Failed to load data'); return; }
  document.getElementById('preview-title').textContent = `Preview: ${name} (${d.count} rows)`;
  document.getElementById('preview-head').innerHTML = `<tr>${d.cols.map(c=>`<th>${escHtml(c)}</th>`).join('')}</tr>`;
  document.getElementById('preview-body').innerHTML = d.rows.slice(0,200).map(row=>
    `<tr>${d.cols.map(c=>`<td>${escHtml(String(row[c]??''))}</td>`).join('')}</tr>`
  ).join('') + (d.count>200?`<tr><td colspan="${d.cols.length}" class="text-center text-secondary small">Showing first 200 of ${d.count} rows</td></tr>`:'');
  document.getElementById('report-preview').style.display='block';
  document.getElementById('report-preview').scrollIntoView({behavior:'smooth'});
}

function openRunModal() {
  new bootstrap.Modal(document.getElementById('runModal')).show();
}

function openRunModalWith(tmpl) {
  document.getElementById('modal-tmpl').value = tmpl.id;
  document.getElementById('modal-name').value = tmpl.name;
  onTemplateSelect();
  new bootstrap.Modal(document.getElementById('runModal')).show();
}

function onTemplateSelect() {
  const sel  = document.getElementById('modal-tmpl');
  const opt  = sel.options[sel.selectedIndex];
  const type = opt?.dataset?.type || '';
  const name = document.getElementById('modal-name');
  if (!name.value || name.value === name.dataset.prev) {
    name.value = opt?.text || '';
    name.dataset.prev = name.value;
  }
  // Future: show per-type config (date range, filters)
  const extra = document.getElementById('modal-config-extra');
  if (type === 'schedule') {
    extra.innerHTML=''; return;
  }
  let html = '';
  if (['vulnerability','patch_compliance','tickets','alerts','user_activity'].includes(type)) {
    html = `<div class="row g-2">
      <div class="col-6">
        <label class="form-label text-secondary" style="font-size:.8rem">Date From</label>
        <input type="date" class="form-control form-control-sm" id="cfg-date-from" style="background:#121c2b;color:#cdd9e5;border-color:#2d3a50">
      </div>
      <div class="col-6">
        <label class="form-label text-secondary" style="font-size:.8rem">Date To</label>
        <input type="date" class="form-control form-control-sm" id="cfg-date-to" style="background:#121c2b;color:#cdd9e5;border-color:#2d3a50">
      </div>
    </div>`;
  }
  extra.innerHTML = html;
}

async function runReport() {
  const sel     = document.getElementById('modal-tmpl');
  const tmplId  = parseInt(sel.value)||null;
  const tmpl    = templates.find(t=>t.id===tmplId);
  if (!tmplId) { alert('Please select a template'); return; }
  const name    = document.getElementById('modal-name').value || tmpl?.name;
  const config  = {};
  const df = document.getElementById('cfg-date-from');
  const dt = document.getElementById('cfg-date-to');
  if (df?.value) config.date_from = df.value;
  if (dt?.value) config.date_to   = dt.value;

  const resp = await fetch('/api/reports/run', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ template_id:tmplId, report_type:tmpl?.report_type, name, config })
  });
  const d = await resp.json();
  bootstrap.Modal.getInstance(document.getElementById('runModal'))?.hide();
  if (resp.ok) {
    showToast('Report generating… Run #' + d.run_id, 'success');
    pollRun(d.run_id);
  } else {
    showToast(d.error||'Failed', 'danger');
  }
}

async function pollRun(runId) {
  for (let i=0; i<60; i++) {
    await new Promise(r=>setTimeout(r,2000));
    const resp = await fetch(`/api/reports/runs/${runId}`);
    const d    = await resp.json();
    if (d.status === 'ready' || d.status === 'failed') {
      loadRuns();
      if (d.status==='ready') showToast(`Report ready (${d.row_count} rows)`, 'success');
      else showToast('Report failed: ' + (d.error_detail||'unknown'), 'danger');
      return;
    }
  }
}

function escHtml(s) { return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function showToast(msg, type='info') {
  const t = document.createElement('div');
  t.className = `alert alert-${type} position-fixed`;
  t.style.cssText = 'bottom:20px;right:20px;z-index:9999;min-width:240px';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(()=>t.remove(), 4000);
}

// init
loadTemplates();
loadRuns();
setInterval(loadRuns, 15000);
