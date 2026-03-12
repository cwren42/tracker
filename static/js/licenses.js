function filterExpiringSoon() {
    // Get all rows in the table
    const rows = document.querySelectorAll('tbody tr');
    let hasExpiring = false;
    
    rows.forEach(row => {
        const statusCell = row.cells[4]; // Status column
        if (statusCell && statusCell.textContent.includes('Expiring Soon')) {
            row.style.display = '';
            hasExpiring = true;
        } else {
            row.style.display = 'none';
        }
    });
    
    if (!hasExpiring) {
        alert('No licenses expiring soon!');
        rows.forEach(row => row.style.display = '');
    } else {
        // Scroll to table
        document.querySelector('.table-responsive').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

// Enable tooltips
document.addEventListener('DOMContentLoaded', function() {
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
});
