async function startRustDeskSession(assetId) {
  const reason = prompt('Reason for remote session (required for audit log):');
  if (!reason || !reason.trim()) { alert('A reason is required.'); return; }
  const resp = await fetch(`/assets/${assetId}/remote/rustdesk/start`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({reason: reason.trim()})
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || !data.success) { alert(data.error || 'Failed to start session'); return; }
  const pw = (data.rustdesk_password || '').trim();
  const id = (data.rustdesk_id || '').trim();
  if (pw) {
    try { await navigator.clipboard.writeText(pw); showToast('Password copied to clipboard ✓'); }
    catch (_) { showToast('Password: ' + pw, 8000); }
  }
  if (id) { setTimeout(() => { window.location.href = pw ? `rustdesk://${encodeURIComponent(id)}?password=${encodeURIComponent(pw)}&fullscreen=1` : `rustdesk://${encodeURIComponent(id)}?fullscreen=1`; }, 600); }
}
function showToast(msg, ms=3500) {
  let t = document.getElementById('rdToast');
  if (!t) {
    t = document.createElement('div'); t.id = 'rdToast';
    t.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;background:#198754;color:#fff;padding:10px 18px;border-radius:8px;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,.3);transition:opacity .4s';
    document.body.appendChild(t);
  }
  t.textContent = msg; t.style.opacity = '1';
  clearTimeout(t._hide); t._hide = setTimeout(() => { t.style.opacity = '0'; }, ms);
}

// ── AI Suggestion ─────────────────────────────────────────────────────────────
const TICKET_ID = window.VIEWTICKETCFG.ticket_id;

async function requestAiSuggestion() {
  const btn  = document.getElementById('btn-ai-suggest');
  const body = document.getElementById('ai-panel-body');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
  body.innerHTML = '<div class="text-muted small text-center py-2">Thinking…</div>';
  try {
    const resp = await fetch(`/api/ai/ticket/${TICKET_ID}/suggest`, { method: 'POST' });
    const d    = await resp.json();
    if (!resp.ok) { body.innerHTML = `<div class="alert alert-warning p-2 small">${d.error||'AI not configured — add your OpenAI key in Settings → AI.'}</div>`; return; }
    renderSuggestion(d, body);
  } catch(e) {
    body.innerHTML = `<div class="alert alert-danger p-2 small">${e.message}</div>`;
  } finally {
    btn.disabled = false; btn.innerHTML = '<i class="bi bi-lightning-fill"></i> Suggest';
  }
}

function renderSuggestion(d, body) {
  const p = d.parsed || {};
  if (!d.informed_by) d.informed_by = p.informed_by || [];
  const conf = p.confidence ? Math.round(p.confidence * 100) : null;
  const confCol = conf >= 85 ? '#5fcf8b' : conf >= 60 ? '#f8a84a' : '#f87171';
  const sugId = d.suggestion_id || d.id;
  body.innerHTML = `
    <div class="mb-2 d-flex align-items-center">
      <strong class="small">AI Diagnosis</strong>
      ${conf ? `<span class="ms-auto badge" style="background:${confCol}22;color:${confCol};font-size:.72rem">${conf}% confidence</span>` : ''}
    </div>
    <p class="small text-secondary mb-2">${escHtml(p.diagnosis||d.suggestion||'—')}</p>
    ${p.resolution_steps&&p.resolution_steps.length ? `<div class="small mb-2"><strong>Steps:</strong><ol class="mb-1 ps-3">${p.resolution_steps.map(s=>`<li>${escHtml(s)}</li>`).join('')}</ol></div>` : ''}
    ${p.estimated_minutes ? `<div class="small text-muted mb-2"><i class="bi bi-clock me-1"></i>Est. ${p.estimated_minutes} min</div>` : ''}
    ${(d.informed_by && d.informed_by.length) ? `<div class="small text-muted mb-2 border-top pt-2"><i class="bi bi-clock-history me-1"></i>Informed by past resolved tickets:<ul class="mb-0 ps-3">${d.informed_by.map(t=>`<li><a href="/tickets/${t.id}" class="text-decoration-none">#${t.id}</a> ${escHtml(t.subject||'')}</li>`).join('')}</ul></div>` : ''}
    ${sugId ? `<div class="d-flex gap-2 mt-2">
      <button class="btn btn-sm btn-outline-success py-0 px-2" onclick="applySugg(${sugId})"><i class="bi bi-check-lg"></i> Apply as Note</button>
      <button class="btn btn-sm btn-outline-secondary py-0 px-2" onclick="dismissSugg(${sugId})"><i class="bi bi-x-lg"></i> Dismiss</button>
    </div>` : ''}
  `;
}

async function applySugg(id) {
  const resp = await fetch(`/api/ai/suggestions/${id}/apply`, { method:'POST' });
  if (resp.ok) { showFlash('AI suggestion added as a ticket note.', 2000); document.getElementById('ai-panel-body').innerHTML='<div class="text-muted small text-center py-2">Applied ✓</div>'; }
}
async function dismissSugg(id) {
  await fetch(`/api/ai/suggestions/${id}/dismiss`, { method:'POST' });
  document.getElementById('ai-panel-body').innerHTML='<div class="text-muted small text-center py-2">Dismissed.</div>';
}
function escHtml(s) { return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// Auto-load existing suggestions if any
fetch(`/api/ai/ticket/${TICKET_ID}/suggestions`).then(r=>r.json()).then(list=>{
  if (!list.length) return;
  const latest = list[0];
  if (latest.status === 'pending') renderSuggestion(latest, document.getElementById('ai-panel-body'));
});

async function aiTriage(ticket_id) {
  const btn = document.getElementById('aiTriageBtn');
  const box = document.getElementById('aiTriageResult');
  btn.disabled = true;
  box.className = 'alert alert-info mt-2';
  box.textContent = 'AI is triaging this ticket…';
  try {
    const r = await fetch(`/api/ai/triage-ticket/${ticket_id}`, {method:'POST'});
    const d = await r.json();
    if (d.error) { box.className='alert alert-danger mt-2'; box.textContent=d.error; }
    else {
      box.className='alert alert-success mt-2';
      box.innerHTML = `<strong>AI Triage Result</strong> <span class="badge bg-secondary">${escHtml(d.source)}</span><br>
        Priority: <strong>${escHtml(d.priority)}</strong> &nbsp;|&nbsp; Category: <strong>${escHtml(d.category)}</strong><br>
        <span class="text-muted small">${escHtml(d.reason)}</span>`;
    }
  } catch(e) { box.className='alert alert-danger mt-2'; box.textContent='Triage request failed.'; }
  finally { btn.disabled = false; }
}
