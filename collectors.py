"""System probes — Layer 3 of the knowledge brain: live facts.

Run read-only PowerShell collectors (and a couple of safe actions) on a system's HOST AGENT
via the RMM execution substrate (workflow_engine._dispatch_to_agent → gateway → agent). A
collector's JSON output is parsed into it_system.facts + a versioned 'Live facts' doc, so the
agent reasons over current truth and humans see it on the system page. Read-only Get-* scripts
are low risk; they only run when an admin clicks Run and the system has a host asset with an
online agent. See docs/AGENTIC_IT_OS_GAMEPLAN.md.
"""
import json
import logging
import re
from datetime import datetime

log = logging.getLogger("collectors")


def _stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ── PowerShell (read-only Get-*; one action) ─────────────────────────────────────
AD_PS = r"""
$ErrorActionPreference='Stop'
$d=Get-ADDomain; $f=Get-ADForest
$rw=@($d.ReplicaDirectoryServers); $ro=@($d.ReadOnlyReplicaDirectoryServers)
$pp=Get-ADDefaultDomainPasswordPolicy
$en=@(Get-ADUser -Filter 'Enabled -eq $true' -Properties LastLogonDate -ResultSetSize 5000)
$dis=@(Get-ADUser -Filter 'Enabled -eq $false' -ResultSetSize 5000).Count
$cut=(Get-Date).AddDays(-90)
$stale=@($en | Where-Object {$_.LastLogonDate -and $_.LastLogonDate -lt $cut}).Count
function CountGroup($n){ try { @(Get-ADGroupMember $n -Recursive -ErrorAction Stop).Count } catch { -1 } }
[pscustomobject]@{
  domain=$d.DNSRoot; netbios=$d.NetBIOSName; domain_mode="$($d.DomainMode)"; forest_mode="$($f.ForestMode)";
  dc_count=($rw.Count+$ro.Count); writable_dcs=$rw; rodcs=$ro;
  fsmo_pdc=$d.PDCEmulator; fsmo_rid=$d.RIDMaster; fsmo_infrastructure=$d.InfrastructureMaster;
  fsmo_schema=$f.SchemaMaster; fsmo_domain_naming=$f.DomainNamingMaster;
  users_total=($en.Count+$dis); users_enabled=$en.Count; users_disabled=$dis; users_stale_90d=$stale;
  domain_admins=(CountGroup 'Domain Admins'); enterprise_admins=(CountGroup 'Enterprise Admins');
  computers=@(Get-ADComputer -Filter * -ResultSetSize 5000).Count;
  pwd_complexity=$pp.ComplexityEnabled; pwd_min_length=$pp.MinPasswordLength;
  pwd_min_age_days=$pp.MinPasswordAge.Days; pwd_max_age_days=$pp.MaxPasswordAge.Days;
  pwd_history=$pp.PasswordHistoryCount; pwd_reversible_encryption=$pp.ReversibleEncryptionEnabled;
  lockout_threshold=$pp.LockoutThreshold; lockout_duration_min=[int]$pp.LockoutDuration.TotalMinutes;
  lockout_window_min=[int]$pp.LockoutObservationWindow.TotalMinutes
} | ConvertTo-Json -Compress
"""

