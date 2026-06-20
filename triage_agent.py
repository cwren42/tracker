"""
AI Triage Agent — the agentic tool-calling loop behind the AI Triage Chat layer
(Phase 2 of Proactive AI Remediation).

WHAT IT DOES (Chris's ask, literally):
    Given an incident (or a free-form question about an asset), drive gpt-4o in a
    tool-calling loop that:
        1. auto-runs READ-ONLY diagnostics on the box,
        2. reasons over the results,
        3. produces a grounded DIAGNOSIS + recommended FIX + WHY-it-works,
        4. emits the change as a GATED proposal (propose_fix) — NEVER executed
           until a human clicks Approve in the chat thread.

DESIGN / SAFETY
    * READ-ONLY tools run AUTONOMOUSLY (Chris OK'd this — they are non-mutating).
      The diagnostic tool runs a STRICTLY-WHITELISTED PowerShell command on the
      box via the existing rmm_remediation_queue round-trip (enqueue -> the agent
      runs it -> result lands in rmm_remediation_queue.result_json -> we poll).
    * CHANGES never execute here. propose_fix returns a structured proposal that
      the chat UI renders with an Approve button; approval goes through
      blueprints/incidents.py -> incident_service._enqueue_action (the EXISTING
      remediation path). The loop is incapable of mutating a box.
    * The diagnostic whitelist is by KEY, not by free-form command. The model
      cannot pass a command string — it picks a diag_key from DIAGNOSTICS and we
      look up the canonical, vetted, non-mutating script. It is therefore
      IMPOSSIBLE to run a mutating command "as a diagnostic".
    * Hard caps: <=6 iterations, a token budget, per-tool timeouts. Any AI/tool
      error degrades to a plain assistant message — it NEVER crashes the feed.
    * Every tool call + result + the AI reasoning is persisted to incident_message
      (full audit trail).
    * GUARD: never touches ai_engine's ticket_note.created_by path.
"""
import json
import logging
import time
from datetime import datetime

logger = logging.getLogger("triage_agent")

# ── caps / budget ──────────────────────────────────────────────────────────
_MAX_ITERS = 6
_TOKEN_BUDGET = 20000          # cumulative prompt+completion tokens across the loop
_DIAG_POLL_TIMEOUT = 130       # seconds to wait for a read-only diagnostic result
_DIAG_POLL_INTERVAL = 2.5
_DIAG_SCRIPT_TIMEOUT = 110     # per-script cap sent to the agent (< poll timeout)


def _db():
    from pg_db import pg_connect
    return pg_connect()


def _gw():
    try:
        from app import app
        return app.config.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')
    except Exception:
        return 'http://127.0.0.1:8765'


# ─────────────────────────────────────────────────────────────────────────────
# READ-ONLY DIAGNOSTIC WHITELIST
# Each entry is a canonical, vetted, STRICTLY NON-MUTATING PowerShell snippet.
# The model selects a diag_key; it can NEVER supply a command string. A couple of
# diagnostics accept a single tightly-validated argument (a service name, an event
# log name, a tail count) — sanitized below before substitution.
# ─────────────────────────────────────────────────────────────────────────────
def _diag_volumes():
    return ("Get-Volume | Where-Object DriveLetter | "
            "Select-Object DriveLetter,FileSystemLabel,"
            "@{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}},"
            "@{n='FreeGB';e={[math]::Round($_.SizeRemaining/1GB,1)}},"
            "@{n='Free%';e={if($_.Size){[math]::Round($_.SizeRemaining/$_.Size*100,1)}else{0}}} "
            "| Format-Table -Auto | Out-String")


def _diag_disk_hogs():
    """READ-ONLY 'what's filling C:' — the KNOWN hog paths + top-level folders
    only (depth-1 sizing), NOT a full recursive scan of the whole drive. A full
    Get-ChildItem -Recurse of C: can take minutes on a real box and times out;
    this bounded version returns in seconds and is enough to ground triage. All
    Get-*/Measure-Object — never mutates."""
    return (
        "$ErrorActionPreference='SilentlyContinue'\n"
        "function SizeGB($p){ if(Test-Path $p){ "
        "$b=(Get-ChildItem -LiteralPath $p -Recurse -Force -File -EA SilentlyContinue | "
        "Measure-Object Length -Sum).Sum; if($b){[math]::Round($b/1GB,2)}else{0} } else {'n/a'} }\n"
        "$ld=Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='C:'\"\n"
        "if($ld){ \"C: FreeGB={0} SizeGB={1}\" -f [math]::Round($ld.FreeSpace/1GB,1),[math]::Round($ld.Size/1GB,1) }\n"
        "\"`n=== Known hogs ===\"\n"
        "\"Windows\\Temp                  : $(SizeGB 'C:\\Windows\\Temp') GB\"\n"
        "\"SoftwareDistribution\\Download : $(SizeGB 'C:\\Windows\\SoftwareDistribution\\Download') GB\"\n"
        "\"Windows\\Installer             : $(SizeGB 'C:\\Windows\\Installer') GB\"\n"
        "\"Windows.old                   : $(SizeGB 'C:\\Windows.old') GB\"\n"
        "\"Recycle Bin                   : $(SizeGB 'C:\\$Recycle.Bin') GB\"\n"
        "$pf='C:\\pagefile.sys'; if(Test-Path $pf){ \"pagefile.sys                  : $([math]::Round((Get-Item $pf -Force).Length/1GB,2)) GB\" }\n"
        "$hb='C:\\hiberfil.sys'; if(Test-Path $hb){ \"hiberfil.sys                  : $([math]::Round((Get-Item $hb -Force).Length/1GB,2)) GB\" }\n"
        "\"`n=== Top-level folders on C: ===\"\n"
        "Get-ChildItem -LiteralPath 'C:\\' -Directory -Force -EA SilentlyContinue | "
        "Select-Object Name | Format-Table -Auto | Out-String\n"
        "\"(Per-folder sizes omitted — full recursive sizing of C: is too slow for live triage. "
        "The known-hog paths above are what the safe cache cleanup targets.)\"\n")


