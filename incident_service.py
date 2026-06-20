"""
Proactive AI Remediation — detector / orchestrator (Phase 1 / MVP).

The loop:
    Detect (telemetry / patch jobs / Defender)  ->
    Diagnose (ai_engine narrative + confidence) ->
    Propose TEMPLATED, deterministic fix actions ->
    push to an in-app feed (pluggable Notifier) ->
    a human approves (or disk auto-handles)     ->
    enqueue to the EXISTING rmm_remediation_queue (reuse alert_service) ->
    verify on the next pass -> resolve or escalate.

DESIGN NOTES
------------
* NEW code only: the agent_incident record + this orchestrator + the in-app feed
  (blueprints/incidents.py + template). Execution REUSES
  alert_service._enqueue_remediation -> the gateway reconnect-remediation engine
  and rmm_remediation_queue. We do NOT reinvent run_script delivery.

* proposed_actions are TEMPLATED per signal_type (deterministic payloads defined
  HERE). The AI produces narrative ONLY (diagnosis_text + ai_confidence). The AI
  never generates run scripts in the MVP.

* The whole scan is wrapped so a failure in one signal/one agent can never break
  the host scheduler loop. Each detector is also individually try/except'd and
  will NO-OP (rather than fabricate) when its data source isn't reliably present.

* Autonomy tiers live in AUTONOMY (a dict, easy to change, overridable per-signal
  by a Setting key 'incident_tier_<signal>'). Critical/server assets are FORCED
  to Tier-2 notify regardless of the per-signal tier.

* GUARD: ai_engine.py has a latent bug INSERTing a non-existent created_by column
  into ticket_note. We never write ticket_note with created_by — ticket creation
  here goes through the normal columns only.
"""
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


# ── DB (reuse the same pg_db shim every background service uses) ───────────────
def _get_db():
    from pg_db import pg_connect
    return pg_connect()


def _now():
    return datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────────────
# Autonomy tiers
#   tier-0 = AUTO  (auto-enqueue the safe action, record as auto_handled)
#   tier-1 = PROPOSE (await human approval in the feed)
#   tier-2 = NOTIFY  (no auto-action; ticket/dismiss only)
# Server / critical assets are forced to tier-2 (see _force_tier_for_asset).
# Override any signal at runtime via Setting key 'incident_tier_<signal>'.
# ─────────────────────────────────────────────────────────────────────────────
TIER_AUTO, TIER_PROPOSE, TIER_NOTIFY = 0, 1, 2

AUTONOMY = {
    'disk_low':             TIER_AUTO,     # proven-safe cache cleanup -> auto
    'service_down':         TIER_PROPOSE,  # restart service -> await approval
    'patch_failed':         TIER_PROPOSE,  # retry patch -> await approval
    'agent_offline_but_up': TIER_NOTIFY,   # box/agent unreachable -> notify only
    'defender_critical':    TIER_NOTIFY,   # never auto-remediate CVEs -> notify only
}

# Asset categories / OS that must NEVER auto-remediate (force tier-2).
_SERVER_CATEGORIES = {'server', 'servers', 'hypervisor', 'domain controller',
                      'dc', 'nas', 'storage'}

# Name-based server detection (Bug-3 backstop). SENSEL-SERVER-1 was filed as
# category='Workstation' / device_type='Windows Workstation' with a NULL OS, so
# the category/OS checks missed it and a cleanup auto-ran on a server. Asset data
# is not always trustworthy; the hostname usually is. These regexes catch the
# common server-name conventions so a mislabeled asset can never auto-remediate.
import re as _re
_SERVER_NAME_RE = _re.compile(
    r'(?:^|[-_ ])(?:srv|server|svr|dc\d*|nas|esx|esxi|vmhost|hyperv|hyper-v|'
    r'prox(?:mox)?|filesrv|fileserver|sql|exch(?:ange)?|vcenter)'
    r'(?:[-_ \d]|$)', _re.IGNORECASE)

# Cooldown (minutes) after an incident reaches a terminal state before the same
# (asset, signal) may open a fresh incident. Stops resolve->re-open flapping.
_RESOLVE_COOLDOWN_MIN = {
    'disk_low':             120,
    'service_down':         60,
    'patch_failed':         720,
    'agent_offline_but_up': 60,
    'defender_critical':    1440,
}

# Circuit breaker: a run-action that fails this many times -> escalate, stop retry.
_MAX_ATTEMPTS = 2

_OPEN_STATUSES = ('new', 'diagnosed', 'awaiting_approval', 'remediating')


# ─────────────────────────────────────────────────────────────────────────────
# Notifier seam — InAppNotifier now; clean place to bolt a TeamsNotifier later.
# ─────────────────────────────────────────────────────────────────────────────
class Notifier:
    """A push channel for a freshly-created incident. Phase 2 adds a
    TeamsNotifier (Graph Adaptive Card) implementing the same .push()."""
    channel = 'none'

    def push(self, incident: dict) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class InAppNotifier(Notifier):
    """The MVP channel: the incident simply lands in the in-app /incidents feed
    (and the nav bell badge counts it). No external call — the row IS the push."""
    channel = 'in_app'

    def push(self, incident: dict) -> None:
        logger.info('incident #%s pushed to in-app feed (%s on asset %s)',
                    incident.get('id'), incident.get('signal_type'),
                    incident.get('asset_id'))


