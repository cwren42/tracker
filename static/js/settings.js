/* settings.html extracted JS — merged from 3 script blocks */
/* Expects window.SETTINGS_CFG set by template config block */


/* ─── Block 1 ─────────────────────────────────── */

                async function testADConnection() {
                    const resultEl = document.getElementById('adTestResult');
                    resultEl.style.display = 'block';
                    resultEl.className = 'alert alert-info';
                    resultEl.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Testing connection...';
                    try {
                        const res = await fetch('window.SETTINGS_CFG.adTestUrl', {method: 'POST', headers: {'Content-Type': 'application/json'}});
                        const data = await res.json();
                        if (data.success) {
                            resultEl.className = 'alert alert-success';
                            resultEl.textContent = data.message || 'Connected';
                        } else {
                            resultEl.className = 'alert alert-danger';
                            resultEl.textContent = data.error || 'Connection failed';
                        }
                    } catch (e) {
                        resultEl.className = 'alert alert-danger';
                        resultEl.textContent = e?.message || 'Connection failed';
                    }
                }
                
/* ─── Block 2 ─────────────────────────────────── */

                (function() {
                    let scriptLibraryCache = [];
                    let scriptsAutoRefreshTimer = null;

                    // Immediate boot marker: if this never appears, this script block is not executing.
                    const bootWrap = document.getElementById('scriptLibraryList');
                    if (bootWrap && (bootWrap.textContent || '').includes('Loading scripts')) {
                        bootWrap.innerHTML = '<div class="text-warning">Initializing scripts module...</div>';
                    }

                    // Surface runtime errors directly in the Scripts panel for faster diagnosis.
                    window.addEventListener('error', function(e) {
                        const wrap = document.getElementById('scriptLibraryList');
                        if (!wrap) return;
                        const msg = e && e.message ? e.message : 'unknown JavaScript error';
                        wrap.innerHTML = `<div class="text-danger">Scripts JS error: ${esc(msg)}</div>`;
                    });

                    function esc(v) {
                        return (v || '')
                            .replace(/&/g, '&amp;')
                            .replace(/</g, '&lt;')
                            .replace(/>/g, '&gt;')
                            .replace(/"/g, '&quot;')
                            .replace(/'/g, '&#39;');
                    }

                    function setLibraryMessage(message, type) {
                        const wrap = document.getElementById('scriptLibraryList');
                        if (!wrap) return;
                        const cls = type === 'danger' ? 'text-danger' : (type === 'warning' ? 'text-warning' : 'text-muted');
                        wrap.innerHTML = `<div class="${cls}">${esc(message)}</div>`;
                    }

                    async function fetchJsonWithTimeout(url, options, timeoutMs) {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), timeoutMs || 10000);
                        try {
                            const resp = await fetch(url, {...(options || {}), signal: controller.signal});
                            return resp;
                        } finally {
                            clearTimeout(timer);
                        }
                    }

                    function renderScriptTestPicker() {
                        const picker = document.getElementById('scriptTestScript');
                        if (!picker) return;
                        const options = scriptLibraryCache.map(s =>
                            `<option value="${s.id}">${esc(s.name)} ${esc(s.file_type)} ${s.is_tested ? '(tested)' : '(untested)'}</option>`
                        ).join('');
                        picker.innerHTML = options || '<option value="">No scripts saved</option>';
                    }

                    function renderScriptLibraryList() {
                        const wrap = document.getElementById('scriptLibraryList');
                        if (!wrap) return;
                        if (!scriptLibraryCache.length) {
                            wrap.innerHTML = '<div class="text-muted">No scripts saved yet.</div>';
                            return;
                        }

                        wrap.innerHTML = scriptLibraryCache.map(s => {
                            const tested = s.is_tested
                                ? '<span class="badge bg-success">Tested</span>'
                                : '<span class="badge bg-secondary">Untested</span>';
                            const testedMeta = s.last_tested_at
                                ? `<small class="text-muted">Last test: ${new Date(s.last_tested_at).toLocaleString()}${s.last_tested_agent_id ? ' on ' + esc(s.last_tested_agent_id) : ''}</small>`
                                : '<small class="text-muted">Not tested yet</small>';

                            return `
                                <div class="border rounded p-2 mb-2">
                                    <div class="d-flex justify-content-between align-items-center gap-2">
                                        <div>
                                            <strong>${esc(s.name)}</strong>
                                            <span class="badge bg-light text-dark border ms-1">${esc(s.file_type)}</span>
                                            ${tested}
                                        </div>
                                        <div class="d-flex gap-1">
                                            <button class="btn btn-sm btn-outline-secondary" title="Clone" onclick="cloneScriptLibraryItem(${s.id})"><i class="bi bi-files"></i></button>
                                            <button class="btn btn-sm btn-outline-dark" title="Copy" onclick="copyScriptLibraryItem(${s.id})"><i class="bi bi-clipboard"></i></button>
                                            <button class="btn btn-sm btn-outline-primary" title="Edit" onclick="editScriptLibraryItem(${s.id})"><i class="bi bi-pencil"></i></button>
                                            <button class="btn btn-sm btn-outline-danger" title="Delete" onclick="deleteScriptLibraryItem(${s.id})"><i class="bi bi-trash"></i></button>
                                        </div>
                                    </div>
                                    <div class="small mt-1">${esc(s.description || '')}</div>
                                    <div class="mt-1">${testedMeta}</div>
                                </div>
                            `;
                        }).join('');
                    }

                    async function loadScriptLibrary() {
                        const status = document.getElementById('scriptSaveStatus');
                        setLibraryMessage('Loading scripts...');
                        if (status) status.textContent = 'Loading scripts...';
                        try {
                            const r = await fetchJsonWithTimeout('/api/settings/scripts', {cache: 'no-store'}, 10000);
                            if (!r.ok) throw new Error(`HTTP ${r.status}`);
                            const data = await r.json();
                            if (!data.ok) throw new Error(data.error || 'Failed to load scripts');
                            scriptLibraryCache = Array.isArray(data.scripts) ? data.scripts : [];
                            renderScriptLibraryList();
                            renderScriptTestPicker();
                            if (status) status.textContent = `Loaded ${scriptLibraryCache.length} script(s)`;
                        } catch (e) {
                            scriptLibraryCache = [];
                            renderScriptTestPicker();
                            const msg = e && e.name === 'AbortError' ? 'request timed out' : e.message;
                            setLibraryMessage(`Failed to load scripts: ${msg}`, 'danger');
                            if (status) status.textContent = `Error loading scripts: ${msg}`;
                        }
                    }

                    async function loadOnlineAgentsForScripts() {
                        const picker = document.getElementById('scriptTestAgent');
                        const status = document.getElementById('scriptTestStatus');
                        if (!picker) return;
                        picker.innerHTML = '<option value="">Loading...</option>';
                        if (status) status.textContent = 'Loading online agents...';
                        try {
                            const r = await fetchJsonWithTimeout('/api/rmm/online-agents', {cache: 'no-store'}, 10000);
                            if (!r.ok) throw new Error(`HTTP ${r.status}`);
                            const data = await r.json();
                            const agents = (data.ok && Array.isArray(data.agents)) ? data.agents : [];
                            picker.innerHTML = agents.length
                                ? agents.map(a => `<option value="${esc(a.agent_id)}">${esc(a.hostname)} (${esc(a.agent_id)})</option>`).join('')
                                : '<option value="">No online agents</option>';
                            if (status) status.textContent = agents.length ? `Loaded ${agents.length} online agent(s)` : 'No online agents available';
                        } catch (e) {
                            picker.innerHTML = '<option value="">Failed to load agents</option>';
                            const msg = e && e.name === 'AbortError' ? 'request timed out' : e.message;
                            if (status) status.textContent = `Failed to load agents: ${msg}`;
                        }
                    }

                    function clearScriptEditor() {
                        const nameEl = document.getElementById('scriptName');
                        if (!nameEl) return;
                        nameEl.value = '';
                        document.getElementById('scriptDescription').value = '';
                        document.getElementById('scriptContent').value = '';
                        document.getElementById('scriptFileType').value = '.ps1';
                        nameEl.removeAttribute('data-edit-id');
                    }

                    function editScriptLibraryItem(scriptId) {
                        const item = scriptLibraryCache.find(s => Number(s.id) === Number(scriptId));
                        if (!item) return;
                        const nameEl = document.getElementById('scriptName');
                        nameEl.value = item.name || '';
                        document.getElementById('scriptDescription').value = item.description || '';
                        document.getElementById('scriptContent').value = item.script_content || '';
                        document.getElementById('scriptFileType').value = item.file_type || '.ps1';
                        nameEl.setAttribute('data-edit-id', String(item.id));
                        document.getElementById('scriptSaveStatus').textContent = `Editing: ${item.name}`;
                    }

                    function cloneScriptLibraryItem(scriptId) {
                        const item = scriptLibraryCache.find(s => Number(s.id) === Number(scriptId));
                        if (!item) return;
                        const nameEl = document.getElementById('scriptName');
                        nameEl.value = `${item.name || 'Script'} (Copy)`;
                        document.getElementById('scriptDescription').value = item.description || '';
                        document.getElementById('scriptContent').value = item.script_content || '';
                        document.getElementById('scriptFileType').value = item.file_type || '.ps1';
                        nameEl.removeAttribute('data-edit-id');
                        document.getElementById('scriptSaveStatus').textContent = `Cloned from: ${item.name}`;
                    }

                    async function copyScriptLibraryItem(scriptId) {
                        const item = scriptLibraryCache.find(s => Number(s.id) === Number(scriptId));
                        const status = document.getElementById('scriptSaveStatus');
                        if (!item) return;
                        try {
                            await navigator.clipboard.writeText(item.script_content || '');
                            if (status) status.textContent = `Copied script content: ${item.name}`;
                        } catch (e) {
                            if (status) status.textContent = 'Clipboard copy failed. Use Edit then copy manually.';
                        }
                    }

                    async function saveScriptLibraryItem() {
                        const nameEl = document.getElementById('scriptName');
                        const status = document.getElementById('scriptSaveStatus');
                        const payload = {
                            id: nameEl.getAttribute('data-edit-id') || null,
                            name: nameEl.value.trim(),
                            description: document.getElementById('scriptDescription').value.trim(),
                            file_type: document.getElementById('scriptFileType').value,
                            script_content: document.getElementById('scriptContent').value,
                        };
                        if (!payload.name || !payload.script_content.trim()) {
                            status.textContent = 'Name and script content are required.';
                            return;
                        }
                        status.textContent = 'Saving...';
                        try {
                            const r = await fetch('/api/settings/scripts', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify(payload),
                            });
                            const data = await r.json();
                            if (!data.ok) {
                                status.textContent = data.error || 'Failed to save script';
                                return;
                            }
                            status.textContent = data.message || 'Saved';
                            await loadScriptLibrary();
                            clearScriptEditor();
                        } catch (e) {
                            status.textContent = `Save failed: ${e.message}`;
                        }
                    }

                    async function uploadScriptFile() {
                        const f = document.getElementById('scriptUploadFile').files[0];
                        const status = document.getElementById('scriptUploadStatus');
                        if (!f) {
                            status.textContent = 'Pick a file first.';
                            return;
                        }
                        const fd = new FormData();
                        fd.append('file', f);
                        status.textContent = 'Uploading...';
                        try {
                            const r = await fetch('/api/settings/scripts/upload', {method: 'POST', body: fd});
                            const data = await r.json();
                            if (!data.ok) {
                                status.textContent = data.error || 'Upload failed';
                                return;
                            }
                            status.textContent = 'Upload complete.';
                            document.getElementById('scriptUploadFile').value = '';
                            await loadScriptLibrary();
                        } catch (e) {
                            status.textContent = `Upload failed: ${e.message}`;
                        }
                    }

                    async function deleteScriptLibraryItem(scriptId) {
                        if (!confirm('Delete this script from the library?')) return;
                        try {
                            const r = await fetch(`/api/settings/scripts/${scriptId}`, {method: 'DELETE'});
                            const data = await r.json();
                            if (!data.ok) {
                                alert(data.error || 'Delete failed');
                                return;
                            }
                            await loadScriptLibrary();
                        } catch (e) {
                            alert(`Delete failed: ${e.message}`);
                        }
                    }

                    async function generateScriptWithAI() {
                        const status = document.getElementById('scriptAiStatus');
                        const prompt = document.getElementById('scriptAiPrompt').value.trim();
                        const fileType = document.getElementById('scriptFileType').value;
                        if (!prompt) {
                            status.textContent = 'Enter a prompt for AI generation.';
                            return;
                        }
                        status.textContent = 'Generating script...';
                        try {
                            const r = await fetch('/api/settings/scripts/generate', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({prompt: prompt, file_type: fileType}),
                            });
                            const data = await r.json();
                            if (!data.ok) {
                                status.textContent = data.error || 'AI generation failed';
                                return;
                            }
                            document.getElementById('scriptContent').value = data.script_content || '';
                            status.textContent = 'Generated. Review and save the script.';
                        } catch (e) {
                            status.textContent = `AI generation failed: ${e.message}`;
                        }
                    }

                    async function testSavedScript() {
                        const scriptId = document.getElementById('scriptTestScript').value;
                        const agentId = document.getElementById('scriptTestAgent').value;
                        const status = document.getElementById('scriptTestStatus');
                        const out = document.getElementById('scriptTestOutput');
                        if (!scriptId || !agentId) {
                            status.textContent = 'Select both a script and an online agent.';
                            return;
                        }
                        status.textContent = 'Running test...';
                        out.textContent = '';
                        try {
                            const r = await fetch(`/api/settings/scripts/${scriptId}/test`, {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({agent_id: agentId, timeout: 90}),
                            });
                            const data = await r.json();
                            if (!data.ok) {
                                status.textContent = data.error || 'Test failed';
                                out.textContent = [data.stdout || '', data.stderr || ''].filter(Boolean).join('\n\n');
                                await loadScriptLibrary();
                                return;
                            }
                            status.textContent = `Success (exit ${data.exit_code})`;
                            out.textContent = [data.stdout || '', data.stderr ? `STDERR:\n${data.stderr}` : ''].filter(Boolean).join('\n\n');
                            await loadScriptLibrary();
                        } catch (e) {
                            status.textContent = `Test failed: ${e.message}`;
                        }
                    }

                    function initScriptsTab() {
                        const scriptsTabButton = document.getElementById('scripts-tab');
                        if (!scriptsTabButton) return;
                        const activeSection = window.SETTINGS_CFG.activeSection;

                        if (activeSection && activeSection !== 'scripts') {
                            return;
                        }

                        scriptsTabButton.addEventListener('shown.bs.tab', function() {
                            loadScriptLibrary();
                            loadOnlineAgentsForScripts();
                        });

                        const activeTarget = document.querySelector('#settingsTabs .nav-link.active')?.getAttribute('data-bs-target');
                        loadScriptLibrary();
                        if (activeTarget === '#scripts') {
                            loadOnlineAgentsForScripts();
                        }

                        if (!scriptsAutoRefreshTimer) {
                            scriptsAutoRefreshTimer = setInterval(function() {
                                const currentActive = document.querySelector('#settingsTabs .nav-link.active')?.getAttribute('data-bs-target');
                                if (currentActive === '#scripts') {
                                    loadOnlineAgentsForScripts();
                                }
                            }, 15000);
                        }
                    }

                    window.loadScriptLibrary = loadScriptLibrary;
                    window.loadOnlineAgentsForScripts = loadOnlineAgentsForScripts;
                    window.clearScriptEditor = clearScriptEditor;
                    window.editScriptLibraryItem = editScriptLibraryItem;
                    window.cloneScriptLibraryItem = cloneScriptLibraryItem;
                    window.copyScriptLibraryItem = copyScriptLibraryItem;
                    window.saveScriptLibraryItem = saveScriptLibraryItem;
                    window.uploadScriptFile = uploadScriptFile;
                    window.deleteScriptLibraryItem = deleteScriptLibraryItem;
                    window.generateScriptWithAI = generateScriptWithAI;
                    window.testSavedScript = testSavedScript;

                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', initScriptsTab);
                    } else {
                        initScriptsTab();
                    }
                })();
                
