function filterControls(filter) {
    const rows = document.querySelectorAll('#controlsTable tbody tr');
    rows.forEach(row => {
        if (filter === 'all') {
            row.style.display = '';
        } else if (filter === 'automated') {
            row.style.display = row.dataset.automated === 'True' ? '' : 'none';
        } else {
            row.style.display = row.dataset.progress === filter ? '' : 'none';
        }
    });
}

function viewEvidence(controlId) {
    // TODO: Navigate to evidence viewer page
    window.location.href = `/soc2/evidence/${controlId}`;
}

function runAzureSync() {
    if (confirm('Run Azure Security sync? This will collect firewall rules, vulnerability scans, encryption settings, and security alerts from Azure. This may take several minutes.')) {
        const btn = event.target.closest('button');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Syncing Azure...';
        
        fetch('/api/soc2/azure-security-sync', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert(`Azure Security Sync complete!\n\nNSGs: ${data.nsgs}\nSecurity Alerts: ${data.alerts}\nDatabases: ${data.databases}\nStorage: ${data.storage}\nVMs: ${data.vms}\nVulnerability Scans: ${data.assessments}\nMonitor Alerts: ${data.monitor}\nNetworks: ${data.network}\n\nTotal: ${data.total_items} items`);
                location.reload();
            } else {
                alert('Azure sync failed: ' + data.error);
            }
        })
        .catch(error => {
            alert('Azure sync error: ' + error);
        })
        .finally(() => {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-cloud"></i> Azure Security Sync';
        });
    }
}

function sortTable(columnIndex) {
    const table = document.getElementById('controlsTable');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    
    // Determine sort direction
    const currentSort = table.dataset.sortColumn;
    const currentDir = table.dataset.sortDir || 'asc';
    const newDir = (currentSort == columnIndex && currentDir === 'asc') ? 'desc' : 'asc';
    
    rows.sort((a, b) => {
        const aCell = a.cells[columnIndex].textContent.trim();
        const bCell = b.cells[columnIndex].textContent.trim();
        
        if (newDir === 'asc') {
            return aCell.localeCompare(bCell);
        } else {
            return bCell.localeCompare(aCell);
        }
    });
    
    // Re-append rows in sorted order
    rows.forEach(row => tbody.appendChild(row));
    
    // Update sort indicators
    table.dataset.sortColumn = columnIndex;
    table.dataset.sortDir = newDir;
    
    // Update header icons
    const headers = table.querySelectorAll('th');
    headers.forEach((header, idx) => {
        const icon = header.querySelector('i');
        if (icon) {
            if (idx === columnIndex) {
                icon.className = newDir === 'asc' ? 'bi bi-arrow-up' : 'bi bi-arrow-down';
            } else {
                icon.className = 'bi bi-arrow-down-up';
            }
        }
    });
}

function runSync() {
    if (confirm('Run M365/Intune sync? This will collect users, devices, and software from Microsoft 365. This may take several minutes.')) {
        const btn = event.target.closest('button');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Syncing...';
        
        fetch('/api/soc2/sync', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert(`M365 Sync complete!\nUsers: ${data.users_synced}\nDevices: ${data.devices_synced}`);
                location.reload();
            } else {
                alert('Sync failed: ' + data.error);
            }
        })
        .catch(error => {
            alert('Sync error: ' + error);
        })
        .finally(() => {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> M365 Sync';
        });
    }
}