def _diag_services_auto_stopped():
    return ("Get-Service | Where-Object {$_.StartType -eq 'Automatic' -and "
            "$_.Status -ne 'Running'} | Select-Object Name,DisplayName,Status,StartType "
            "| Format-Table -Auto | Out-String")


def _diag_service_status(name):
    safe = _sanitize_token(name, default='Spooler')
    return (f"$n='{safe}'; $s=Get-Service -Name $n -EA SilentlyContinue; "
            f"if($s){{ $s | Select-Object Name,DisplayName,Status,StartType | "
            f"Format-List | Out-String }} else {{ \"Service '$n' not found\" }}")


def _diag_top_processes():
    return ("Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 12 "
            "Name,Id,@{n='CPU(s)';e={[math]::Round($_.CPU,1)}},"
            "@{n='RAM_MB';e={[math]::Round($_.WorkingSet64/1MB,0)}} "
            "| Format-Table -Auto | Out-String")


def _diag_recent_errors(log_name, count=20):
    log = _sanitize_logname(log_name)
    n = _sanitize_count(count, default=20, lo=1, hi=60)
    return (f"Get-WinEvent -FilterHashtable @{{LogName='{log}';Level=1,2}} "
            f"-MaxEvents {n} -EA SilentlyContinue | "
            f"Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,"
            f"@{{n='Message';e={{($_.Message -split \"`n\")[0]}}}} "
            f"| Format-Table -Auto -Wrap | Out-String")


def _diag_pending_updates():
    return ("$s=New-Object -ComObject Microsoft.Update.Session;"
            "$sr=$s.CreateUpdateSearcher();"
            "try{$r=$sr.Search('IsInstalled=0 and IsHidden=0');"
            "if($r.Updates.Count -eq 0){'No pending updates.'}"
            "else{$r.Updates | ForEach-Object{$_.Title} | Out-String}}"
            "catch{\"Update search failed: $($_.Exception.Message)\"}")


def _diag_ipconfig():
    return "ipconfig /all | Out-String"


def _diag_computer_info():
    return ("Get-ComputerInfo -Property CsName,OsName,OsVersion,OsBuildNumber,"
            "WindowsProductName,OsArchitecture,CsManufacturer,CsModel,"
            "OsLastBootUpTime,CsNumberOfLogicalProcessors,"
            "@{n='RAM_GB';e={[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)}} "
            "-EA SilentlyContinue | Format-List | Out-String")


def _diag_uptime():
    return ("$os=Get-CimInstance Win32_OperatingSystem;"
            "$up=(Get-Date)-$os.LastBootUpTime;"
            "\"Last boot: $($os.LastBootUpTime)`nUptime: $([math]::Floor($up.TotalDays))d "
            "$($up.Hours)h $($up.Minutes)m\"")


# diag_key -> (human label, builder, accepts_arg)
DIAGNOSTICS = {
    'volumes':          ('List all volumes with free space', _diag_volumes, False),
    'disk_hogs':        ("What's filling C: (folders/large files)", _diag_disk_hogs, False),
    'services_stopped': ('Automatic services that are not running', _diag_services_auto_stopped, False),
    'service_status':   ('Status of one named service', _diag_service_status, True),
    'top_processes':    ('Top processes by memory', _diag_top_processes, False),
    'recent_errors':    ('Recent error/critical events from an event log', _diag_recent_errors, True),
    'pending_updates':  ('Pending Windows Updates', _diag_pending_updates, False),
    'ipconfig':         ('Network adapter / IP configuration', _diag_ipconfig, False),
    'computer_info':    ('OS / hardware summary', _diag_computer_info, False),
    'uptime':           ('Last boot time / uptime', _diag_uptime, False),
}

import re as _re

# Defensive last-line check (belt-and-braces ON TOP of the whitelist-by-key): a
# built diagnostic must not contain a MUTATING cmdlet/operator. We match specific
# mutating cmdlets PRECISELY (not bare verb prefixes) so read-only cmdlets that
# share a verb stem — Format-Table/Format-List, Set-Location, Select-Object,
# Sort-Object — are not falsely refused. Matched case-insensitively as whole
# tokens (\b boundaries).
_FORBIDDEN_CMDLETS = (
    r'remove-\w+', r'clear-recyclebin', r'clear-content', r'clear-eventlog',
    r'restart-service', r'stop-service', r'start-service', r'set-service',
    r'new-service', r'restart-computer', r'stop-computer', r'stop-process',
    r'set-itemproperty', r'new-itemproperty', r'remove-itemproperty',
    r'set-content', r'add-content', r'out-file', r'set-item', r'new-item',
    r'rename-item', r'move-item', r'copy-item', r'mkdir', r'rmdir',
    r'format-volume', r'format-disk', r'set-volume', r'initialize-disk',
    r'install-\w+', r'uninstall-\w+', r'enable-\w+', r'disable-\w+',
    r'invoke-expression', r'invoke-webrequest', r'start-process',
    r'set-executionpolicy', r'register-\w+', r'unregister-\w+',
    r'iex', r'iwr', r'curl', r'wget',
)
# Mutating shell operators / external tools (substring match is fine for these).
_FORBIDDEN_TOKENS = (
    '\ndel ', '; del ', '\nrm ', '; rm ', ' erase ', 'reg delete', 'reg add',
    'shutdown', 'wusa', ' dism ', '>>', 'cleanmgr', 'sc delete', 'sc config',
    'sc stop', 'sc start', ' iex ', '| iex', 'takeown', 'icacls',
)
_FORBIDDEN_RE = _re.compile(r'\b(?:' + '|'.join(_FORBIDDEN_CMDLETS) + r')\b',
                            _re.IGNORECASE)


def _sanitize_token(s, default=''):
    """A service name etc.: letters/digits/space/.-_ only, single-quote-escaped."""
    s = (s or '').strip()
    out = ''.join(c for c in s if c.isalnum() or c in ' ._-')
    out = out[:64].replace("'", "''")
    return out or default


def _sanitize_logname(s):
    allowed = {'System', 'Application', 'Security', 'Setup',
               'Microsoft-Windows-WindowsUpdateClient/Operational'}
    s = (s or '').strip()
    # exact match (case-insensitive) against the allowlist, else default to System
    for a in allowed:
        if s.lower() == a.lower():
            return a
    return 'System'