/* ─── Block 3 ─────────────────────────────────── */

function toggleSmtpEdit() {
    const viewMode = document.getElementById('smtpViewMode');
    const editMode = document.getElementById('smtpEditMode');
    
    if (viewMode.style.display === 'none') {
        viewMode.style.display = 'block';
        editMode.style.display = 'none';
    } else {
        viewMode.style.display = 'none';
        editMode.style.display = 'block';
    }
}

function toggleSiteToken() {
    const inp = document.getElementById('siteTokenInput');
    const icon = document.getElementById('siteTokenEyeIcon');
    if (inp.type === 'password') {
        inp.type = 'text';
        icon.className = 'bi bi-eye-slash';
    } else {
        inp.type = 'password';
        icon.className = 'bi bi-eye';
    }
}

function copySiteToken() {
    const val = document.getElementById('siteTokenInput').value;
    navigator.clipboard.writeText(val).then(() => {
        const btn = event.currentTarget;
        btn.innerHTML = '<i class="bi bi-check"></i>';
        setTimeout(() => btn.innerHTML = '<i class="bi bi-clipboard"></i>', 1500);
    });
}

function regenerateSiteToken() {
    if (!confirm('Rotate the site enrollment token?\n\nExisting installers will stop enrolling new devices. Rebuild CirqueRMM.exe after rotating.')) return;
    fetch('/api/rmm/site-token/regenerate', {method: 'POST', headers: {'X-CSRFToken': document.querySelector('meta[name=csrf-token]')?.content || ''}})
        .then(r => r.ok ? location.reload() : r.text().then(t => alert('Error: ' + t)));
}

