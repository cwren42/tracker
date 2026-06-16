function _flashCopied(btn) {
    if (!btn) return;
    btn.innerHTML = '<i class="bi bi-check text-success"></i>';
    setTimeout(function() { btn.innerHTML = '<i class="bi bi-clipboard"></i>'; }, 2000);
}

function copyById(id, btn) {
    var el = document.getElementById(id);
    if (!el) return;
    // Select the field text first — works even on non-secure (self-signed cert) origins.
    el.focus();
    el.select();
    try { el.setSelectionRange(0, 99999); } catch (e) {}
    // Primary: legacy execCommand (no secure-context requirement).
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    if (ok) { _flashCopied(btn); return; }
    // Fallback: async clipboard API (only works in a secure context).
    if (navigator.clipboard) {
        navigator.clipboard.writeText(el.value).then(function () { _flashCopied(btn); }).catch(function(){});
    }
}

function toggleSiteToken() {
    const inp = document.getElementById('siteTokenInput');
    const icon = document.getElementById('siteTokenEyeIcon');
    if (inp.type === 'password') { inp.type = 'text'; icon.className = 'bi bi-eye-slash'; }
    else { inp.type = 'password'; icon.className = 'bi bi-eye'; }
}

function copySiteToken() {
    var el = document.getElementById('siteTokenInput');
    var wasPwd = el.type === 'password';
    if (wasPwd) el.type = 'text';
    el.focus();
    el.select();
    try { el.setSelectionRange(0, 99999); } catch (e) {}
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    if (!ok && navigator.clipboard) { navigator.clipboard.writeText(el.value).catch(function(){}); }
    if (wasPwd) el.type = 'password';
}

// Render the install one-liners / URLs for a given site token. Called on page
// load and again after a token regeneration so the copy boxes never show a
// stale (now-invalidated) token.
function renderInstallSnippets(tok) {
    var base = window.SETTINGSRMMCFG.host_url.replace(/\/+$/, "");
    var installUrl = base + "/download/site-install.ps1?t=" + tok;
    var deployUrl  = base + "/download/deploy-silent.ps1?t=" + tok;
    // Force TLS 1.2 (Windows PowerShell 5.1 defaults to TLS 1.0/1.1 → "unexpected error
    // on a send") AND trust the internal cert chain. Both are required or the irm fails.
    var sslBypass = "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; [Net.ServicePointManager]::ServerCertificateValidationCallback={$true}; ";
    // Off-LAN / remote: hit the public Cloudflare host + public=1 so the served script
    // uses public URLs throughout (enroll, agent files, gateway) — no VPN needed.
    var remoteUrl = "https://tracker.cirquetools.com/download/site-install.ps1?t=" + tok + "&public=1";
    document.getElementById("ps1OneLiner").value    = sslBypass + "irm '" + installUrl + "' -UseBasicParsing | iex";
    document.getElementById("remoteOneLiner").value = sslBypass + "irm '" + remoteUrl  + "' -UseBasicParsing | iex";
    // TeamViewer 15.76+ / hardened: TV injects tv_x64.dll which breaks .NET HTTP, so the
    // irm bootstrap fails ("unexpected error on a send"). Fetch + run the script via
    // curl.exe (native libcurl, unaffected); the script itself also uses curl. -k = internal cert.
    document.getElementById("curlOneLiner").value =
        'curl.exe -sSk "' + installUrl + '" -o "$env:TEMP\\cqrmm.ps1"; powershell -ExecutionPolicy Bypass -File "$env:TEMP\\cqrmm.ps1"';
    document.getElementById("gpoOneLiner").value  = sslBypass + "irm '" + deployUrl  + "' -UseBasicParsing | iex";
    document.getElementById("intuneScriptUrl").value = deployUrl;
    document.getElementById("psremoteCmd").value  = "Invoke-Command -ComputerName PC-NAME -ScriptBlock { " + sslBypass + "irm '" + deployUrl + "' | iex }";
    document.getElementById("intuneDownloadLink").href = deployUrl;
}

function regenerateSiteToken() {
    if (!confirm('Regenerate the site enrollment token?\n\nThis invalidates the current MSI — rebuild before deploying to new devices.')) return;
    fetch('/api/rmm/site-token/regenerate', {method: 'POST'})
        .then(r => r.json()).then(d => {
            if (d.ok) {
                document.getElementById('siteTokenInput').value = d.token;
                // Refresh the one-liners so they embed the NEW token, not the
                // one captured at page load (which is now invalid).
                renderInstallSnippets(d.token);
                alert('Token regenerated. Rebuild the MSI before deploying.');
            }
        });
}

document.addEventListener('DOMContentLoaded', function() {
    renderInstallSnippets(window.SETTINGSRMMCFG.rmm_site_token);
});
