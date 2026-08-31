"""PROX2 Active Directory machine-trust health monitor (SSH probe).

PROX2 (10.15.0.34) is a Debian/Samba fileserver joined to CORP.CIRQUE.COM via
winbind.  When its machine-account trust with AD breaks, every domain-credential
SMB operation — printer scan-to-folder, share access — fails with a misleading
"Incorrect credentials".  PROX2 is NOT in the RMM fleet, so we can't watch it via
the agent; instead we probe it over SSH using a dedicated key that is locked to a
forced command on PROX2 (/usr/local/sbin/prox-trust-check) which can ONLY ever
print TRUST_OK / TRUST_FAIL — the key grants no shell and no other command.

Edge-triggered: notifies only on OK->FAIL and FAIL->OK transitions, so a
persistent outage never spams.  State persists in Setting keys.  Gated off by
default; set Setting 'prox2_trust_monitor_enabled' = '1' to arm.

Root cause + fix are recorded in memory 'prox2-samba-domain-trust'.
"""

import logging
import subprocess
from datetime import datetime

logger = logging.getLogger(__name__)

PROX2_HOST = '10.15.0.34'
PROX2_USER = 'root'
SSH_KEY = '/home/webuser/.ssh/prox2_trust_ed25519'
KNOWN_HOSTS = '/home/webuser/.ssh/known_hosts'

ENABLED_KEY = 'prox2_trust_monitor_enabled'   # '1' to arm
STATUS_KEY = 'prox2_trust_status'             # 'ok' | 'fail' | 'unknown'
CHECKED_KEY = 'prox2_trust_checked_at'        # ISO timestamp of last probe

REMEDIATION = (
    "Fix (run on PROX2 as root @ 10.15.0.34):\n"
    "  net ads join -S deathstar.corp.cirque.com -U IR-Service   # -S bypasses flaky CLDAP; IR-Service has join rights (no vader needed)\n"
    "  systemctl restart winbind smbd\n"
    "  net ads testjoin -S deathstar.corp.cirque.com   # expect: Join is OK\n"
    "  wbinfo -t                                        # expect: succeeded\n\n"
    "Cause: PROX2's Samba machine-account secret drifted out of sync with the "
    "PROX2$ computer object in AD (AD rotates the machine pw at its 30-day max-age). "
    "machine password timeout is now 604800 (7d) so winbind proactively re-keys and "
    "keeps secrets.tdb + keytab + AD in sync — if this STILL fires, confirm that "
    "setting held (an earlier 'machine password timeout = 0' was the original bug). "
    "See internal memory: prox2-samba-domain-trust."
)


def _probe():
    """Run the locked SSH probe. Returns 'ok' | 'fail' | 'unreachable'."""
    try:
        out = subprocess.run(
            ['ssh', '-i', SSH_KEY, '-n', '-T',
             '-o', 'BatchMode=yes',
             '-o', 'StrictHostKeyChecking=no',
             '-o', f'UserKnownHostsFile={KNOWN_HOSTS}',
             '-o', 'ConnectTimeout=10',
             f'{PROX2_USER}@{PROX2_HOST}'],
            capture_output=True, text=True, timeout=25)
        txt = (out.stdout or '') + '\n' + (out.stderr or '')
        if 'TRUST_OK' in txt:
            return 'ok'
        if 'TRUST_FAIL' in txt:
            return 'fail'
        logger.warning('PROX2 trust probe: unexpected output rc=%s: %s',
                       out.returncode, txt.strip()[:300])
        return 'unreachable'
    except Exception as e:
        logger.warning('PROX2 trust probe error: %s', e)
        return 'unreachable'


def run_prox2_trust_check_job(flask_app):
    """Scheduler entry point. Edge-triggered notify on trust state change."""
    with flask_app.app_context():
        from app import db, Setting

        def _get(k, default=''):
            r = Setting.query.filter_by(key=k).first()
            return (r.value or '').strip() if r and r.value is not None else default

        def _set(k, v):
            r = Setting.query.filter_by(key=k).first()
            if r is None:
                db.session.add(Setting(key=k, value=v))
            else:
                r.value = v

        if _get(ENABLED_KEY) not in ('1', 'true', 'yes', 'on'):
            return  # disarmed

        prev = _get(STATUS_KEY) or 'unknown'
        result = _probe()
        now = datetime.utcnow().isoformat()
        _set(CHECKED_KEY, now)

        # 'unreachable' is a probe/network problem, not a proven trust break —
        # keep the last known trust state and don't fire a false trust alert.
        if result == 'unreachable':
            logger.warning('PROX2 trust probe unreachable at %s (keeping prev=%s)', now, prev)
            db.session.commit()
            return

        _set(STATUS_KEY, result)
        db.session.commit()

        if result == 'fail' and prev != 'fail':
            logger.error('PROX2 AD trust FAIL (was %s)', prev)
            subject = '🚨 PROX2 AD trust BROKEN — scan-to-folder will fail'
            body = (
                '<p><b>PROX2</b> (10.15.0.34) has lost its Active Directory '
                'machine-account trust. Domain-credential SMB auth (printer '
                'scan-to-folder, share access) will now fail with a misleading '
                '<i>"Incorrect credentials"</i>.</p>'
                '<pre style="font-family:monospace;white-space:pre-wrap">%s</pre>'
                % REMEDIATION)
            _notify(subject, body, REMEDIATION)
        elif result == 'ok' and prev == 'fail':
            logger.info('PROX2 AD trust recovered')
            _notify('✅ PROX2 AD trust recovered',
                    '<p><b>PROX2</b> (10.15.0.34) AD machine-trust is healthy '
                    'again — scan-to-folder / domain SMB auth restored.</p>',
                    'PROX2 AD trust recovered — scan-to-folder restored.')


def _notify(subject, body_html, teams_text):
    """Send through the Tracker's existing alert channels (email + optional Teams)."""
    try:
        from alert_service import _send_email, _send_teams, _get_db, _get_setting
        _send_email(subject, body_html)
        try:
            con = _get_db()
            hook = _get_setting(con, 'teams_webhook_url', '')
            con.close()
            if hook:
                _send_teams(hook, subject, teams_text)
        except Exception:
            pass
    except Exception as e:
        logger.warning('PROX2 trust notify failed: %s', e)