// Theme preview functionality
const themes = {
    'default': {
        name: 'Default',
        description: 'Clean, professional Bootstrap theme with familiar blue accents.',
        colors: {
            bg: '#ffffff',
            text: '#212529',
            accent: '#0d6efd'
        }
    },
    'deep-forest': {
        name: 'Deep Forest',
        description: 'Dark, sophisticated theme with forest green accents and warm tan text.',
        colors: {
            bg: '#0D0D0D',
            text: '#F2E8CF',
            accent: '#BC986A'
        }
    },
    'modern-naturalist': {
        name: 'Modern Naturalist',
        description: 'Light, natural theme with soft sand background and forest green headers.',
        colors: {
            bg: '#F5F2ED',
            text: '#1A1A1A',
            accent: '#2D4639'
        }
    },
    'heritage': {
        name: 'Heritage',
        description: 'Classic theme with hunter green hero sections and desert tan highlights.',
        colors: {
            bg: '#FAF9F6',
            text: '#000000',
            accent: '#C2B280'
        }
    }
};

function previewTheme(themeKey) {
    const theme = themes[themeKey];
    if (!theme) return;
    
    // Apply theme to body for preview
    if (themeKey === 'default') {
        document.body.removeAttribute('data-theme');
    } else {
        document.body.setAttribute('data-theme', themeKey);
    }
    
    // Update description
    const descDiv = document.getElementById('themeDescription');
    descDiv.innerHTML = `
        <div class="alert alert-info">
            <h6><i class="bi bi-info-circle"></i> ${theme.name}</h6>
            <p class="mb-2">${theme.description}</p>
            <div class="d-flex gap-2 mt-2">
                <div class="d-flex align-items-center">
                    <div style="width: 20px; height: 20px; background-color: ${theme.colors.bg}; border: 1px solid #ccc; border-radius: 3px;" class="me-1"></div>
                    <small>Background</small>
                </div>
                <div class="d-flex align-items-center">
                    <div style="width: 20px; height: 20px; background-color: ${theme.colors.text}; border: 1px solid #ccc; border-radius: 3px;" class="me-1"></div>
                    <small>Text</small>
                </div>
                <div class="d-flex align-items-center">
                    <div style="width: 20px; height: 20px; background-color: ${theme.colors.accent}; border: 1px solid #ccc; border-radius: 3px;" class="me-1"></div>
                    <small>Accent</small>
                </div>
            </div>
        </div>
    `;
}