def _notifier() -> Notifier:
    """Resolve the active notifier. Today always in-app; Phase 2 can branch on a
    Setting (e.g. incident_push_channel) without touching the detector."""
    return InAppNotifier()


# ─────────────────────────────────────────────────────────────────────────────
# Settings / helpers
# ─────────────────────────────────────────────────────────────────────────────
def _get_setting(con, key, default=''):
    try:
        r = con.execute("SELECT value FROM setting WHERE key=%s", (key,)).fetchone()
        return r['value'] if r and r['value'] is not None else default
    except Exception:
        return default


def _effective_tier(con, signal_type):
    override = _get_setting(con, f'incident_tier_{signal_type}', '')
    if override.strip() in ('0', '1', '2'):
        return int(override.strip())
    return AUTONOMY.get(signal_type, TIER_NOTIFY)


def _is_server_or_critical(con, asset_id):
    """Robustly decide whether an asset is a server / critical box that must
    never auto-remediate. Checks (in order, any hit wins):
      * category in the server set (server/dc/nas/hypervisor/…),
      * 'server' in device_type,
      * 'server' in os_version (e.g. 'Windows Server 2019'),
      * the hostname matching a server-name convention (the Bug-3 backstop, for
        mislabeled assets like SENSEL-SERVER-1 filed as a 'Workstation').
    Fail-CLOSED: if we cannot read the asset, treat it as server (never auto)."""
    if asset_id is None:
        return False
    try:
        a = con.execute(
            "SELECT name, category, device_type, os_version FROM asset WHERE id=%s",
            (asset_id,)
        ).fetchone()
    except Exception:
        return True  # fail closed — can't verify it's safe, so don't auto-run
    if not a:
        return True
    cat = (a['category'] or '').strip().lower()
    dtype = (a['device_type'] or '').strip().lower()
    osv = (a['os_version'] or '').strip().lower()
    name = (a['name'] or '').strip()
    if cat in _SERVER_CATEGORIES or cat == 'dc':
        return True
    if 'server' in dtype or 'server' in osv:
        return True
    if name and _SERVER_NAME_RE.search(name):
        return True
    return False


def _force_tier_for_asset(con, asset_id, tier):
    """Servers/critical assets are forced to tier-2 (notify only) — never auto."""
    if asset_id is None:
        return tier
    if _is_server_or_critical(con, asset_id):
        return max(tier, TIER_NOTIFY)
    return tier


# ─────────────────────────────────────────────────────────────────────────────
# Action templates (DETERMINISTIC — the AI never writes these)
# Each returns a list of action dicts: {key,label,kind,risk_tier,run_payload}
#   kind: 'run' (enqueue run_script) | 'ticket' (open a ticket) | 'dismiss'
# run_payload mirrors what alert_service._enqueue_remediation expects as `payload`.
# ─────────────────────────────────────────────────────────────────────────────
_TICKET_ACTION = {'key': 'ticket', 'label': 'Open ticket', 'kind': 'ticket',
                  'risk_tier': 1, 'run_payload': None}
_DISMISS_ACTION = {'key': 'dismiss', 'label': 'Dismiss', 'kind': 'dismiss',
                   'risk_tier': 0, 'run_payload': None}


def _disk_cleanup_payload():
    """The proven-safe C: cache cleanup (reuses alert_service._disk_cleanup_code)."""
    import alert_service as _svc
    return {
        'type':    'run_script',
        'shell':   'powershell',
        'code':    _svc._disk_cleanup_code('C:'),
        'timeout': _svc._DISK_SCRIPT_TIMEOUT,
    }


def _restart_service_payload(service_name):
    # NB: service_down is currently NO-OP (no telemetry source), but the template
    # is here and safe so the action set is complete the moment a source exists.
    safe = (service_name or '').replace("'", "''")
    code = (f"$ErrorActionPreference='SilentlyContinue'\n"
            f"$svc='{safe}'\n"
            f"\"Restarting service $svc...\"\n"
            f"Restart-Service -Name $svc -Force -EA SilentlyContinue\n"
            f"Start-Sleep -Seconds 3\n"
            f"$s=Get-Service -Name $svc -EA SilentlyContinue\n"
            f"if($s){{ \"Service $svc is now: $($s.Status)\" }} else {{ \"Service $svc not found\" }}")
    return {'type': 'run_script', 'shell': 'powershell', 'code': code, 'timeout': 120}


def build_actions(con, signal_type, ctx, asset_id=None):
    """Return the templated action set for a signal. `ctx` carries detector
    specifics (e.g. {'service': 'Spooler'} or {'patch_job_id': 42}).

    Bug-3 guard: for server/critical assets we STRIP every 'run' action so the
    feed never renders an Approve-to-run button (notify-only: ticket/dismiss).
    This is belt-and-braces with _force_tier_for_asset (which forces Tier-2): the
    stored proposed_actions themselves carry no runnable action for a server, so
    even the chat/approve_fix paths have nothing to execute."""
    actions = _build_actions_raw(signal_type, ctx)
    if asset_id is not None and _is_server_or_critical(con, asset_id):
        actions = [a for a in actions if a.get('kind') != 'run']
    return actions


