// Scroll position save/restore across filter/sort navigations
function navigateTo(url) {
    sessionStorage.setItem('emp_scroll', window.scrollY);
    window.location.href = url;
}

// View mode management
let currentView = localStorage.getItem('employeeView') || 'table';

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
    // Restore scroll position after filter/sort navigation
    const savedScroll = sessionStorage.getItem('emp_scroll');
    if (savedScroll !== null) {
        sessionStorage.removeItem('emp_scroll');
        window.scrollTo(0, parseInt(savedScroll, 10));
    }

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

    // Location filter
    document.getElementById('locationFilter').addEventListener('change', applyFilters);
    
    // Sort select
    document.getElementById('sortSelect').addEventListener('change', applyFilters);
});

function applyFilters() {
    const search = document.getElementById('searchInput').value;
    const department = document.getElementById('departmentFilter').value;
    const location = document.getElementById('locationFilter').value;
    const sort = document.getElementById('sortSelect').value;
    
    const params = new URLSearchParams(window.location.search);
    if (search) params.set('search', search); else params.delete('search');
    if (department) params.set('department', department); else params.delete('department');
    if (location) params.set('location', location); else params.delete('location');
    if (sort) params.set('sort', sort); else params.delete('sort');
    
    navigateTo(window.location.pathname + '?' + params.toString());
}

function sortBy(column) {
    const params = new URLSearchParams(window.location.search);
    const currentSort = params.get('sort') || 'name';
    const currentOrder = params.get('order') || 'asc';
    let newOrder = 'asc';
    
    if (currentSort === column) {
        newOrder = currentOrder === 'asc' ? 'desc' : 'asc';
    }
    
    params.set('sort', column);
    params.set('order', newOrder);
    
    navigateTo(window.location.pathname + '?' + params.toString());
}

function toggleSortOrder() {
    const params = new URLSearchParams(window.location.search);
    const currentOrder = params.get('order') || 'asc';
    params.set('order', currentOrder === 'asc' ? 'desc' : 'asc');
    navigateTo(window.location.pathname + '?' + params.toString());
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

function clearLocation() {
    document.getElementById('locationFilter').value = '';
    applyFilters();
}