function resetThemePreview() {
    const currentTheme = 'window.SETTINGS_CFG.currentTheme';
    document.getElementById('themeSelector').value = currentTheme;
    previewTheme(currentTheme);
}

// Initialize theme description on page load
document.addEventListener('DOMContentLoaded', function() {
    const currentTheme = document.getElementById('themeSelector').value;
    previewTheme(currentTheme);
    
    // Load license info
    loadLicenseInfo();
});

// License Management Functions
function loadLicenseInfo() {
    const container = document.getElementById('licenseInfo');
    
    fetch('/api/license')
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to fetch license information');
            }
            return response.json();
        })
        .then(data => {
            if (!data.exists) {
                container.innerHTML = `
                    <div class="alert alert-warning">
                        <i class="bi bi-exclamation-triangle"></i> No license configured
                    </div>
                    <button type="button" class="btn btn-primary" onclick="showLicenseForm()">
                        <i class="bi bi-plus-circle"></i> Add License
                    </button>
                `;
                return;
            }
            
            const lic = data.license;
            const statusClass = lic.status === 'active' ? 'success' : lic.status === 'expired' ? 'danger' : 'warning';
            const statusIcon = lic.status === 'active' ? 'check-circle' : lic.status === 'expired' ? 'x-circle' : 'exclamation-triangle';
            
            let statusBadge = `<span class="badge bg-${statusClass}"><i class="bi bi-${statusIcon}"></i> ${lic.status.toUpperCase()}</span>`;
            
            // Show grace period warning if applicable
            let gracePeriodWarning = '';
            if (lic.grace_period_ends) {
                const graceEnd = new Date(lic.grace_period_ends);
                gracePeriodWarning = `
                    <div class="alert alert-warning mt-2">
                        <i class="bi bi-exclamation-triangle"></i> <strong>Grace Period Active</strong><br>
                        License server unreachable. Grace period expires: ${graceEnd.toLocaleString()}
                    </div>
                `;
            }
            
            container.innerHTML = `
                <div class="row mb-3">
                    <div class="col-md-6">
                        <h6 class="text-muted">Status</h6>
                        <p class="mb-0">${statusBadge}</p>
                    </div>
                    <div class="col-md-6">
                        <h6 class="text-muted">License Key</h6>
                        <p class="mb-0"><code>${lic.license_key}</code></p>
                    </div>
                </div>
                <div class="row mb-3">
                    <div class="col-md-6">
                        <h6 class="text-muted">API Key</h6>
                        <p class="mb-0">${lic.api_key ? '<code>' + lic.api_key + '</code>' : '<span class="text-muted">Using server default</span>'}</p>
                    </div>
                    <div class="col-md-6">
                        <h6 class="text-muted">Device ID</h6>
                        <p class="mb-0">${lic.device_id ? '<code>' + lic.device_id + '</code>' : '<span class="text-muted">Auto-generated</span>'}</p>
                    </div>
                </div>
                ${gracePeriodWarning}
                <div class="row mb-3">
                    <div class="col-md-6">
                        <h6 class="text-muted">Company</h6>
                        <p class="mb-0">${lic.company_name || 'Not set'}</p>
                    </div>
                    <div class="col-md-6">
                        <h6 class="text-muted">Plan</h6>
                        <p class="mb-0">${lic.plan_name || 'N/A'}</p>
                    </div>
                </div>
                <div class="row mb-3">
                    <div class="col-md-6">
                        <h6 class="text-muted">Expires</h6>
                        <p class="mb-0">${lic.expiry_date ? new Date(lic.expiry_date).toLocaleDateString() : 'N/A'}</p>
                    </div>
                    <div class="col-md-6">
                        <h6 class="text-muted">Days Remaining</h6>
                        <p class="mb-0">${lic.days_remaining !== null ? lic.days_remaining + ' days' : 'N/A'}</p>
                    </div>
                </div>
                <div class="row mb-3">
                    <div class="col-md-6">
                        <h6 class="text-muted">Last Checked</h6>
                        <p class="mb-0">${lic.last_checked ? new Date(lic.last_checked).toLocaleString() : 'Never'}</p>
                    </div>
                    <div class="col-md-6">
                        <h6 class="text-muted">Check Status</h6>
                        <p class="mb-0">${lic.last_check_status || 'N/A'}</p>
                    </div>
                </div>
                <div class="d-flex gap-2">
                    <button type="button" class="btn btn-primary btn-sm" onclick="showLicenseForm()">
                        <i class="bi bi-pencil"></i> Edit License
                    </button>
                    <button type="button" class="btn btn-danger btn-sm" onclick="deleteLicense(${lic.id})">
                        <i class="bi bi-trash"></i> Remove
                    </button>
                </div>
            `;
        })
        .catch(error => {
            console.error('Error loading license:', error);
            container.innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle"></i> Error loading license information: ${error.message}
                </div>
                <button type="button" class="btn btn-primary" onclick="loadLicenseInfo()">
                    <i class="bi bi-arrow-clockwise"></i> Retry
                </button>
            `;
        });
}

function showLicenseForm() {
    document.getElementById('licenseInfo').style.display = 'none';
    document.getElementById('licenseForm').style.display = 'block';
}

function cancelEditLicense() {
    document.getElementById('licenseInfo').style.display = 'block';
    document.getElementById('licenseForm').style.display = 'none';
}

function saveLicense(event) {
    event.preventDefault();
    
    const licenseKey = document.getElementById('licenseKey').value;
    const apiKey = document.getElementById('apiKey').value;
    const deviceId = document.getElementById('deviceId').value;
    const companyName = document.getElementById('companyName').value;
    
    fetch('/api/license', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            licenseKey: licenseKey,
            apiKey: apiKey || null,
            deviceId: deviceId || null,
            companyName: companyName
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }
        
        alert('License saved successfully! Click "Verify Now" to validate it.');
        cancelEditLicense();
        loadLicenseInfo();
        
        // Clear form
        document.getElementById('licenseKey').value = '';
        document.getElementById('apiKey').value = '';
        document.getElementById('deviceId').value = '';
        document.getElementById('companyName').value = '';
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Failed to save license');
    });
}

function verifyLicense() {
    const btn = event.target.closest('button');
    const originalHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Verifying...';
    
    fetch('/api/license/verify', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        btn.disabled = false;
        btn.innerHTML = originalHTML;
        
        if (data.error) {
            alert('Verification failed: ' + data.error);
            return;
        }
        
        alert('License verification completed!');
        loadLicenseInfo();
    })
    .catch(error => {
        btn.disabled = false;
        btn.innerHTML = originalHTML;
        console.error('Error:', error);
        alert('Failed to verify license');
    });
}

function deleteLicense(licenseId) {
    if (!confirm('Are you sure you want to remove this license?')) {
        return;
    }
    
    fetch(`/api/license/${licenseId}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }
        
        alert('License removed successfully');
        loadLicenseInfo();
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Failed to remove license');
    });
}

