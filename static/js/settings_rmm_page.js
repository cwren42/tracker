function copyById(id, btn) {
    navigator.clipboard.writeText(document.getElementById(id).value).then(function() {
        btn.innerHTML = '<i class="bi bi-check text-success"></i>';
        setTimeout(function() { btn.innerHTML = '<i class="bi bi-clipboard"></i>'; }, 2000);
    }).catch(function(){});
}

function toggleSiteToken() {
    const inp = document.getElementById('siteTokenInput');
    const icon = document.getElementById('siteTokenEyeIcon');
    if (inp.type === 'password') { inp.type = 'text'; icon.className = 'bi bi-eye-slash'; }
    else { inp.type = 'password'; icon.className = 'bi bi-eye'; }
}

function copySiteToken() {
    navigator.clipboard.writeText(document.getElementById('siteTokenInput').value);
    console.log('Token copied to clipboard');
}

function regenerateSiteToken() {
    if (!confirm('Regenerate the site enrollment token?\n\nThis invalidates the current MSI — rebuild before deploying to new devices.')) return;
    fetch('/api/rmm/site-token/regenerate', {method: 'POST'})
        .then(r => r.json()).then(d => {
            if (d.ok) {
                document.getElementById('siteTokenInput').value = d.token;
                alert('Token regenerated. Rebuild the MSI before deploying.');
            }
        });
}

document.addEventListener('DOMContentLoaded', function() {
    var tok  = window.SETTINGSRMMCFG.rmm_site_token;
    var base = window.SETTINGSRMMCFG.host_url.replace(/\/+$/, "");
    var installUrl = base + "/download/site-install.ps1?t=" + tok;
    var deployUrl  = base + "/download/deploy-silent.ps1?t=" + tok;
    var sslBypass = "[Net.ServicePointManager]::ServerCertificateValidationCallback={$true}; ";
    document.getElementById("ps1OneLiner").value  = sslBypass + "irm '" + installUrl + "' -UseBasicParsing | iex";
    document.getElementById("gpoOneLiner").value  = sslBypass + "irm '" + deployUrl  + "' -UseBasicParsing | iex";
    document.getElementById("intuneScriptUrl").value = deployUrl;
    document.getElementById("psremoteCmd").value  = "Invoke-Command -ComputerName PC-NAME -ScriptBlock { irm '" + deployUrl + "' | iex }";
    document.getElementById("intuneDownloadLink").href = deployUrl;
});