def _sanitize_count(n, default=20, lo=1, hi=60):
    try:
        v = int(n)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _build_diagnostic(diag_key, arg=None):
    """Resolve a whitelisted diagnostic to its canonical PowerShell. Returns
    (code, label) or (None, reason). Refuses anything that fails the forbidden
    scan (defence in depth)."""
    entry = DIAGNOSTICS.get(diag_key)
    if not entry:
        return None, f"unknown diagnostic '{diag_key}'"
    label, builder, accepts_arg = entry
    try:
        code = builder(arg) if accepts_arg else builder()
    except Exception as e:
        return None, f"diagnostic build failed: {e}"
    low = code.lower()
    m = _FORBIDDEN_RE.search(code)
    if m:
        logger.error("triage diagnostic %s contained forbidden cmdlet %r — refusing",
                     diag_key, m.group(0))
        return None, "diagnostic failed safety check (refused)"
    for bad in _FORBIDDEN_TOKENS:
        if bad in low:
            logger.error("triage diagnostic %s contained forbidden token %r — refusing",
                         diag_key, bad)
            return None, "diagnostic failed safety check (refused)"
    return code, label


# ─────────────────────────────────────────────────────────────────────────────
# TOOL IMPLEMENTATIONS (the AI's read-only senses)
# Each returns a JSON-serializable dict. None of these mutate a box.
# ─────────────────────────────────────────────────────────────────────────────
def tool_get_asset_context(con, asset_id):
    if not asset_id:
        return {'error': 'no asset_id'}
    a = con.execute(
        """SELECT a.id, a.name, a.category, a.device_type, a.os_version,
                  a.manufacturer, a.model, a.serial_number, a.online_state,
                  a.last_seen, a.location, e.name AS owner, e.email AS owner_email
           FROM asset a LEFT JOIN employee e ON e.id = a.employee_id
           WHERE a.id=%s""", (asset_id,)).fetchone()
    if not a:
        return {'error': f'asset {asset_id} not found'}
    d = {k: a[k] for k in a.keys()}
    # recent incident history for this asset
    hist = con.execute(
        """SELECT signal_type, status, severity, created_at, verify_result
           FROM agent_incident WHERE asset_id=%s
           ORDER BY created_at DESC LIMIT 8""", (asset_id,)).fetchall()
    d['recent_incidents'] = [
        {'signal': h['signal_type'], 'status': h['status'],
         'severity': h['severity'], 'when': str(h['created_at']),
         'result': h['verify_result']} for h in hist]
    return _jsonable(d)


def tool_get_latest_telemetry(con, agent_id):
    if not agent_id:
        return {'error': 'no agent_id'}
    t = con.execute(
        """SELECT hostname, os_name, os_version, os_build, cpu_name, cpu_cores,
                  cpu_percent, ram_total_gb, ram_available_gb, ram_percent,
                  disk_json, battery_percent, uptime_seconds, logged_in_user,
                  agent_version, captured_at, public_ip
           FROM rmm_telemetry WHERE agent_id=%s
           ORDER BY captured_at DESC LIMIT 1""", (agent_id,)).fetchone()
    if not t:
        return {'error': f'no telemetry for agent {agent_id}'}
    d = {k: t[k] for k in t.keys()}
    if d.get('disk_json'):
        try:
            d['disks'] = json.loads(d['disk_json'])
        except Exception:
            pass
    d.pop('disk_json', None)
    # staleness hint so the AI knows whether to trust it / run a live diagnostic
    try:
        from datetime import timezone
        cap = t['captured_at']
        if cap:
            age = (datetime.now(timezone.utc) - cap).total_seconds()
            d['telemetry_age_minutes'] = round(age / 60, 1)
    except Exception:
        pass
    return _jsonable(d)


def tool_run_readonly_diagnostic(con, agent_id, asset_id, diag_key, arg=None):
    """Run a WHITELISTED, NON-MUTATING command on the box and return its output.
    Enqueues via the gateway remediation path (negative-session correlation),
    then polls rmm_remediation_queue.result_json until the agent echoes a result
    or we time out. Read-only — runs without human approval (per Chris)."""
    code, label = _build_diagnostic(diag_key, arg)
    if not code:
        return {'error': label, 'diag_key': diag_key}
    if not agent_id:
        return {'error': 'no agent_id — cannot run a live diagnostic'}

    payload = {'type': 'run_script', 'shell': 'powershell', 'code': code,
               'timeout': _DIAG_SCRIPT_TIMEOUT}
    import requests
    try:
        resp = requests.post(
            f"{_gw()}/remediation/{agent_id}/enqueue",
            json={'action_type': 'run_script', 'payload': payload,
                  'asset_id': asset_id, 'created_by': None},
            timeout=10)
        gw = resp.json()
    except Exception as e:
        return {'error': f'gateway unreachable: {e}', 'diag_key': diag_key}

    rq_id = gw.get('id')
    if not rq_id:
        return {'error': f'enqueue failed: {gw}', 'diag_key': diag_key}
    if not gw.get('delivered'):
        # agent offline — the script is queued but we can't wait on a result
        return {'diag_key': diag_key, 'label': label, 'delivered': False,
                'note': 'agent is offline; diagnostic queued but no live output available'}

    # Poll for the result (the gateway writes result_json on script_result).
    deadline = time.time() + _DIAG_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(_DIAG_POLL_INTERVAL)
        row = con.execute(
            "SELECT status, result_json FROM rmm_remediation_queue WHERE id=%s",
            (rq_id,)).fetchone()
        if not row:
            continue
        if row['status'] in ('completed', 'failed', 'no_op', 'abandoned'):
            out = {}
            try:
                out = json.loads(row['result_json']) if row['result_json'] else {}
            except Exception:
                out = {'raw': row['result_json']}
            stdout = (out.get('stdout') or '').strip()
            stderr = (out.get('stderr') or '').strip()
            # bound the output so a chatty script can't blow the token budget
            if len(stdout) > 6000:
                stdout = stdout[:6000] + '\n…(truncated)'
            return {'diag_key': diag_key, 'label': label, 'status': row['status'],
                    'exit_code': out.get('exit_code'),
                    'stdout': stdout or '(no output)',
                    'stderr': stderr[:1500]}
    return {'diag_key': diag_key, 'label': label, 'status': 'timeout',
            'note': f'diagnostic did not return within {_DIAG_POLL_TIMEOUT}s'}


