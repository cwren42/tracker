// View mode management
let currentView = localStorage.getItem('employeeView') || 'grid';

function setView(view) {
    currentView = view;
    localStorage.setItem('employeeView', view);
    
    // Hide all views
    document.getElementById('gridViewContainer').classList.add('d-none');
    document.getElementById('listViewContainer').classList.add('d-none');
    document.getElementById('tableViewContainer').classList.add('d-none');
    
    // Show selected view
    document.getElementById(view + 'ViewContainer').classList.remove('d-none');
    
    // Update button states
    document.querySelectorAll('.view-toggle .btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.getElementById(view + 'View').classList.add('active');
}

// Initialize view on page load
document.addEventListener('DOMContentLoaded', function() {
    setView(currentView);
    
    // View toggle buttons
    document.getElementById('gridView').addEventListener('click', () => setView('grid'));
    document.getElementById('listView').addEventListener('click', () => setView('list'));
    document.getElementById('tableView').addEventListener('click', () => setView('table'));
    
    // Search functionality with debounce
    let searchTimeout;
    document.getElementById('searchInput').addEventListener('input', function(e) {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            applyFilters();
        }, 500);
    });
    
    // Department filter
    document.getElementById('departmentFilter').addEventListener('change', applyFilters);
    
    // Sort select
    document.getElementById('sortSelect').addEventListener('change', applyFilters);
});

function applyFilters() {
    const search = document.getElementById('searchInput').value;
    const department = document.getElementById('departmentFilter').value;
    const sort = document.getElementById('sortSelect').value;
    
    const params = new URLSearchParams();
    if (search) params.append('search', search);
    if (department) params.append('department', department);
    if (sort) params.append('sort', sort);
    params.append('order', 'window.EMPLOYEESCFG.sort_order');
    
    window.location.href = 'window.EMPLOYEESCFG.employees_url?' + params.toString();
}

function sortBy(column) {
    const currentSort = 'window.EMPLOYEESCFG.sort_by';
    const currentOrder = 'window.EMPLOYEESCFG.sort_order';
    let newOrder = 'asc';
    
    if (currentSort === column) {
        newOrder = currentOrder === 'asc' ? 'desc' : 'asc';
    }
    
    const params = new URLSearchParams(window.location.search);
    params.set('sort', column);
    params.set('order', newOrder);
    
    window.location.href = 'window.EMPLOYEESCFG.employees_url?' + params.toString();
}

function toggleSortOrder() {
    const params = new URLSearchParams(window.location.search);
    const currentOrder = 'window.EMPLOYEESCFG.sort_order';
    params.set('order', currentOrder === 'asc' ? 'desc' : 'asc');
    window.location.href = 'window.EMPLOYEESCFG.employees_url?' + params.toString();
}

function clearSearch() {
    document.getElementById('searchInput').value = '';
    applyFilters();
}

function showDepartmentModal() {
    const modal = new bootstrap.Modal(document.getElementById('departmentModal'));
    modal.show();
}

function filterByAssets() {
    // Filter to show only employees with assets
    const employees = document.querySelectorAll('.employee-item');
    let hasAssets = false;
    
    employees.forEach(item => {
        const badges = item.querySelectorAll('.badge');
        const assetBadge = Array.from(badges).find(b => b.textContent.includes('') && b.classList.contains('bg-info'));
        const assetCount = assetBadge ? parseInt(assetBadge.textContent.trim()) : 0;
        
        if (assetCount > 0) {
            item.style.display = '';
            hasAssets = true;
        } else {
            item.style.display = 'none';
        }
    });
    
    // Show message if no results
    if (!hasAssets) {
        alert('No employees with assets assigned.');
        employees.forEach(item => item.style.display = '');
    }
}

function filterByLicenses() {
    // Filter to show only employees with licenses
    const employees = document.querySelectorAll('.employee-item');
    let hasLicenses = false;
    
    employees.forEach(item => {
        const badges = item.querySelectorAll('.badge');
        const licenseBadge = Array.from(badges).find(b => {
            const title = (b.getAttribute('title') || '').toLowerCase();
            return title.includes('license');
        });
        const licenseCount = licenseBadge ? parseInt(licenseBadge.textContent.trim()) : 0;
        
        if (licenseCount > 0) {
            item.style.display = '';
            hasLicenses = true;
        } else {
            item.style.display = 'none';
        }
    });
    
    // Show message if no results
    if (!hasLicenses) {
        alert('No employees with licenses assigned.');
        employees.forEach(item => item.style.display = '');
    }
}
function clearDepartment() {
    document.getElementById('departmentFilter').value = '';
    applyFilters();
}
