/* assets.html extracted JS */

// Multi-select categories enhancement
document.addEventListener('DOMContentLoaded', function() {
    const categoriesSelect = document.getElementById('categoriesSelect');
    if (categoriesSelect) {
        categoriesSelect.size = 3;
    }
    
    // Load saved filters on page load
    loadSavedFilters();
});

// Bulk Operations Functions
function toggleSelectAll(checkbox) {
    const checkboxes = document.querySelectorAll('.asset-checkbox');
    checkboxes.forEach(cb => cb.checked = checkbox.checked);
    updateBulkToolbar();
}

function updateBulkToolbar() {
    const checkboxes = document.querySelectorAll('.asset-checkbox:checked');
    const count = checkboxes.length;
    const toolbar = document.getElementById('bulkToolbar');
    const countSpan = document.getElementById('selectedCount');
    
    if (count > 0) {
        toolbar.style.display = 'block';
        countSpan.textContent = count;
    } else {
        toolbar.style.display = 'none';
        document.getElementById('selectAll').checked = false;
    }
}

function getSelectedAssetIds() {
    const checkboxes = document.querySelectorAll('.asset-checkbox:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

function clearSelection() {
    document.querySelectorAll('.asset-checkbox').forEach(cb => cb.checked = false);
    document.getElementById('selectAll').checked = false;
    updateBulkToolbar();
}

// Bulk Status Update
function bulkUpdateStatus() {
    const count = getSelectedAssetIds().length;
    document.getElementById('bulkStatusCount').textContent = count;
    const modal = new bootstrap.Modal(document.getElementById('bulkStatusModal'));
    modal.show();
}

function submitBulkStatus() {
    const assetIds = getSelectedAssetIds();
    const status = document.getElementById('bulkStatus').value;
    
    fetch('/assets/bulk/status', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            asset_ids: assetIds,
            status: status
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert(`Successfully updated ${data.count} assets`);
            location.reload();
        } else {
            alert('Error: ' + data.message);
        }
    })
    .catch(error => {
        alert('Error updating assets: ' + error);
    });
}

// Bulk Department Assignment
function bulkAssignDepartment() {
    const count = getSelectedAssetIds().length;
    document.getElementById('bulkDeptCount').textContent = count;
    const modal = new bootstrap.Modal(document.getElementById('bulkDepartmentModal'));
    modal.show();
}

function submitBulkDepartment() {
    const assetIds = getSelectedAssetIds();
    const department = document.getElementById('bulkDepartment').value;
    
    if (!department) {
        alert('Please enter a department name');
        return;
    }
    
    fetch('/assets/bulk/department', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            asset_ids: assetIds,
            department: department
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert(`Successfully assigned ${data.count} assets to ${department}`);
            location.reload();
        } else {
            alert('Error: ' + data.message);
        }
    })
    .catch(error => {
        alert('Error assigning department: ' + error);
    });
}

// Bulk Export Selected
function bulkExportSelected() {
    const assetIds = getSelectedAssetIds();
    
    // Create a form and submit it to trigger file download
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/assets/bulk/export';
    
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'asset_ids';
    input.value = JSON.stringify(assetIds);
    
    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
    document.body.removeChild(form);
}

// Bulk Delete
function bulkDelete() {
    const assetIds = getSelectedAssetIds();
    
    if (!confirm(`Are you sure you want to delete ${assetIds.length} assets? This action cannot be undone!`)) {
        return;
    }
    
    fetch('/assets/bulk/delete', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            asset_ids: assetIds
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert(`Successfully deleted ${data.count} assets`);
            location.reload();
        } else {
            alert('Error: ' + data.message);
        }
    })
    .catch(error => {
        alert('Error deleting assets: ' + error);
    });
}

// Single Asset Delete
function deleteSingleAsset(btn) {
    const assetId = btn.dataset.assetId;
    const assetName = btn.dataset.assetName;
    if (!confirm(`Delete "${assetName}"?\n\nThis action cannot be undone.`)) return;
    fetch('/assets/bulk/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset_ids: [String(assetId)] })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            location.reload();
        } else {
            alert('Error: ' + data.message);
        }
    })
    .catch(err => alert('Error deleting asset: ' + err));
}

