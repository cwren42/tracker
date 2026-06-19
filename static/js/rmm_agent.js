// Cirque RMM Agent JS – included by view_asset.html and rmm_section.html
                (function(){
                    const agentId = (document.getElementById('rmm-js-config')||{}).dataset?.agentId || null;
                    let _chart = null;
                    let _swData = [], _svcData = [];
                    let _savedScripts = [];
                    let _fsCurrent = null, _fsHistory = [], _fsPending = null;
                    let _eagleEnabled = false;
                    let _eagleScreenshots = true;
                    // Format any timestamp string into browser local time
                    function fmtLocal(ts){
                        if(!ts) return '—';
                        try {
                            // Date-only strings ("2026-03-09") must NOT go through Date() as UTC midnight
                            if(/^\d{4}-\d{2}-\d{2}$/.test(ts)){
                                const [y,m,d]=ts.split('-'); return `${+m}/${+d}/${y}`;
                            }
                            // Normalize space-separated datetimes to ISO T-format
                            const d = new Date(ts.replace(' ','T'));
                            if(isNaN(d)) return ts;
                            return d.toLocaleString('en-US', {month:'numeric',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true});
                        } catch(_) { return ts; }
                    }

                    /* ── helpers ──────────────────────────────── */
                    window.rmmBLToggle = function(btn){
                        const s = document.getElementById(btn.dataset.blspan);
                        const shown = btn.dataset.shown === '1';
                        s.textContent = shown ? '••••••••••••' : btn.dataset.blkey;
                        btn.textContent = shown ? 'Show' : 'Hide';
                        btn.dataset.shown = shown ? '0' : '1';
                    };
                    function el(id){ return document.getElementById(id) || {style:{},className:'',textContent:'',innerHTML:'',classList:{add:()=>{},remove:()=>{},contains:()=>false,toggle:()=>{}},disabled:false,value:'',checked:false,getContext:()=>null,scrollTop:0,focus:()=>{}}; }
                    function rmmSetStatus(online){
                        const b = el('rmm-status-badge');
                        if(online){
                            b.className='badge bg-success';
                            b.innerHTML='<i class="bi bi-circle-fill me-1" style="font-size:.55rem;"></i>Online';
                            el('rmm-shell-btn').classList.remove('disabled');
                            el('rmm-screenshot-btn').classList.remove('disabled');
                            el('rmm-update-agent-btn').classList.remove('disabled');
                        } else {
                            b.className='badge bg-danger';
                            b.innerHTML='<i class="bi bi-circle-fill me-1" style="font-size:.55rem;"></i>Offline';
                            el('rmm-shell-btn').classList.add('disabled');
                            el('rmm-screenshot-btn').classList.add('disabled');
                            el('rmm-update-agent-btn').classList.add('disabled');
                        }
                    }
                    function bar(id, pct){
                        const b = el(id);
                        if(!b) return;
                        b.style.width = pct+'%';
                        b.className = 'progress-bar ' + (pct>85?'bg-danger':pct>65?'bg-warning':'bg-'+id.split('-')[2]);
                    }
                    async function rmmSend(msg){
                        const r = await fetch(`/api/rmm/cmd/${agentId}`,{
                            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(msg)
                        });
                        return r.json().catch(()=>({ok:false}));
                    }
                    async function rmmPoll(sid, expectedTypes, timeoutMs=25000){
                        const types = Array.isArray(expectedTypes) ? expectedTypes : [expectedTypes];
                        const deadline = Date.now()+timeoutMs;
                        while(Date.now()<deadline){
                            await new Promise(r=>setTimeout(r,1200));
                            const r = await fetch(`/api/rmm/cmd-result/${agentId}/${sid}`).then(x=>x.json()).catch(()=>({ok:false}));
                            if(r.ok && r.ready && types.includes(r.event_type)) return r;
                        }
                        return null;
                    }

                    /* ── initial load ─────────────────────────── */
                    async function rmmLoad(){
                        // status
                        const s = await fetch(`/api/rmm/agent-status/${agentId}`).then(x=>x.json()).catch(()=>({online:false}));
                        rmmSetStatus(s.online);
                        // Overview summary badge
                        (function(){
                            const ob=el('ov-rmm-badge');
                            if(ob){ob.className='badge '+(s.online?'bg-success':'bg-danger');ob.innerHTML=(s.online?'<i class="bi bi-circle-fill me-1" style="font-size:.55rem;"></i>Online':'<i class="bi bi-circle-fill me-1" style="font-size:.55rem;"></i>Offline');}
                        })();
                        // telemetry
                        const t = await fetch(`/api/rmm/telemetry/${agentId}`).then(x=>x.json()).catch(()=>({ok:false}));
                        if(!t.ok){ el('rmm-no-telemetry').style.display=''; return; }
                        const d = t.telemetry;
                        el('rmm-telemetry-panel').style.display='';
                        el('rmm-no-telemetry').style.display='none';


                        // Mini cards
                        const cpu = Math.round(d.cpu_percent||0);
                        el('rmm-cpu-label').textContent = cpu+'%';
                        el('rmm-cpu-label').className = 'badge '+(cpu>85?'bg-danger':cpu>65?'bg-warning':'bg-info');
                        bar('rmm-cpu-bar', cpu);
                        if(d.cpu_name) el('rmm-cpu-name').textContent = d.cpu_name;

                        const ram = Math.round(d.ram_percent||0);
                        el('rmm-ram-label').textContent = ram+'%';
                        el('rmm-ram-label').className = 'badge '+(ram>85?'bg-danger':ram>65?'bg-warning':'bg-warning');
                        bar('rmm-ram-bar', ram);
                        if(d.ram_total_gb){
                            const used = (d.ram_total_gb - (d.ram_available_gb||0));
                            el('rmm-ram-detail').textContent = `${used.toFixed(1)} / ${(+d.ram_total_gb).toFixed(1)} GB`;
                        }

                        const disks = d.disk_json||[];
                        if(disks.length){
                            const dk = disks[0];
                            const dp = Math.round(dk.percent||0);
                            el('rmm-disk-label').textContent = dp+'%';
                            el('rmm-disk-label').className = 'badge '+(dp>90?'bg-danger':dp>75?'bg-warning':'bg-success');
                            bar('rmm-disk-bar', dp);
                            const used_gb = (dk.total_gb||0)-(dk.free_gb||0);
                            el('rmm-disk-detail').textContent = `${dk.device||dk.mountpoint||''}: ${used_gb.toFixed(0)}/${(dk.total_gb||0).toFixed(0)} GB`;
                        }

                        if(d.battery_percent!=null){
                            el('rmm-battery-col').style.display='';
                            const bp = Math.round(d.battery_percent);
                            el('rmm-battery-label').textContent = bp+'%'+(d.battery_charging?' ⚡':'');
                            el('rmm-battery-label').className='badge '+(bp<20?'bg-danger':bp<50?'bg-warning':'bg-success');
                            bar('rmm-battery-bar', bp);
                            const minLeft = d.battery_minutes_left;
                            el('rmm-battery-detail').textContent = d.battery_charging ? 'Plugged in' : (minLeft ? `${Math.floor(minLeft/60)}h ${Math.round(minLeft%60)}m left` : '');
                        }

                        // Also update Overview summary bars
                        (function(){
                            const ot=el('ov-rmm-time'); if(ot && d.captured_at) ot.textContent='Last seen: '+fmtLocal(d.captured_at);
                            const ocl=el('ov-cpu-label'); if(ocl) ocl.textContent=cpu+'%';
                            const ocb=el('ov-cpu-bar'); if(ocb){ocb.style.width=cpu+'%'; ocb.className='progress-bar '+(cpu>85?'bg-danger':cpu>65?'bg-warning':'bg-info');}
                            const orl=el('ov-ram-label'); if(orl) orl.textContent=ram+'%';
                            const orb=el('ov-ram-bar'); if(orb){orb.style.width=ram+'%'; orb.className='progress-bar '+(ram>85?'bg-danger':ram>65?'bg-warning':'bg-warning');}
                            if(disks.length){
                                const _dp=Math.round(disks[0].percent||0);
                                const odl=el('ov-disk-label'); if(odl) odl.textContent=_dp+'%';
                                const odb=el('ov-disk-bar'); if(odb){odb.style.width=_dp+'%'; odb.className='progress-bar '+(disks[0].percent>90?'bg-danger':disks[0].percent>75?'bg-warning':'bg-success');}
                            }
                        })();

                        // Network
                        const nets = d.network_json||[];
                        const _vpnKw = /vpn|tunnel|tap-|tun\d|wireguard|anyconnect|openvpn|nordvpn|expressvpn|privatevpn|pia |proton/i;
                        const netRows = nets.filter(n=>{
                            const ip = (n.ips&&n.ips[0])||n.ip||'';
                            return ip && !ip.startsWith('169.');
                        }).map(n=>{
                            const ip = (n.ips&&n.ips[0])||n.ip||'';
                            const isVpn = !n.mac || _vpnKw.test(n.interface||'');
                            const ssidBadge = n.ssid ? ` <span class="badge bg-info text-dark ms-1" style="font-size:.65rem;font-weight:500;">${n.ssid}</span>` : '';
                            const vpnBadge  = isVpn  ? ` <span class="badge bg-warning text-dark ms-1" style="font-size:.65rem;font-weight:500;">VPN</span>` : '';
                            return `<div><strong>${n.interface||n.name||'NIC'}</strong>${ssidBadge}${vpnBadge}: ${ip}</div>`;
                        }).join('')||'<span class="text-muted">No adapters</span>';
                        const pubIp = d.public_ip ? `<div class="mt-1 pt-1" style="border-top:1px solid rgba(128,128,128,.2);"><span style="opacity:.55;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;">Public IP</span> <strong>${d.public_ip}</strong></div>` : '';
                        el('rmm-net-info').innerHTML = netRows + pubIp;

                        // System
                        const up = d.uptime_seconds ? (() => {
                            const h=Math.floor(d.uptime_seconds/3600), m=Math.floor((d.uptime_seconds%3600)/60);
                            return `${h}h ${m}m`;
                        })() : '—';
                        const sysRows = [
                            ['Hostname',   d.hostname],
                            ['OS',         d.os_edition||d.os_name],
                            ['Version',    d.os_version],
                            ['Build',      d.os_build],
                            ['Domain',     d.domain],
                            ['User',       d.logged_in_user||d.last_login_user],
                            ['Uptime',     up],
                        ].filter(r=>r[1]);
                        el('rmm-sys-info').innerHTML =
                            '<table style="width:100%;border-collapse:collapse;">' +
                            sysRows.map(([k,v])=>
                                `<tr><td style="opacity:.55;white-space:nowrap;padding-right:.6rem;padding-bottom:.15rem;vertical-align:top;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;">${k}</td>`+
                                `<td style="word-break:break-word;padding-bottom:.15rem;">${v}</td></tr>`
                            ).join('') + '</table>';

                        // timestamp — display in agent local time
                        el('rmm-captured-at').textContent = fmtLocal(d.captured_at);

                        // agent version
                        if(d.agent_version){
                            el('rmm-agent-version-badge').textContent = 'v'+d.agent_version;
                            el('rmm-agent-version').textContent = d.agent_version;
                            el('rmm-agent-version-line').style.display='';
                        }

                        // Hardware tab
                        rmmFillHw(d);

                        // Security tab
                        rmmFillSec(d);

                        // System info tab
                        rmmFillSysInfo(d);

                        // Per-disk Storage card
                        rmmFillStorage(d);

                        // Saved tested scripts list (scripts tab)
                        if (document.getElementById('rmm-saved-script-select')) {
                            rmmLoadSavedScripts();
                        }

                        // Eagle Eyes toggle — sync state on every page load
                        rmmEagleLoad();
                    }

                    function rmmRefreshTelemetry(){ rmmLoad(); }

                    async function rmmInstallTray(){
                        const btn = el('rmm-tray-install-btn');
                        if(!agentId){ alert('No RMM agent linked to this asset.'); return; }
                        btn.disabled = true;
                        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Running…';
                        const done = () => { btn.disabled=false; btn.innerHTML='<i class="bi bi-display"></i> Install Tray'; };
                        try {
                        const psCode = `$out = [System.Collections.Generic.List[string]]::new()
function L($m){ $out.Add($m) }
L('=== Tray Installer ===')
L('User: ' + $env:USERNAME)
L('Agent: ' + $env:RMM_AGENT_ID)
$tray = 'C:\\CirqueRMM\\tray.py'
if (Test-Path $tray) { L("tray.py exists OK") } else { L("MISSING: $tray"); $out -join "\`n"; exit 1 }
$trayLog = 'C:\\CirqueRMM\\tray.log'
if (Test-Path $trayLog) {
    L('--- tray.log (last 20 lines) ---')
    Get-Content $trayLog -Tail 20 | ForEach-Object { L("  $_") }
    L('--- end tray.log ---')
} else { L('tray.log not found (tray never started)') }
$pyw = $null
$candidates = @(
    (Join-Path (Split-Path (Get-Command python.exe -EA SilentlyContinue | Select-Object -Exp Source)) 'pythonw.exe'),
    (Get-ChildItem 'C:\\Users\\*\\AppData\\Local\\Programs\\Python\\Python*\\pythonw.exe' -EA SilentlyContinue | Select-Object -First 1 -Exp FullName),
    (Get-ChildItem 'C:\\Program Files\\Python*\\pythonw.exe' -EA SilentlyContinue | Select-Object -First 1 -Exp FullName),
    (Get-ChildItem 'C:\\Python*\\pythonw.exe' -EA SilentlyContinue | Select-Object -First 1 -Exp FullName)
) | Where-Object { $_ -and (Test-Path $_) }
if ($candidates) { $pyw = $candidates[0]; L("pythonw: $pyw") } else { L('FATAL: No pythonw.exe found') }
$py = (Get-Command python.exe -EA SilentlyContinue | Select-Object -Exp Source)
if (-not $py) { L('FATAL: python.exe not found in PATH'); $out -join "\`n"; exit 1 }
L("python.exe: $py")
L('--- pip install pystray pillow ---')
& $py -m pip install --quiet pystray pillow 2>&1 | ForEach-Object { L("  $_") }
L('--- import test ---')
$test = & $py -c "import pystray; from PIL import Image; print('OK pystray=' + pystray.__version__)" 2>&1
L("  $test")
L('--- session info ---')
$sess = & qwinsta 2>$null | Select-String 'Active'
L("  qwinsta Active: $sess")
$wmiUser = (Get-WmiObject Win32_ComputerSystem -EA SilentlyContinue).UserName
L("  WMI UserName: $wmiUser")
$sessId = [System.Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()
$activeSession = (Get-WmiObject -Class Win32_LogonSession -EA SilentlyContinue | Where-Object {$_.LogonType -eq 2} | Select-Object -First 1)
L("  Interactive sessions: " + (@(Get-WmiObject -Class Win32_LogonSession -EA SilentlyContinue | Where-Object {$_.LogonType -eq 2}).Count))
L('--- killing old tray ---')
Get-WmiObject Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*tray.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue; L("  Killed PID $($_.ProcessId)") }
Start-Sleep 1
if ($pyw) {
    L('--- launching tray via schtask ---')
    $username = $wmiUser -replace '.*\\\\',''
    if (-not $username) { $username = ($sess -replace '.*>','').Trim() -split '\\s+' | Select-Object -Index 1 }
    L("  Target user: $username")
    if ($username) {
        $a = New-ScheduledTaskAction -Execute $pyw -Argument 'C:\\CirqueRMM\\tray.py' -WorkingDirectory 'C:\\CirqueRMM'
        $p = New-ScheduledTaskPrincipal -UserId $username -LogonType Interactive -RunLevel Limited
        $regErr = $null
        Register-ScheduledTask -TaskName 'CirqueTrayLaunch' -Action $a -Principal $p -Force -ErrorVariable regErr | Out-Null
        if ($regErr) { L("  ERROR registering task: $regErr") } else {
          Start-ScheduledTask -TaskName 'CirqueTrayLaunch' -ErrorAction SilentlyContinue
          L('  Task registered and started — check taskbar in 3 seconds')
        }
    } else { L('  WARN: could not determine username, skipping task') }
} else { L('Cannot launch: no pythonw.exe') }
L('=== Done ===')
$out -join "\`n" `;
                        const r = await rmmSend({type:'run_script', shell:'powershell', code:psCode, timeout:120});
                        if(!r.ok){ done(); alert('Agent offline: '+(r.error||'?')); return; }
                        const res = await rmmPoll(r.session_id, 'script_result', 125000);
                        done();
                        const out = res ? ((res.data.stdout||'') + (res.data.stderr ? '\nSTDERR: '+res.data.stderr : '')).trim() : '(no response)';
                        alert('Install Tray output (exit ' + (res?res.data.exit_code:'?') + '):\n\n' + out);
                        } catch(e) { done(); alert('Error: '+e.message); }
                    }
                    async function rmmRestartAgent(){
                        const btn = el('rmm-restart-agent-btn');
                        if(!confirm('Restart the RMM agent service on this machine?')) return;
                        btn.disabled = true;
                        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Restarting…';
                        const r = await rmmSend({type:'restart_agent'});
                        if(!r.ok){ btn.disabled=false; btn.innerHTML='<i class="bi bi-bootstrap-reboot"></i> Restart Agent'; alert('Failed: '+(r.error||'Agent offline')); return; }
                        // Wait for ack then poll for reconnect
                        const ack = await rmmPoll(r.session_id, 'restart_agent_ack', 8000);
                        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Waiting…';
                        // Poll until agent comes back online (up to 60s)
                        let back = false;
                        for(let i=0;i<20;i++){
                            await new Promise(res=>setTimeout(res,3000));
                            const s = await fetch(`/api/rmm/agent-status/${agentId}`).then(x=>x.json()).catch(()=>({online:false}));
                            if(s.online){ back=true; break; }
                        }
                        btn.disabled = false;
                        btn.innerHTML = '<i class="bi bi-bootstrap-reboot"></i> Restart Agent';
                        if(back){ rmmLoad(); } else { alert('Agent restarted but has not reconnected yet. Refresh in a moment.'); }
                    }

                    async function rmmUpdateAgent(){
                        const btn = el('rmm-update-agent-btn');
                        if(!confirm('Force the agent to check for and install any available update now?')) return;
                        btn.disabled = true;
                        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Updating…';
                        const r = await rmmSend({type:'update_now'});
                        if(!r.ok){ btn.disabled=false; btn.innerHTML='<i class="bi bi-cloud-download"></i> Update Agent'; alert('Failed: '+(r.error||'Agent offline')); return; }
                        // Poll for update_result — agent will disconnect if it actually updated
                        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Waiting…';
                        const res = await rmmPoll(r.session_id, 'update_result', 30000);
                        // Whether or not we got ack, poll for reconnect (agent restarts on update)
                        let back = false;
                        for(let i=0;i<25;i++){
                            await new Promise(rv=>setTimeout(rv,3000));
                            const s = await fetch(`/api/rmm/agent-status/${agentId}`).then(x=>x.json()).catch(()=>({online:false}));
                            if(s.online){ back=true; break; }
                        }
                        btn.disabled = false;
                        btn.innerHTML = '<i class="bi bi-cloud-download"></i> Update Agent';
                        if(back){
                            rmmLoad();
                            alert(res&&res.updated ? 'Agent updated and reconnected.' : 'Agent is already up to date.');
                        } else {
                            alert('Update sent. Agent may still be restarting — refresh in a moment.');
                        }
                    }

                    // Re-identify a repurposed/renamed box's agent IN PLACE: server renames
                    // the rmm_agent row + re-keys all agent_id-keyed tables, then pushes a
                    // PowerShell env-swap + service restart to the OLD-id agent so it
                    // reconnects under the new id with the same token. No re-enroll.
                    async function rmmReidentify(){
                        const link = document.getElementById('rmm-reidentify-btn');
                        if(!link) return;
                        const assetId = link.dataset.assetId;
                        const oldId   = link.dataset.oldId || agentId || '';
                        const newId   = link.dataset.newId || '';
                        if(!assetId){ alert('No asset id.'); return; }
                        if(newId && newId === oldId){
                            alert(`Agent id already matches the hostname (${oldId}). Nothing to do.`);
                            return;
                        }
                        const tgt = newId || '(computed from hostname)';
                        if(!confirm(`Re-identify this agent?\n\n  ${oldId}  ->  ${tgt}\n\n`
                            +`This renames the agent in place (keeps history + token) and tells `
                            +`the box to swap its service env var and restart. The agent will `
                            +`briefly disconnect and reconnect under the new id.`)) return;
                        try{
                            const r = await fetch('/api/rmm/admin/reidentify-agent',{
                                method:'POST', headers:{'Content-Type':'application/json'},
                                body:JSON.stringify({asset_id: parseInt(assetId,10),
                                                     new_id: newId || undefined})
                            }).then(x=>x.json()).catch(()=>({ok:false,error:'request failed'}));
                            if(!r.ok){
                                alert('Re-identify failed: '+(r.error||'unknown error'));
                                return;
                            }
                            if(r.noop){
                                alert(r.message || `Agent id already correct (${r.new_id}).`);
                                return;
                            }
                            const rk = r.rekeyed || {};
                            const lines = Object.keys(rk).sort().map(k=>`  ${k}: ${rk[k]}`).join('\n');
                            const dd = r.dispatch || {};
                            const dispMsg = dd.ok
                                ? (dd.delivered === 'queue'
                                    ? 'Env-swap QUEUED (agent offline) — runs on next check-in.'
                                    : 'Env-swap + restart sent to the box.')
                                : ('Env-swap dispatch FAILED: '+(dd.error||'unknown')
                                   +'\nThe DB rename succeeded; re-run once the box is reachable.');
                            alert(`Re-identified ${r.old_id} -> ${r.new_id}\n\nRows re-keyed:\n`
                                  +lines+`\n\n`+dispMsg+`\n\nReloading…`);
                            location.reload();
                        }catch(e){
                            alert('Re-identify error: '+e.message);
                        }
                    }

                    /* ── hardware tab ─────────────────────────── */
                    function rmmFillHw(d){
                        const set = (id,v) => { const e=el(id); if(e) e.textContent=v||'—'; };
                        set('rmm-hw-vendor', d.vendor||d.bios_manufacturer);
                        const modelStr = [d.model_name, d.bios_date?`(BIOS ${d.bios_date})`:''].filter(Boolean).join(' ');
                        set('rmm-hw-model',  d.model_name);
                        set('rmm-hw-serial', d.serial_number);
                        set('rmm-hw-mb',     d.motherboard);
                        const biosStr = [d.bios_version, d.bios_date].filter(Boolean).join(' — ');
                        set('rmm-hw-bios',   biosStr||d.bios_version);
                        const gpus = d.gpu||[];
                        set('rmm-hw-gpu', gpus.map(g=>g.name+(g.vram_gb?` (${g.vram_gb} GB)`:'')).join('\n'));
                        set('rmm-hw-sound', (d.sound_card||'').split(/,\s*/).join('\n'));
                        const nets = (d.network_json||[]).filter(n=>n.mac&&n.mac!='00:00:00:00:00:00');
                        set('rmm-hw-macs', nets.map(n=>`${n.interface||''}: ${n.mac}`).join('\n'));
                    }

                    /* ── security tab ─────────────────────────── */
                    function rmmFillSec(d){
                        const sec = d.security||{};
                        const setEl = (id,v) => { const e=el(id); if(e) e.textContent=v||'—'; };
                        setEl('rmm-sec-os', d.os_edition||d.os_name||d.os_version);

                        // sec.av / sec.fw / sec.as are arrays of {name, active, updated}
                        function secBadge(arr){
                            if(!arr||!arr.length) return '—';
                            return arr.map(p=>{
                                const ok=p.active; const upd=p.updated;
                                const st=ok?(upd?'✔ Active':'⚠ Active/Outdated'):'✘ Inactive';
                                return `${p.name} (${st})`;
                            }).join(', ');
                        }
                        setEl('rmm-sec-av', secBadge(sec.av));
                        setEl('rmm-sec-fw', secBadge(sec.fw));
                        setEl('rmm-sec-as', secBadge(sec.as||sec['as']));
                        const ls = sec.last_scan;
                        setEl('rmm-sec-lastscan', ls ? `${ls.type||''} — ${fmtLocal(ls.time||'')}`.trim() : '—');
                    }

                    /* ── system info tab ───────────────────── */
                    function rmmFillSysInfo(d){
                        const si = d.sysinfo || {};
                        const setH = (id,v) => { const e=el(id); if(e) e.innerHTML=v||'—'; };

                        // BitLocker
                        if(si.bitlocker && si.bitlocker.length){
                            let _blIdx = 0;
                            setH('rmm-si-bitlocker', si.bitlocker.map(v=>{
                                const ok = v.status==='FullyEncrypted' && v.protected;
                                const label = ok ? 'Encrypted' : (v.status || 'Not Encrypted');
                                const badge = ok
                                    ? '<span class="badge bg-success"><i class="bi bi-lock-fill me-1"></i>'+label+'</span>'
                                    : '<span class="badge bg-danger"><i class="bi bi-unlock-fill me-1"></i>'+label+'</span>';
                                const method = v.method ? ' <small class="text-muted">'+v.method+'</small>' : '';
                                let keyHtml = '';
                                if(v.keys && v.keys.length){
                                    keyHtml = v.keys.map(k=>{
                                        const idx = _blIdx++;
                                        const spanId = 'bl-key-'+idx;
                                        return '<div class="mt-1" style="font-size:.78rem;">'
                                            +'<span class="text-muted me-1">Recovery Key:</span>'
                                            +'<span id="'+spanId+'" style="font-family:monospace;letter-spacing:.05em;">••••••••••••</span>'
                                            +' <button class="btn btn-link btn-sm p-0 ms-1" style="font-size:.72rem;text-decoration:none;"'
                                            +' data-blspan="'+spanId+'" data-blkey="'+k+'" data-shown="0"'
                                            +' onclick="rmmBLToggle(this)">Show</button>'
                                            +'</div>';
                                    }).join('');
                                }
                                return '<div><strong>'+v.drive+'</strong> '+badge+method+keyHtml+'</div>';
                            }).join('<div class="mt-1 border-top" style="border-color:rgba(128,128,128,.15)!important;"></div>'));
                        }

                        // TPM
                        if(si.tpm){
                            const t = si.tpm;
                            const ok = t.present && t.enabled;
                            const badge = ok ? '<span class="badge bg-success">OK</span>' : '<span class="badge bg-warning text-dark">Issue</span>';
                            setH('rmm-si-tpm', badge+' v'+(t.version||'?')+(t.enabled?'':' (disabled)'));
                        }

                        // Windows Activation
                        if(si.windows_licensed !== undefined){
                            setH('rmm-si-licensed', si.windows_licensed
                                ? '<span class="badge bg-success">Licensed</span>'
                                : '<span class="badge bg-danger">Not Activated</span>');
                        }

                        // RDP
                        if(si.rdp_enabled !== undefined){
                            setH('rmm-si-rdp', si.rdp_enabled
                                ? '<span class="badge bg-info text-dark">Enabled</span>'
                                : '<span class="badge bg-secondary">Disabled</span>');
                        }

                        // Power Plan
                        if(si.power_plan){ setH('rmm-si-power', si.power_plan); }

                        // Last Windows Update
                        if(si.last_wu && si.last_wu.date){
                            const title = si.last_wu.title
                                ? '<br><small class="text-muted" style="font-size:.72rem;">'+si.last_wu.title+'</small>' : '';
                            setH('rmm-si-lastwu', fmtLocal(si.last_wu.date)+title);
                        }

                        // (Local Admins removed — replaced by AD Groups, loaded server-side via loadAdGroups())

                        // Printers
                        if(si.printers && si.printers.length){
                            setH('rmm-si-printers', si.printers.map(p=>
                                '<div>'+(p.default?'<i class="bi bi-star-fill text-warning me-1" title="Default"></i>':'')+p.name+'</div>'
                            ).join(''));
                        }

                        // (Mapped Drives removed — those are per-user, not machine-level)

                        // USB / Devices — exclude disk drives (shown in the Storage card)
                        if(si.usb_devices && si.usb_devices.length){
                            const devs = si.usb_devices.filter(u=>(u.class||'')!=='DiskDrive');
                            setH('rmm-si-usb', devs.length ? devs.map(u=>'<div style="break-inside:avoid;">'+u.name+'</div>').join('') : '<span class="text-muted">—</span>');
                        }

                        // Startup Apps
                        if(si.startup && si.startup.length){
                            setH('rmm-si-startup', si.startup.map(s=>
                                '<div><strong>'+s.name+'</strong>'+(s.location||s.user?' <small class="text-muted">('+( s.location||s.user)+')</small>':'')+'</div>'
                            ).join(''));
                        }

                        // Pending Reboot
                        if(si.reboot_pending !== undefined){
                            setH('rmm-si-reboot', si.reboot_pending
                                ? '<span class="badge bg-warning text-dark"><i class="bi bi-arrow-repeat me-1"></i>Reboot Required</span>'
                                : '<span class="badge bg-success">No</span>');
                        }

                        // Default Browser
                        if(si.default_browser){ setH('rmm-si-browser', si.default_browser); }

                        // DNS Servers
                        if(si.dns_servers && si.dns_servers.length){
                            setH('rmm-si-dns', si.dns_servers.map(s=>'<span class="badge bg-secondary me-1 font-monospace">'+s+'</span>').join(''));
                        }

                        // GP Last Refresh — hide the field entirely when there's no data
                        if(si.gp_last_refresh){ setH('rmm-si-gp', fmtLocal(si.gp_last_refresh)); }
                        else { const w=document.getElementById('rmm-si-gp-wrap'); if(w) w.style.display='none'; }

                        // Disk Health (SMART)
                        if(si.disk_health && si.disk_health.length){
                            setH('rmm-si-diskhealth', si.disk_health.map(d=>{
                                const ok = (d.health||'').toLowerCase()==='healthy';
                                const badge = ok
                                    ? '<span class="badge bg-success">Healthy</span>'
                                    : '<span class="badge bg-danger">'+(d.health||'Unknown')+'</span>';
                                return '<div>'+badge+' <span class="text-muted" style="font-size:.78rem;">'+(d.name||'')+(d.size_gb?' – '+d.size_gb+' GB':'')+(d.type?' ('+d.type+')':'')+'</span></div>';
                            }).join(''));
                        }

                        // Last BSOD / Crashes
                        if(si.last_bsod && si.last_bsod.length){
                            setH('rmm-si-bsod', si.last_bsod.map(ev=>
                                '<div class="mb-1"><span class="badge bg-danger me-1">ID '+ev.id+'</span>'
                                +'<small class="text-muted">'+fmtLocal(ev.time)+'</small>'
                                +(ev.msg?'<div style="font-size:.72rem;opacity:.75;">'+ev.msg+'</div>':'')+'</div>'
                            ).join(''));
                        } else if(si.last_bsod !== undefined){
                            setH('rmm-si-bsod', '<span class="text-success">None in last 30 days</span>');
                        }

                        // Monitors
                        if(si.monitors && si.monitors.length){
                            setH('rmm-si-monitors', si.monitors.map((m,i)=>
                                '<div><strong>Monitor '+(i+1)+'</strong> '+(m.model||'')+(m.serial?' <small class="text-muted">S/N: '+m.serial+'</small>':'')+'</div>'
                            ).join(''));
                        }

                        // Windows Update Channel
                        if(si.wu_channel){ setH('rmm-si-wuchannel', si.wu_channel); }

                        // Screen Lock Timeout
                        if(si.screen_lock){
                            const sec = si.screen_lock.timeout_sec || 0;
                            const mins = sec ? (sec < 60 ? sec+'s' : Math.round(sec/60)+'m') : 'Not set';
                            const secure = si.screen_lock.secure;
                            setH('rmm-si-screenlock', mins + (sec ? (secure
                                ? ' <span class="badge bg-success ms-1">Locked on resume</span>'
                                : ' <span class="badge bg-warning text-dark ms-1">No password on resume</span>') : ''));
                        }

                        // Listening Ports
                        if(si.open_ports && si.open_ports.length){
                            const _wkPorts = {21:'FTP',22:'SSH',23:'Telnet',25:'SMTP',53:'DNS',80:'HTTP',
                                110:'POP3',135:'RPC',139:'NetBIOS',143:'IMAP',443:'HTTPS',445:'SMB',
                                3306:'MySQL',3389:'RDP',5432:'PostgreSQL',5900:'VNC',5985:'WinRM',
                                8080:'HTTP-alt',8443:'HTTPS-alt',47001:'WinRM'};
                            const rows = si.open_ports.slice().sort((a,b)=>(a.port||0)-(b.port||0)).map(p=>{
                                const addr = p.addr||'';
                                const localOnly = (addr==='127.0.0.1' || addr==='::1');  // not externally reachable
                                const svc = _wkPorts[p.port] || '';
                                const portCell = '<code>'+p.port+'</code>' + (p.port===3389 ? ' <span class="badge bg-warning text-dark" style="font-size:.58rem;">RDP</span>' : '');
                                return '<tr'+(localOnly?' class="text-muted"':'')+'>'
                                    + '<td style="white-space:nowrap;">'+portCell+'</td>'
                                    + '<td>'+svc+'</td>'
                                    + '<td>'+(p.process||'')+'</td>'
                                    + '<td><small>'+addr+(localOnly?' <span title="local-only">🔒</span>':'')+'</small></td></tr>';
                            }).join('');
                            setH('rmm-si-ports',
                                '<div style="max-height:230px;overflow-y:auto;">'
                                + '<table class="table table-sm table-hover mb-0" style="font-size:.76rem;">'
                                + '<thead><tr><th>Port</th><th>Service</th><th>Process</th><th>Bind</th></tr></thead>'
                                + '<tbody>'+rows+'</tbody></table></div>'
                                + '<small class="text-muted" style="font-size:.68rem;">'+si.open_ports.length+' listening · greyed = localhost-only</small>');
                        }
                    }

                    /* ── per-disk storage card ────────────────── */
                    function rmmFillStorage(d){
                        const section = document.getElementById('rmm-storage-section');
                        const wrap = document.getElementById('rmm-storage-cards');
                        if(!wrap){ return; }
                        const si = d.sysinfo || {};
                        const phys = (si.disk_health || []);        // physical disks (model/type/health/serial/size_gb)
                        const vols = (d.disk_json || []);           // volumes (mountpoint/total_gb/free_gb/percent)
                        const bl   = (si.bitlocker || []);          // per-drive bitlocker {drive, status, protected}

                        if(!phys.length && !vols.length){ return; }   // container stays empty when no disks

                        const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
                        const fmtGB = n => (n==null?'—':(+n).toFixed(n<10?1:0)+' GB');

                        // BitLocker lookup by drive letter (normalise "C:" / "C:\\" → "C")
                        function blFor(letter){
                            if(!letter) return null;
                            const L = letter.replace(/[:\\\/]/g,'').toUpperCase();
                            return bl.find(b => (b.drive||'').replace(/[:\\\/]/g,'').toUpperCase()===L) || null;
                        }
                        function blBadge(letter){
                            const b = blFor(letter);
                            if(!b) return '';
                            const on = (b.status==='FullyEncrypted' && b.protected);
                            return on
                                ? ' <span class="badge bg-success" style="font-size:.6rem;"><i class="bi bi-lock-fill me-1"></i>BitLocker</span>'
                                : ' <span class="badge bg-secondary" style="font-size:.6rem;"><i class="bi bi-unlock me-1"></i>No BitLocker</span>';
                        }
                        function healthBadge(h){
                            const ok = (h||'').toLowerCase()==='healthy';
                            return ok
                                ? '<span class="badge bg-success" style="font-size:.62rem;">Healthy</span>'
                                : '<span class="badge bg-danger" style="font-size:.62rem;">'+esc(h||'Unknown')+'</span>';
                        }
                        function volBlock(v){
                            const letter = v.mountpoint||v.device||'';
                            const total = v.total_gb||0, free = v.free_gb||0, used = total-free;
                            const pct = Math.round(v.percent|| (total?used/total*100:0));
                            const barColor = pct>90?'bg-danger':pct>75?'bg-warning':'bg-success';
                            return '<div class="mt-2" style="font-size:.78rem;">'
                                +'<div class="d-flex justify-content-between align-items-center">'
                                +'<span><strong>'+esc(letter)+'</strong>'+blBadge(letter)+'</span>'
                                +'<span class="text-muted">'+fmtGB(used)+' / '+fmtGB(total)+' ('+fmtGB(free)+' free)</span>'
                                +'</div>'
                                +'<div class="progress mt-1" style="height:6px;"><div class="progress-bar '+barColor+'" style="width:'+pct+'%;"></div></div>'
                                +'</div>';
                        }

                        let html = '';
                        if(phys.length){
                            // Common case: 1 physical disk ↔ its volumes. We can't reliably map
                            // which volume sits on which physical disk from this data, so when
                            // there's a single physical disk we attach ALL volumes to it; with
                            // multiple physical disks we list the disks, then show all volumes
                            // in a shared sub-section (adjacent rows) rather than forcing a join.
                            const singleDisk = phys.length===1;
                            phys.forEach((p,i)=>{
                                const typeBadge = p.type ? '<span class="badge bg-info text-dark" style="font-size:.6rem;">'+esc(p.type)+'</span>' : '';
                                let body = '<div class="d-flex align-items-center gap-2 flex-wrap mb-1">'
                                    +'<strong style="font-size:.82rem;">Disk '+i+'</strong>'
                                    +typeBadge+' '+healthBadge(p.health)
                                    +(p.size_gb?'<span class="text-muted" style="font-size:.76rem;">'+fmtGB(p.size_gb)+'</span>':'')
                                    +'</div>';
                                body += '<div style="font-size:.76rem;">'+esc(p.name||'Unknown model')+'</div>';
                                if(p.serial){ body += '<div class="text-muted" style="font-size:.72rem;">S/N: <code>'+esc(p.serial)+'</code></div>'; }
                                if(singleDisk){ vols.forEach(v=>{ body += volBlock(v); }); }
                                html += '<div class="col-md-4"><div class="p-2 rounded h-100" style="border:1px solid rgba(128,128,128,.2);">'+body+'</div></div>';
                            });
                            if(!singleDisk && vols.length){
                                let volHtml = '<div class="p-2 rounded" style="border:1px solid rgba(128,128,128,.2);">'
                                    +'<div class="field-label mb-1">Volumes</div>';
                                vols.forEach(v=>{ volHtml += volBlock(v); });
                                volHtml += '</div>';
                                html += '<div class="col-12">'+volHtml+'</div>';
                            }
                        } else {
                            // No physical-disk data (older agent / non-Windows) — show volumes only.
                            let volHtml = '<div class="p-2 rounded" style="border:1px solid rgba(128,128,128,.2);">';
                            vols.forEach(v=>{ volHtml += volBlock(v); });
                            volHtml += '</div>';
                            html += '<div class="col-12">'+volHtml+'</div>';
                        }
                        wrap.innerHTML = html;
                    }

                    /* ── AV scan ──────────────────────────────── */
                    async function rmmAvScan(type){
                        const st = el('rmm-scan-status'), out = el('rmm-scan-out'), wrap = el('rmm-scan-out-wrap');
                        st.textContent = 'Sending scan request…';
                        const code = type==='full'
                            ? `Start-MpScan -ScanType FullScan; Write-Output "Scan complete"`
                            : `Start-MpScan -ScanType QuickScan; Write-Output "Scan complete"`;
                        const r = await rmmSend({type:'run_script', shell:'powershell', code, timeout:600});
                        if(!r.ok){ st.textContent='Error: '+(r.error||'failed'); return; }
                        st.textContent = `${type} scan launched… polling for result`;
                        const res = await rmmPoll(r.session_id, 'script_result', 660000);
                        const _d=new Date(),_p=n=>String(n).padStart(2,'0'); const now=`${_d.getFullYear()}-${_p(_d.getMonth()+1)}-${_p(_d.getDate())}T${_p(_d.getHours())}:${_p(_d.getMinutes())}:${_p(_d.getSeconds())}`;
                        await fetch(`/api/rmm/last-scan/${agentId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scan_type:type,scan_time:now})});
                        if(res){
                            wrap.style.display='';
                            out.textContent=(res.data.stdout||'')+(res.data.stderr?'\nSTDERR:\n'+res.data.stderr:'');
                            st.textContent = `Scan finished (exit ${res.data.exit_code})`;
                            el('rmm-sec-lastscan').textContent = `${type} — ${now}`;
                        } else {
                            st.textContent='Timed out waiting for scan result';
                        }
                    }

                    /* ── metrics ──────────────────────────────── */
                    async function rmmLoadMetrics(){
                        const data = await fetch(`/api/rmm/metrics-history/${agentId}?hours=24`).then(x=>x.json()).catch(()=>({ok:false}));
                        if(!data.ok||!data.data||!data.data.length){
                            // Empty-state lives in the metrics tab-pane body, not a (nonexistent) #rmm-tab-metrics.
                            const mc=el('rmm-metrics-chart');
                            const body=mc&&mc.closest?mc.closest('.card-body'):null;
                            if(body) body.innerHTML='<p class="text-muted mb-0">No metrics history yet.</p>';
                            return;
                        }
                        if(typeof Chart==='undefined'){
                            const mc=el('rmm-metrics-chart');
                            const body=mc&&mc.closest?mc.closest('.card-body'):null;
                            if(body) body.innerHTML='<p class="text-danger mb-0">Chart library failed to load.</p>';
                            return;
                        }
                        const rows=data.data,
                              labels=rows.map(r=>{ const d=new Date(r.ts); return d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',hour12:true}); }),
                              cpuVals=rows.map(r=>r.cpu||0), ramVals=rows.map(r=>r.ram||0);
                        const ctx=el('rmm-metrics-chart');
                        if(!ctx) return;
                        if(_chart) _chart.destroy();
                        _chart = new Chart(ctx,{
                            type:'line',
                            data:{labels,datasets:[
                                {label:'CPU %',data:cpuVals,borderColor:'#0dcaf0',tension:.3,fill:false,pointRadius:0},
                                {label:'RAM %',data:ramVals,borderColor:'#ffc107',tension:.3,fill:false,pointRadius:0},
                            ]},
                            options:{scales:{y:{min:0,max:100}},plugins:{legend:{position:'bottom'}},animation:false}
                        });
                    }

                    /* ── availability / session events ────────── */
                    async function rmmLoadAvailability(){
                        // Eagle eyes config
                        rmmEagleLoad();
                        // Availability
                        const av = await fetch(`/api/rmm/availability/${agentId}?limit=50`).then(x=>x.json()).catch(()=>({ok:false}));
                        const avEl = el('rmm-avail-content');
                        if(av.ok && av.events && av.events.length){
                            avEl.innerHTML = av.events.map(e=>{
                                const on=e.event==='online', ts=fmtLocal(e.ts);
                                return `<div class="d-flex align-items-center gap-2 mb-1" style="font-size:.8rem;">
                                    <span class="badge ${on?'bg-success':'bg-secondary'}" style="width:60px;">${on?'Online':'Offline'}</span>
                                    <span class="text-muted">${ts}</span></div>`;
                            }).join('');
                        } else {
                            avEl.innerHTML='<span class="text-muted" style="font-size:.8rem;">No availability events.</span>';
                        }
                        // Session events
                        rmmLoadSessionEvents(false);
                    }

                    async function rmmLoadSessionEvents(reset){
                        const days = el('rmm-sev-days').value;
                        const se = await fetch(`/api/rmm/session-events/${agentId}?days=${days}`).then(x=>x.json()).catch(()=>({ok:false}));
                        const seEl = el('rmm-sev-content');
                        const icons={logon:'person-check',logoff:'person-dash',lock:'lock',unlock:'unlock',sleep:'moon',wake:'sun'};
                        if(se.ok && se.events && se.events.length){
                            seEl.innerHTML='<table class="table table-sm mb-0"><thead><tr><th>Event</th><th>User</th><th>Time</th></tr></thead><tbody>'+
                                se.events.map(e=>{
                                    const ic=icons[e.type]||'circle'; const ts=fmtLocal(e.time||e.ts||'');
                                    return `<tr><td><i class="bi bi-${ic} me-1"></i>${e.type||e.event}</td><td>${e.user||''}</td><td>${ts}</td></tr>`;
                                }).join('')+'</tbody></table>';
                        } else {
                            seEl.innerHTML='<span class="text-muted">No session events.</span>';
                        }
                    }

                    /* ── eagle eyes ───────────────────────────── */
                    async function rmmEagleLoad(){
                        const cfg = await fetch(`/api/rmm/eagle-eyes/${agentId}`).then(x=>x.json()).catch(()=>({ok:false}));
                        if(!cfg.ok) return;
                        _eagleEnabled = !!cfg.enabled;
                        _eagleScreenshots = cfg.screenshots_enabled !== false;
                        const tog = el('rmm-eagle-toggle');
                        if(tog) tog.checked = _eagleEnabled;
                        const ssRow = el('rmm-eagle-screenshots-row');
                        if(ssRow) ssRow.style.display = _eagleEnabled ? '' : 'none';
                        const ssTog = el('rmm-eagle-screenshots-toggle');
                        if(ssTog){ ssTog.checked = _eagleScreenshots; ssTog.disabled = !_eagleEnabled; }
                        const dashBtn = el('rmm-eagle-dash-btn');
                        if(dashBtn){
                            dashBtn.style.display = _eagleEnabled?'':'none';
                            dashBtn.href = `/rmm/eagle-eyes/${agentId}`;
                        }
                    }
                    async function rmmEagleToggle(enabled){
                        if(!agentId){ alert('No RMM agent linked to this asset.'); return; }
                        const r = await fetch(`/api/rmm/eagle-eyes/${agentId}`,{
                            method:'POST', headers:{'Content-Type':'application/json'},
                            body:JSON.stringify({enabled, screenshots_enabled: _eagleScreenshots})
                        }).then(x=>x.json()).catch(()=>({ok:false}));
                        if(!r.ok){ alert('Failed to save Eagle Eyes setting.'); return; }
                        _eagleEnabled = enabled;
                        const tog = el('rmm-eagle-toggle');
                        if(tog) tog.checked = enabled;
                        const ssRow = el('rmm-eagle-screenshots-row');
                        if(ssRow) ssRow.style.display = enabled ? '' : 'none';
                        const ssTog = el('rmm-eagle-screenshots-toggle');
                        if(ssTog) ssTog.disabled = !enabled;
                        const dashBtn = el('rmm-eagle-dash-btn');
                        if(dashBtn) dashBtn.style.display = enabled?'':'none';
                    }
                    async function rmmEagleScreenshotsToggle(enabled){
                        if(!agentId){ alert('No RMM agent linked to this asset.'); return; }
                        const r = await fetch(`/api/rmm/eagle-eyes/${agentId}`,{
                            method:'POST', headers:{'Content-Type':'application/json'},
                            body:JSON.stringify({enabled: _eagleEnabled, screenshots_enabled: enabled})
                        }).then(x=>x.json()).catch(()=>({ok:false}));
                        if(!r.ok){ alert('Failed to save screenshot setting.'); return; }
                        _eagleScreenshots = enabled;
                        const ssTog = el('rmm-eagle-screenshots-toggle');
                        if(ssTog) ssTog.checked = enabled;
                    }
                    // NOTE: availability/session-events now live on the RMM ▸ Activity tab
                    // (#tab-rmm-avail), wired from view_asset.html. The old #tab-history
                    // wiring was removed — its duplicate IDs made getElementById grab the
                    // wrong (hidden) node and the Activity tab hung on "Loading…".
                    document.querySelectorAll('[data-bs-target="#tab-software"]').forEach(btn=>{
                        btn.addEventListener('shown.bs.tab', ()=>rmmLoadSoftware());
                    });
                    document.querySelectorAll('[data-bs-target="#tab-scripts"]').forEach(btn=>{
                        btn.addEventListener('shown.bs.tab', ()=>rmmLoadSavedScripts());
                    });
                    // Re-identify Agent (Manage ▸ RMM dropdown)
                    (function(){
                        const ri = document.getElementById('rmm-reidentify-btn');
                        if(ri) ri.addEventListener('click', (ev)=>{ ev.preventDefault(); rmmReidentify(); });
                    })();
                    // Rotate Power chevron with collapse state
                    const powerEl = document.getElementById('powerSubItems');
                    if(powerEl){
                        const chev = document.getElementById('powerChevron');
                        powerEl.addEventListener('show.bs.collapse',()=>{ if(chev) chev.style.transform='rotate(90deg)'; });
                        powerEl.addEventListener('hide.bs.collapse',()=>{ if(chev) chev.style.transform=''; });
                    }

                    /* ── patches ──────────────────────────────── */
                    let _pendingUpdates = [];
                    async function rmmLoadPatches(){
                        // Pending updates
                        const pu = await fetch(`/api/rmm/pending-updates/${agentId}`).then(x=>x.json()).catch(()=>({ok:false}));
                        const puEl = el('rmm-pending-content');
                        _pendingUpdates = (pu.ok && pu.updates)||[];
                        if(_pendingUpdates.length){
                            puEl.innerHTML='<table class="table table-sm table-hover mb-0"><thead class="table-light sticky-top"><tr>'
                                +'<th><input type="checkbox" id="rmm-chk-all" onchange="rmmChkAll(this.checked)"></th>'
                                +'<th>Title</th><th>Severity</th><th>Size</th><th>Reboot</th></tr></thead><tbody>'
                                +_pendingUpdates.map((u,i)=>{
                                    const sev={Critical:'danger',Important:'warning',Moderate:'info',Low:'secondary'}[u.severity]||'secondary';
                                    return `<tr><td><input type="checkbox" class="rmm-update-chk" data-idx="${i}" onchange="rmmChkChanged()"></td>`
                                        +`<td style="font-size:.78rem;">${u.title}<br><small class="text-muted">${(u.kb_ids||[]).join(', ')}</small></td>`
                                        +`<td><span class="badge bg-${sev}">${u.severity||'?'}</span></td>`
                                        +`<td>${u.size_mb?u.size_mb.toFixed(0)+' MB':'?'}</td>`
                                        +`<td>${u.reboot_required?'<i class="bi bi-arrow-clockwise text-warning"></i>':''}</td></tr>`;
                                }).join('')+'</tbody></table>';
                            el('rmm-approve-btn').disabled=false;
                        } else {
                            puEl.innerHTML='<p class="text-muted mb-0">No pending updates.</p>';
                        }

                        // Deployment jobs
                        const jobs = await fetch(`/api/rmm/patch-jobs/${agentId}`).then(x=>x.json()).catch(()=>({ok:false}));
                        const jEl = el('rmm-jobs-content');
                        const jobList = (jobs.ok&&jobs.jobs)||[];
                        el('rmm-jobs-updated').textContent = jobList.length?`${jobList.length} job(s)`:'';
                        if(jobList.length){
                            jEl.innerHTML='<table class="table table-sm mb-0"><thead><tr><th>ID</th><th>Status</th><th>Updates</th><th>Queued</th><th></th></tr></thead><tbody>'+
                                jobList.map(j=>{
                                    const sc={queued:'secondary',deploying:'primary',completed:'success',failed:'danger'}[j.status]||'secondary';
                                    return `<tr><td>${j.id}</td><td><span class="badge bg-${sc}">${j.status}</span></td>`
                                        +`<td>${(j.titles||[]).slice(0,2).join(', ')+(j.titles&&j.titles.length>2?'…':'')}</td>`
                                        +`<td style="font-size:.72rem;">${(j.approved_at||j.created_at||'').slice(0,10)}</td>`
                                        +`<td>${j.status==='queued'||j.status==='failed'?`<button class="btn btn-xs btn-outline-primary py-0 px-1" style="font-size:.7rem;" onclick="rmmDeployJob(${j.id})">Deploy</button>`:''}</td></tr>`;
                                }).join('')+'</tbody></table>';
                        } else { jEl.innerHTML='<p class="text-muted mb-0">No deployment jobs.</p>'; }

                        // Installed patches (expanded)
                        const pi = await fetch(`/api/rmm/patches/${agentId}`).then(x=>x.json()).catch(()=>({ok:false}));
                        const piEl = el('rmm-patches-content');
                        const patches=(pi.ok&&pi.patches)||[];
                        if(patches.length){
                            piEl.innerHTML='<table class="table table-sm mb-0"><thead><tr><th>KB</th><th>Description</th><th>Installed</th></tr></thead><tbody>'+
                                patches.map(p=>`<tr><td><code>${p.id}</code></td><td style="font-size:.78rem;">${p.description||''}</td><td>${fmtLocal(p.installed_on||'')}</td></tr>`
                                ).join('')+'</tbody></table>';
                        } else { piEl.innerHTML='<p class="text-muted mb-0">No patch data.</p>'; }
                    }
                    function rmmChkAll(v){ document.querySelectorAll('.rmm-update-chk').forEach(c=>c.checked=v); rmmChkChanged(); }
                    function rmmChkChanged(){ el('rmm-approve-btn').disabled=!document.querySelector('.rmm-update-chk:checked'); }
                    async function rmmApprovePatches(){
                        const idxs=[...document.querySelectorAll('.rmm-update-chk:checked')].map(c=>+c.dataset.idx);
                        const selected=idxs.map(i=>_pendingUpdates[i]).filter(Boolean);
                        if(!selected.length) return;
                        const r=await fetch(`/api/rmm/patch-jobs/${agentId}`,{
                            method:'POST', headers:{'Content-Type':'application/json'},
                            body:JSON.stringify({update_ids:selected.map(u=>u.update_id),kb_ids:selected.flatMap(u=>u.kb_ids||[]),titles:selected.map(u=>u.title)})
                        }).then(x=>x.json()).catch(()=>({ok:false}));
                        if(r.ok){ rmmLoadPatches(); } else { alert('Failed: '+(r.error||'unknown')); }
                    }
                    async function rmmScanPatches(){
                        const btn = el('rmm-scan-btn');
                        if(btn){ btn.disabled=true; btn.innerHTML='<span class="spinner-border spinner-border-sm" style="width:.7rem;height:.7rem;"></span>'; }
                        try {
                            const r = await fetch(`/api/rmm/cmd/${agentId}`, {
                                method:'POST', headers:{'Content-Type':'application/json'},
                                body: JSON.stringify({type:'request_patch_scan'})
                            }).then(x=>x.json()).catch(()=>({ok:false}));
                            if(!r.ok){ alert('Scan failed: agent may be offline'); return; }
                            // Online WUA search can take 1-3 min; poll every 30s up to 3 min
                            el('rmm-pending-content').innerHTML='<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>Scanning for updates (may take 1-2 min)…</span>';
                            let prevCount = (await fetch(`/api/rmm/pending-updates/${agentId}`).then(x=>x.json()).catch(()=>({}))).updates?.length ?? -1;
                            for (let i = 0; i < 6; i++) {
                                await new Promise(res=>setTimeout(res, 30000));
                                const check = await fetch(`/api/rmm/pending-updates/${agentId}`).then(x=>x.json()).catch(()=>({}));
                                const nowCount = check.updates?.length ?? -1;
                                if (nowCount !== prevCount) break; // results changed, stop waiting
                            }
                            await rmmLoadPatches();
                        } finally {
                            if(btn){ btn.disabled=false; btn.innerHTML='<i class="bi bi-arrow-clockwise me-1"></i>Scan'; }
                        }
                    }
                    async function rmmDeployJob(jobId){
                        const r=await fetch(`/api/rmm/patch-jobs/${agentId}/${jobId}/deploy`,{
                            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})
                        }).then(x=>x.json()).catch(()=>({ok:false}));
                        if(r.ok){ setTimeout(rmmLoadPatches,2000); } else { alert('Deploy failed: '+(r.error||'unknown')); }
                    }

                    /* ── software ─────────────────────────────── */
                    async function rmmLoadSoftware(){
                        if(_swData.length){ rmmRenderSoftware(); return; }
                        el('rmm-sw-content').innerHTML='<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>Loading…</span>';
                        const r=await fetch(`/api/rmm/software/${agentId}`).then(x=>x.json()).catch(()=>({ok:false}));
                        _swData=(r.ok&&r.software)||[];
                        el('rmm-sw-count').textContent=_swData.length||'';
                        rmmRenderSoftware();
                    }
                    async function rmmRefreshSoftware(){ _swData=[]; await rmmLoadSoftware(); }
                    async function rmmRescanSoftware(){
                        // Query agent via PowerShell registry scan for live software list
                        const ps = [
                            "$p=@(",
                            "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',",
                            "'HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',",
                            "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*');",
                            "$p|ForEach-Object{Get-ItemProperty $_ -EA SilentlyContinue}",
                            "|Where-Object{$_.DisplayName -and $_.DisplayName.Trim()}",
                            "|Select-Object @{N='name';E={$_.DisplayName}},@{N='version';E={$_.DisplayVersion}},@{N='publisher';E={$_.Publisher}},@{N='install_date';E={$_.InstallDate}}",
                            "|Sort-Object name",
                            "|ConvertTo-Json -Compress"
                        ].join('');
                        const sent = await rmmSend({type:'run_script', shell:'powershell', code:ps, timeout:30});
                        if(!sent.ok){ await rmmRefreshSoftware(); return; }
                        const ev = await rmmPoll(sent.session_id, 'script_result', 35000);
                        if(!ev || !ev.data || !ev.data.stdout){ await rmmRefreshSoftware(); return; }
                        try {
                            let parsed = JSON.parse(ev.data.stdout.trim());
                            if(!Array.isArray(parsed)) parsed = [parsed];
                            _swData = parsed.filter(s=>s.name);
                            el('rmm-sw-count').textContent = _swData.length||'';
                            rmmRenderSoftware();
                        } catch(_e){ await rmmRefreshSoftware(); }
                    }
                    function rmmRenderSoftware(){
                        const q=(el('rmm-sw-filter')||{}).value||'';
                        const rows=_swData.filter(s=>!q||`${s.name}${s.publisher}${s.version}`.toLowerCase().includes(q.toLowerCase()));
                        el('rmm-sw-content').innerHTML = rows.length
                            ? '<table class="table table-sm table-hover mb-0"><thead class="table-light sticky-top"><tr><th>Name</th><th>Version</th><th>Publisher</th><th></th></tr></thead><tbody>'
                              +rows.map(s=>`<tr><td style="font-size:.78rem;">${s.name||''}</td><td style="font-size:.76rem;">${s.version||''}</td>`
                                +`<td style="font-size:.76rem;">${s.publisher||''}</td>`
                                +`<td><button class="btn btn-outline-danger py-0 px-1" style="font-size:.68rem;" onclick='rmmUninstallSw(${JSON.stringify(s.name||"")},this)'><i class="bi bi-trash"></i></button></td></tr>`
                                ).join('')+'</tbody></table>'
                            : '<span class="text-muted">No matches.</span>';
                    }
                    function rmmFilterSoftware(){ rmmRenderSoftware(); }
                    function rmmSwToggle(panel){
                        ['winget','msi'].forEach(p=> el(`rmm-sw-${p}-panel`).style.display=p===panel&&el(`rmm-sw-${p}-panel`).style.display==='none'?'':'none');
                    }
                    async function rmmUninstallSw(name, btn){
                        if(!confirm(`Uninstall "${name}"?`)) return;
                        const row = btn.closest('tr');
                        btn.disabled = true;
                        btn.className = 'btn rmm-btn-undoing py-0 px-2';
                        btn.style.fontSize = '.68rem';
                        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Uninstalling…';

                        const sent = await rmmSend({type:'sw_uninstall', name});
                        if(!sent.ok){
                            btn.disabled=false; btn.className='btn btn-outline-danger py-0 px-1'; btn.style.fontSize='.68rem'; btn.innerHTML='<i class="bi bi-trash"></i>';
                            alert('Failed: '+(sent.error||'unknown')); return;
                        }

                        // Poll for install_done (agent uses same event for install+uninstall)
                        const deadline = Date.now()+120000;
                        let done = null;
                        while(Date.now()<deadline){
                            await new Promise(r=>setTimeout(r,2000));
                            const ev = await fetch(`/api/rmm/cmd-result/${agentId}/${sent.session_id}`).then(x=>x.json()).catch(()=>({ok:false}));
                            if(ev.ok && ev.ready && ev.event_type==='install_done'){ done=ev; break; }
                        }

                        if(done && done.data && done.data.success){
                            row.style.transition='opacity .5s';
                            row.style.opacity='0';
                            setTimeout(()=>{ row.remove(); }, 500);
                            _swData = _swData.filter(s=>s.name!==name);
                            el('rmm-sw-count').textContent = _swData.length||'';
                        } else {
                            btn.disabled=false; btn.className='btn btn-outline-danger py-0 px-1'; btn.style.fontSize='.68rem'; btn.innerHTML='<i class="bi bi-trash"></i>';
                            alert('Uninstall failed or timed out.');
                        }
                    }
                    async function rmmWingetSearch(){
                        const term=el('rmm-winget-q').value.trim(); if(!term) return;
                        const res=el('rmm-winget-results'); res.innerHTML='Searching…';
                        const r=await rmmSend({type:'winget_search',term});
                        if(!r.ok){ res.innerHTML='Error: '+(r.error||'failed'); return; }
                        const data=await rmmPoll(r.session_id,'winget_search_result',30000);
                        if(!data){ res.innerHTML='<span class="text-danger">Timed out.</span>'; return; }
                        const results=data.data.results||[];
                        if(data.data.error){ res.innerHTML=`<span class="text-danger">${data.data.error}</span>`; return; }
                        res.innerHTML=results.length
                            ?'<table class="table table-sm mb-0"><thead><tr><th>ID</th><th>Name</th><th>Version</th><th></th></tr></thead><tbody>'
                              +results.map(p=>`<tr><td style="font-size:.75rem;"><code>${p.id}</code></td><td>${p.name}</td><td>${p.version||'?'}</td>`
                                +`<td><button class="btn btn-sm btn-outline-success py-0 px-1" style="font-size:.72rem;border-radius:50px;" onclick='rmmWingetInstall(${JSON.stringify(p.id)},this)'>Install</button></td></tr>`
                              ).join('')+'</tbody></table>'
                            :'<span class="text-muted">No results.</span>';
                    }
                    async function rmmWingetInstall(pkg, btn){
                        if(!confirm(`Install ${pkg}?`)) return;

                        // Save original state
                        const origClass = btn.className;
                        const origHtml  = btn.innerHTML;
                        const origStyle = btn.getAttribute('style')||'';

                        // → Installing state: pill fills up left→right
                        btn.disabled = true;
                        btn.className = 'btn rmm-btn-progress py-0 px-3';
                        btn.style.cssText = 'font-size:.72rem;border-radius:50px;min-width:100px;';
                        btn.style.setProperty('--fill','5%');
                        btn.innerHTML = '<span style="position:relative;z-index:1;"><span class="spinner-border spinner-border-sm me-1"></span>Installing…</span>';

                        // Animate fill percentage every 3 s (caps at 88% until done)
                        let fillPct = 5;
                        const fillTimer = setInterval(()=>{
                            fillPct = Math.min(fillPct + (fillPct < 50 ? 6 : 2), 88);
                            btn.style.setProperty('--fill', fillPct+'%');
                        }, 3000);

                        // Note: agent key is 'id', not 'package_id'
                        const sent = await rmmSend({type:'winget_install', id: pkg});
                        if(!sent.ok){
                            clearInterval(fillTimer);
                            btn.disabled=false; btn.className=origClass; btn.setAttribute('style',origStyle); btn.innerHTML=origHtml;
                            alert('Error: '+(sent.error||'failed')); return;
                        }

                        // Poll for install_done (streaming install_chunk events in between)
                        const deadline = Date.now()+300000; // 5 min max
                        let done = null;
                        while(Date.now()<deadline){
                            await new Promise(r=>setTimeout(r,2000));
                            const ev = await fetch(`/api/rmm/cmd-result/${agentId}/${sent.session_id}`).then(x=>x.json()).catch(()=>({ok:false}));
                            if(!ev.ok) continue;
                            // Show last chunk text inside the button while waiting
                            if(ev.ready && ev.event_type==='install_chunk' && ev.data && ev.data.text){
                                const short = ev.data.text.slice(0,28)+(ev.data.text.length>28?'…':'');
                                btn.innerHTML=`<span style="position:relative;z-index:1;"><span class="spinner-border spinner-border-sm me-1"></span>${short}</span>`;
                            }
                            if(ev.ready && ev.event_type==='install_done'){ done=ev; break; }
                        }

                        clearInterval(fillTimer);
                        // Jump fill to 100%
                        btn.style.setProperty('--fill','100%');
                        btn.classList.remove('rmm-btn-progress');

                        await new Promise(r=>setTimeout(r,400)); // brief flash at 100%

                        if(done && done.data && done.data.success){
                            btn.className='btn rmm-btn-done-ok py-0 px-3';
                            btn.style.cssText='font-size:.72rem;border-radius:50px;min-width:100px;';
                            btn.innerHTML='<i class="bi bi-check-circle-fill me-1"></i>Installed';
                            // Rescan software via agent PS registry query
                            rmmRescanSoftware();
                        } else {
                            btn.className='btn rmm-btn-done-err py-0 px-3';
                            btn.style.cssText='font-size:.72rem;border-radius:50px;min-width:100px;';
                            btn.innerHTML='<i class="bi bi-x-circle me-1"></i>Failed';
                            // Reset after 5 s so user can retry
                            setTimeout(()=>{
                                btn.disabled=false; btn.className=origClass;
                                btn.setAttribute('style',origStyle); btn.innerHTML=origHtml;
                            }, 5000);
                        }
                    }
                    async function rmmMsiInstall(){
                        const file=el('rmm-msi-file').files[0];
                        if(!file){ alert('Pick a file first.'); return; }
                        if(file.size>80*1024*1024){ alert('File too large (max 80 MB). Use the File Transfer tab to upload large installers, then run via Scripts tab.'); return; }
                        el('rmm-install-out-wrap').style.display=''; el('rmm-install-status').textContent='Uploading…';
                        const ar=new Promise((res,rej)=>{ const rd=new FileReader(); rd.onload=()=>res(rd.result); rd.onerror=rej; rd.readAsDataURL(file); });
                        const dataUrl=await ar;
                        const b64=dataUrl.split(',')[1];
                        const destPath=`C:\\Windows\\Temp\\${file.name}`;
                        const ur=await rmmSend({type:'file_upload',path:destPath,data:b64});
                        if(!ur.ok){ el('rmm-install-status').textContent='Upload failed: '+(ur.error||''); return; }
                        const upResult=await rmmPoll(ur.session_id,'file_upload_result',60000);
                        if(!upResult||!upResult.data.success){ el('rmm-install-status').textContent='Upload failed.'; return; }
                        el('rmm-install-status').textContent='Running installer…';
                        const args=el('rmm-msi-args').value.trim();
                        const isMsi=file.name.toLowerCase().endsWith('.msi');
                        const code=isMsi
                            ?`Start-Process msiexec -ArgumentList '/i "${destPath}" /qn ${args}' -Wait; Write-Output "Done"`
                            :`Start-Process "${destPath}" -ArgumentList "${args}" -Wait; Write-Output "Done"`;
                        const sr=await rmmSend({type:'run_script',shell:'powershell',code,timeout:300});
                        if(!sr.ok){ el('rmm-install-status').textContent='Script failed.'; return; }
                        const sres=await rmmPoll(sr.session_id,'script_result',320000);
                        if(sres){ el('rmm-install-out').textContent=sres.data.stdout||'(no output)'; el('rmm-install-status').textContent='Finished.'; }
                        else { el('rmm-install-status').textContent='Timed out.'; }
                    }

                    /* ── scripts ──────────────────────────────── */
                    async function rmmLoadSavedScripts(){
                        const sel = el('rmm-saved-script-select');
                        if(!sel || !sel.tagName) return;
                        sel.innerHTML = '<option value="">Loading tested scripts...</option>';
                        try {
                            const r = await fetch('/api/rmm/scripts/tested').then(x=>x.json()).catch(()=>({ok:false}));
                            _savedScripts = (r.ok && r.scripts) ? r.scripts : [];
                            if(!_savedScripts.length){
                                sel.innerHTML = '<option value="">No tested scripts saved</option>';
                                el('rmm-saved-script-meta').textContent = 'Create and test scripts in Settings -> Scripts.';
                                return;
                            }
                            sel.innerHTML = _savedScripts.map(s=>
                                `<option value="${s.id}">${s.name} ${s.file_type}</option>`
                            ).join('');
                            rmmSavedScriptChanged();
                            sel.onchange = rmmSavedScriptChanged;
                        } catch(_e){
                            sel.innerHTML = '<option value="">Failed to load scripts</option>';
                        }
                    }

                    function rmmSavedScriptChanged(){
                        const sel = el('rmm-saved-script-select');
                        const chosen = _savedScripts.find(s=>String(s.id)===String(sel.value));
                        if(!chosen){
                            el('rmm-saved-script-meta').textContent = '';
                            return;
                        }
                        const testedAt = chosen.last_tested_at ? fmtLocal(chosen.last_tested_at) : 'unknown';
                        el('rmm-saved-script-meta').textContent = `${chosen.description||''} Last tested: ${testedAt}${chosen.last_tested_agent_id?` on ${chosen.last_tested_agent_id}`:''}`.trim();
                        // Prefill editor so tech can inspect before running.
                        el('rmm-script-shell').value = chosen.shell || 'powershell';
                        el('rmm-script-code').value = chosen.script_content || '';
                    }

                    async function _rmmExecuteScript(shell, code, timeout){
                        const btn=el('rmm-script-run-btn');
                        btn.disabled=true; btn.textContent='Running...';
                        el('rmm-script-output').textContent=''; el('rmm-script-status').textContent='Sending...';
                        const r=await rmmSend({type:'run_script',shell,code,timeout});
                        if(!r.ok){ el('rmm-script-status').textContent='Error: '+(r.error||'failed'); btn.disabled=false; btn.innerHTML='<i class="bi bi-play-fill"></i> Run'; return; }
                        el('rmm-script-status').textContent='Waiting for result...';
                        const data=await rmmPoll(r.session_id,'script_result',(timeout+10)*1000);
                        if(data){
                            el('rmm-script-output').textContent=(data.data.stdout||'')+(data.data.stderr?'\n--- STDERR ---\n'+data.data.stderr:'');
                            el('rmm-script-status').textContent=`Exited ${data.data.exit_code}`;
                        } else {
                            el('rmm-script-status').textContent='Timed out.';
                        }
                        btn.disabled=false; btn.innerHTML='<i class="bi bi-play-fill"></i> Run';
                    }

                    async function rmmRunScript(){
                        const code=el('rmm-script-code').value.trim(); if(!code) return;
                        const shell=el('rmm-script-shell').value;
                        const timeout=+el('rmm-script-timeout').value||60;
                        await _rmmExecuteScript(shell, code, timeout);
                    }

                    async function rmmRunSavedScript(){
                        const sel = el('rmm-saved-script-select');
                        const chosen = _savedScripts.find(s=>String(s.id)===String(sel.value));
                        if(!chosen){ alert('Select a tested script first.'); return; }
                        const timeout=+el('rmm-script-timeout').value||60;
                        await _rmmExecuteScript(chosen.shell||'powershell', chosen.script_content||'', timeout);
                    }

                    /* ── services ─────────────────────────────── */
                    async function rmmLoadServices(){
                        el('rmm-svc-content').innerHTML='<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>Loading services…</span>';
                        el('rmm-svc-status').textContent='Requesting…';
                        const r=await rmmSend({type:'list_services'});
                        if(!r.ok){ el('rmm-svc-status').textContent='Error: '+(r.error||'failed'); return; }
                        const data=await rmmPoll(r.session_id,'services_result',20000);
                        _svcData=(data&&data.data&&data.data.services)||[];
                        el('rmm-svc-status').textContent=_svcData.length?`${_svcData.length} services`:'No data';
                        rmmRenderServices();
                    }
                    function rmmRenderServices(){
                        const q=(el('rmm-svc-filter')||{}).value||'';
                        const rows=_svcData.filter(s=>!q||`${s.name}${s.display_name}`.toLowerCase().includes(q.toLowerCase()));
                        el('rmm-svc-content').innerHTML=rows.length
                            ?'<table class="table table-sm table-hover mb-0"><thead class="table-light sticky-top"><tr><th>Name</th><th>Status</th><th>Start</th><th></th></tr></thead><tbody>'
                              +rows.map(s=>{
                                  const run=s.status==='running'||s.status==='Running';
                                  return `<tr><td style="font-size:.78rem;">${s.display_name||s.name}</td>`
                                      +`<td><span class="badge ${run?'bg-success':'bg-secondary'}">${s.status}</span></td>`
                                      +`<td style="font-size:.72rem;">${s.start_type||''}</td>`
                                      +`<td class="text-end"><button class="btn py-0 px-1 btn-outline-${run?'warning':'success'}" style="font-size:.68rem;" onclick="rmmServiceAction(${JSON.stringify(s.name)},${JSON.stringify(run?'stop':'start')})">${run?'Stop':'Start'}</button></td></tr>`;
                              }).join('')+'</tbody></table>'
                            :'<span class="text-muted">No services.</span>';
                    }
                    function rmmFilterServices(){ rmmRenderServices(); }
                    async function rmmServiceAction(name,action){
                        el('rmm-svc-status').textContent=`${action} ${name}…`;
                        const r=await rmmSend({type:'service_action',action,name});
                        if(!r.ok){ el('rmm-svc-status').textContent='Error: '+(r.error||'failed'); return; }
                        const data=await rmmPoll(r.session_id,'service_action_result',15000);
                        el('rmm-svc-status').textContent=data?(data.data&&data.data.success?`${action} ${name}: done`:('Failed: '+((data.data&&data.data.error)||'unknown'))):'Timed out';
                        setTimeout(rmmLoadServices,1500);
                    }

                    /* ── event viewer ─────────────────────────── */
                    async function rmmLoadEvents(){
                        const log=el('rmm-ev-log').value, lvl=el('rmm-ev-level').value,
                              src=el('rmm-ev-source').value, max=+el('rmm-ev-max').value||100;
                        el('rmm-ev-status').textContent='Querying…';
                        el('rmm-ev-content').innerHTML='<span class="text-muted"><span class="spinner-border spinner-border-sm me-1"></span>Loading…</span>';
                        const r=await rmmSend({type:'get_event_log',log_name:log,level_filter:lvl?+lvl:null,max_events:max,source_filter:src||null});
                        if(!r.ok){ el('rmm-ev-status').textContent='Error: '+(r.error||'failed'); return; }
                        const data=await rmmPoll(r.session_id,'event_log_result',30000);
                        const evts=(data&&data.data&&data.data.events)||[];
                        el('rmm-ev-status').textContent=evts.length?`${evts.length} events`:'No results';
                        // The agent sends level as a name string ("Error"/"Warning"/…) and the id
                        // as event_id. Older payloads used numeric level + id — normalise both.
                        const lvlNumByName={critical:1,error:2,warning:3,information:4,info:4,verbose:5};
                        const lvlBadge={1:'bg-danger',2:'bg-danger',3:'bg-warning',4:'bg-secondary',5:'bg-secondary'};
                        const lvlName={1:'Critical',2:'Error',3:'Warning',4:'Info',5:'Verbose'};
                        el('rmm-ev-content').innerHTML=evts.length
                            ?'<table class="table table-sm table-hover mb-0" style="font-size:.73rem;"><thead class="table-light sticky-top"><tr><th>Time</th><th>ID</th><th>Level</th><th>Source</th><th>Message</th></tr></thead><tbody>'
                              +evts.map(e=>{
                                const id=(e.event_id!=null?e.event_id:e.id)||'';
                                const lvNum=(typeof e.level==='number')?e.level:(lvlNumByName[String(e.level||'').toLowerCase()]||0);
                                const lvLabel=lvlName[lvNum]||e.level||'';
                                return `<tr><td class="text-nowrap">${fmtLocal(e.time||e.ts||'')}</td>`
                                +`<td>${id}</td><td><span class="badge ${lvlBadge[lvNum]||'bg-secondary'}">${lvLabel}</span></td>`
                                +`<td>${e.source||''}</td><td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${e.message||''}</td></tr>`;
                              }).join('')+'</tbody></table>'
                            :'<span class="text-muted">No events found.</span>';
                    }

                    /* ── file system ──────────────────────────── */
                    function rmmFsTabClick(){ if(!_fsCurrent) rmmFsNav(''); }
                    async function rmmFsNav(path){
                        if(_fsCurrent!==null && path!==_fsCurrent) _fsHistory.push(_fsCurrent);
                        _fsCurrent=path;
                        const pathEl=el('rmm-fs-path'); if(pathEl) pathEl.value=path||'';
                        el('rmm-fs-content').innerHTML='<div class="text-muted small p-3"><span class="spinner-border spinner-border-sm me-1"></span>Loading…</div>';
                        const r=await rmmSend({type:'list_directory',path:path||''});
                        if(!r.ok){ el('rmm-fs-content').innerHTML=`<div class="text-danger p-3">${r.error||'Failed'}</div>`; return; }
                        const data=await rmmPoll(r.session_id,'list_dir_result',20000);
                        const entries=(data&&data.data&&data.data.entries)||[];
                        if(!entries.length){ el('rmm-fs-content').innerHTML='<div class="text-muted small p-3">Empty or access denied.</div>'; return; }
                        const dirs=entries.filter(e=>e.is_dir), files=entries.filter(e=>!e.is_dir);
                        const rows=[...dirs,...files].map(e=>{
                            const icon=e.is_dir?'folder-fill':'file-earmark';
                            const color=e.is_dir?'text-warning':'text-secondary';
                            const sizeStr=e.is_dir?'':(e.size>1048576?(e.size/1048576).toFixed(1)+' MB':e.size>1024?(e.size/1024).toFixed(0)+' KB':e.size+' B');
                            // Normalise: strip any trailing backslashes from path then join
                            const base = path ? path.replace(/\\+$/, '') + '\\' : '';
                            const newPath = base + e.name;
                            // Use single-quoted onclick so JSON.stringify double-quotes don't break the HTML attribute
                            return `<tr class="rmm-fs-row" style="cursor:${e.is_dir?'pointer':'default'};" onclick='${e.is_dir?`rmmFsNav(${JSON.stringify(newPath)})`:''}'>`
                                +`<td><i class="bi bi-${icon} ${color} me-1"></i>${e.name}</td><td class="text-muted">${sizeStr}</td>`
                                +`<td>${(e.modified||'').slice(0,10)}</td>`
                                +`<td class="text-end">${!e.is_dir?`<button class="btn btn-outline-secondary py-0 px-1" style="font-size:.68rem;" onclick='event.stopPropagation();rmmFsSelect(${JSON.stringify(newPath)},this)'><i class="bi bi-check2"></i></button>`:''}</td></tr>`;
                        });
                        el('rmm-fs-content').innerHTML='<table class="table table-sm table-hover mb-0"><thead class="table-light"><tr><th>Name</th><th>Size</th><th>Modified</th><th></th></tr></thead><tbody>'+rows.join('')+'</tbody></table>';
                    }
                    function rmmFsSelect(path,btn){
                        document.querySelectorAll('#rmm-fs-content .rmm-fs-row').forEach(r=>r.classList.remove('table-primary'));
                        btn.closest('tr').classList.add('table-primary');
                        _fsPending=path;
                        const sEl=el('rmm-fs-selected'); if(sEl) sEl.textContent=path;
                        const dlBtn=el('rmm-fs-dl-btn'); if(dlBtn) dlBtn.disabled=false;
                    }
                    function rmmFsBack(){
                        const prev=_fsHistory.pop();
                        if(prev!=null){ _fsCurrent=null; rmmFsNav(prev); }
                    }
                    function rmmFsRefresh(){ if(_fsCurrent!=null){ const c=_fsCurrent; _fsCurrent=null; _fsHistory.pop(); rmmFsNav(c); } }
                    async function rmmFsDownload(){
                        if(!_fsPending) return;
                        el('rmm-fs-status').textContent='Requesting download…';
                        const r=await rmmSend({type:'file_download',path:_fsPending});
                        if(!r.ok){ el('rmm-fs-status').textContent='Error: '+(r.error||'failed'); return; }
                        const data=await rmmPoll(r.session_id,'file_download_data',30000);
                        if(!data||data.data.error){ el('rmm-fs-status').textContent='Download failed: '+(data&&data.data.error||'timeout'); return; }
                        const blob=new Blob([Uint8Array.from(atob(data.data.data),c=>c.charCodeAt(0))]);
                        const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=data.data.filename||'file'; a.click();
                        el('rmm-fs-status').textContent=`Downloaded ${data.data.filename}`;
                    }
                    async function rmmFsUpload(){
                        const file=el('rmm-ul-file').files[0]; if(!file||!_fsCurrent) return;
                        el('rmm-fs-status').textContent='Uploading…';
                        const ar=new Promise((res,rej)=>{ const rd=new FileReader(); rd.onload=()=>res(rd.result); rd.onerror=rej; rd.readAsDataURL(file); });
                        const dataUrl=await ar; const b64=dataUrl.split(',')[1];
                        const destPath=(_fsCurrent?_fsCurrent.replace(/\\$/,'')+'\\'  :'')+file.name;
                        const r=await rmmSend({type:'file_upload',path:destPath,data:b64});
                        if(!r.ok){ el('rmm-fs-status').textContent='Upload error: '+(r.error||'failed'); return; }
                        const data=await rmmPoll(r.session_id,'file_upload_result',60000);
                        if(data&&data.data.success){ el('rmm-fs-status').textContent='Uploaded.'; rmmFsRefresh(); }
                        else { el('rmm-fs-status').textContent='Upload failed.'; }
                    }

                    /* ── power ────────────────────────────────── */
                    async function rmmPowerAction(action){
                        if(action==='shutdown'||action==='restart'){
                            if(!confirm(`Really ${action} this device? The user will get a 30-second warning.`)) return;
                        }
                        el('rmm-power-status').textContent=`Sending ${action}…`;
                        el('rmm-power-cancel-btn').disabled=action==='cancel';
                        const r=await rmmSend({type:'power_action',action});
                        el('rmm-power-status').textContent=r.ok?`${action} command sent.`:'Error: '+(r.error||'failed');
                        if(r.ok&&action==='shutdown'||action==='restart') el('rmm-power-cancel-btn').disabled=false;
                        if(action==='cancel') el('rmm-power-cancel-btn').disabled=true;
                    }

                    /* ── screenshot ───────────────────────────── */
                    async function rmmRequestScreenshot(){
                        const modal=new bootstrap.Modal(el('rmmScreenshotModal'));
                        const img=el('rmm-ss-img'), spin=el('rmm-ss-spinner'), err=el('rmm-ss-error'), meta=el('rmm-ss-meta'), dl=el('rmm-ss-download');
                        img.style.display='none'; err.style.display='none'; spin.style.display=''; dl.style.display='none';
                        meta.textContent=''; modal.show();
                        const r=await fetch(`/api/rmm/screenshot/${agentId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(x=>x.json()).catch(()=>({ok:false}));
                        if(!r.ok){ spin.style.display='none'; err.textContent='Failed to send screenshot request: '+(r.error||'agent not connected'); err.style.display=''; return; }
                        // Poll for latest screenshot (compare timestamps)
                        const before=Date.now();
                        let found=null;
                        for(let i=0;i<30;i++){
                            await new Promise(res=>setTimeout(res,2000));
                            const s=await fetch(`/api/rmm/screenshot/${agentId}/latest`).then(x=>x.json()).catch(()=>({ok:false}));
                            if(s.ok && s.screenshot){
                                const ts=new Date(s.screenshot.captured_at||'').getTime();
                                if(ts>before-5000){ found=s.screenshot; break; }
                            }
                        }
                        spin.style.display='none';
                        if(found){
                            const shotId=found.id;
                            const fmt=found.format||found.image_format||'jpeg';
                            if(found.image_b64){
                                img.src=`data:image/${fmt};base64,${found.image_b64}`;
                                img.style.display='';
                            } else {
                                // Fallback: fetch via eagle-eyes JSON endpoint (handles file_path)
                                const imgD=await fetch(`/api/rmm/eagle-eyes/screenshot/${shotId}`).then(x=>x.json()).catch(()=>({ok:false}));
                                if(imgD.ok && imgD.screenshot && imgD.screenshot.data){
                                    img.src=`data:image/${imgD.screenshot.format||fmt};base64,${imgD.screenshot.data}`;
                                    img.style.display='';
                                } else {
                                    err.textContent='Screenshot captured but image data unavailable.';
                                    err.style.display='';
                                }
                            }
                            meta.textContent=`Captured: ${fmtLocal(found.captured_at)} | ${found.width||'?'}×${found.height||'?'}`;
                            dl.href=`/api/rmm/eagle-eyes/screenshot/${shotId}/download`;
                            dl.style.display='';
                        } else {
                            err.textContent='Screenshot not received in time. Make sure the agent is connected.';
                            err.style.display='';
                        }
                    }

                    async function rmmRemoveAgent() {
                        const modal = bootstrap.Modal.getInstance(document.getElementById('rmmRemoveAgentModal'));
                        if (modal) modal.hide();
                        try {
                            const r = await fetch(`/api/rmm/agent/${agentId}/remove`, {method: 'POST'});
                            const j = await r.json();
                            if (j.ok) {
                                window.location.href = document.querySelector('.breadcrumb-item:nth-child(2) a')?.href || '/assets';
                            } else {
                                alert('Remove failed: ' + (j.error || 'Unknown error'));
                            }
                        } catch(e) {
                            alert('Remove failed: ' + e.message);
                        }
                    }

                    // AD group memberships of this machine's computer object (server-side LDAP).
                    async function loadAdGroups(){
                        const tgt = el('rmm-si-adgroups'); if(!tgt) return;
                        const m = location.pathname.match(/\/assets\/(\d+)/); if(!m) return;
                        try {
                            const r = await fetch('/assets/'+m[1]+'/ad-groups', {credentials:'same-origin'});
                            const d = await r.json();
                            if(d.enabled === false){ tgt.innerHTML = '<span class="text-muted">AD not configured</span>'; return; }
                            const g = d.groups || [];
                            tgt.innerHTML = g.length
                                ? g.map(n=>'<span class="badge bg-secondary me-1">'+n.replace(/</g,'&lt;')+'</span>').join('')
                                : '<span class="text-muted">No group memberships</span>';
                        } catch(e){
                            tgt.innerHTML = '<span class="text-muted">Unavailable</span>';
                        }
                    }

                    // Expose to global scope for inline onclick handlers
                    Object.assign(window,{
                        rmmLoad, rmmRefreshTelemetry, rmmAvScan, rmmLoadMetrics,
                        rmmLoadAvailability, rmmLoadSessionEvents, rmmEagleLoad, rmmEagleToggle, rmmEagleScreenshotsToggle,
                        rmmLoadPatches, rmmApprovePatches, rmmScanPatches, rmmChkAll, rmmChkChanged, rmmDeployJob,
                        rmmLoadSoftware, rmmRefreshSoftware, rmmRescanSoftware, rmmFilterSoftware, rmmSwToggle,
                        rmmUninstallSw, rmmWingetSearch, rmmWingetInstall, rmmMsiInstall,
                        rmmRunScript, rmmLoadSavedScripts, rmmRunSavedScript,
                        rmmLoadServices, rmmFilterServices, rmmServiceAction,
                        rmmLoadEvents,
                        rmmFsTabClick, rmmFsNav, rmmFsSelect, rmmFsBack, rmmFsRefresh, rmmFsDownload, rmmFsUpload,
                        rmmPowerAction, rmmRequestScreenshot,
                        rmmRestartAgent, rmmUpdateAgent, rmmInstallTray,
                        rmmRemoveAgent, rmmReidentify
                    });

                    rmmLoad().catch(err => {
                        const b = el('rmm-status-badge');
                        if(b){ b.className='badge bg-danger'; b.textContent='JS Error: '+err.message; }
                        console.error('[RMM] rmmLoad failed:', err);
                    });
                    // Pre-load patches and software so data is ready when those tabs are clicked
                    rmmLoadPatches().catch(() => {});
                    rmmLoadSoftware().catch(() => {});
                    loadAdGroups().catch(() => {});
                })();