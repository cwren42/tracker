// Search functionality
document.getElementById('searchInput')?.addEventListener('input', function(e) {
    const searchTerm = e.target.value.toLowerCase();
    const rows = document.querySelectorAll('#assetsTable tbody tr');
    
    rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(searchTerm) ? '' : 'none';
    });
});

// Assign profile modal
const assignModal = document.getElementById('assignModal');
if (assignModal) {
    assignModal.addEventListener('show.bs.modal', function(event) {
        const button = event.relatedTarget;
        const assetId = button.getAttribute('data-asset-id');
        const assetName = button.getAttribute('data-asset-name');
        
        document.getElementById('modal-asset-name').textContent = assetName;
        document.getElementById('assignForm').action = `/monitoring/assign/${assetId}`;
    });
}

// Unassign profile
function unassignProfile(assetId, assetName) {
    if (confirm(`Remove monitoring profile from "${assetName}"?`)) {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = `/monitoring/unassign/${assetId}`;
        document.body.appendChild(form);
        form.submit();
    }
}
