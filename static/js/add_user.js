const roleDescs = {
    base_user:  'Submit tickets and track their status. No access to other modules.',
    viewer:     'Submit tickets and view all tickets. No Assets, Employees, Licenses, or Reports.',
    eagle_eyes: 'Access to the Eagle Eyes fleet monitor and ticket dashboard.',
    manager:    'View and edit assets, employees, tickets, and reports.',
    admin:      'Full access including user management and system settings.',
};
document.getElementById('role').addEventListener('change', function () {
    document.getElementById('roleDesc').textContent = roleDescs[this.value] || '';
});
// Set on load
document.getElementById('roleDesc').textContent = roleDescs[document.getElementById('role').value] || '';

function checkStrength(pw) {
    const bar = document.getElementById('strengthBar');
    const lbl = document.getElementById('strengthLabel');
    let score = 0;
    if (pw.length >= 6)  score++;
    if (pw.length >= 10) score++;
    if (/[A-Z]/.test(pw)) score++;
    if (/[0-9]/.test(pw)) score++;
    if (/[^A-Za-z0-9]/.test(pw)) score++;
    const levels = [
        {w:0,   c:'bg-secondary', t:''},
        {w:20,  c:'bg-danger',    t:'Weak'},
        {w:40,  c:'bg-warning',   t:'Fair'},
        {w:60,  c:'bg-info',      t:'Good'},
        {w:80,  c:'bg-success',   t:'Strong'},
        {w:100, c:'bg-success',   t:'Very strong'},
    ];
    const lvl = levels[Math.min(score, levels.length - 1)];
    bar.style.width = lvl.w + '%';
    bar.className = 'progress-bar ' + lvl.c;
    lbl.textContent = lvl.t;
}