def _build_actions_raw(signal_type, ctx):
    if signal_type == 'disk_low':
        return [
            {'key': 'clear_caches', 'label': 'Clear safe caches (C:)',
             'kind': 'run', 'risk_tier': 0, 'run_payload': _disk_cleanup_payload()},
            _TICKET_ACTION, _DISMISS_ACTION,
        ]
    if signal_type == 'service_down':
        svc = ctx.get('service') or 'the service'
        return [
            {'key': 'restart_service', 'label': f'Restart {svc}',
             'kind': 'run', 'risk_tier': 1,
             'run_payload': _restart_service_payload(ctx.get('service'))},
            _TICKET_ACTION, _DISMISS_ACTION,
        ]
    if signal_type == 'patch_failed':
        # "Retry patch" re-enqueues the failed job's update_ids as a fresh
        # install_patches remediation. Handled specially in the act endpoint
        # (re-INSERT into rmm_patch_job) — payload carries the source job id.
        return [
            {'key': 'retry_patch', 'label': 'Retry patch install',
             'kind': 'run', 'risk_tier': 1,
             'run_payload': {'type': 'retry_patch_job',
                             'patch_job_id': ctx.get('patch_job_id')}},
            _TICKET_ACTION, _DISMISS_ACTION,
        ]
    # agent_offline_but_up + defender_critical: notify-only.
    return [_TICKET_ACTION, _DISMISS_ACTION]


# ─────────────────────────────────────────────────────────────────────────────
# Diagnosis (AI = narrative ONLY; fail-safe deterministic fallback)
# ─────────────────────────────────────────────────────────────────────────────
def _diagnose(signal_type, ctx, fallback):
    """Return (diagnosis_text, ai_confidence, ai_model). AI narrative only; if the
    AI isn't configured or errors, return the deterministic fallback string."""
    try:
        import ai_config
        import ai_engine
        if not ai_config.ready():
            return fallback, None, None
        model = ai_config.chat_model()
        prompt = (
            "You are an IT operations assistant. In 1-3 plain-English sentences, "
            "explain the likely cause of this device condition and what the "
            "proposed fix will do. Be concise and specific. Do NOT output code, "
            "commands, or JSON — narrative only.\n\n"
            f"Signal: {signal_type}\nContext: {json.dumps(ctx, default=str)}"
        )
        text = ai_engine._openai_chat([{"role": "user", "content": prompt}],
                                      max_tokens=220)
        # Confidence: we can't get a true probability from chat; use a fixed,
        # honest heuristic (0.7) to mean "AI-generated narrative present".
        return (text or fallback), 0.7, model
    except Exception as e:
        logger.info('incident diagnose fell back to deterministic (%s): %s',
                    signal_type, e)
        return fallback, None, None


# ─────────────────────────────────────────────────────────────────────────────
# Incident upsert (dedup + cooldown)
#
# DEDUP CONTRACT (Bug-1 fix): there must be at most ONE open incident per
# (asset_id, signal_type). Re-detection of an already-open condition UPDATEs the
# existing row (bumps updated_at/last-seen, detect_count, severity) and returns
# None — it NEVER inserts a second row. A NEW insert happens only when no open
# incident exists and the (asset, signal) is not in post-resolve cooldown.
#
# Two layers of protection:
#   1. The scheduled scan runs single-instance (a file lock in sync_scheduler) so
#      the 5 gunicorn workers don't each fire the same scan concurrently. This is
#      the primary fix — the original duplicates were 5 workers racing the
#      check-then-insert, all committing before any other saw the row.
#   2. This check-then-update-else-insert + the partial-unique index
#      (uq_agent_incident_open) as a backstop against any residual race (e.g. a
#      manual "Scan now" overlapping the scheduled pass).
# ─────────────────────────────────────────────────────────────────────────────
# Severity rank for "upgrade only" on re-detection (never downgrade an open one).
_SEVERITY_RANK = {'info': 0, 'warning': 1, 'critical': 2}


def _find_open_incident(con, asset_id, signal_type):
    """Return the existing OPEN incident row for (asset, signal) or None."""
    return con.execute(
        f"""SELECT id, severity, attempt_count FROM agent_incident
            WHERE asset_id IS NOT DISTINCT FROM %s AND signal_type=%s
              AND status IN {str(_OPEN_STATUSES)}
            ORDER BY id LIMIT 1""",
        (asset_id, signal_type)
    ).fetchone()


def _in_cooldown(con, asset_id, signal_type):
    mins = _RESOLVE_COOLDOWN_MIN.get(signal_type, 60)
    row = con.execute(
        """SELECT 1 FROM agent_incident
           WHERE asset_id IS NOT DISTINCT FROM %s AND signal_type=%s
             AND status IN ('resolved','dismissed','auto_handled','escalated')
             AND updated_at > NOW() - (%s || ' minutes')::interval
           LIMIT 1""",
        (asset_id, signal_type, str(mins))
    ).fetchone()
    return bool(row)


