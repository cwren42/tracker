function aiEsc(v) { return (v || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
function toggleAiKeyInput() { const inp = document.getElementById('aiKeyInput'); if (inp.type === 'password') { inp.type = 'text'; inp.value = ''; inp.placeholder = 'Paste new API key (sk-...)'; } else { inp.type = 'password'; inp.value = '••••••••'; inp.placeholder = 'sk-...'; } }
async function saveAiSettings() {
    const st = document.getElementById('aiSaveStatus');
    st.textContent = 'Saving...';
    const p = {
        openai_api_key: document.getElementById('aiKeyInput').value.trim(),
        openai_model: document.getElementById('aiModel').value,
        ai_ticket_enabled: document.getElementById('aiTicketEnabled').checked ? 'true' : 'false',
        ai_ticket_auto_mode: document.getElementById('aiTicketAutoMode').checked ? 'true' : 'false',
        ai_security_monitor_enabled: document.getElementById('aiSecurityMonitor').checked ? 'true' : 'false',
    };
    try {
        const r = await fetch('/api/ai/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(p)
        });
        const d = await r.json();
        if (!r.ok || d.error) {
            st.textContent = 'Error: ' + (d.error || 'Failed to save');
            return;
        }
        st.textContent = 'Saved';
        document.getElementById('aiKeyInput').type = 'password';
        if (p.openai_api_key) {
            const masked = p.openai_api_key.length > 12
                ? (p.openai_api_key.slice(0, 8) + '…' + p.openai_api_key.slice(-4))
                : '••••••••';
            document.getElementById('aiKeyInput').value = masked;
        }
    } catch (e) {
        st.textContent = 'Error: ' + e.message;
    }
}
async function testAiConnection() { const st = document.getElementById('aiSaveStatus'); st.textContent = 'Testing...'; try { const r = await fetch('/api/ai/test', {method:'POST'}); const d = await r.json(); st.textContent = d.ok ? 'Connected!' : ('Error: ' + (d.error || 'Unknown')); } catch(e) { st.textContent = 'Error: ' + e.message; } }
async function generateSecuritySummary() { const st = document.getElementById('aiSecurityStatus'); const out = document.getElementById('aiSecuritySummaryOutput'); st.textContent = 'Generating...'; out.textContent = ''; try { const r = await fetch('/api/ai/security-summary/generate', {method:'POST'}); const d = await r.json(); if (!d.ok) { st.textContent = 'Error: ' + (d.error || 'Failed'); return; } out.textContent = d.summary || 'No summary generated'; st.textContent = 'Done'; } catch(e) { st.textContent = 'Error: ' + e.message; out.textContent = e.message; } }
document.addEventListener('DOMContentLoaded', function() { loadAiSettings(); });
async function loadAiSettings() {
    try {
        const r = await fetch('/api/ai/settings');
        const d = await r.json();
        if (!r.ok || d.error) return;

        if (d.openai_model) document.getElementById('aiModel').value = d.openai_model;
        if (d.openai_api_key) document.getElementById('aiKeyInput').value = d.openai_api_key;
        document.getElementById('aiTicketEnabled').checked = String(d.ai_ticket_enabled) === 'true';
        document.getElementById('aiTicketAutoMode').checked = String(d.ai_ticket_auto_mode) === 'true';
        document.getElementById('aiSecurityMonitor').checked = String(d.ai_security_monitor_enabled) === 'true';
    } catch (e) {
        // Keep page usable even if settings preload fails.
        console.warn('Failed to load AI settings:', e);
    }
}
