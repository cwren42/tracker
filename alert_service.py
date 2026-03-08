"""
Alert Evaluation Service
Runs as a background thread; evaluates all alert_rule rows and fires
alert_log entries, creates tickets, sends email/Teams notifications.
"""
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, date

import requests

logger = logging.getLogger(__name__)

# How often to run the evaluator (seconds)
EVAL_INTERVAL_S = 300   # 5 minutes


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_str():
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')


def _now():
    return datetime.utcnow()


def _get_db():
    """Return a PostgreSQL connection (thread-safe)."""
    from pg_db import pg_connect
    return pg_connect()


def _send_email(subject, body_html):
    """Send via the app's configured SMTP – import lazily to avoid circular."""
    try:
        from app import app, send_admin_notification
        with app.app_context():
            send_admin_notification(subject, body_html)
    except Exception as e:
        logger.warning(f'Alert email failed: {e}')


def _send_teams(webhook_url, title, body):
    """Post an Adaptive Card to a Teams webhook."""
    if not webhook_url:
        return
    try:
        payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "size": "Medium", "weight": "Bolder",
                         "text": f"🚨 {title}", "wrap": True},
                        {"type": "TextBlock", "text": body, "wrap": True, "isSubtle": True}
                    ]
                }
            }]
        }
        requests.post(webhook_url, json=payload, timeout=10)
    except Exception as e:
        logger.warning(f'Teams notification failed: {e}')


def _get_setting(con, key, default=''):
    row = con.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return row['value'] if row else default


def _cooldown_ok(con, rule_id, agent_id, asset_id, cooldown_minutes):
    """Return True if enough time has passed since the last fire for this rule+target."""
    since = (_now() - timedelta(minutes=cooldown_minutes)).strftime('%Y-%m-%d %H:%M:%S')
    row = con.execute(
        "SELECT id FROM alert_log WHERE rule_id=? AND (agent_id=? OR asset_id=?) AND fired_at > ?",
        (rule_id, agent_id or '', asset_id or 0, since)
    ).fetchone()
    return row is None