GPO_PS = r"""
Import-Module GroupPolicy -ErrorAction Stop
Import-Module ActiveDirectory -ErrorAction Stop
$all=Get-GPO -All
# Collect every linked GPO GUID from OUs, the domain root, and sites (gPLink).
$linked=New-Object System.Collections.Generic.HashSet[string]
$soms=@()
$soms += Get-ADObject -LDAPFilter '(objectClass=organizationalUnit)' -Properties gPLink
$soms += Get-ADObject -Identity ((Get-ADDomain).DistinguishedName) -Properties gPLink
try { $cfg=(Get-ADRootDSE).configurationNamingContext
      $soms += Get-ADObject -SearchBase "CN=Sites,$cfg" -LDAPFilter '(objectClass=site)' -Properties gPLink } catch {}
foreach($s in $soms){ if($s.gPLink){ foreach($m in [regex]::Matches($s.gPLink,'\{([0-9A-Fa-f\-]+)\}')){ [void]$linked.Add($m.Groups[1].Value.ToLower()) } } }
$gpos=$all | Sort-Object DisplayName | ForEach-Object {
  [pscustomobject]@{ name=$_.DisplayName; status="$($_.GpoStatus)"; modified=$_.ModificationTime.ToString('yyyy-MM-dd');
                     linked=$linked.Contains($_.Id.ToString().ToLower()) } }
$unlinked=@($gpos | Where-Object { -not $_.linked })
$baselines=@($unlinked | Where-Object { $_.name -like 'MSFT *' -or $_.name -like '*Baseline*' })
[pscustomobject]@{
  gpo_count=$all.Count; unlinked_count=$unlinked.Count; unlinked_baselines=$baselines.Count;
  all_disabled=@($all|Where-Object{$_.GpoStatus -eq 'AllSettingsDisabled'}).Count;
  user_disabled=@($all|Where-Object{$_.GpoStatus -eq 'UserSettingsDisabled'}).Count;
  computer_disabled=@($all|Where-Object{$_.GpoStatus -eq 'ComputerSettingsDisabled'}).Count;
  unlinked=@($unlinked|Select-Object -ExpandProperty name);
  unlinked_baseline_names=@($baselines|Select-Object -ExpandProperty name); gpos=@($gpos)
} | ConvertTo-Json -Compress -Depth 4
"""

FGPP_PS = r"""
$ErrorActionPreference='Stop'
$ps=@(Get-ADFineGrainedPasswordPolicy -Filter * -ErrorAction SilentlyContinue)
$list=foreach($p in $ps){
  [pscustomobject]@{ name=$p.Name; min_length=$p.MinPasswordLength; max_age_days=$p.MaxPasswordAge.Days;
    history=$p.PasswordHistoryCount; lockout=$p.LockoutThreshold; precedence=$p.Precedence;
    applies_to=@($p.AppliesTo | ForEach-Object { ($_ -split ',')[0] -replace '^CN=','' }) } }
[pscustomobject]@{ fgpp_count=$ps.Count; policies=@($list) } | ConvertTo-Json -Compress -Depth 4
"""

REPL_PS = r"""
$ErrorActionPreference='Stop'
# Only probe WRITABLE DCs — querying the Taiwan RODC hangs (latency) and can't be cleanly
# bounded. RODCs are reported separately (count only), not deep-probed.
$wdc=@((Get-ADDomainController -Filter {IsReadOnly -eq $false}).HostName)
$rodc=@((Get-ADDomainController -Filter {IsReadOnly -eq $true}).HostName)
$rep=foreach($dc in $wdc){
  $f=@(Get-ADReplicationFailure -Target $dc -ErrorAction SilentlyContinue)
  [pscustomobject]@{ dc=$dc; failures=$f.Count }
}
[pscustomobject]@{
  writable_dc_count=$wdc.Count; rodc_count=$rodc.Count; rodcs=@($rodc);
  total_failures=(@($rep|Measure-Object -Property failures -Sum).Sum); replication=@($rep)
} | ConvertTo-Json -Compress -Depth 4
"""

CERT_PS = r"""
$now=Get-Date
$certs=Get-ChildItem Cert:\LocalMachine\My | Where-Object {$_.NotAfter -gt $now} |
  Sort-Object NotAfter | Select-Object -First 60 `
    @{n='subject';e={$_.Subject}}, @{n='expires';e={$_.NotAfter.ToString('yyyy-MM-dd')}}, `
    @{n='days_left';e={[int]($_.NotAfter-$now).TotalDays}}
[pscustomobject]@{ cert_count=@($certs).Count; soon_expiring=@($certs|Where-Object {$_.days_left -lt 30}).Count; certs=@($certs) } | ConvertTo-Json -Compress
"""

ENTRA_SYNC_PS = "Start-ADSyncSyncCycle -PolicyType Delta | ConvertTo-Json -Compress"


def _facts_doc(title, facts, extra_lines=None):
    lines = [f"# {title} — collected {_stamp()}", ""]
    for k, v in facts.items():
        lines.append(f"- **{k.replace('_', ' ')}:** {v}")
    if extra_lines:
        lines += [""] + extra_lines
    return "\n".join(lines)