function toggleTeamViewerFields() {
    const enabled = document.getElementById('teamviewer_enabled').checked;
    const fields = document.getElementById('teamviewerFields');
    fields.style.display = enabled ? 'block' : 'none';
}

function toggleTokenVisibility() {
    const input = document.getElementById('teamviewer_token');
    const icon = document.getElementById('toggleIcon');
    
    if (input.type === 'password') {
        input.type = 'text';
        icon.className = 'bi bi-eye-slash';
    } else {
        input.type = 'password';
        icon.className = 'bi bi-eye';
    }
}

function testConnection() {
    const token = document.getElementById('teamviewer_token').value;
    const resultDiv = document.getElementById('testResult');
    
    if (!token) {
        resultDiv.innerHTML = '<span class="text-danger"><small>Please enter a token first</small></span>';
        return;
    }
    
    resultDiv.innerHTML = '<span class="text-info"><small><i class="bi bi-hourglass-split"></i> Testing...</small></span>';
    
    fetch('/settings/teamviewer/test', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ token: token })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            const className = data.warning ? 'text-warning' : 'text-success';
            resultDiv.innerHTML = `<span class="${className}"><small><i class="bi bi-check-circle"></i> ${data.message}</small></span>`;
        } else {
            resultDiv.innerHTML = `<span class="text-danger"><small><i class="bi bi-x-circle"></i> ${data.message}</small></span>`;
        }
    })
    .catch(error => {
        resultDiv.innerHTML = `<span class="text-danger"><small><i class="bi bi-x-circle"></i> Error: ${error}</small></span>`;
    });
}

