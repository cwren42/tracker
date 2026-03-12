async function testADConnection() {
    const resultEl = document.getElementById('adTestResult');
    resultEl.style.display = 'block';
    resultEl.className = 'alert alert-info';
    resultEl.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Testing connection...';
    try {
        const res = await fetch('/api/ad/test', {method: 'POST', headers: {'Content-Type': 'application/json'}});
        const data = await res.json();
        if (data.success) {
            resultEl.className = 'alert alert-success';
            resultEl.textContent = data.message || 'Connected successfully';
        } else {
            resultEl.className = 'alert alert-danger';
            resultEl.textContent = data.error || 'Connection failed';
        }
    } catch (e) {
        resultEl.className = 'alert alert-danger';
        resultEl.textContent = e?.message || 'Connection failed';
    }
}