def _ad_parse(o):
    scalar_keys = ('domain', 'netbios', 'domain_mode', 'forest_mode', 'dc_count',
                   'fsmo_pdc', 'fsmo_rid', 'fsmo_infrastructure', 'fsmo_schema', 'fsmo_domain_naming',
                   'users_total', 'users_enabled', 'users_disabled', 'users_stale_90d',
                   'domain_admins', 'enterprise_admins', 'computers',
                   'pwd_complexity', 'pwd_min_length', 'pwd_min_age_days', 'pwd_max_age_days',
                   'pwd_history', 'pwd_reversible_encryption',
                   'lockout_threshold', 'lockout_duration_min', 'lockout_window_min')
    facts = {k: o.get(k) for k in scalar_keys if k in o}
    extra = []
    if o.get('writable_dcs'):
        extra += ["## Domain controllers (writable)", ""] + [f"- {d}" for d in o['writable_dcs']]
    if o.get('rodcs'):
        extra += ["", "## Read-only DCs", ""] + [f"- {d}" for d in o['rodcs']]
    return facts, _facts_doc("Active Directory — live state", facts, extra or None)


def _gpo_parse(o):
    facts = {k: o.get(k) for k in ('gpo_count', 'unlinked_count', 'unlinked_baselines',
                                   'all_disabled', 'user_disabled', 'computer_disabled') if k in o}
    extra = []
    if o.get('unlinked_baseline_names'):
        extra += ["## Unlinked Microsoft security baselines (staged, NOT applied)", ""] + [f"- {g}" for g in o['unlinked_baseline_names']]
    if o.get('unlinked'):
        extra += ["", "## All unlinked GPOs", ""] + [f"- {g}" for g in o['unlinked']]
    if o.get('gpos'):
        extra += ["", "## All GPOs (name · status · modified · linked)", ""]
        for g in o['gpos']:
            extra.append(f"- {g.get('name')} — {g.get('status')} · {g.get('modified')} · {'linked' if g.get('linked') else 'UNLINKED'}")
    return facts, _facts_doc("Group Policy — live inventory", facts, extra or None)


def _fgpp_parse(o):
    facts = {'fgpp_count': o.get('fgpp_count')}
    extra = ["## Fine-grained password policies (PSOs)", ""]
    if o.get('policies'):
        for p in o['policies']:
            extra.append(f"- **{p.get('name')}** (precedence {p.get('precedence')}): min {p.get('min_length')} chars, "
                         f"max age {p.get('max_age_days')}d, history {p.get('history')}, lockout {p.get('lockout')} "
                         f"→ applies to: {', '.join(p.get('applies_to') or []) or '(none)'}")
    else:
        extra.append("_No fine-grained password policies — the default domain policy applies to everyone._")
    return facts, _facts_doc("Fine-grained password policies", facts, extra)


def _repl_parse(o):
    facts = {'writable_dc_count': o.get('writable_dc_count'), 'rodc_count': o.get('rodc_count'),
             'replication_failures': o.get('total_failures')}
    extra = ["## Replication health — writable DCs", ""]
    for r in (o.get('replication') or []):
        extra.append(f"- {r.get('dc')}: {'healthy ✓' if not r.get('failures') else str(r.get('failures')) + ' failures ⚠️'}")
    if o.get('rodcs'):
        extra += ["", "## Read-only DCs (not deep-probed — latency)", ""] + [f"- {d}" for d in o['rodcs']]
    return facts, _facts_doc("Active Directory — replication health", facts, extra)


def _cert_parse(o):
    facts = {'cert_count': o.get('cert_count'), 'soon_expiring': o.get('soon_expiring')}
    extra = ["## Certificates (soonest expiry first)", ""]
    for c in (o.get('certs') or []):
        flag = ' ⚠️' if (c.get('days_left') is not None and c['days_left'] < 30) else ''
        extra.append(f"- {c.get('subject')} — expires {c.get('expires')} ({c.get('days_left')}d){flag}")
    return facts, _facts_doc("Certificates — live state", facts, extra)


def _is_ad(s):
    n = (s.name or '').lower()
    return 'active directory' in n and 'certificate' not in n and 'sync' not in n and 'entra' not in n
def _is_cert(s):
    return 'cert' in (s.name or '').lower()
