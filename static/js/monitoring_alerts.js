// Search functionality
document.getElementById('searchInput')?.addEventListener('input', function(e) {
    const searchTerm = e.target.value.toLowerCase();
    const alerts = document.querySelectorAll('.alert-item');
    
    alerts.forEach(alert => {
        const text = alert.textContent.toLowerCase();
        alert.style.display = text.includes(searchTerm) ? '' : 'none';
    });
});

// Resolve modal
const resolveModal = document.getElementById('resolveModal');
if (resolveModal) {
    resolveModal.addEventListener('show.bs.modal', function(event) {
        const button = event.relatedTarget;
        const alertId = button.getAttribute('data-alert-id');
        const alertTitle = button.getAttribute('data-alert-title');
        
        document.getElementById('modal-alert-title').textContent = alertTitle;
        document.getElementById('resolveForm').action = `/monitoring/alert/${alertId}/resolve`;
    });
}
