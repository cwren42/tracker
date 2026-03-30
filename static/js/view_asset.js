/* view_asset.html extracted JS */
/* Expects window.ASSET_CFG.assetId set by template */


/* ─── Block 1 ─────────────────────────────────── */

            let activeRustDeskSessionId = null;
            function copyRustDeskId() {
                const id = document.getElementById('rustdeskId').textContent.trim();
                navigator.clipboard.writeText(id).catch(() => prompt('Copy RustDesk ID:', id));
            }
            async function startRustDeskSession(assetId) {
                const reason = prompt('Reason for remote session (required for audit log):');
                if (!reason || !reason.trim()) { alert('A reason is required.'); return; }
                const resp = await fetch(`/assets/${assetId}/remote/rustdesk/start`, {
                    method: 'POST', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({reason: reason.trim()})
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.success) { alert(data.error || 'Failed to start session'); return; }
                activeRustDeskSessionId = data.session_id;
                document.getElementById('rustdeskEndBtn').classList.remove('d-none');
                const id = (data.rustdesk_id || '').trim();
                const pw = (data.rustdesk_password || '').trim();
                // Silently copy password to clipboard before opening RustDesk
                if (pw) {
                    try {
                        await navigator.clipboard.writeText(pw);
                        showRustDeskToast(`Password copied to clipboard ✓`);
                    } catch (_) {
                        showRustDeskToast(`Password: ${pw}`, 8000);
                    }
                }
                if (id) { setTimeout(() => { window.location.href = pw ? `rustdesk://${encodeURIComponent(id)}?password=${encodeURIComponent(pw)}&fullscreen=1` : `rustdesk://${encodeURIComponent(id)}?fullscreen=1`; }, 600); }
            }
            function showRustDeskToast(msg, ms = 3500) {
                let t = document.getElementById('rdToast');
                if (!t) {
                    t = document.createElement('div');
                    t.id = 'rdToast';
                    t.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;background:#198754;color:#fff;padding:10px 18px;border-radius:8px;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,.3);transition:opacity .4s';
                    document.body.appendChild(t);
                }
                t.textContent = msg;
                t.style.opacity = '1';
                clearTimeout(t._hide);
                t._hide = setTimeout(() => { t.style.opacity = '0'; }, ms);
            }
            async function endRustDeskSession() {
                if (!activeRustDeskSessionId) return;
                const resp = await fetch(`/remote-sessions/${activeRustDeskSessionId}/end`, {
                    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({})
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok || !data.success) { alert(data.error || 'Failed to end session'); return; }
                activeRustDeskSessionId = null;
                document.getElementById('rustdeskEndBtn').classList.add('d-none');
            }
            
/* ─── Block 2 ─────────────────────────────────── */

const _assetId = window.ASSET_CFG.assetId;
let _assetVulnsLoaded = false;

async function loadAssetVulns() {
  if (_assetVulnsLoaded) return;
  _assetVulnsLoaded = true;
  const spinner = document.getElementById('asset-vuln-spinner');
  const empty   = document.getElementById('asset-vuln-empty');
  const table   = document.getElementById('asset-vuln-table');
  const tbody   = document.getElementById('asset-vuln-tbody');
  try {
    const r = await fetch(`/api/vulnerabilities/devices?asset_id=${_assetId}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const vulns = d.devices || [];
    spinner.style.display = 'none';
    if (!vulns.length) { empty.style.display = ''; }
    else {
      table.style.display = '';
      tbody.innerHTML = vulns.map(v => {
        const sevClass = {Critical:'text-danger', High:'text-warning', Medium:'text-info', Low:'text-secondary'}[v.severity] || '';
        const cvssColor = v.cvss >= 9 ? '#ff6b6b' : v.cvss >= 7 ? '#ff8c42' : v.cvss >= 4 ? '#fcc419' : '#74c0fc';
        const statusBadge = {Open:'bg-danger', Accepted:'bg-warning text-dark', Remediated:'bg-success'}[v.status] || 'bg-secondary';
        const cveKey = v.cve_id.replace(/[^a-z0-9]/gi, '_');
        const reviewedCell = v.updated_by
          ? `<div class="text-success" style="font-size:.7rem;"><i class="bi bi-check-circle me-1"></i>${v.updated_by}</div><div class="text-muted" style="font-size:.68rem;">${(v.updated_at||'').slice(0,16)}</div>`
          : `<span class="text-muted" style="font-size:.7rem;">—</span>`;
        const desc = (v.description || v.vuln_name || '').replace(/"/g, '&quot;');
        const shortDesc = desc.length > 120 ? desc.slice(0, 120) + '…' : desc;
        return `<tr>
          <td><input type="checkbox" class="vuln-row-check" value="${v.cve_id}" onchange="updateVulnSelection()"></td>
          <td><a href="https://nvd.nist.gov/vuln/detail/${v.cve_id}" target="_blank" class="text-info" style="font-size:.78rem;">${v.cve_id}</a></td>
          <td class="text-muted" style="font-size:.75rem;" title="${desc}">${shortDesc || '—'}</td>
          <td><span class="${sevClass}" style="font-weight:700;font-size:.78rem;">${v.severity||'—'}</span></td>
          <td><span style="font-weight:700;color:${cvssColor};">${v.cvss ? parseFloat(v.cvss).toFixed(1) : '—'}</span></td>
          <td class="text-muted" style="font-size:.75rem;">${v.product_name||'—'}</td>
          <td>
            <select class="form-select form-select-sm py-0 vuln-status-select" style="font-size:.72rem;" data-cve="${v.cve_id}" onchange="updateVulnStatus('${v.cve_id}', this.value)">
              <option value="Open" ${v.status==='Open'?'selected':''}>Open</option>
              <option value="Accepted" ${v.status==='Accepted'?'selected':''}>Accept Risk</option>
              <option value="Remediated" ${v.status==='Remediated'?'selected':''}>Remediated</option>
            </select>
          </td>
          <td>${reviewedCell}</td>
          <td>
            <button class="btn btn-sm btn-outline-warning py-0 px-1" style="font-size:.65rem;" id="asset-deploy-${cveKey}"
                onclick="deployCveFromAsset('${v.cve_id}','${cveKey}')" title="Deploy Windows patch for this CVE via RMM">
              <i class="bi bi-download"></i>
            </button>
            <div id="asset-deploy-status-${cveKey}" style="font-size:.6rem;"></div>
          </td>
        </tr>`;
      }).join('');
    }
  } catch(e) {
    spinner.style.display = 'none';
    table.style.display = '';
    tbody.innerHTML = `<tr><td colspan="9" class="text-danger py-3 text-center">Error: ${e.message}</td></tr>`;
  }
  // Always load history
  loadVulnHistory();
}

function updateVulnSelection() {
  const checks = document.querySelectorAll('.vuln-row-check:checked');
  const bar = document.getElementById('asset-vuln-bulk-bar');
  const cnt = document.getElementById('asset-vuln-sel-count');
  if (checks.length > 0) {
    bar.style.removeProperty('display');
    cnt.textContent = `${checks.length} selected —`;
  } else {
    bar.style.display = 'none';
    cnt.textContent = '';
  }
}

function toggleAllVulns(master) {
  document.querySelectorAll('.vuln-row-check').forEach(cb => cb.checked = master.checked);
  updateVulnSelection();
}

async function bulkVulnStatus(newStatus) {
  const checks = document.querySelectorAll('.vuln-row-check:checked');
  if (!checks.length) return;
  const cveIds = Array.from(checks).map(cb => cb.value);
  try {
    const r = await fetch('/api/vulnerabilities/bulk-status', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({asset_id: _assetId, cve_ids: cveIds, status: newStatus})
    });
    if (r.ok) {
      _assetVulnsLoaded = false;
      document.getElementById('vuln-check-all').checked = false;
      loadAssetVulns();
    }
  } catch(e) { alert('Bulk update failed: ' + e.message); }
}

async function updateVulnStatus(cveId, newStatus) {
  try {
    const r = await fetch(`/api/vulnerabilities/${encodeURIComponent(cveId)}/status`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({asset_id: _assetId, status: newStatus})
    });
    if (r.ok) { _assetVulnsLoaded = false; loadAssetVulns(); }
  } catch(e) { alert('Failed: ' + e.message); }
}

async function loadVulnHistory() {
  const spinner = document.getElementById('asset-vuln-history-spinner');
  const empty   = document.getElementById('asset-vuln-history-empty');
  const table   = document.getElementById('asset-vuln-history-table');
  const tbody   = document.getElementById('asset-vuln-history-tbody');
  spinner.style.display = '';
  empty.style.display = 'none';
  try {
    const r = await fetch(`/api/vulnerabilities/patch-history?asset_id=${_assetId}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const jobs = d.jobs || [];
    spinner.style.display = 'none';
    if (!jobs.length) { empty.style.display = ''; return; }
    table.style.display = '';
    tbody.innerHTML = jobs.map(j => {
      const statusColor = {installed:'text-success', no_patch:'text-muted', failed:'text-danger', deploying:'text-info', queued:'text-secondary'}[j.status] || 'text-muted';
      const reboot = j.reboot_required ? '<i class="bi bi-arrow-clockwise text-warning" title="Reboot required"></i> Yes' : '<span class="text-muted">No</span>';
      let result = '—';
      try { const rj = j.result_json ? JSON.parse(j.result_json) : null; result = rj?.installed ? `${rj.installed} patch(es) installed` : rj?.error || '—'; } catch(_) {}
      return `<tr>
        <td><a href="https://nvd.nist.gov/vuln/detail/${j.cve_id}" target="_blank" class="text-info" style="font-size:.78rem;">${j.cve_id}</a></td>
        <td><span class="${statusColor}" style="font-weight:600;font-size:.78rem;">${j.status}</span></td>
        <td class="text-muted" style="font-size:.75rem;">${j.deployed_by||'—'}</td>
        <td class="text-muted" style="font-size:.75rem;">${(j.deployed_at||'').slice(0,16)||'—'}</td>
        <td class="text-muted" style="font-size:.75rem;">${(j.completed_at||'').slice(0,16)||'—'}</td>
        <td class="text-muted" style="font-size:.75rem;">${result}</td>
        <td style="font-size:.78rem;">${reboot}</td>
      </tr>`;
    }).join('');
  } catch(e) {
    spinner.style.display = 'none';
    empty.style.display = '';
  }
}

async function deployCveFromAsset(cveId, cveKey) {
  const btn = document.getElementById('asset-deploy-' + cveKey);
  const statusEl = document.getElementById('asset-deploy-status-' + cveKey);
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm" style="width:.6rem;height:.6rem;"></span>'; }
  if (statusEl) statusEl.innerHTML = '<span class="text-muted">Searching…</span>';
  try {
    const r = await fetch(`/api/vulnerabilities/${encodeURIComponent(cveId)}/deploy`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({asset_id: _assetId})
    });
    const d = await r.json();
    if (!d.ok || !d.dispatched?.length) {
      const reason = d.error || (d.errors?.[0]?.error) || 'Agent offline';
      if (statusEl) statusEl.innerHTML = `<span class="text-warning" title="${reason}">Offline</span>`;
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-download"></i>'; }
      return;
    }
    const jobId = d.dispatched[0].job_id;
    if (statusEl) statusEl.innerHTML = '<span class="text-info">Installing…</span>';
    pollAssetCveJob(cveId, cveKey, jobId);
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span class="text-danger">${e.message}</span>`;
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-download"></i>'; }
  }
}

async function pollAssetCveJob(cveId, cveKey, jobId, attempt=0) {
  const statusEl = document.getElementById('asset-deploy-status-' + cveKey);
  const btn      = document.getElementById('asset-deploy-' + cveKey);
  if (attempt > 60) {
    if (statusEl) statusEl.innerHTML = '<span class="text-muted">Timed out</span>';
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-download"></i>'; }
    return;
  }
  await new Promise(res => setTimeout(res, 10000));
  try {
    const r = await fetch(`/api/vulnerabilities/cve-patch-jobs?cve_id=${encodeURIComponent(cveId)}&asset_id=${_assetId}`);
    const d = await r.json();
    const job = d.jobs?.find(j => j.id == jobId);
    if (!job || job.status === 'deploying' || job.status === 'queued') {
      pollAssetCveJob(cveId, cveKey, jobId, attempt + 1);
      return;
    }
    const reboot = job.reboot_required ? ' <i class="bi bi-arrow-clockwise text-warning" title="Reboot required"></i>' : '';
    if (job.status === 'installed') {
      const alreadyCurrent = job.result?.error === 'Already up to date';
      const lbl = alreadyCurrent ? '✓ Already current' : `✓${reboot}`;
      if (statusEl) statusEl.innerHTML = `<span class="text-success">${lbl}</span>`;
    } else if (job.status === 'no_patch') {
      if (statusEl) statusEl.innerHTML = '<span class="text-muted">No patch</span>';
    } else {
      const err = job.result?.error || job.status;
      if (statusEl) statusEl.innerHTML = `<span class="text-danger" title="${err}">Failed</span>`;
    }
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-download"></i>'; }
    // Refresh history after job completes
    loadVulnHistory();
  } catch(e) {
    pollAssetCveJob(cveId, cveKey, jobId, attempt + 1);
  }
}