// Saved Filters Functions
function saveCurrentFilters() {
    const form = document.getElementById('filterForm');
    const formData = new FormData(form);
    const params = new URLSearchParams(formData);
    
    // Get all filter values
    const filters = {
        search: formData.get('search') || '',
        categories: formData.getAll('categories'),
        status: formData.get('status') || '',
        purchase_from: formData.get('purchase_from') || '',
        purchase_to: formData.get('purchase_to') || '',
        warranty_status: formData.get('warranty_status') || '',
        lifecycle: formData.get('lifecycle') || ''
    };
    
    // Check if any filters are set
    const hasFilters = Object.values(filters).some(v => 
        Array.isArray(v) ? v.length > 0 : v !== ''
    );
    
    if (!hasFilters) {
        alert('No filters to save. Please set some filters first.');
        return;
    }
    
    const name = prompt('Enter a name for this saved search:');
    if (!name) return;
    
    // Get existing saved filters
    let savedFilters = JSON.parse(localStorage.getItem('assetFilters') || '[]');
    
    // Add new filter
    savedFilters.push({
        id: Date.now(),
        name: name,
        filters: filters,
        created: new Date().toISOString()
    });
    
    // Save to localStorage
    localStorage.setItem('assetFilters', JSON.stringify(savedFilters));
    
    alert('Search filter saved successfully!');
}

function loadSavedFilters() {
    const savedFilters = JSON.parse(localStorage.getItem('assetFilters') || '[]');
    const listDiv = document.getElementById('savedFiltersList');
    
    if (savedFilters.length === 0) {
        listDiv.innerHTML = '<p class="text-muted">No saved filters yet.</p>';
        return;
    }
    
    let html = '<div class="list-group">';
    savedFilters.forEach(filter => {
        const date = new Date(filter.created).toLocaleDateString();
        html += `
            <div class="list-group-item">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <strong>${filter.name}</strong>
                        <br><small class="text-muted">Saved on ${date}</small>
                    </div>
                    <div>
                        <button class="btn btn-sm btn-outline-primary" onclick="applyFilter(${filter.id})">
                            <i class="bi bi-funnel"></i> Apply
                        </button>
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteFilter(${filter.id})">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;
    });
    html += '</div>';
    
    listDiv.innerHTML = html;
}

function applyFilter(filterId) {
    const savedFilters = JSON.parse(localStorage.getItem('assetFilters') || '[]');
    const filter = savedFilters.find(f => f.id === filterId);
    
    if (!filter) return;
    
    // Build URL with filter parameters
    const params = new URLSearchParams();
    
    if (filter.filters.search) params.append('search', filter.filters.search);
    if (filter.filters.status) params.append('status', filter.filters.status);
    if (filter.filters.purchase_from) params.append('purchase_from', filter.filters.purchase_from);
    if (filter.filters.purchase_to) params.append('purchase_to', filter.filters.purchase_to);
    if (filter.filters.warranty_status) params.append('warranty_status', filter.filters.warranty_status);
    if (filter.filters.lifecycle) params.append('lifecycle', filter.filters.lifecycle);
    
    // Add multiple categories
    if (filter.filters.categories && filter.filters.categories.length > 0) {
        filter.filters.categories.forEach(cat => params.append('categories', cat));
    }
    
    // Navigate to filtered page
    window.location.href = '/assets?' + params.toString();
}

function deleteFilter(filterId) {
    if (!confirm('Delete this saved filter?')) return;
    
    let savedFilters = JSON.parse(localStorage.getItem('assetFilters') || '[]');
    savedFilters = savedFilters.filter(f => f.id !== filterId);
    localStorage.setItem('assetFilters', JSON.stringify(savedFilters));
    
    loadSavedFilters();
}

function sortTable(column) {
    const urlParams = new URLSearchParams(window.location.search);
    const currentSort = urlParams.get('sort');
    const currentDir = urlParams.get('dir') || 'asc';
    
    // Toggle direction if clicking same column, otherwise default to asc
    if (currentSort === column) {
        urlParams.set('dir', currentDir === 'asc' ? 'desc' : 'asc');
    } else {
        urlParams.set('sort', column);
        urlParams.set('dir', 'asc');
    }
    
    window.location.search = urlParams.toString();
}