let _scriptLibraryCache = [];

function scriptEsc(v) {
    return (v || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

async function loadOnlineAgentsForScripts() {
    const picker = document.getElementById('scriptTestAgent');
    const status = document.getElementById('scriptTestStatus');
    if (!picker) return;
    picker.innerHTML = '<option value="">Loading...</option>';
    if (status) status.textContent = 'Loading online agents...';
    try {
        const r = await fetch('/api/rmm/online-agents', {cache: 'no-store'});
        const data = await r.json();
        const agents = (data.ok && data.agents) ? data.agents : [];
        picker.innerHTML = agents.length
            ? agents.map(a => `<option value="${scriptEsc(a.agent_id)}">${scriptEsc(a.hostname)} (${scriptEsc(a.agent_id)})</option>`).join('')
            : '<option value="">No online agents</option>';
        if (status) status.textContent = agents.length ? `Loaded ${agents.length} online agent(s)` : 'No online agents available';
    } catch (e) {
        picker.innerHTML = '<option value="">Failed to load agents</option>';
        if (status) status.textContent = `Failed to load agents: ${e.message}`;
    }
}

function renderScriptTestPicker() {
    const picker = document.getElementById('scriptTestScript');
    if (!picker) return;
    const options = _scriptLibraryCache.map(s =>
        `<option value="${s.id}">${scriptEsc(s.name)} ${s.file_type} ${s.is_tested ? '(tested)' : '(untested)'}</option>`
    ).join('');
    picker.innerHTML = options || '<option value="">No scripts saved</option>';
}

function renderScriptLibraryList() {
    const wrap = document.getElementById('scriptLibraryList');
    if (!wrap) return;
    if (!_scriptLibraryCache.length) {
        wrap.innerHTML = '<div class="text-muted">No scripts saved yet.</div>';
        return;
    }
    wrap.innerHTML = _scriptLibraryCache.map(s => {
        const tested = s.is_tested
            ? `<span class="badge bg-success">Tested</span>`
            : `<span class="badge bg-secondary">Untested</span>`;
        const testedMeta = s.last_tested_at
            ? `<small class="text-muted">Last test: ${new Date(s.last_tested_at).toLocaleString()}${s.last_tested_agent_id ? ' on ' + scriptEsc(s.last_tested_agent_id) : ''}</small>`
            : '<small class="text-muted">Not tested yet</small>';
        return `
            <div class="border rounded p-2 mb-2">
                <div class="d-flex justify-content-between align-items-center gap-2">
                    <div>
                        <strong>${scriptEsc(s.name)}</strong>
                        <span class="badge bg-light text-dark border ms-1">${scriptEsc(s.file_type)}</span>
                        ${tested}
                    </div>
                    <div class="d-flex gap-1">
                        <button class="btn btn-sm btn-outline-secondary" title="Clone" onclick="cloneScriptLibraryItem(${s.id})"><i class="bi bi-files"></i></button>
                        <button class="btn btn-sm btn-outline-dark" title="Copy" onclick="copyScriptLibraryItem(${s.id})"><i class="bi bi-clipboard"></i></button>
                        <button class="btn btn-sm btn-outline-primary" onclick="editScriptLibraryItem(${s.id})"><i class="bi bi-pencil"></i></button>
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteScriptLibraryItem(${s.id})"><i class="bi bi-trash"></i></button>
                    </div>
                </div>
                <div class="small mt-1">${scriptEsc(s.description || '')}</div>
                <div class="mt-1">${testedMeta}</div>
            </div>
        `;
    }).join('');
}

function setScriptLibraryMessage(message, type = 'muted') {
    const wrap = document.getElementById('scriptLibraryList');
    if (!wrap) return;
    const cls = type === 'danger' ? 'text-danger' : (type === 'warning' ? 'text-warning' : 'text-muted');
    wrap.innerHTML = `<div class="${cls}">${scriptEsc(message)}</div>`;
}

async function loadScriptLibrary() {
    const status = document.getElementById('scriptSaveStatus');
    setScriptLibraryMessage('Loading scripts...');
    if (status) status.textContent = 'Loading scripts...';
    try {
        const r = await fetch('/api/settings/scripts', {cache: 'no-store'});
        if (!r.ok) {
            throw new Error(`HTTP ${r.status}`);
        }
        const data = await r.json();
        if (!data.ok) {
            throw new Error(data.error || 'Failed to load scripts');
        }
        _scriptLibraryCache = (data.ok && data.scripts) ? data.scripts : [];
        renderScriptLibraryList();
        renderScriptTestPicker();
        if (status) status.textContent = `Loaded ${_scriptLibraryCache.length} script(s)`;
    } catch (e) {
        _scriptLibraryCache = [];
        renderScriptTestPicker();
        setScriptLibraryMessage(`Failed to load scripts: ${e.message}`, 'danger');
        if (status) status.textContent = `Error loading scripts: ${e.message}`;
    }
}

function clearScriptEditor() {
    document.getElementById('scriptName').value = '';
    document.getElementById('scriptDescription').value = '';
    document.getElementById('scriptContent').value = '';
    document.getElementById('scriptFileType').value = '.ps1';
    document.getElementById('scriptName').removeAttribute('data-edit-id');
}

function editScriptLibraryItem(scriptId) {
    const item = _scriptLibraryCache.find(s => Number(s.id) === Number(scriptId));
    if (!item) return;
    document.getElementById('scriptName').value = item.name || '';
    document.getElementById('scriptDescription').value = item.description || '';
    document.getElementById('scriptContent').value = item.script_content || '';
    document.getElementById('scriptFileType').value = item.file_type || '.ps1';
    document.getElementById('scriptName').setAttribute('data-edit-id', String(item.id));
    document.getElementById('scriptSaveStatus').textContent = `Editing: ${item.name}`;
}

function cloneScriptLibraryItem(scriptId) {
    const item = _scriptLibraryCache.find(s => Number(s.id) === Number(scriptId));
    if (!item) return;
    document.getElementById('scriptName').value = `${item.name || 'Script'} (Copy)`;
    document.getElementById('scriptDescription').value = item.description || '';
    document.getElementById('scriptContent').value = item.script_content || '';
    document.getElementById('scriptFileType').value = item.file_type || '.ps1';
    document.getElementById('scriptName').removeAttribute('data-edit-id');
    document.getElementById('scriptSaveStatus').textContent = `Cloned from: ${item.name}`;
}

async function copyScriptLibraryItem(scriptId) {
    const item = _scriptLibraryCache.find(s => Number(s.id) === Number(scriptId));
    const status = document.getElementById('scriptSaveStatus');
    if (!item) return;
    try {
        await navigator.clipboard.writeText(item.script_content || '');
        if (status) status.textContent = `Copied script content: ${item.name}`;
    } catch (e) {
        if (status) status.textContent = 'Clipboard copy failed. Use Edit then copy manually.';
    }
}

async function saveScriptLibraryItem() {
    const nameEl = document.getElementById('scriptName');
    const status = document.getElementById('scriptSaveStatus');
    const payload = {
        id: nameEl.getAttribute('data-edit-id') || null,
        name: nameEl.value.trim(),
        description: document.getElementById('scriptDescription').value.trim(),
        file_type: document.getElementById('scriptFileType').value,
        script_content: document.getElementById('scriptContent').value,
    };
    if (!payload.name || !payload.script_content.trim()) {
        status.textContent = 'Name and script content are required.';
        return;
    }
    status.textContent = 'Saving...';
    try {
        const r = await fetch('/api/settings/scripts', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const data = await r.json();
        if (!data.ok) {
            status.textContent = data.error || 'Failed to save script';
            return;
        }
        status.textContent = data.message || 'Saved';
        await loadScriptLibrary();
        clearScriptEditor();
    } catch (e) {
        status.textContent = `Save failed: ${e.message}`;
    }
}

async function uploadScriptFile() {
    const f = document.getElementById('scriptUploadFile').files[0];
    const status = document.getElementById('scriptUploadStatus');
    if (!f) {
        status.textContent = 'Pick a file first.';
        return;
    }
    const fd = new FormData();
    fd.append('file', f);
    status.textContent = 'Uploading...';
    try {
        const r = await fetch('/api/settings/scripts/upload', {method: 'POST', body: fd});
        const data = await r.json();
        if (!data.ok) {
            status.textContent = data.error || 'Upload failed';
            return;
        }
        status.textContent = 'Upload complete.';
        document.getElementById('scriptUploadFile').value = '';
        await loadScriptLibrary();
    } catch (e) {
        status.textContent = `Upload failed: ${e.message}`;
    }
}

async function deleteScriptLibraryItem(scriptId) {
    if (!confirm('Delete this script from the library?')) return;
    try {
        const r = await fetch(`/api/settings/scripts/${scriptId}`, {method: 'DELETE'});
        const data = await r.json();
        if (!data.ok) {
            alert(data.error || 'Delete failed');
            return;
        }
        await loadScriptLibrary();
    } catch (e) {
        alert(`Delete failed: ${e.message}`);
    }
}

async function generateScriptWithAI() {
    const status = document.getElementById('scriptAiStatus');
    const prompt = document.getElementById('scriptAiPrompt').value.trim();
    const fileType = document.getElementById('scriptFileType').value;
    if (!prompt) {
        status.textContent = 'Enter a prompt for AI generation.';
        return;
    }
    status.textContent = 'Generating script...';
    try {
        const r = await fetch('/api/settings/scripts/generate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({prompt: prompt, file_type: fileType}),
        });
        const data = await r.json();
        if (!data.ok) {
            status.textContent = data.error || 'AI generation failed';
            return;
        }
        document.getElementById('scriptContent').value = data.script_content || '';
        status.textContent = 'Generated. Review and save the script.';
    } catch (e) {
        status.textContent = `AI generation failed: ${e.message}`;
    }
}

async function testSavedScript() {
    const scriptId = document.getElementById('scriptTestScript').value;
    const agentId = document.getElementById('scriptTestAgent').value;
    const status = document.getElementById('scriptTestStatus');
    const out = document.getElementById('scriptTestOutput');
    if (!scriptId || !agentId) {
        status.textContent = 'Select both a script and an online agent.';
        return;
    }
    status.textContent = 'Running test...';
    out.textContent = '';
    try {
        const r = await fetch(`/api/settings/scripts/${scriptId}/test`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({agent_id: agentId, timeout: 90}),
        });
        const data = await r.json();
        if (!data.ok) {
            status.textContent = data.error || 'Test failed';
            out.textContent = [data.stdout || '', data.stderr || ''].filter(Boolean).join('\n\n');
            await loadScriptLibrary();
            return;
        }
        status.textContent = `Success (exit ${data.exit_code})`;
        out.textContent = [data.stdout || '', data.stderr ? `STDERR:\n${data.stderr}` : ''].filter(Boolean).join('\n\n');
        await loadScriptLibrary();
    } catch (e) {
        status.textContent = `Test failed: ${e.message}`;
    }
}

// Tab persistence - remember which tab was active
document.addEventListener('DOMContentLoaded', function() {
    const activeSection = window.SETTINGS_CFG.activeSection;

    if (activeSection) {
        localStorage.removeItem('settingsActiveTab');
        if (activeSection === 'scripts') {
            loadScriptLibrary();
            loadOnlineAgentsForScripts();
        }
        checkAndShowLicenseWarning();
        return;
    }

    // Check if URL has a hash for direct tab navigation (e.g., #license-tab)
    const urlHash = window.location.hash;
    if (urlHash && urlHash.startsWith('#')) {
        const targetTab = urlHash.substring(1); // Remove the #
        const tabButton = document.getElementById(targetTab);
        if (tabButton) {
            const tab = new bootstrap.Tab(tabButton);
            tab.show();
            // Show license warning if we're on the license tab
            if (targetTab === 'license-tab') {
                checkAndShowLicenseWarning();
            }
        }
    } else {
        // Restore last active tab from localStorage
        const lastActiveTab = localStorage.getItem('settingsActiveTab');
        if (lastActiveTab) {
            const tabButton = document.querySelector(`button[data-bs-target="${lastActiveTab}"]`);
            if (tabButton) {
                const tab = new bootstrap.Tab(tabButton);
                tab.show();
            }
        }
    }
    
    // Check and show license warning on page load
    checkAndShowLicenseWarning();
    
    // Save active tab to localStorage when changed
    const tabButtons = document.querySelectorAll('#settingsTabs button[data-bs-toggle="tab"]');
    tabButtons.forEach(button => {
        button.addEventListener('shown.bs.tab', function(event) {
            const target = event.target.getAttribute('data-bs-target');
            localStorage.setItem('settingsActiveTab', target);
            if (target === '#scripts') {
                loadOnlineAgentsForScripts();
                loadScriptLibrary();
            }
        });
    });

    const primeScriptsTabData = () => {
        const activeTarget = document.querySelector('#settingsTabs .nav-link.active')?.getAttribute('data-bs-target');
        const scriptsPaneActive = document.getElementById('scripts')?.classList.contains('show');
        // Always prefetch scripts once so the panel never stays stuck on "Loading..."
        loadScriptLibrary();
        if (activeTarget === '#scripts' || scriptsPaneActive) {
            loadOnlineAgentsForScripts();
        }
    };

    // Prime once after tabs settle, and keep agent list fresh while Scripts is open.
    setTimeout(primeScriptsTabData, 150);
    setInterval(() => {
        const activeTarget = document.querySelector('#settingsTabs .nav-link.active')?.getAttribute('data-bs-target');
        if (activeTarget === '#scripts') {
            loadOnlineAgentsForScripts();
        }
    }, 15000);
});

// Function to check license status and show warning banner
function checkAndShowLicenseWarning() {
    fetch('/api/license')
        .then(response => response.json())
        .then(data => {
            const warningBanner = document.getElementById('licenseWarningBanner');
            
            if (data.exists && data.license) {
                const status = data.license.status.toLowerCase();
                
                // Show warning banner only if status is expired, invalid, or suspended
                if (status === 'expired' || status === 'invalid' || status === 'suspended') {
                    warningBanner.style.display = 'block';
                } else {
                    warningBanner.style.display = 'none';
                }
            } else {
                // No license exists - show warning
                warningBanner.style.display = 'block';
            }
        })
        .catch(error => {
            console.error('Error checking license status:', error);
        });
}
