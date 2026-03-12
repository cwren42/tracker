function filterVMs() {
    const q = document.getElementById('vmSearch').value.toLowerCase();
    document.querySelectorAll('.vm-row').forEach(row => {
        const name = row.dataset.name || '';
        const node = row.dataset.node || '';
        row.style.display = (name.includes(q) || node.includes(q)) ? '' : 'none';
    });
}

function triggerSync() {
    const btn = document.getElementById('syncBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-arrow-repeat spin"></i> Syncing…';

    const toast = new bootstrap.Toast(document.getElementById('syncToast'), {autohide: false});
    document.getElementById('syncToastTitle').textContent = 'Syncing…';
    document.getElementById('syncToastBody').textContent = 'Contacting Proxmox API…';
    document.getElementById('syncToastHeader').className = 'toast-header';
    toast.show();

    fetch('/api/proxmox/sync', {method: 'POST', headers: {'Content-Type': 'application/json'}})
        .then(r => r.json())
        .then(data => {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Sync Now';
            if (data.success) {
                document.getElementById('syncToastHeader').className = 'toast-header text-success';
                document.getElementById('syncToastTitle').textContent = 'Sync complete';
                const errs = (data.errors || []).length;
                document.getElementById('syncToastBody').textContent =
                    `Nodes: ${data.nodes_synced}, Pools: ${data.pools_synced}, ` +
                    `VMs: ${data.vms_synced}, Alerts: ${data.alerts_fired}` +
                    (errs ? `, Errors: ${errs}` : '');
                // Reload after short delay so the table updates
                setTimeout(() => location.reload(), 2000);
            } else {
                document.getElementById('syncToastHeader').className = 'toast-header text-danger';
                document.getElementById('syncToastTitle').textContent = 'Sync failed';
                document.getElementById('syncToastBody').textContent = data.error || 'Unknown error';
            }
        })
        .catch(err => {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Sync Now';
            document.getElementById('syncToastHeader').className = 'toast-header text-danger';
            document.getElementById('syncToastTitle').textContent = 'Error';
            document.getElementById('syncToastBody').textContent = err.toString();
        });
}
