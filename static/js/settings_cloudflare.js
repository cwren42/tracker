function renderCloudflareStatus(status) {
    const installedBadge = document.getElementById('cfInstalledBadge');
    const statusBadge = document.getElementById('cfStatusBadge');
    const bootBadge = document.getElementById('cfBootBadge');
    const msg = document.getElementById('cfMessage');
    const stateText = document.getElementById('cfStateText');
    const toggleBtn = document.getElementById('cfToggleBtn');

    installedBadge.textContent = status.installed ? 'Yes' : 'No';
    installedBadge.className = 'badge ' + (status.installed ? 'bg-success' : 'bg-secondary');

    statusBadge.textContent = status.status || 'unknown';
    statusBadge.className = 'badge ' + (status.active ? 'bg-success' : 'bg-danger');

    stateText.textContent = status.active ? 'ON' : 'OFF';
    stateText.className = 'fw-semibold ' + (status.active ? 'text-success' : 'text-danger');

    bootBadge.textContent = status.enabled_on_boot ? 'Yes' : 'No';
    bootBadge.className = 'badge ' + (status.enabled_on_boot ? 'bg-success' : 'bg-secondary');

    msg.textContent = status.message || '';

    const turnOn = !status.active;
    toggleBtn.className = 'btn ' + (turnOn ? 'btn-success' : 'btn-danger');
    toggleBtn.innerHTML = '<i class="bi ' + (turnOn ? 'bi-play-circle' : 'bi-stop-circle') + ' me-1"></i>' + (turnOn ? 'Turn Tunnel On' : 'Turn Tunnel Off');
    toggleBtn.setAttribute('onclick', 'toggleCloudflareTunnel(' + (turnOn ? 'true' : 'false') + ')');
}

async function refreshCloudflareStatus() {
    const r = await fetch('/api/cloudflare/status');
    const d = await r.json();
    if (d.ok) {
        renderCloudflareStatus(d.status);
    }
}

async function toggleCloudflareTunnel(enable) {
    const msg = document.getElementById('cfActionMsg');
    msg.textContent = (enable ? 'Starting' : 'Stopping') + ' cloudflared...';

    try {
        const r = await fetch('/api/cloudflare/tunnel/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enable })
        });
        const d = await r.json();
        if (!d.ok) {
            msg.textContent = 'Error: ' + (d.error || 'Failed');
            if (d.status) renderCloudflareStatus(d.status);
            return;
        }
        renderCloudflareStatus(d.status);
        msg.textContent = enable ? 'Tunnel started.' : 'Tunnel stopped.';
    } catch (e) {
        msg.textContent = 'Error: ' + e.message;
    }
}

document.addEventListener('DOMContentLoaded', function() {
    refreshCloudflareStatus();
    // Keep tunnel state indicator fresh while page is open.
    setInterval(refreshCloudflareStatus, 15000);
});
