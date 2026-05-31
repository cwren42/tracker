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
from datetime import datetime

log = logging.getLogger("collectors")


def _stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ── PowerShell (read-only Get-*; one action) ─────────────────────────────────────
AD_PS = r"""
$ErrorActionPreference='Stop'
$d=Get-ADDomain; $f=Get-ADForest
$dcs=@($d.ReplicaDirectoryServers)
[pscustomobject]@{
  domain=$d.DNSRoot; netbios=$d.NetBIOSName; domain_mode="$($d.DomainMode)"; forest_mode="$($f.ForestMode)";
  pdc_emulator=$d.PDCEmulator; dc_count=$dcs.Count; dcs=$dcs;
  user_count=@(Get-ADUser -Filter * -ResultSetSize 5000).Count;
  computer_count=@(Get-ADComputer -Filter * -ResultSetSize 5000).Count
} | ConvertTo-Json -Compress
"""

GPO_PS = r"""
Import-Module GroupPolicy -ErrorAction Stop
$g=Get-GPO -All
[pscustomobject]@{ gpo_count=$g.Count; gpos=@($g|Sort-Object DisplayName|Select-Object -ExpandProperty DisplayName) } | ConvertTo-Json -Compress
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
    facts = {k: o.get(k) for k in ('domain', 'netbios', 'domain_mode', 'forest_mode',
                                   'pdc_emulator', 'dc_count', 'user_count', 'computer_count') if k in o}
    extra = (["## Domain controllers", ""] + [f"- {d}" for d in (o.get('dcs') or [])]) if o.get('dcs') else None
    return facts, _facts_doc("Active Directory — live state", facts, extra)


def _gpo_parse(o):
    facts = {'gpo_count': o.get('gpo_count')}
    extra = (["## Group Policy Objects", ""] + [f"- {g}" for g in (o.get('gpos') or [])]) if o.get('gpos') else None
    return facts, _facts_doc("Group Policy — live inventory", facts, extra)


def _cert_parse(o):
    facts = {'cert_count': o.get('cert_count'), 'soon_expiring': o.get('soon_expiring')}
    extra = ["## Certificates (soonest expiry first)", ""]
    for c in (o.get('certs') or []):
        flag = ' ⚠️' if (c.get('days_left') is not None and c['days_left'] < 30) else ''
        extra.append(f"- {c.get('subject')} — expires {c.get('expires')} ({c.get('days_left')}d){flag}")
    return facts, _facts_doc("Certificates — live state", facts, extra)


def _is_ad(s):
    n = (s.name or '').lower()
    return 'active directory' in n and 'certificate' not in n
def _is_cert(s):
    return 'cert' in (s.name or '').lower()


PROBES = [
    {'key': 'ad_domain', 'label': 'AD domain & forest', 'kind': 'collect', 'shell': 'powershell',
     'applies': _is_ad, 'script': AD_PS, 'parse': _ad_parse},
    {'key': 'gpo', 'label': 'Group Policy inventory', 'kind': 'collect', 'shell': 'powershell',
     'applies': _is_ad, 'script': GPO_PS, 'parse': _gpo_parse},
    {'key': 'certs', 'label': 'Certificate expiry', 'kind': 'collect', 'shell': 'powershell',
     'applies': lambda s: _is_cert(s) or _is_ad(s), 'script': CERT_PS, 'parse': _cert_parse},
    {'key': 'entra_sync', 'label': 'Run Entra (AAD Connect) delta sync', 'kind': 'action', 'shell': 'powershell',
     'applies': _is_ad, 'script': ENTRA_SYNC_PS},
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

    success, output = we._dispatch_to_agent(
        agent_id, online, 'powershell', probe['script'],
        asset_id=asset_id, reason=f"collector:{key}", timeout_s=120, wait_result=True)
    if not success:
        return False, {'error': output.get('error') or output.get('stderr') or 'dispatch failed'}

    stdout = (output.get('stdout') or '').strip()
    if probe['kind'] == 'action':
        log.info("probe action %s on system %s ok", key, system_id)
        return True, {'output': stdout or 'triggered'}

    try:
        parsed = json.loads(stdout)
    except Exception:
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