def tool_get_similar_past_fixes(con, signal_type):
    """Prior resolutions for this signal type across the fleet — what worked, plus
    the LEARNING-LOOP success stats (Feature 3): for each action that was applied
    to this signal, how many past cases it resolved (resolved N / total M)."""
    rows = con.execute(
        """SELECT i.asset_id, a.name AS host, i.status, i.chosen_action,
                  i.verify_result, i.created_at
           FROM agent_incident i LEFT JOIN asset a ON a.id=i.asset_id
           WHERE i.signal_type=%s
             AND i.status IN ('resolved','auto_handled')
           ORDER BY i.created_at DESC LIMIT 6""", (signal_type,)).fetchall()
    # Per-action success rate from the recorded outcomes (the learning loop).
    learning = {'signal_overall': None, 'by_action': []}
    try:
        import incident_service as _inc
        learning['signal_overall'] = _inc.fix_success_stats(con, signal_type)
        ar = con.execute(
            """SELECT chosen_action,
                      COUNT(*) FILTER (WHERE success) AS resolved,
                      COUNT(*) AS total
               FROM incident_fix_outcome
               WHERE signal_type=%s AND chosen_action IS NOT NULL
               GROUP BY chosen_action ORDER BY total DESC""",
            (signal_type,)).fetchall()
        learning['by_action'] = [
            {'action': a['chosen_action'], 'resolved': a['resolved'],
             'total': a['total'],
             'summary': f"resolved {a['resolved']}/{a['total']} past cases"}
            for a in ar]
    except Exception as e:
        logger.info('similar-past-fixes learning stats skipped (%s): %s',
                    signal_type, e)
    return {'signal_type': signal_type, 'learning': learning, 'past_fixes': [
        {'host': r['host'], 'status': r['status'],
         'action': r['chosen_action'], 'result': r['verify_result'],
         'when': str(r['created_at'])} for r in rows]}


def _jsonable(d):
    return json.loads(json.dumps(d, default=str))


