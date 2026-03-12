let activeRole = 'all';

function setRole(btn, role) {
    activeRole = role;
    document.querySelectorAll('#roleFilter .btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    filterUsers();
}

function filterUsers() {
    const q = document.getElementById('userSearch').value.toLowerCase();
    const rows = document.querySelectorAll('#usersTable tbody tr');
    let visible = 0;
    rows.forEach(row => {
        const matchRole = activeRole === 'all' || row.dataset.role === activeRole;
        const matchText = !q || row.dataset.name.includes(q) ||
                          row.dataset.email.includes(q) || row.dataset.username.includes(q);
        const show = matchRole && matchText;
        row.style.display = show ? '' : 'none';
        if (show) visible++;
    });
    document.getElementById('noResults').style.display = (visible === 0 && document.querySelector('#usersTable')) ? '' : 'none';
}
