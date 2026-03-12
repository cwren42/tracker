function filterByType(type) {
    const rows = document.querySelectorAll('#evidenceTable tbody tr');
    const buttons = document.querySelectorAll('.btn-group button');
    
    // Update button states
    buttons.forEach(btn => {
        btn.classList.remove('active');
        if (btn.textContent.includes(type) || (type === 'All' && btn.textContent === 'All')) {
            btn.classList.add('active');
        }
    });
    
    // Filter rows
    rows.forEach(row => {
        if (type === 'All') {
            row.style.display = '';
        } else {
            const rowType = row.getAttribute('data-type');
            row.style.display = rowType === type ? '' : 'none';
        }
    });
}

function filterBySource(source) {
    const rows = document.querySelectorAll('#evidenceTable tbody tr');
    
    // Filter rows
    rows.forEach(row => {
        const rowSource = row.getAttribute('data-source');
        row.style.display = rowSource === source ? '' : 'none';
    });
}

function sortEvidence(columnIndex) {
    const table = document.getElementById('evidenceTable');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    
    // Determine sort direction
    const currentSort = table.dataset.sortColumn;
    const currentDir = table.dataset.sortDir || 'asc';
    const newDir = (currentSort == columnIndex && currentDir === 'asc') ? 'desc' : 'asc';
    
    rows.sort((a, b) => {
        let aText = a.cells[columnIndex].textContent.trim();
        let bText = b.cells[columnIndex].textContent.trim();
        
        // Remove badge text for proper sorting
        aText = aText.replace(/Automated|ISMS Policy|Azure/g, '').trim();
        bText = bText.replace(/Automated|ISMS Policy|Azure/g, '').trim();
        
        if (newDir === 'asc') {
            return aText.localeCompare(bText);
        } else {
            return bText.localeCompare(aText);
        }
    });
    
    // Re-append rows in sorted order
    rows.forEach(row => tbody.appendChild(row));
    
    // Update sort indicators
    table.dataset.sortColumn = columnIndex;
    table.dataset.sortDir = newDir;
    
    // Update header icons
    const headers = table.querySelectorAll('th i');
    headers.forEach((icon, idx) => {
        if (idx === columnIndex) {
            icon.className = newDir === 'asc' ? 'bi bi-arrow-up' : 'bi bi-arrow-down';
        } else {
            icon.className = 'bi bi-arrow-down-up';
        }
    });
}

function generateEvidenceFiles() {
    if (!confirm('Generate evidence files for all automated evidence items? This will create Excel files that can be uploaded to StrikeGraph.')) {
        return;
    }
    
    const btn = event.target.closest('button');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Generating...';
    
    fetch('/api/soc2/generate-evidence-files', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        btn.disabled = false;
        btn.innerHTML = originalText;
        
        if (data.success) {
            alert(`Evidence files generated successfully!\n\nSuccess: ${data.stats.success}\nErrors: ${data.stats.errors}\nTotal: ${data.stats.total}\n\nPage will reload to show download buttons.`);
            location.reload();
        } else {
            alert('Error generating evidence files: ' + data.error);
        }
    })
    .catch(error => {
        btn.disabled = false;
        btn.innerHTML = originalText;
        alert('Error: ' + error);
    });
}

function generateSoftwareInventory() {
    const btn = event.target.closest('button');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Generating...';
    
    fetch('/api/soc2/generate-software-inventory', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        btn.disabled = false;
        btn.innerHTML = originalText;
        
        if (data.success) {
            alert(`Software Inventory Generated!\n\nTotal Software: ${data.total_software}\nFile: ${data.filename}\nSize: ${(data.size / 1024).toFixed(1)} KB\n\nDownloading now...`);
            
            // Trigger download
            if (data.file_path) {
                const link = document.createElement('a');
                link.href = data.file_path;
                link.download = data.filename;
                link.click();
            }
        } else {
            alert('Error generating software inventory: ' + (data.error || data.message));
        }
    })
    .catch(error => {
        btn.disabled = false;
        btn.innerHTML = originalText;
        alert('Error: ' + error);
    });
}

function downloadAllEvidence() {
    window.location.href = '/api/soc2/download-all-evidence';
}