def _is_entra(s):
    n = (s.name or '').lower()
    return 'entra' in n or 'aad connect' in n or 'ad connect' in n or ('azure' in n and 'sync' in n)


PROBES = [
    {'key': 'ad_domain', 'label': 'AD domain & forest', 'kind': 'collect', 'shell': 'powershell',
     'applies': _is_ad, 'script': AD_PS, 'parse': _ad_parse},
    {'key': 'gpo', 'label': 'Group Policy inventory', 'kind': 'collect', 'shell': 'powershell',
     'applies': _is_ad, 'script': GPO_PS, 'parse': _gpo_parse},
    {'key': 'fgpp', 'label': 'Fine-grained password policies', 'kind': 'collect', 'shell': 'powershell',
     'applies': _is_ad, 'script': FGPP_PS, 'parse': _fgpp_parse},
    {'key': 'ad_replication', 'label': 'AD replication health', 'kind': 'collect', 'shell': 'powershell',
     'applies': _is_ad, 'script': REPL_PS, 'parse': _repl_parse, 'timeout': 300},
    {'key': 'certs', 'label': 'Certificate expiry', 'kind': 'collect', 'shell': 'powershell',
     'applies': lambda s: _is_cert(s) or _is_ad(s), 'script': CERT_PS, 'parse': _cert_parse},
    {'key': 'entra_sync', 'label': 'Run Entra (AAD Connect) delta sync', 'kind': 'action', 'shell': 'powershell',
     'applies': _is_entra, 'script': ENTRA_SYNC_PS},
]


def applicable(system):
    return [{'key': p['key'], 'label': p['label'], 'kind': p['kind']} for p in PROBES if p['applies'](system)]


def run_probe(system_id, key, user='system'):
    """Dispatch a probe to the system's host agent, parse + persist (collect) or report
    (action). Returns (success, info). Safe to call from a background thread."""
    import workflow_engine as we
    from extensions import db
    from models import ITSystem
    s = ITSystem.query.get(system_id)
    probe = next((p for p in PROBES if p['key'] == key), None)
    if not s or not probe:
        return False, {'error': 'Unknown system or probe.'}
    if not s.asset_id:
        return False, {'error': 'Set a host asset (a server with an RMM agent) on this system first.'}
    agent_id, asset_id, online = we._resolve_agent(asset_id=s.asset_id)
    if not agent_id:
        return False, {'error': 'No RMM agent found on the host asset.'}

    _success, output = we._dispatch_to_agent(
        agent_id, online, 'powershell', probe['script'],
        asset_id=asset_id, reason=f"collector:{key}", timeout_s=probe.get('timeout', 120), wait_result=True)
    # NOTE: PowerShell-via-agent returns exit_code=1 even on success, so we DON'T trust
    # _dispatch_to_agent's exit-code grading — we grade on the captured stdout instead.
    stdout = (output.get('stdout') or '').strip()
    stderr = (output.get('stderr') or '').strip()
    if not stdout:
        return False, {'error': output.get('error') or stderr
                       or 'No output returned (agent offline, timed out, or the script errored).'}

    if probe['kind'] == 'action':
        log.info("probe action %s on system %s ok", key, system_id)
        return True, {'output': stdout}

    try:
        parsed = json.loads(stdout)
    except Exception:
        # Tolerate stray banner/CLIXML lines around the JSON object.
        m = re.search(r'(\{.*\})', stdout, re.S)
        if m:
            try:
                parsed = json.loads(m.group(1))
            except Exception:
                parsed = None
        else:
            parsed = None
        if parsed is None:
            return False, {'error': 'Collector ran but output was not valid JSON.', 'stdout': stdout[:400]}

    facts, doc = probe['parse'](parsed)
    merged = dict(s.facts or {})
    merged.update({k: v for k, v in facts.items() if v is not None})
    s.facts = merged
    s.updated_by = user
    db.session.commit()
    from blueprints.systems import save_doc
    save_doc(system_id, f"Live facts — {probe['label']}", doc, source='collector',
             doc_key=f"live-{key}", user=user, change_summary='Collected live state')
    log.info("collector %s on system %s -> %s facts", key, system_id, len(facts))
    return True, {'facts': facts}