def _refresh_open_incident(con, inc_row, severity, diag):
    """Re-detection of an already-open condition: bump last-seen + detect_count,
    upgrade severity if it worsened, refresh the diagnosis text. Idempotent — does
    NOT change status, does NOT re-enqueue, does NOT create a row.

    Commits ITSELF: the detectors only commit when _create_incident returns a new
    id (truthy), and this refresh path returns None, so without committing here
    the bump would be rolled back when the pooled connection is returned. (Bug-1
    follow-up — the refresh must actually persist.)"""
    cur_rank = _SEVERITY_RANK.get((inc_row.get('severity') or '').lower(), 1)
    new_rank = _SEVERITY_RANK.get((severity or '').lower(), 1)
    sev = severity if new_rank > cur_rank else inc_row['severity']
    con.execute(
        """UPDATE agent_incident
           SET updated_at=NOW(),
               detect_count=COALESCE(detect_count,1)+1,
               severity=%s,
               diagnosis_text=COALESCE(%s, diagnosis_text)
           WHERE id=%s""",
        (sev, diag, inc_row['id']))
    con.commit()


def _create_incident(con, *, asset_id, agent_id, signal_type, severity,
                     dedup_key, ctx, fallback_diag):
    """Detect-or-update. Returns a NEW incident id if one was created, else None
    (re-detection of an already-open incident, or in post-resolve cooldown).
    Caller commits."""
    # (1) Already open? Refresh it (last-seen/severity/count) and do NOT insert.
    existing = _find_open_incident(con, asset_id, signal_type)
    if existing:
        _refresh_open_incident(con, existing, severity, None)
        return None
    # (2) Recently resolved? Honour the cooldown before re-opening.
    if _in_cooldown(con, asset_id, signal_type):
        return None

    diag, conf, model = _diagnose(signal_type, ctx, fallback_diag)
    actions = build_actions(con, signal_type, ctx, asset_id=asset_id)

    tier = _force_tier_for_asset(con, asset_id, _effective_tier(con, signal_type))

    # Tier-0 auto: only valid if the signal actually has a 'run' action whose
    # risk_tier is 0 (the proven-safe one). Otherwise downgrade to propose.
    auto_action = None
    if tier == TIER_AUTO:
        auto_action = next((a for a in actions
                            if a['kind'] == 'run' and a.get('risk_tier') == 0), None)
        if not auto_action:
            tier = TIER_PROPOSE

    # Bug-2 fix: the status must reflect REALITY. A Tier-0 auto incident is NOT
    # "handled" the instant it is enqueued — the box may be offline and the
    # cleanup only queued. It enters 'remediating' (an OPEN state); only the
    # verify pass flips it to 'resolved' once the queue row completes (exit 0)
    # AND the signal has actually cleared. Tier-1 -> awaiting_approval (human
    # clicks), Tier-2 -> diagnosed (notify-only).
    status = ('remediating' if (tier == TIER_AUTO and auto_action)
              else 'awaiting_approval' if tier == TIER_PROPOSE
              else 'diagnosed')

    try:
        row = con.execute(
            """INSERT INTO agent_incident
                 (asset_id, agent_id, signal_type, severity, dedup_key, status,
                  diagnosis_text, ai_confidence, ai_model, proposed_actions,
                  pushed_channel, detect_count, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,NOW(),NOW())
               RETURNING id""",
            (asset_id, agent_id, signal_type, severity, dedup_key, status,
             diag, conf, model, json.dumps(actions),
             _notifier().channel)
        ).fetchone()
    except Exception as e:
        # Backstop: a residual race (manual scan overlapping the scheduled pass)
        # could collide on uq_agent_incident_open. Treat the unique violation as
        # "already open" — refresh the existing row instead of inserting.
        con.rollback()
        logger.info('incident insert collided (already open) asset=%s %s: %s',
                    asset_id, signal_type, e)
        again = _find_open_incident(con, asset_id, signal_type)
        if again:
            _refresh_open_incident(con, again, severity, None)
        return None
    inc_id = row['id']

    # Tier-0: auto-enqueue the safe action right now and stamp it. Status stays
    # 'remediating' until verify confirms it actually ran AND cleared.
    #
    # If the enqueue genuinely FAILED (rq_id is None — e.g. the gateway POST
    # errored, NOT merely an offline box which still gets a queued row), do NOT
    # leave the incident stranded in 'remediating': the verify pass keys off the
    # rmm_remediation_queue row, so a NULL queue id would never be re-evaluated
    # and re-detection stays blocked forever. Park it as awaiting_approval so a
    # human can retry the safe action from the feed. An offline box returns a real
    # queued row id, so it correctly stays remediating until the box flushes it.
    if tier == TIER_AUTO and auto_action:
        rq_id = _enqueue_action(con, asset_id, agent_id, auto_action)
        if rq_id is None:
            con.execute(
                """UPDATE agent_incident
                   SET status='awaiting_approval', chosen_action=%s, updated_at=NOW(),
                       verify_result='auto-enqueue failed (gateway unreachable) — awaiting manual retry'
                   WHERE id=%s""",
                (auto_action['key'], inc_id))
        else:
            con.execute(
                """UPDATE agent_incident
                   SET chosen_action=%s, remediation_queue_id=%s,
                       attempt_count=attempt_count+1, updated_at=NOW()
                   WHERE id=%s""",
                (auto_action['key'], rq_id, inc_id))

    _notifier().push({'id': inc_id, 'signal_type': signal_type,
                      'asset_id': asset_id})

    # ── Auto-kick the agentic triage loop for ACTIONABLE signals only ──────────
    # disk_low / patch_failed / service_down (Tier 0/1) → triage NOW so the
    # recommendation is ready when a human opens it. Tier-2 NOTIFY signals
    # (defender_critical, agent_offline_but_up) triage LAZILY on first open — we
    # do NOT auto-burn dozens of gpt-4o loops for the Defender list (cost control).
    # Fail-safe: a triage error never affects incident creation.
    if signal_type in _AUTO_TRIAGE_SIGNALS:
        try:
            import triage_agent
            triage_agent.triage_incident(inc_id)
        except Exception as e:
            logger.info('auto-triage skipped for incident %s: %s', inc_id, e)
    return inc_id


