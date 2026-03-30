let _eeAllAgents = window.SETTINGSEAGLEEYE_CFG.ee_all_agents;
let _eeExcluded = new Set(window.SETTINGSEAGLEEYE_CFG.ee_excluded_agents);
let _eeInitialExcluded = new Set(window.SETTINGSEAGLEEYE_CFG.ee_excluded_agents);
let _eeActiveAgents = window.SETTINGSEAGLEEYE_CFG.ee_active_agents;
let _eeFilterActive = '';
let _eeFilterExcluded = '';

function eeEsc(v) { 
    return (v || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); 
}

function eeFilterList() {
    _eeFilterActive = document.getElementById('eeFilterActive').value.toLowerCase();
    _eeFilterExcluded = document.getElementById('eeFilterExcluded').value.toLowerCase();
    eeRenderLists();
}

function eeRenderLists() {
    const activeContainer = document.getElementById('eeActiveList');
    const excludedContainer = document.getElementById('eeExcludedList');
    
    const activeAgents = _eeAllAgents.filter(a => !_eeExcluded.has(a.agent_id));
    const excludedAgents = _eeAllAgents.filter(a => _eeExcluded.has(a.agent_id));
    
    const filterActive = (list) => list.filter(a => !_eeFilterActive || (a.hostname||'').toLowerCase().includes(_eeFilterActive) || (a.agent_id||'').toLowerCase().includes(_eeFilterActive));
    const renderList = (agents) => agents.map(a => `
        <div class="ee-item d-flex align-items-center">
            <input type="checkbox" class="form-check-input" data-agent-id="${eeEsc(a.agent_id)}" style="cursor:pointer;">
            <div class="ms-2 flex-grow-1" style="cursor:pointer;">
                <strong>${eeEsc(a.hostname || a.agent_id)}</strong><br>
                <small class="text-muted">${eeEsc(a.agent_id)}</small>
            </div>
        </div>
    `).join('');
    
    activeContainer.innerHTML = filterActive(activeAgents).length ? renderList(filterActive(activeAgents)) : '<div class="text-muted text-center py-4">No agents</div>';
    excludedContainer.innerHTML = filterActive(excludedAgents).length ? renderList(filterActive(excludedAgents)) : '<div class="text-muted text-center py-4">No excluded agents</div>';
    
    document.getElementById('eeActiveCount').textContent = activeAgents.length;
    document.getElementById('eeExcludedCount').textContent = excludedAgents.length;
}

function eeGetSelectedActive() {
    return Array.from(document.querySelectorAll('#eeActiveList input[type="checkbox"]:checked')).map(el => el.dataset.agentId);
}

function eeGetSelectedExcluded() {
    return Array.from(document.querySelectorAll('#eeExcludedList input[type="checkbox"]:checked')).map(el => el.dataset.agentId);
}

function eeMoveToExcluded() {
    const sel = eeGetSelectedActive();
    if (!sel.length) { alert('Select agents to exclude'); return; }
    sel.forEach(id => _eeExcluded.add(id));
    eeRenderLists();
}

function eeMoveToActive() {
    const sel = eeGetSelectedExcluded();
    if (!sel.length) { alert('Select agents to restore'); return; }
    sel.forEach(id => _eeExcluded.delete(id));
    eeRenderLists();
}

async function eeSaveExclusions() {
    const st = document.getElementById('eeStatus');
    st.textContent = 'Saving...';

    const toAdd    = [..._eeExcluded].filter(id => !_eeInitialExcluded.has(id));
    const toRemove = [..._eeInitialExcluded].filter(id => !_eeExcluded.has(id));

    try {
        for (const agent_id of toAdd) {
            const r = await fetch('/api/settings/eagle-eye-exclusions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ agent_id })
            });
            const d = await r.json();
            if (!d.ok) { st.textContent = 'Error: ' + (d.error || 'Failed adding ' + agent_id); return; }
        }
        for (const agent_id of toRemove) {
            const r = await fetch('/api/settings/eagle-eye-exclusions/' + encodeURIComponent(agent_id), {
                method: 'DELETE'
            });
            const d = await r.json();
            if (!d.ok) { st.textContent = 'Error: ' + (d.error || 'Failed removing ' + agent_id); return; }
        }
        // Update baseline so subsequent saves work correctly
        _eeInitialExcluded = new Set(_eeExcluded);
        st.textContent = toAdd.length + toRemove.length === 0 ? 'No changes to save.' : '✓ Saved';
    } catch(e) { st.textContent = 'Error: ' + e.message; }
}

document.addEventListener('DOMContentLoaded', function() { eeRenderLists(); });