def _fire_alert(con, rule, message, agent_id=None, asset_id=None,
                hostname=None, extra_html=''):
    """
    Create alert_log row, optional ticket, email, Teams notification, bell.
    `rule` is a sqlite3.Row from alert_rule.
    """
    rule_id       = rule['id']
    category      = rule['category']
    alert_type    = rule['alert_type']
    cooldown      = rule['cooldown_minutes'] or 60
    email_notify  = rule['email_notify']
    teams_notify  = rule['teams_notify']
    teams_wh      = rule['teams_webhook_url'] or _get_setting(con, 'teams_webhook_url')
    auto_ticket   = rule['auto_ticket']
    priority      = rule['ticket_priority'] or 'Normal'
    assigned_uid  = rule['assigned_to_user_id']
    label         = rule['label'] or alert_type

    if not _cooldown_ok(con, rule_id, agent_id, asset_id, cooldown):
        return  # already fired recently

    # Create ticket
    ticket_id = None
    if auto_ticket:
        try:
            csat_token = str(uuid.uuid4()).replace('-', '')
            cat_map = {
                'agent': 'Hardware', 'asset': 'Hardware',
                'vulnerability': 'Security', 'eagle_eyes': 'General'
            }
            cat = cat_map.get(category, 'General')
            cur = con.execute(
                """INSERT INTO support_ticket
                   (status, priority, category, source, subject, description,
                    hostname, asset_id, assigned_to_user_id, csat_token, created_at, updated_at)
                   VALUES ('Open', ?, ?, 'alert', ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
                (priority, cat, f'[ALERT] {label}',
                 f'{message}\n\nAuto-created by alert rule #{rule_id}.',
                 hostname or '', asset_id, assigned_uid, csat_token)
            )
            ticket_id = cur.lastrowid
        except Exception as e:
            logger.error(f'Auto-ticket creation failed: {e}')

    # Insert alert log
    con.execute(
        """INSERT INTO alert_log (rule_id, category, alert_type, agent_id, asset_id, message, ticket_id, fired_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (rule_id, category, alert_type, agent_id or '', asset_id or 0, message, ticket_id)
    )

    # Insert notification bell
    icon_map = {
        'agent': 'bi-pc-display', 'asset': 'bi-hdd',
        'vulnerability': 'bi-shield-exclamation', 'eagle_eyes': 'bi-eye'
    }
    color_map = {
        'Urgent': 'danger', 'High': 'danger', 'Normal': 'warning', 'Low': 'info'
    }
    link = f'/alerts/center#{category}'
    if ticket_id:
        link = f'/tickets/{ticket_id}'
    con.execute(
        """INSERT INTO notification_bell (title, body, icon, color, link, read_flag, created_at)
           VALUES (?, ?, ?, ?, ?, 0, datetime('now'))""",
        (label, message, icon_map.get(category, 'bi-bell'),
         color_map.get(priority, 'warning'), link)
    )

    con.commit()

    # Email
    if email_notify:
        body_html = f"""
        <div style="font-family:sans-serif;max-width:600px;">
          <h3 style="color:#dc3545;">🚨 Alert: {label}</h3>
          <p><strong>Category:</strong> {category.replace('_',' ').title()}</p>
          <p><strong>Details:</strong> {message}</p>
          {f'<p><strong>Hostname:</strong> {hostname}</p>' if hostname else ''}
          {f'<p><strong>Ticket created:</strong> <a href="/tickets/{ticket_id}">#{ticket_id}</a></p>' if ticket_id else ''}
          {extra_html}
          <hr><p style="color:#888;font-size:.85em;">Alert rule #{rule_id} · {_now().strftime('%Y-%m-%d %H:%M')} UTC</p>
        </div>"""
        _send_email(f'[ALERT] {label}', body_html)

    # Teams
    if teams_notify and teams_wh:
        _send_teams(teams_wh, label,
                    f'{message}{" | Hostname: " + hostname if hostname else ""}')

    logger.info(f'Alert fired: [{category}] {alert_type} — {message}')


# ─────────────────────────────────────────────────────────────────────────────
# Evaluators
# ─────────────────────────────────────────────────────────────────────────────

def _eval_agent_alerts(con, rules_by_type):
    """Check RMM telemetry for every active agent."""
    agents = con.execute(
        """SELECT a.agent_id, a.asset_id, t.hostname,
                  t.cpu_percent, t.ram_percent, t.battery_percent,
                  t.battery_present, t.battery_charging,
                  t.disk_json, t.security_json, a.last_seen_at
           FROM rmm_agent a
           LEFT JOIN rmm_telemetry t ON t.agent_id = a.agent_id
           WHERE a.enabled = true"""
    ).fetchall()

    for ag in agents:
        aid      = ag['agent_id']
        asset_id = ag['asset_id']
        host     = ag['hostname'] or aid

        # ── Offline ──────────────────────────────────────────────────────────
        rule = rules_by_type.get('offline')
        if rule and rule['enabled']:
            last_seen = ag['last_seen_at']
            if last_seen:
                try:
                    ls = datetime.strptime(last_seen[:19], '%Y-%m-%d %H:%M:%S')
                    thresh_m = rule['threshold_value'] or 60
                    if (_now() - ls).total_seconds() / 60 > thresh_m:
                        _fire_alert(con, rule,
                                    f'{host} has not checked in for over {int(thresh_m)} minutes.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)
                except Exception:
                    pass

        # Skip telemetry checks if no data
        if ag['cpu_percent'] is None:
            continue

        # ── CPU ──────────────────────────────────────────────────────────────
        rule = rules_by_type.get('cpu_high')
        if rule and rule['enabled']:
            if (ag['cpu_percent'] or 0) >= rule['threshold_value']:
                _fire_alert(con, rule,
                            f'{host} CPU at {ag["cpu_percent"]:.0f}% (threshold {rule["threshold_value"]:.0f}%).',
                            agent_id=aid, asset_id=asset_id, hostname=host)

        # ── RAM ──────────────────────────────────────────────────────────────
        rule = rules_by_type.get('ram_high')
        if rule and rule['enabled']:
            if (ag['ram_percent'] or 0) >= rule['threshold_value']:
                _fire_alert(con, rule,
                            f'{host} RAM at {ag["ram_percent"]:.0f}% (threshold {rule["threshold_value"]:.0f}%).',
                            agent_id=aid, asset_id=asset_id, hostname=host)

        # ── Disk ─────────────────────────────────────────────────────────────
        disk_json = ag['disk_json']
        if disk_json:
            try:
                disks = json.loads(disk_json)
                for d in (disks if isinstance(disks, list) else []):
                    pct_free = 100 - (d.get('percent', 100))
                    drive    = d.get('device', '?')
                    for rtype in ('disk_critical', 'disk_low'):
                        rule = rules_by_type.get(rtype)
                        if rule and rule['enabled'] and pct_free <= rule['threshold_value']:
                            _fire_alert(con, rule,
                                        f'{host} drive {drive} free space at '
                                        f'{pct_free:.1f}% (threshold {rule["threshold_value"]:.0f}%).',
                                        agent_id=aid, asset_id=asset_id, hostname=host)
                            break  # only fire the most severe
            except Exception:
                pass

        # ── Battery ──────────────────────────────────────────────────────────
        if ag['battery_present']:
            rule = rules_by_type.get('battery_low')
            if rule and rule['enabled']:
                bat = ag['battery_percent'] or 100
                if bat <= rule['threshold_value']:
                    _fire_alert(con, rule,
                                f'{host} battery health at {bat:.0f}% '
                                f'(threshold {rule["threshold_value"]:.0f}%).',
                                agent_id=aid, asset_id=asset_id, hostname=host)

            rule = rules_by_type.get('battery_not_chg')
            if rule and rule['enabled']:
                if ag['battery_present'] and not ag['battery_charging'] \
                        and (ag['battery_percent'] or 100) < 20:
                    _fire_alert(con, rule,
                                f'{host} battery at {ag["battery_percent"]:.0f}% and not charging.',
                                agent_id=aid, asset_id=asset_id, hostname=host)

        # ── Security JSON (AV, Firewall, pending reboot) ─────────────────────
        sec_json = ag['security_json']
        if sec_json:
            try:
                sec = json.loads(sec_json)

                rule = rules_by_type.get('av_disabled')
                if rule and rule['enabled']:
                    av_ok = sec.get('antivirus_enabled', True)
                    av_upd = sec.get('antivirus_updated', True)
                    if not av_ok or not av_upd:
                        reason = 'disabled' if not av_ok else 'definitions out of date'
                        _fire_alert(con, rule,
                                    f'{host} antivirus is {reason}.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)

                rule = rules_by_type.get('firewall_off')
                if rule and rule['enabled']:
                    if not sec.get('firewall_enabled', True):
                        _fire_alert(con, rule,
                                    f'{host} Windows Firewall is disabled.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)

                rule = rules_by_type.get('pending_reboot')
                if rule and rule['enabled']:
                    reboot_since = sec.get('pending_reboot_since')
                    if reboot_since:
                        try:
                            rb = datetime.strptime(reboot_since[:10], '%Y-%m-%d')
                            days = (date.today() - rb.date()).days
                            if days >= rule['threshold_value']:
                                _fire_alert(con, rule,
                                            f'{host} has had a pending reboot for {days} days.',
                                            agent_id=aid, asset_id=asset_id, hostname=host)
                        except Exception:
                            pass

                rule = rules_by_type.get('failed_logins')
                if rule and rule['enabled']:
                    fails = sec.get('failed_logins_24h', 0)
                    if fails >= rule['threshold_value']:
                        _fire_alert(con, rule,
                                    f'{host} had {fails} failed login attempts in the last 24 hours.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)

                rule = rules_by_type.get('new_local_admin')
                if rule and rule['enabled']:
                    new_admins = sec.get('new_local_admins', [])
                    for acct in new_admins:
                        _fire_alert(con, rule,
                                    f'{host}: new local admin account created — {acct}.',
                                    agent_id=aid, asset_id=asset_id, hostname=host)

            except Exception:
                pass


def _eval_asset_alerts(con, rules_by_type):
    """Check asset table for lifecycle/warranty conditions."""
    assets = con.execute(
        """SELECT a.id, a.name, a.warranty_expiry, a.eol_date,
                  a.purchase_date, a.expected_life_years, a.replacement_date, a.last_seen,
                  CASE WHEN ra.asset_id IS NOT NULL THEN 1 ELSE 0 END AS has_rmm
           FROM asset a
           LEFT JOIN rmm_agent ra ON ra.asset_id = a.id AND ra.enabled = true
           WHERE a.status != 'Disposed'"""
    ).fetchall()

    today = date.today()

    for a in assets:
        aid  = a['id']
        name = a['name'] or f'Asset #{aid}'

        # ── Warranty ─────────────────────────────────────────────────────────
        if a['warranty_expiry']:
            try:
                exp = date.fromisoformat(str(a['warranty_expiry']))
                days_left = (exp - today).days

                # Expired
                rule = rules_by_type.get('warranty_expired')
                if rule and rule['enabled'] and days_left < 0:
                    _fire_alert(con, rule,
                                f'{name} warranty expired {abs(days_left)} days ago ({exp}).',
                                asset_id=aid, hostname=name)

                # 30-day warning
                rule = rules_by_type.get('warranty_30')
                if rule and rule['enabled'] and 0 <= days_left <= 30:
                    _fire_alert(con, rule,
                                f'{name} warranty expires in {days_left} days ({exp}).',
                                asset_id=aid, hostname=name)

                # 90-day warning
                rule = rules_by_type.get('warranty_90')
                if rule and rule['enabled'] and 30 < days_left <= 90:
                    _fire_alert(con, rule,
                                f'{name} warranty expires in {days_left} days ({exp}).',
                                asset_id=aid, hostname=name)
            except Exception:
                pass

        # ── OS End of Life ────────────────────────────────────────────────────
        if a['eol_date']:
            try:
                eol = date.fromisoformat(str(a['eol_date']))
                days_left = (eol - today).days

                rule = rules_by_type.get('eol_os_passed')
                if rule and rule['enabled'] and days_left < 0:
                    _fire_alert(con, rule,
                                f'{name} OS reached End of Life {abs(days_left)} days ago ({eol}).',
                                asset_id=aid, hostname=name)

                rule = rules_by_type.get('eol_os')
                if rule and rule['enabled'] and 0 <= days_left <= 90:
                    _fire_alert(con, rule,
                                f'{name} OS End of Life in {days_left} days ({eol}). Planning required.',
                                asset_id=aid, hostname=name)
            except Exception:
                pass

        # ── Device age vs expected life ───────────────────────────────────────
        rule = rules_by_type.get('device_age')
        if rule and rule['enabled'] and a['purchase_date']:
            try:
                pd   = date.fromisoformat(str(a['purchase_date']))
                life = a['expected_life_years'] or 3
                age_years = (today - pd).days / 365.25
                if age_years > life:
                    _fire_alert(con, rule,
                                f'{name} is {age_years:.1f} years old (expected life: {life} years).',
                                asset_id=aid, hostname=name)
            except Exception:
                pass

        # ── Not seen (only for non-RMM assets; 'offline' rule covers agents) ──
        rule = rules_by_type.get('not_seen')
        if rule and rule['enabled'] and a['last_seen'] and not a['has_rmm']:
            try:
                ls = datetime.fromisoformat(str(a['last_seen'])[:19])
                days_absent = (_now() - ls).days
                if days_absent >= rule['threshold_value']:
                    _fire_alert(con, rule,
                                f'{name} has not been seen for {days_absent} days.',
                                asset_id=aid, hostname=name)
            except Exception:
                pass


def _eval_vulnerability_alerts(con, rules_by_type):
    """
    Check vulnerability_cache for new Critical/High CVEs and unpatched CVEs.
    Defender sync must have been run first to populate the cache.
    """
    rule_crit = rules_by_type.get('cve_critical')
    rule_high  = rules_by_type.get('cve_high')
    rule_old   = rules_by_type.get('cve_unpatched')

    if rule_crit and rule_crit['enabled']:
        new_crits = con.execute(
            """SELECT cve_id, name, exposed_machines FROM vulnerability_cache
               WHERE severity='Critical'
               AND synced_at::timestamp > NOW() - INTERVAL '1 day'"""
        ).fetchall()
        for v in new_crits:
            if not (v["exposed_machines"] and int(v["exposed_machines"]) > 0):
                continue
            _fire_alert(con, rule_crit,
                        f'Critical CVE detected: {v["cve_id"]} — {v["name"]} '
                        f'({v["exposed_machines"]} device(s) exposed).',
                        hostname='Defender Vulnerability Feed')

    if rule_high and rule_high['enabled']:
        new_highs = con.execute(
            """SELECT cve_id, name, exposed_machines FROM vulnerability_cache
               WHERE severity='High'
               AND synced_at::timestamp > NOW() - INTERVAL '1 day'"""
        ).fetchall()
        for v in new_highs:
            if not (v["exposed_machines"] and int(v["exposed_machines"]) > 0):
                continue
            _fire_alert(con, rule_high,
                        f'High CVE detected: {v["cve_id"]} — {v["name"]} '
                        f'({v["exposed_machines"]} device(s) exposed).',
                        hostname='Defender Vulnerability Feed')

    if rule_old and rule_old['enabled']:
        thresh_days = int(rule_old['threshold_value'] or 30)
        old_open = con.execute(
            f"""SELECT dv.cve_id, dv.severity, dv.asset_id, a.name as aname
               FROM device_vulnerability dv
               LEFT JOIN asset a ON a.id = dv.asset_id
               WHERE dv.status='Open'
               AND dv.synced_at::timestamp < NOW() - INTERVAL '{thresh_days} days'"""
        ).fetchall()
        for v in old_open:
            _fire_alert(con, rule_old,
                        f'CVE {v["cve_id"]} ({v["severity"]}) on {v["aname"] or v["asset_id"]} '
                        f'has been open for over {thresh_days} days.',
                        asset_id=v['asset_id'])


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def run_evaluator():
    """Blocking loop – run in a daemon thread."""
    logger.info('Alert evaluator started.')
    while True:
        try:
            _run_once()
        except Exception as e:
            logger.error(f'Alert evaluator error: {e}', exc_info=True)
        time.sleep(EVAL_INTERVAL_S)


def _run_once():
    con = _get_db()
    try:
        # Load enabled rules, keyed by alert_type
        rows = con.execute("SELECT * FROM alert_rule").fetchall()
        rules_by_type = {r['alert_type']: r for r in rows}

        _eval_agent_alerts(con, rules_by_type)
        _eval_asset_alerts(con, rules_by_type)
        _eval_vulnerability_alerts(con, rules_by_type)
    finally:
        con.close()


def start_background_thread():
    """Call once from app startup to launch the evaluator daemon thread."""
    t = threading.Thread(target=run_evaluator, daemon=True, name='AlertEvaluator')
    t.start()
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Defender sync helper (called from UI route)
# ─────────────────────────────────────────────────────────────────────────────

def sync_defender_vulnerabilities():
    """
    Pull vulnerabilities from Defender API into vulnerability_cache
    and device_vulnerability tables. Returns (vuln_count, device_count, error).
    """
    try:
        from defender_service import DefenderService
        svc = DefenderService()

        con = _get_db()

        # Sync CVE catalogue
        vulns = svc.get_vulnerabilities()
        vuln_count = 0
        for v in vulns:
            cve_id = v.get('id') or v.get('cveId', '')
            if not cve_id:
                continue
            con.execute(
                """INSERT INTO vulnerability_cache
                   (cve_id, name, severity, cvss, description, exposed_machines, published_on, synced_at)
                   VALUES (?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(cve_id) DO UPDATE SET
                     name=excluded.name, severity=excluded.severity, cvss=excluded.cvss,
                     exposed_machines=excluded.exposed_machines, synced_at=excluded.synced_at""",
                (cve_id, v.get('name',''), v.get('severity','Unknown'),
                 v.get('cvssV3', 0) or 0, v.get('description',''),
                 v.get('exposedMachines', 0) or 0, v.get('publishedOn',''))
            )
            vuln_count += 1

        # Build machine_id → asset_id map from Defender machines list
        machines  = svc.get_machines()
        machine_asset_map = {}  # Defender machine GUID → tracker asset_id
        matched = 0
        for m in machines:
            machine_id = m.get('id')
            fqdn = m.get('computerDnsName', '')
            if not machine_id or not fqdn:
                continue
            # Short name is everything before the first dot (handles FQDNs like HOST.domain.local)
            short_name = fqdn.split('.')[0]

            asset_row = None
            # 1. Exact match against rmm_telemetry hostname
            asset_row = con.execute(
                "SELECT t.asset_id FROM rmm_telemetry t WHERE LOWER(t.hostname)=LOWER(?) LIMIT 1",
                (fqdn,)
            ).fetchone()
            # 2. Short-name match against rmm_telemetry
            if not asset_row and short_name:
                asset_row = con.execute(
                    "SELECT t.asset_id FROM rmm_telemetry t WHERE LOWER(t.hostname)=LOWER(?) LIMIT 1",
                    (short_name,)
                ).fetchone()
            # 3. Exact match against asset.name
            if not asset_row:
                asset_row = con.execute(
                    "SELECT id AS asset_id FROM asset WHERE LOWER(name)=LOWER(?) AND status!='Disposed' LIMIT 1",
                    (fqdn,)
                ).fetchone()
            # 4. Short-name match against asset.name
            if not asset_row and short_name:
                asset_row = con.execute(
                    "SELECT id AS asset_id FROM asset WHERE LOWER(name)=LOWER(?) AND status!='Disposed' LIMIT 1",
                    (short_name,)
                ).fetchone()
            if asset_row:
                machine_asset_map[machine_id] = asset_row['asset_id']
                matched += 1
        logger.info(f'Defender sync: matched {matched}/{len(machines)} Defender machines to tracked assets')

        # Bulk-fetch ALL machine-CVE pairs in one API call (much faster than per-machine calls)
        all_machine_vulns = svc.get_all_machine_vulnerabilities()
        dev_count = 0
        for mv in all_machine_vulns:
            machine_id = mv.get('machineId', '')
            asset_id   = machine_asset_map.get(machine_id)
            if not asset_id:
                continue  # skip machines we can't map to a tracked asset
            cve_id = mv.get('cveId', '')
            if not cve_id:
                continue
            con.execute(
                """INSERT INTO device_vulnerability
                   (asset_id, agent_id, cve_id, severity, cvss, product_name, status, synced_at)
                   VALUES (?,?,?,?,?,?,'Open',datetime('now'))
                   ON CONFLICT(asset_id, cve_id) DO UPDATE SET
                     severity=excluded.severity, cvss=excluded.cvss,
                     product_name=excluded.product_name, synced_at=excluded.synced_at""",
                (asset_id, machine_id, cve_id,
                 mv.get('severity', 'Unknown'), mv.get('cvssV3', 0) or 0,
                 mv.get('productName', ''))
            )
            dev_count += 1

        con.commit()
        con.close()
        return vuln_count, dev_count, None

    except Exception as e:
        logger.error(f'Defender sync failed: {e}', exc_info=True)
        return 0, 0, str(e)