# Signals whose triage loop fires automatically on creation (the rest are lazy).
_AUTO_TRIAGE_SIGNALS = {'disk_low', 'patch_failed', 'service_down'}


def _enqueue_action(con, asset_id, agent_id, action):
    """Enqueue a 'run' action via the EXISTING remediation path. Returns the
    rmm_remediation_queue row id (or None). Never raises."""
    try:
        import alert_service as _svc
        payload = action.get('run_payload') or {}
        # Stable dedup substring so the existing in-flight guard works.
        dedup = None
        code = payload.get('code') or ''
        if 'Safe cache cleanup on' in code:
            dedup = 'Safe cache cleanup on'
        res = _svc._enqueue_remediation(
            agent_id, asset_id, 'run_script', payload, dedup_substr=dedup)
        return res.get('row_id') or res.get('existing_id')
    except Exception as e:
        logger.warning('incident enqueue_action failed: %s', e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DETECTORS — each returns the count it created. Individually fail-safe.
# Build them all; NO-OP (don't fabricate) when a source isn't reliably present.
# ─────────────────────────────────────────────────────────────────────────────
def _detect_disk_low(con):
    """Latest telemetry per agent; FIXED-drive free% below threshold."""
    created = 0
    threshold_pct = float(_get_setting(con, 'incident_disk_low_pct', '10'))
    threshold_gb = float(_get_setting(con, 'incident_disk_low_gb', '10'))
    rows = con.execute(
        """SELECT DISTINCT ON (t.agent_id)
                  t.agent_id, t.asset_id, t.hostname, t.disk_json
           FROM rmm_telemetry t
           WHERE t.captured_at > NOW() - INTERVAL '2 hours'
           ORDER BY t.agent_id, t.captured_at DESC"""
    ).fetchall()
    for r in rows:
        if not r['disk_json']:
            continue
        try:
            disks = json.loads(r['disk_json'])
        except Exception:
            continue
        for d in (disks if isinstance(disks, list) else []):
            # DriveType gate: FIXED only (3). No drive_type -> legacy OS-drive gate.
            dt = d.get('drive_type')
            mp = (d.get('mountpoint') or '').strip().upper().rstrip('\\').rstrip('/')
            is_os = mp == 'C:' or (d.get('mountpoint') or '') == '/'
            if dt is not None:
                try:
                    if int(dt) != 3:
                        continue
                except (TypeError, ValueError):
                    if not is_os:
                        continue
            elif not is_os:
                continue
            total = d.get('total_gb') or 0
            free = d.get('free_gb')
            if free is None and 'percent' in d and total:
                free = round(total * (100 - d['percent']) / 100.0, 1)
            free_pct = (100 - d['percent']) if 'percent' in d else (
                round(free / total * 100, 1) if total else 100)
            low = free_pct <= threshold_pct or (free is not None and free <= threshold_gb)
            if not low:
                continue
            drive = d.get('device', mp or '?')
            ctx = {'host': r['hostname'], 'drive': drive,
                   'free_pct': round(free_pct, 1), 'free_gb': free,
                   'total_gb': total}
            fb = (f"{r['hostname']} drive {drive} is low on space "
                  f"({free_pct:.0f}% free). The safe-cache cleanup will clear "
                  f"Windows/Temp, Windows Update cache, per-user temp, browser "
                  f"caches and the Recycle Bin on C: — no user data is touched.")
            if _create_incident(con, asset_id=r['asset_id'], agent_id=r['agent_id'],
                                signal_type='disk_low',
                                severity='critical' if free_pct <= 5 else 'warning',
                                dedup_key=f"disk_low:{r['asset_id']}:{drive}",
                                ctx=ctx, fallback_diag=fb):
                con.commit()
                created += 1
            break  # one disk incident per agent
    return created


def _detect_service_down(con):
    """A watched critical service not Running — IF telemetry carries service state.
    Our telemetry (security_json/sysinfo_json) does NOT include running-service
    state today, so this detector NO-OPs (it does not fabricate). The action
    template + tier are already defined so it lights up the moment the agent
    starts shipping a services list."""
    # No reliable source -> no-op. (Checked sysinfo_json/security_json schema.)
    return 0


def _detect_agent_offline_but_up(con):
    """asset.online_state shows online/recent via a NON-RMM signal while the RMM
    agent's last_seen is stale (>30m) and the agent is enabled. Notify-only —
    you can't run_script on an offline agent."""
    created = 0
    rows = con.execute(
        """SELECT ra.agent_id, ra.asset_id, a.name AS host, a.online_state,
                  a.last_seen AS asset_seen, ra.last_seen_at AS agent_seen
           FROM rmm_agent ra
           JOIN asset a ON a.id = ra.asset_id
           WHERE ra.enabled = TRUE
             AND a.online_state = 'online'
             AND a.last_seen > NOW() - INTERVAL '30 minutes'
             AND (ra.last_seen_at IS NULL
                  OR ra.last_seen_at < NOW() - INTERVAL '30 minutes')"""
    ).fetchall()
    for r in rows:
        ctx = {'host': r['host'], 'asset_online_state': r['online_state'],
               'asset_last_seen': r['asset_seen'], 'agent_last_seen': r['agent_seen']}
        fb = (f"{r['host']} appears reachable on the network but its RMM agent "
              f"has not checked in for over 30 minutes. The agent service may be "
              f"stopped or wedged — it likely needs a manual restart or reinstall "
              f"(no remote script can run until the agent reconnects).")
        if _create_incident(con, asset_id=r['asset_id'], agent_id=r['agent_id'],
                            signal_type='agent_offline_but_up', severity='warning',
                            dedup_key=f"agent_offline:{r['asset_id']}",
                            ctx=ctx, fallback_diag=fb):
            con.commit()
            created += 1
    return created


def _detect_patch_failed(con):
    """rmm_patch_job status='failed' in the last 48h not already incident'd."""
    created = 0
    rows = con.execute(
        """SELECT j.id AS job_id, j.agent_id, ra.asset_id, a.name AS host,
                  j.titles, j.result_json, j.updated_at
           FROM rmm_patch_job j
           LEFT JOIN rmm_agent ra ON ra.agent_id = j.agent_id
           LEFT JOIN asset a ON a.id = ra.asset_id
           WHERE j.status = 'failed'
             AND j.updated_at > NOW() - INTERVAL '48 hours'
           ORDER BY j.updated_at DESC"""
    ).fetchall()
    for r in rows:
        titles = []
        try:
            titles = json.loads(r['titles']) if r['titles'] else []
        except Exception:
            titles = []
        ctx = {'host': r['host'], 'patch_job_id': r['job_id'],
               'titles': titles[:5]}
        title_str = ('; '.join(titles[:3]) + ('…' if len(titles) > 3 else '')) or 'update(s)'
        fb = (f"A Windows Update install job on {r['host'] or r['agent_id']} "
              f"failed ({title_str}). Retrying re-queues the same updates for "
              f"install; if it fails again it likely needs a Windows Update "
              f"reset or manual review.")
        if _create_incident(con, asset_id=r['asset_id'], agent_id=r['agent_id'],
                            signal_type='patch_failed', severity='warning',
                            dedup_key=f"patch_failed:{r['job_id']}",
                            ctx=ctx, fallback_diag=fb):
            con.commit()
            created += 1
    return created


def _detect_defender_critical(con):
    """device_vulnerability status='Open' severity='Critical', deduped PER ASSET
    (not per CVE — don't spam). Notify-only (no auto-remediate). Maps on asset_id
    = asset.id (NOT the device hash)."""
    created = 0
    rows = con.execute(
        """SELECT v.asset_id, a.name AS host, ra.agent_id,
                  COUNT(*) AS cve_count,
                  MIN(v.cve_id) AS sample_cve, MAX(v.cvss) AS max_cvss
           FROM device_vulnerability v
           JOIN asset a ON a.id = v.asset_id
           LEFT JOIN rmm_agent ra ON ra.asset_id = v.asset_id AND ra.enabled = TRUE
           WHERE v.status = 'Open' AND LOWER(v.severity) = 'critical'
           GROUP BY v.asset_id, a.name, ra.agent_id"""
    ).fetchall()
    for r in rows:
        ctx = {'host': r['host'], 'critical_cve_count': r['cve_count'],
               'sample_cve': r['sample_cve'], 'max_cvss': r['max_cvss']}
        fb = (f"{r['host']} has {r['cve_count']} open CRITICAL vulnerabilit"
              f"{'y' if r['cve_count'] == 1 else 'ies'} reported by Defender "
              f"(e.g. {r['sample_cve']}). These need patching/upgrade review — "
              f"open a ticket to triage; the Tracker does not auto-remediate CVEs.")
        if _create_incident(con, asset_id=r['asset_id'], agent_id=r['agent_id'],
                            signal_type='defender_critical', severity='critical',
                            dedup_key=f"defender_critical:{r['asset_id']}",
                            ctx=ctx, fallback_diag=fb):
            con.commit()
            created += 1
    return created


_DETECTORS = (
    ('disk_low',             _detect_disk_low),
    ('service_down',         _detect_service_down),
    ('agent_offline_but_up', _detect_agent_offline_but_up),
    ('patch_failed',         _detect_patch_failed),
    ('defender_critical',    _detect_defender_critical),
)


# ─────────────────────────────────────────────────────────────────────────────
# VERIFY pass — re-evaluate remediating incidents whose queue row finished.
# ─────────────────────────────────────────────────────────────────────────────
def _verify_pass(con):
    """For each incident in remediating/auto_handled with a finished queue row,
    re-check the signal: cleared -> resolved, still present -> escalate (or retry
    until the circuit-breaker trips)."""
    checked = 0
    # (a) Queue-backed incidents (disk_low, service_down) — verify when their
    #     rmm_remediation_queue row reaches a terminal state.
    rows = con.execute(
        """SELECT i.id, i.asset_id, i.agent_id, i.signal_type, i.attempt_count,
                  i.chosen_action, q.status AS q_status, q.result_json
           FROM agent_incident i
           JOIN rmm_remediation_queue q ON q.id = i.remediation_queue_id
           WHERE i.status IN ('remediating','auto_handled')
             AND q.status IN ('completed','failed','no_op','abandoned')"""
    ).fetchall()
    # (b) patch_failed incidents are remediated by re-queueing a NEW rmm_patch_job
    #     (NOT an rmm_remediation_queue row), so they carry no remediation_queue_id.
    #     Verify them off the agent's latest patch-job status instead.
    patch_rows = con.execute(
        """SELECT i.id, i.asset_id, i.agent_id, i.signal_type, i.attempt_count,
                  i.chosen_action,
                  (SELECT status FROM rmm_patch_job j
                   WHERE j.agent_id = i.agent_id
                   ORDER BY j.id DESC LIMIT 1) AS q_status,
                  NULL AS result_json
           FROM agent_incident i
           WHERE i.status = 'remediating'
             AND i.signal_type = 'patch_failed'
             AND i.remediation_queue_id IS NULL
             AND i.agent_id IS NOT NULL"""
    ).fetchall()
    # Only act on patch rows whose latest job actually finished (completed/failed).
    patch_rows = [r for r in patch_rows
                  if r['q_status'] in ('completed', 'failed', 'success')]
    # (c) Self-heal for Tier-0 incidents parked in awaiting_approval because the
    #     auto-enqueue FAILED (gateway unreachable) — these carry NO queue row, so
    #     branch (a) can't see them, and re-detection is blocked while they're
    #     open. If the disk signal has since cleared on its own, resolve them
    #     (which starts the cooldown and unblocks re-detection); otherwise leave
    #     them for the human. Treat them as "succeeded=False" so the resolve only
    #     happens via the cleared-signal check below. Bounded to disk_low (the only
    #     Tier-0 signal) with NULL queue id, older than 30 min, attempt_count 0.
    stranded_rows = con.execute(
        """SELECT i.id, i.asset_id, i.agent_id, i.signal_type, i.attempt_count,
                  i.chosen_action, NULL AS q_status, NULL AS result_json
           FROM agent_incident i
           WHERE i.status = 'awaiting_approval'
             AND i.signal_type = 'disk_low'
             AND i.remediation_queue_id IS NULL
             AND i.chosen_action IS NOT NULL
             AND i.updated_at < NOW() - INTERVAL '30 minutes'"""
    ).fetchall()
    for r in list(rows) + list(patch_rows) + list(stranded_rows):
        checked += 1
        # 'stranded' = a Tier-0 row parked because the auto-enqueue failed; it has
        # no queue row (q_status None) and nothing "ran", so it can only be cleared
        # by the signal resolving on its own (self-heal after a transient gateway
        # blip). It must NOT count as a failed attempt or hit the circuit breaker.
        stranded = r['q_status'] is None and r['signal_type'] != 'patch_failed'
        succeeded = r['q_status'] in ('completed', 'no_op', 'success')
        still_bad = _signal_still_present(con, r['signal_type'], r['agent_id'],
                                          r['asset_id'])
        cleared = (succeeded or stranded) and not still_bad
        # Collision guard: the escalate/park branches below move a row back INTO
        # an OPEN status. If ANOTHER open incident already exists for this
        # (asset, signal) — e.g. several auto_handled rows piled up from rapid
        # scans — reopening this one would violate uq_agent_incident_open. In that
        # case resolve THIS row as superseded instead of crashing the pass.
        other_open = None
        if not cleared:
            other_open = con.execute(
                f"""SELECT id FROM agent_incident
                    WHERE asset_id IS NOT DISTINCT FROM %s AND signal_type=%s
                      AND id <> %s AND status IN {str(_OPEN_STATUSES)} LIMIT 1""",
                (r['asset_id'], r['signal_type'], r['id'])).fetchone()
        if other_open:
            con.execute(
                """UPDATE agent_incident
                   SET status='resolved', resolved_at=NOW(), updated_at=NOW(),
                       verify_result=%s WHERE id=%s""",
                (f'superseded by open incident #{other_open["id"]}', r['id']))
            con.commit()
            continue
        if cleared:
            con.execute(
                """UPDATE agent_incident
                   SET status='resolved', resolved_at=NOW(), updated_at=NOW(),
                       verify_result='resolved: remediation completed and signal cleared'
                   WHERE id=%s""", (r['id'],))
            con.commit()
            _post_verify_to_thread(
                con, r['id'],
                "Verified: the remediation completed and the condition has cleared. "
                "Resolving this incident.", r.get('result_json'))
        elif stranded:
            # Parked-and-still-bad: nothing ran (enqueue had failed), so this is
            # NOT a failed attempt — don't escalate or rewrite it. Leave it
            # awaiting_approval for the human; a later scan will re-check / resolve
            # if the disk frees up on its own.
            continue
        elif r['attempt_count'] >= _MAX_ATTEMPTS:
            # Circuit-breaker: stop retrying, escalate + auto-open a ticket.
            tid = _open_ticket(con, r['asset_id'],
                               f"[Auto-escalated] {r['signal_type']} remediation "
                               f"failed {r['attempt_count']}x", r['signal_type'])
            con.execute(
                """UPDATE agent_incident
                   SET status='escalated', updated_at=NOW(),
                       verify_result=%s
                   WHERE id=%s""",
                (f'escalated to ticket #{tid} after {r["attempt_count"]} attempts'
                 if tid else 'escalated (ticket create failed)', r['id']))
            con.commit()
            _post_verify_to_thread(
                con, r['id'],
                f"The fix was attempted {r['attempt_count']}x but the condition "
                f"persists. Escalated" + (f" to ticket #{tid}." if tid else "."),
                r.get('result_json'))
        else:
            # Failed/still-bad but under the breaker: park as awaiting_approval so a
            # human (or a future re-propose) can decide. Don't auto-retry silently.
            con.execute(
                """UPDATE agent_incident
                   SET status='awaiting_approval', updated_at=NOW(),
                       verify_result='remediation did not clear the signal — needs review'
                   WHERE id=%s""", (r['id'],))
            con.commit()
            _post_verify_to_thread(
                con, r['id'],
                "The fix ran but the condition is still present. Parking for review — "
                "reply here to investigate further or approve another action.",
                r.get('result_json'))
    return checked


def _post_verify_to_thread(con, incident_id, text, result_json=None):
    """Post a remediation result back into the incident chat thread (fail-safe)."""
    try:
        import triage_agent
        meta = None
        if result_json:
            try:
                rj = json.loads(result_json) if isinstance(result_json, str) else result_json
                out = (rj.get('stdout') or '')[:1500]
                if out:
                    text = text + "\n\n[agent output]\n" + out
            except Exception:
                pass
        triage_agent.post_message(con, incident_id, 'system', text, meta=meta)
    except Exception as e:
        logger.info('verify thread post skipped for %s: %s', incident_id, e)


def _signal_still_present(con, signal_type, agent_id, asset_id):
    """Cheap re-check of a single asset's signal for the verify pass.
    Returns True if the original condition is still present."""
    try:
        if signal_type == 'disk_low':
            r = con.execute(
                """SELECT disk_json FROM rmm_telemetry
                   WHERE asset_id=%s ORDER BY captured_at DESC LIMIT 1""",
                (asset_id,)).fetchone()
            if not r or not r['disk_json']:
                return False
            disks = json.loads(r['disk_json'])
            for d in (disks if isinstance(disks, list) else []):
                if d.get('drive_type') not in (None, 3):
                    continue
                if 'percent' in d and (100 - d['percent']) <= 10:
                    return True
            return False
        if signal_type == 'patch_failed':
            # Keyed on agent_id (patch jobs are per-agent). The retry created a
            # NEW job; "still present" = the latest job for this agent FAILED.
            r = con.execute(
                """SELECT status FROM rmm_patch_job
                   WHERE agent_id = %s ORDER BY id DESC LIMIT 1""",
                (agent_id,)).fetchone()
            return bool(r) and r['status'] == 'failed'
    except Exception:
        return False
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Ticket helper — normal columns ONLY (GUARD: never touch ticket_note.created_by)
# ─────────────────────────────────────────────────────────────────────────────
def _open_ticket(con, asset_id, subject, signal_type, body=None):
    """Create a support_ticket via the normal columns. Returns ticket id or None.
    Deliberately avoids ai_engine's ticket_note path (the created_by-column bug)."""
    try:
        host = None
        if asset_id:
            a = con.execute("SELECT name FROM asset WHERE id=%s",
                            (asset_id,)).fetchone()
            host = a['name'] if a else None
        desc = body or f"Auto-created from a Proactive AI Remediation incident ({signal_type})."
        row = con.execute(
            """INSERT INTO support_ticket
                 (status, priority, source, subject, description, asset_id,
                  hostname, category, created_at, updated_at)
               VALUES ('Open','Normal','ai_remediation',%s,%s,%s,%s,%s,NOW(),NOW())
               RETURNING id""",
            (subject, desc, asset_id, host, 'RMM')
        ).fetchone()
        return row['id'] if row else None
    except Exception as e:
        logger.warning('incident _open_ticket failed: %s', e)
        try:
            con.rollback()
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────
def scan(app=None):
    """Run all detectors + the verify pass once. Fully fail-safe — a failure in
    any single detector/agent NEVER propagates to the host scheduler loop.
    Returns a summary dict."""
    summary = {'created': {}, 'verified': 0, 'errors': []}
    if app is not None:
        ctx = app.app_context()
        ctx.push()
    else:
        ctx = None
    con = None
    try:
        con = _get_db()
        if _get_setting(con, 'incident_scan_enabled', '1') not in ('1', 'true', 'True'):
            return {'disabled': True}
        for name, fn in _DETECTORS:
            try:
                summary['created'][name] = fn(con)
            except Exception as e:
                logger.exception('incident detector %s failed', name)
                summary['errors'].append(f'{name}: {e}')
                try:
                    con.rollback()
                except Exception:
                    pass
        try:
            summary['verified'] = _verify_pass(con)
        except Exception as e:
            logger.exception('incident verify pass failed')
            summary['errors'].append(f'verify: {e}')
            try:
                con.rollback()
            except Exception:
                pass
    except Exception as e:
        logger.exception('incident scan failed outright')
        summary['errors'].append(str(e))
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        if ctx is not None:
            ctx.pop()
    logger.info('incident scan: %s', summary)
    return summary


def run_incident_scan_job(flask_app):
    """Scheduler entry point (mirrors sync_scheduler's run_*_job wrappers)."""
    try:
        return scan(flask_app)
    except Exception:
        logger.exception('run_incident_scan_job failed')
        return None