# ─────────────────────────────────────────────────────────────────────────────
# Tool schemas exposed to the model (OpenAI function-calling format)
# ─────────────────────────────────────────────────────────────────────────────
def _tool_specs():
    diag_keys = list(DIAGNOSTICS.keys())
    diag_help = '; '.join(f"{k}={v[0]}" for k, v in DIAGNOSTICS.items())
    return [
        {"type": "function", "function": {
            "name": "get_asset_context",
            "description": "Asset identity, OS, owner, location, online state, and recent incident history.",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "get_latest_telemetry",
            "description": "Latest collected telemetry for the box: CPU/RAM/disk usage, uptime, logged-in user, agent version, telemetry age.",
            "parameters": {"type": "object", "properties": {}, "required": []}}},
        {"type": "function", "function": {
            "name": "run_readonly_diagnostic",
            "description": ("Run ONE strictly read-only, non-mutating diagnostic command live on the box "
                            "and get its output. Use this to GROUND your diagnosis in real evidence. "
                            f"Pick diag_key from: {diag_help}. "
                            "diag_key='service_status' takes arg=<service name>; "
                            "diag_key='recent_errors' takes arg=<event log name: System|Application|Setup>. "
                            "This CANNOT change anything on the box."),
            "parameters": {"type": "object", "properties": {
                "diag_key": {"type": "string", "enum": diag_keys},
                "arg": {"type": "string", "description": "optional argument for service_status/recent_errors"}},
                "required": ["diag_key"]}}},
        {"type": "function", "function": {
            "name": "get_similar_past_fixes",
            "description": "Prior resolutions for this signal type across the fleet — what action fixed it before.",
            "parameters": {"type": "object", "properties": {
                "signal_type": {"type": "string"}}, "required": ["signal_type"]}}},
        {"type": "function", "function": {
            "name": "propose_fix",
            "description": ("Propose your recommended CHANGE (mutating remediation) to fix the issue. "
                            "This is GATED — it is NOT executed; it is shown to the technician with an "
                            "Approve button. Call this ONCE you are confident, after you have run "
                            "read-only diagnostics. Provide a clear diagnosis, the fix, and WHY it works."),
            "parameters": {"type": "object", "properties": {
                "diagnosis": {"type": "string", "description": "what is wrong, grounded in the diagnostics you ran"},
                "fix_label": {"type": "string", "description": "short human label for the action, e.g. 'Restart Spooler'"},
                "why_it_works": {"type": "string", "description": "why this fix resolves the diagnosed cause"},
                "action_kind": {"type": "string", "enum": ["run_script", "restart_service", "clear_disk_cache", "installer_cleanup", "retry_patch", "ticket_only"],
                                "description": ("the category of change; ticket_only when no safe automated fix exists. "
                                                "Use installer_cleanup for a disk_low box whose C:\\Windows\\Installer is "
                                                "a major hog and the safe cache cleanup can't reach it: it runs the SAFE "
                                                "DISM /StartComponentCleanup (WinSxS) and REPORTS orphaned Installer files "
                                                "for manual review — it does NOT delete Installer packages.")},
                "service_name": {"type": "string", "description": "for restart_service: the exact Windows service name"},
                "powershell": {"type": "string", "description": "for run_script: the EXACT mutating PowerShell to run on approval"}},
                "required": ["diagnosis", "fix_label", "why_it_works", "action_kind"]}}},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Persisting chat turns
# ─────────────────────────────────────────────────────────────────────────────
def post_message(con, incident_id, role, content=None, *, tool_name=None,
                 tool_call=None, tool_result=None, proposed_fix=None, meta=None,
                 created_by=None, commit=True):
    row = con.execute(
        """INSERT INTO incident_message
             (incident_id, role, content, tool_name, tool_call, tool_result,
              proposed_fix, meta, created_by, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) RETURNING id""",
        (incident_id, role, content, tool_name,
         json.dumps(tool_call) if tool_call is not None else None,
         json.dumps(tool_result) if tool_result is not None else None,
         json.dumps(proposed_fix) if proposed_fix is not None else None,
         json.dumps(meta) if meta is not None else None,
         created_by)).fetchone()
    if commit:
        con.commit()
    return row['id']


def get_thread(con, incident_id):
    rows = con.execute(
        """SELECT id, role, content, tool_name, tool_call, tool_result,
                  proposed_fix, meta, created_by, created_at
           FROM incident_message WHERE incident_id=%s ORDER BY id""",
        (incident_id,)).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        for j in ('tool_call', 'tool_result', 'proposed_fix', 'meta'):
            if isinstance(d.get(j), str):
                try:
                    d[j] = json.loads(d[j])
                except Exception:
                    pass
        out.append(_jsonable(d))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Normalize a propose_fix tool-call into a gated action the existing act() path
# can execute on approval. We DO NOT trust the model to write arbitrary scripts
# for high-risk kinds — restart_service/clear_disk_cache map to the SAME vetted
# templates incident_service uses. Free-form run_script is allowed but flagged.
# ─────────────────────────────────────────────────────────────────────────────
def _normalize_proposal(args, incident):
    import incident_service as _inc
    kind = (args.get('action_kind') or 'ticket_only').strip()
    diagnosis = (args.get('diagnosis') or '').strip()
    why = (args.get('why_it_works') or '').strip()
    label = (args.get('fix_label') or 'Recommended fix').strip()[:80]

    proposal = {
        'diagnosis': diagnosis, 'why_it_works': why, 'fix_label': label,
        'action_kind': kind,
    }
    if kind == 'clear_disk_cache':
        proposal['run_payload'] = _inc._disk_cleanup_payload()
        proposal['risk_tier'] = 0
        proposal['execute'] = 'run'
    elif kind == 'installer_cleanup':
        # Feature 4: SAFE DISM component-store cleanup + READ-ONLY orphan report.
        # Always Tier-1 (risk_tier 1 -> human Approve). Maps to the vetted template;
        # the model cannot author the script. Never deletes Installer packages.
        proposal['run_payload'] = _inc._installer_cleanup_payload()
        proposal['risk_tier'] = 1
        proposal['execute'] = 'run'
        if not why:
            proposal['why_it_works'] = (
                "Runs the Microsoft-supported DISM /StartComponentCleanup to reclaim "
                "superseded WinSxS components (safe), then produces a READ-ONLY report "
                "of orphaned C:\\Windows\\Installer MSI/MSP files and reclaimable size. "
                "It does NOT delete Installer packages — deleting in-use MSI/MSP breaks "
                "app repair/uninstall, so that reclaim is left for manual review.")
    elif kind == 'restart_service':
        svc = (args.get('service_name') or '').strip()
        if not svc:
            proposal['action_kind'] = 'ticket_only'
            proposal['execute'] = 'ticket'
        else:
            proposal['run_payload'] = _inc._restart_service_payload(svc)
            proposal['service_name'] = svc
            proposal['risk_tier'] = 1
            proposal['execute'] = 'run'
    elif kind == 'retry_patch':
        # retried via the existing retry_patch_job path; carry the source job id
        pa = incident.get('proposed_actions') or []
        if isinstance(pa, str):
            try:
                pa = json.loads(pa)
            except Exception:
                pa = []
        src = next((a for a in pa if a.get('key') == 'retry_patch'), None)
        if src and src.get('run_payload'):
            proposal['run_payload'] = src['run_payload']
            proposal['risk_tier'] = 1
            proposal['execute'] = 'run'
        else:
            proposal['action_kind'] = 'ticket_only'
            proposal['execute'] = 'ticket'
    elif kind == 'run_script':
        ps = (args.get('powershell') or '').strip()
        if not ps:
            proposal['action_kind'] = 'ticket_only'
            proposal['execute'] = 'ticket'
        else:
            proposal['run_payload'] = {'type': 'run_script', 'shell': 'powershell',
                                       'code': ps, 'timeout': 180}
            proposal['risk_tier'] = 1
            proposal['execute'] = 'run'
            proposal['ai_authored'] = True   # flag: model-written script, extra scrutiny
    else:  # ticket_only
        proposal['execute'] = 'ticket'
    return proposal


# ─────────────────────────────────────────────────────────────────────────────
# The agentic loop
# ─────────────────────────────────────────────────────────────────────────────
def _system_prompt(incident, asset_name, learning_note=''):
    return (
        "You are an expert IT operations engineer triaging a device incident for a "
        "managed Windows/Linux fleet. You have tools to read asset context, telemetry, "
        "run STRICTLY READ-ONLY diagnostics live on the box, and look up past fixes.\n\n"
        "Your job:\n"
        "1. Investigate: call read-only tools to GROUND your conclusion in real evidence. "
        "Prefer running at least one live diagnostic before concluding when the box is online.\n"
        "2. Diagnose the most likely root cause.\n"
        "3. Recommend a fix and explain WHY it works.\n"
        "4. Call propose_fix EXACTLY ONCE with your recommendation. The fix is GATED — a "
        "human approves it; you never execute changes yourself.\n\n"
        "Rules: Be concise and specific. Do not invent data — if a tool returns an error or the "
        "box is offline, say so and recommend the safest next step (often ticket_only). For "
        "servers/critical assets, prefer ticket_only or low-risk changes. After propose_fix, give a "
        "short final summary to the technician.\n\n"
        f"Incident: signal={incident.get('signal_type')} severity={incident.get('severity')} "
        f"on asset '{asset_name}'. First-pass diagnosis: {incident.get('diagnosis_text') or '(none)'}"
        + (("\n\n" + learning_note) if learning_note else "")
    )


def _learning_note(con, signal_type):
    """A one-line confidence nudge built from the learning loop (Feature 3): the
    fleet success rate for this signal's past fixes. Empty when no history yet.
    Fail-safe (never raises)."""
    try:
        import incident_service as _inc
        s = _inc.fix_success_stats(con, signal_type)
        if not s.get('total'):
            return ''
        pct = round(s['rate'] * 100)
        lean = ('lean toward proposing the historically-successful automated fix'
                if pct >= 60 else
                'be cautious — past automated fixes for this signal often did NOT '
                'clear it, so prefer deeper diagnosis or ticket_only')
        return (f"LEARNING LOOP: across the fleet, recorded fixes for "
                f"'{signal_type}' resolved {s['resolved']}/{s['total']} past cases "
                f"({pct}%). Weight your recommended-fix confidence accordingly and "
                f"{lean}. Use get_similar_past_fixes for the per-action breakdown.")
    except Exception:
        return ''


def _run_loop(con, incident, seed_messages, created_by=None, max_iters=_MAX_ITERS):
    """Core tool-calling loop. seed_messages is the OpenAI message list (system +
    prior turns + the new user turn). Persists assistant/tool turns to
    incident_message. Returns dict(summary, proposal_posted, iterations, error)."""
    import ai_engine
    asset_id = incident.get('asset_id')
    agent_id = incident.get('agent_id')
    inc_id = incident['id']
    signal = incident.get('signal_type')

    tools = _tool_specs()
    messages = list(seed_messages)
    tokens_used = 0
    proposal_posted = False

    def _dispatch(name, args):
        if name == 'get_asset_context':
            return tool_get_asset_context(con, asset_id)
        if name == 'get_latest_telemetry':
            return tool_get_latest_telemetry(con, agent_id)
        if name == 'run_readonly_diagnostic':
            return tool_run_readonly_diagnostic(
                con, agent_id, asset_id, args.get('diag_key'), args.get('arg'))
        if name == 'get_similar_past_fixes':
            return tool_get_similar_past_fixes(con, args.get('signal_type') or signal)
        return {'error': f'unknown tool {name}'}

    for it in range(max_iters):
        try:
            msg = ai_engine.openai_chat_tools(messages, tools=tools)
        except Exception as e:
            logger.warning("triage loop AI call failed (incident %s): %s", inc_id, e)
            return {'error': str(e), 'iterations': it, 'proposal_posted': proposal_posted}

        usage = msg.pop('_usage', {}) or {}
        tokens_used += int(usage.get('total_tokens', 0) or 0)
        tool_calls = msg.get('tool_calls') or []
        content = (msg.get('content') or '').strip()

        # Keep the assistant message (with tool_calls) in the OpenAI context.
        messages.append({k: v for k, v in msg.items() if k != '_usage'})

        if not tool_calls:
            # Final assistant turn — persist its prose and stop.
            if content:
                post_message(con, inc_id, 'assistant', content,
                             meta={'iteration': it, 'tokens': tokens_used})
            return {'summary': content, 'iterations': it + 1,
                    'proposal_posted': proposal_posted, 'tokens': tokens_used}

        # Surface any interim reasoning text alongside the tool calls.
        if content:
            post_message(con, inc_id, 'assistant', content,
                         meta={'iteration': it, 'interim': True})

        for tc in tool_calls:
            fn = (tc.get('function') or {})
            name = fn.get('name')
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except Exception:
                args = {}

            if name == 'propose_fix':
                proposal = _normalize_proposal(args, incident)
                post_message(con, inc_id, 'assistant',
                             content=proposal.get('diagnosis'),
                             tool_name='propose_fix', tool_call=args,
                             proposed_fix=proposal,
                             meta={'iteration': it})
                # store the live proposal on the incident for the Approve button
                con.execute(
                    "UPDATE agent_incident SET proposed_fix=%s, updated_at=NOW() WHERE id=%s",
                    (json.dumps(proposal), inc_id))
                con.commit()
                proposal_posted = True
                tool_payload = {'ok': True,
                                'note': 'proposal recorded and shown to the technician with an Approve button'}
            else:
                tool_payload = _dispatch(name, args)
                # audit the tool call + result as a 'tool' chat turn
                post_message(con, inc_id, 'tool',
                             content=_summarize_tool(name, args, tool_payload),
                             tool_name=name, tool_call=args, tool_result=tool_payload,
                             meta={'iteration': it})

            # feed the tool result back to the model
            messages.append({
                'role': 'tool', 'tool_call_id': tc.get('id'),
                'name': name, 'content': json.dumps(tool_payload, default=str)[:8000]})

        if tokens_used > _TOKEN_BUDGET:
            logger.info("triage loop hit token budget (incident %s): %s", inc_id, tokens_used)
            post_message(con, inc_id, 'assistant',
                         "(Triage stopped: reached the token budget. The findings above stand.)",
                         meta={'budget_stop': True, 'tokens': tokens_used})
            return {'summary': 'token budget reached', 'iterations': it + 1,
                    'proposal_posted': proposal_posted, 'tokens': tokens_used}

    # Ran out of iterations — ask for a final, toolless conclusion.
    try:
        messages.append({'role': 'user',
                         'content': 'Stop using tools now. Give your final diagnosis, '
                                    'recommended fix, and why it works in plain text.'})
        final = ai_engine.openai_chat_tools(messages, tools=None, max_tokens=500)
        txt = (final.get('content') or '').strip()
        if txt:
            post_message(con, inc_id, 'assistant', txt,
                         meta={'iteration': max_iters, 'forced_conclusion': True})
        return {'summary': txt, 'iterations': max_iters,
                'proposal_posted': proposal_posted, 'tokens': tokens_used}
    except Exception as e:
        return {'error': str(e), 'iterations': max_iters, 'proposal_posted': proposal_posted}


def _summarize_tool(name, args, result):
    """Short human line for the chat thread (the raw JSON is in tool_result)."""
    if name == 'run_readonly_diagnostic':
        dk = args.get('diag_key')
        st = result.get('status') or ('queued' if result.get('delivered') is False else 'done')
        return f"Ran read-only diagnostic: {result.get('label', dk)} ({st})"
    if name == 'get_asset_context':
        return f"Looked up asset context for {result.get('name', '?')}"
    if name == 'get_latest_telemetry':
        age = result.get('telemetry_age_minutes')
        return f"Read latest telemetry" + (f" ({age}m old)" if age is not None else "")
    if name == 'get_similar_past_fixes':
        n = len(result.get('past_fixes') or [])
        return f"Checked {n} similar past fix(es)"
    return f"Called {name}"


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────
def _load_incident(con, incident_id):
    r = con.execute("SELECT * FROM agent_incident WHERE id=%s", (incident_id,)).fetchone()
    if not r:
        return None
    d = {k: r[k] for k in r.keys()}
    return d


def _asset_name(con, asset_id):
    if not asset_id:
        return 'unknown asset'
    a = con.execute("SELECT name FROM asset WHERE id=%s", (asset_id,)).fetchone()
    return (a['name'] if a else None) or f'asset {asset_id}'


def _ai_ready():
    try:
        import ai_config
        return ai_config.ready()
    except Exception:
        return False


def triage_incident(incident_id, *, force=False, app=None):
    """Run (or re-run) the autonomous triage loop for an incident. Idempotent via
    triage_state: returns early if already running/done unless force=True.
    Fully fail-safe — never raises; on any error posts a degraded message."""
    ctx = None
    if app is not None:
        ctx = app.app_context(); ctx.push()
    con = None
    try:
        con = _db()
        inc = _load_incident(con, incident_id)
        if not inc:
            return {'error': 'incident not found'}
        if not force and inc.get('triage_state') in ('running', 'done'):
            return {'skipped': inc.get('triage_state')}

        if not _ai_ready():
            post_message(con, incident_id, 'assistant',
                         "AI triage is unavailable (no AI provider configured). "
                         "Use the manual fix options below.",
                         meta={'degraded': 'ai_not_configured'})
            con.execute("UPDATE agent_incident SET triage_state='error' WHERE id=%s",
                        (incident_id,))
            con.commit()
            return {'error': 'ai_not_configured'}

        # lock
        con.execute("UPDATE agent_incident SET triage_state='running', updated_at=NOW() WHERE id=%s",
                    (incident_id,))
        con.commit()

        asset_name = _asset_name(con, inc.get('asset_id'))
        post_message(con, incident_id, 'system',
                     f"Starting automated triage for {inc.get('signal_type')} on {asset_name}.",
                     meta={'auto': True})

        ln = _learning_note(con, inc.get('signal_type'))
        seed = [{'role': 'system', 'content': _system_prompt(inc, asset_name, ln)},
                {'role': 'user', 'content':
                 f"Triage this {inc.get('signal_type')} incident on {asset_name}. "
                 f"Investigate with read-only tools, then propose a gated fix."}]
        res = _run_loop(con, inc, seed)

        state = 'error' if res.get('error') else 'done'
        if res.get('error'):
            post_message(con, incident_id, 'assistant',
                         "Automated triage could not complete (AI/tool error). "
                         "The manual fix options remain available below.",
                         meta={'degraded': res['error']})
        con.execute("UPDATE agent_incident SET triage_state=%s, updated_at=NOW() WHERE id=%s",
                    (state, incident_id))
        con.commit()
        return res
    except Exception as e:
        logger.exception("triage_incident failed for %s", incident_id)
        try:
            if con:
                con.execute("UPDATE agent_incident SET triage_state='error' WHERE id=%s",
                            (incident_id,))
                con.commit()
        except Exception:
            pass
        return {'error': str(e)}
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        if ctx is not None:
            ctx.pop()


def post_user_message(incident_id, user_text, *, created_by=None):
    """Persist a technician's reply immediately (so the UI shows it at once),
    separate from the (possibly slow, backgrounded) AI continuation."""
    con = _db()
    try:
        post_message(con, incident_id, 'user', user_text, created_by=created_by)
    finally:
        con.close()


def continue_incident_chat(incident_id, user_text, *, created_by=None, post_user=True):
    """A technician replied in the incident thread. Re-run the loop with the full
    prior conversation + the new user turn so the AI can investigate further,
    answer, or (on 'do it') confirm — changes still go through the Approve gate.

    When post_user=False the caller has ALREADY persisted the user turn (the
    route does this synchronously so it appears instantly while the AI runs in
    the background)."""
    con = None
    try:
        con = _db()
        inc = _load_incident(con, incident_id)
        if not inc:
            return {'error': 'incident not found'}
        if post_user:
            post_message(con, incident_id, 'user', user_text, created_by=created_by)

        if not _ai_ready():
            post_message(con, incident_id, 'assistant',
                         "AI is not configured, so I can't respond automatically.",
                         meta={'degraded': 'ai_not_configured'})
            return {'error': 'ai_not_configured'}

        asset_name = _asset_name(con, inc.get('asset_id'))
        seed = _rebuild_openai_messages(con, inc, asset_name)
        seed.append({'role': 'user', 'content': user_text})
        res = _run_loop(con, inc, seed, created_by=created_by)
        if res.get('error'):
            post_message(con, incident_id, 'assistant',
                         "I hit an error continuing the investigation. Try again, or "
                         "use the manual options.",
                         meta={'degraded': res['error']})
        return res
    except Exception as e:
        logger.exception("continue_incident_chat failed for %s", incident_id)
        return {'error': str(e)}
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def _rebuild_openai_messages(con, inc, asset_name):
    """Reconstruct an OpenAI message list from the persisted thread so a reply
    continues with full context. We replay assistant prose, tool summaries (as
    compact context), and user turns. We do NOT replay tool_call/tool_result id
    pairs (those ids are stale) — instead we fold tool results into context as
    plain notes, which keeps the model grounded without id-matching errors."""
    msgs = [{'role': 'system',
             'content': _system_prompt(inc, asset_name,
                                       _learning_note(con, inc.get('signal_type')))}]
    for m in get_thread(con, inc['id']):
        role = m['role']
        if role == 'user':
            msgs.append({'role': 'user', 'content': m['content'] or ''})
        elif role == 'assistant':
            txt = m['content'] or ''
            pf = m.get('proposed_fix')
            if pf:
                txt = (txt + f"\n[Proposed fix already shown to tech: {pf.get('fix_label')} "
                       f"— {pf.get('why_it_works')}]")
            if txt.strip():
                msgs.append({'role': 'assistant', 'content': txt})
        elif role == 'tool':
            tr = m.get('tool_result') or {}
            note = f"[Earlier diagnostic — {m.get('tool_name')}]: " + \
                   json.dumps(tr, default=str)[:2000]
            msgs.append({'role': 'assistant', 'content': note})
        # system rows are skipped (the system prompt already leads)
    return msgs


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL "AI Assist" — same engine, not tied to an incident.
# Backed by a hidden synthetic incident row per conversation so the same chat
# thread + tool loop + Approve gate work unchanged.
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_asset_by_name(con, name):
    if not name:
        return None
    r = con.execute(
        """SELECT a.id, a.name, ra.agent_id
           FROM asset a LEFT JOIN rmm_agent ra ON ra.asset_id=a.id AND ra.enabled=TRUE
           WHERE LOWER(a.name)=LOWER(%s) ORDER BY ra.last_seen_at DESC NULLS LAST LIMIT 1""",
        (name,)).fetchone()
    if r:
        return {'asset_id': r['id'], 'agent_id': r['agent_id'], 'name': r['name']}
    # fuzzy
    r = con.execute(
        """SELECT a.id, a.name, ra.agent_id
           FROM asset a LEFT JOIN rmm_agent ra ON ra.asset_id=a.id AND ra.enabled=TRUE
           WHERE a.name ILIKE %s ORDER BY ra.last_seen_at DESC NULLS LAST LIMIT 1""",
        (f'%{name}%',)).fetchone()
    return {'asset_id': r['id'], 'agent_id': r['agent_id'], 'name': r['name']} if r else None


def get_or_create_assist_thread(con, user_id):
    """One reusable 'assist' incident per user (signal_type='ai_assist')."""
    r = con.execute(
        """SELECT * FROM agent_incident
           WHERE signal_type='ai_assist' AND approved_by=%s
           ORDER BY id DESC LIMIT 1""", (user_id,)).fetchone()
    if r:
        return {k: r[k] for k in r.keys()}
    # status='resolved' (a TERMINAL state) so this chat container never counts as
    # an OPEN incident in the feed/badge, and never collides with the
    # uq_agent_incident_open partial-unique index once an asset gets bound.
    row = con.execute(
        """INSERT INTO agent_incident
             (signal_type, severity, dedup_key, status, approved_by,
              triage_state, created_at, updated_at)
           VALUES ('ai_assist','info',%s,'resolved',%s,'done',NOW(),NOW())
           RETURNING *""",
        (f'ai_assist:{user_id}', user_id)).fetchone()
    con.commit()
    return {k: row[k] for k in row.keys()}


def assist_prepare(user_id, user_text, *, target_asset=None):
    """Synchronous prelude for AI Assist: resolve/create the user's thread, bind a
    named target asset for this turn, and post the user's message NOW so it shows
    instantly. Returns the incident id (the AI loop then runs in the background)."""
    con = _db()
    try:
        inc = get_or_create_assist_thread(con, user_id)
        if target_asset:
            bind = _resolve_asset_by_name(con, target_asset)
            if bind:
                con.execute("UPDATE agent_incident SET asset_id=%s, agent_id=%s WHERE id=%s",
                            (bind['asset_id'], bind['agent_id'], inc['id']))
        post_message(con, inc['id'], 'user', user_text, created_by=user_id)
        return inc['id']
    finally:
        con.close()


def assist_chat(user_id, user_text, *, target_asset=None, post_user=True):
    """Global AI Assist: ask anything. The loop can target any asset by name (the
    model is told it may name an asset; we resolve the most likely one up front
    when the message references one). Read-only auto; changes gated.

    When post_user=False the caller (route) already created the thread, bound the
    target, and posted the user turn via assist_prepare — we just run the loop."""
    con = None
    try:
        con = _db()
        inc = get_or_create_assist_thread(con, user_id)
        # Bind target asset/agent for this turn if named/selected.
        bind = None
        if target_asset:
            bind = _resolve_asset_by_name(con, target_asset)
        if bind:
            inc['asset_id'] = bind['asset_id']
            inc['agent_id'] = bind['agent_id']
            con.execute("UPDATE agent_incident SET asset_id=%s, agent_id=%s WHERE id=%s",
                        (bind['asset_id'], bind['agent_id'], inc['id']))
            con.commit()

        if post_user:
            post_message(con, inc['id'], 'user', user_text, created_by=user_id)
        if not _ai_ready():
            post_message(con, inc['id'], 'assistant',
                         "AI is not configured (Settings → AI).",
                         meta={'degraded': 'ai_not_configured'})
            return {'error': 'ai_not_configured', 'incident_id': inc['id']}

        asset_name = _asset_name(con, inc.get('asset_id')) if inc.get('asset_id') else '(no asset bound)'
        sys = (
            "You are the Cirque IT AI Assist — a fleet operations copilot. You can read "
            "asset context, telemetry, run STRICTLY read-only diagnostics on a box, and "
            "look up past fixes. When the user names a device, that device's asset/agent is "
            "bound for you (asset: " + asset_name + "). Read-only tools run automatically; any "
            "CHANGE you recommend is GATED — call propose_fix and a human approves it. Be concise. "
            "If no device is bound and the user asks about a specific box, ask them to pick it.")
        seed = [{'role': 'system', 'content': sys}]
        for m in get_thread(con, inc['id'])[-20:]:
            if m['role'] == 'user':
                seed.append({'role': 'user', 'content': m['content'] or ''})
            elif m['role'] == 'assistant' and (m['content'] or '').strip():
                seed.append({'role': 'assistant', 'content': m['content']})
            elif m['role'] == 'tool':
                seed.append({'role': 'assistant',
                             'content': f"[diagnostic {m.get('tool_name')}]: " +
                             json.dumps(m.get('tool_result') or {}, default=str)[:1500]})
        res = _run_loop(con, inc, seed, created_by=user_id)
        res['incident_id'] = inc['id']
        return res
    except Exception as e:
        logger.exception("assist_chat failed")
        return {'error': str(e)}
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
