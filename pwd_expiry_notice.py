#!/usr/bin/env python3
"""Mail employees directly, ahead of time, when their AD password is about to expire.

WHY THIS EXISTS
---------------
Windows shows an expiry toast and users demonstrably ignore it. Every ignored
toast becomes an IT interruption a few days later, because the password expires
while the person is remote and the symptom is "the VPN is broken" rather than
"my password expired". Alert rules 39/40 already warn IT -- that stops the
surprise but not the work. This closes the loop by telling the person who can
actually fix it, early, with the two steps that resolve it end to end:

  1. reset at aka.ms/sspr  (Entra SSPR + AD Connect password writeback are
     enabled, so this works with no VPN and no helpdesk), and
  2. re-save the password on the Cirque USA VPN profile.

Step 2 is the one that is always missed and is the direct cause of the repeat
calls: `rasdial <name> *` authenticates for that session only and never writes
the credential, so the auto-reconnect task keeps submitting the PREVIOUS
password after every drop. Verified on STEVEA-MSI 2026-09-09: 07:59:48 link
drop -> 08:00:01 err=691 from the saved credential; after re-saving via
rasphone the same lock/unlock recovered unattended in 17s.

SAFETY
------
This mails real employees, so it is deliberately hard to fire by accident:
  * Setting `pwd_expiry_notify_users` gates it -- 'off' (default) | 'dry' | 'on'.
  * `MAX_PER_RUN` caps a single cycle, so a data problem cannot mail the company.
  * Service accounts and rows without a plausible mailbox are skipped.
  * The pwd_expiry_notice ledger makes each (person, band, password-cycle)
    at-most-once; bands re-arm by themselves on the next password change,
    because AD's expiry timestamp moves.
  * MIN_GAP_HOURS additionally stops a band-boundary crossing from mailing the
    same person twice in one morning.

TIMEZONES
---------
employee.pwd_expires_at is naive **UTC** (ldap_service._filetime_to_dt builds it
from the 1601 UTC epoch). The Postgres session runs with TimeZone=UTC, so
comparing it against NOW() in SQL is correct -- but note that Python's
datetime.now() here is America/Denver, so do NOT mix the two. Everything shown
to a human is converted to Mountain explicitly.
"""
import logging
import math
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger(__name__)

MOUNTAIN = ZoneInfo('America/Denver')

# Tightest-first. A person crossing several bands at once is mailed for the
# tightest one only, never once per band.
BANDS = (1, 3, 7, 14)
EXPIRED_BAND = 0

MAX_PER_RUN = 25

# Minimum spacing between two notices to the same person in the same password
# cycle. The bands are normally days apart, but a person sitting just inside a
# wider band crosses into a tighter one within hours -- Clinton Poduska was at
# 1.01 days when the 3-day notice went out and got the 1-day notice 24 minutes
# later. Escalation is wanted; two mails before lunch is not.
MIN_GAP_HOURS = 12
SSPR_URL = 'https://aka.ms/sspr'
VPN_PROFILE = 'Cirque USA'


def _to_mountain(dt_utc):
    """Naive-UTC -> aware Mountain, for display only."""
    if dt_utc is None:
        return None
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(MOUNTAIN)


def _band_for(days_left):
    """The single band this person is currently in, or None if not due yet."""
    if days_left is None:
        return None
    if days_left < 0:
        return EXPIRED_BAND
    for band in BANDS:
        if days_left <= band:
            return band
    return None


def _plausible_mailbox(email):
    return bool(email) and '@' in email and not email.startswith('@')


def _candidates(con):
    """Enabled human accounts with a real expiry, plus the band they are due for."""
    rows = con.execute(
        """SELECT id, name, email, sam_account_name, account_type, pwd_expires_at,
                  EXTRACT(EPOCH FROM (pwd_expires_at - NOW())) / 86400.0 AS days_left
             FROM employee
            WHERE pwd_expires_at IS NOT NULL
              AND COALESCE(pwd_never_expires, FALSE) = FALSE
              AND COALESCE(ad_enabled, TRUE) = TRUE
            ORDER BY pwd_expires_at"""
    ).fetchall()

    out = []
    for r in rows:
        if (r['account_type'] or '').strip().lower() == 'service':
            continue
        if not _plausible_mailbox(r['email']):
            continue
        days_left = float(r['days_left']) if r['days_left'] is not None else None
        band = _band_for(days_left)
        if band is None:
            continue
        out.append({
            'employee_id': r['id'],
            'name': (r['name'] or '').strip(),
            'email': r['email'].strip(),
            'sam': (r['sam_account_name'] or '').strip(),
            'pwd_expires_at': r['pwd_expires_at'],
            'days_left': days_left,
            'band': band,
        })
    return out


def _already_sent(con, employee_id, band, pwd_expires_at):
    row = con.execute(
        "SELECT id FROM pwd_expiry_notice "
        "WHERE employee_id=? AND band_days=? AND pwd_expires_at=?",
        (employee_id, band, pwd_expires_at)
    ).fetchone()
    return row is not None


def _recently_notified(con, employee_id, pwd_expires_at):
    """True if this person already had a notice for THIS password cycle recently."""
    row = con.execute(
        "SELECT sent_at FROM pwd_expiry_notice "
        "WHERE employee_id=? AND pwd_expires_at=? "
        "  AND sent_at > NOW() - INTERVAL '%d hours' "
        "ORDER BY sent_at DESC LIMIT 1" % MIN_GAP_HOURS,
        (employee_id, pwd_expires_at)
    ).fetchone()
    return row is not None


def _record(con, item, recipient):
    con.execute(
        "INSERT INTO pwd_expiry_notice "
        "(employee_id, sam_account_name, recipient, band_days, pwd_expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (item['employee_id'], item['sam'], recipient, item['band'], item['pwd_expires_at'])
    )


def _send(subject, recipient, text, html):
    """Send one notice inside a Flask app context.

    The alert evaluator runs in a bare background thread with NO app context
    (alert_service._run_once -> _eval_compliance_alerts), but utils.send_email
    needs one for flask_mail and current_app. Without this wrapper every send
    raises and is swallowed into the `failed` counter -- the notices would look
    wired up and quietly reach nobody. alert_service._send_email wraps itself
    the same way for the same reason. Nesting is harmless when called from the
    CLI, which already holds a context.
    """
    from app import app
    from utils import send_email
    with app.app_context():
        return send_email(subject, [recipient], text, html)


def _subject(item):
    """Wording comes from the ACTUAL time remaining, never from the band.

    The band is a dedup bucket, not a description: somebody 1.1 days out sits in
    the 3-day band, and a subject line reading "expires in 3 days" would be a
    lie that costs them the day they had left.
    """
    if item['band'] == EXPIRED_BAND:
        return 'Action required: your Cirque password has expired'
    days = item['days_left']
    if days < 1:
        return 'Action required today: your Cirque password expires within 24 hours'
    whole = int(math.ceil(days))
    unit = 'day' if whole == 1 else 'days'
    return f'Your Cirque password expires in {whole} {unit}'


def _when_phrase(item):
    local = _to_mountain(item['pwd_expires_at'])
    stamp = local.strftime('%A %d %B at %-I:%M %p') + ' Mountain'
    if item['band'] == EXPIRED_BAND:
        return f"Your password expired on {stamp}."
    if item['days_left'] < 1:
        return f"Your password expires {stamp} — less than a day from now."
    return f"Your password expires on {stamp}."


def render(item):
    """Return (subject, text_body, html_body)."""
    first = (item['name'].split()[0] if item['name'] else 'there')
    when = _when_phrase(item)
    expired = item['band'] == EXPIRED_BAND

    if expired:
        lead = ("Until you reset it you will not be able to sign in to Windows off-site, "
                "connect to the VPN, or reach email on your phone.")
        how = (f"Reset it yourself now at {SSPR_URL}. This works without the VPN and "
               f"without waiting for IT.")
    else:
        lead = ("If it lapses you will be locked out of the VPN and of Windows when you are "
                "away from the office, so please do this before it expires.")
        how = ("In the office or on the VPN: press Ctrl+Alt+Delete and choose "
               f"“Change a password”. Anywhere else: use {SSPR_URL}.")

    text = f"""Hi {first},

{when}

{lead}

HOW TO CHANGE IT
{how}

IF YOU USE THE "{VPN_PROFILE}" VPN -- TWO STEPS, PLEASE DO BOTH
Your laptop keeps its own copy of your password, and changing it in the cloud
does not update that copy. Until you do, the VPN will keep failing every time
it reconnects even though your password is correct.

  STEP 1 -- get connected once.
    Connect the "{VPN_PROFILE}" VPN. If it asks for a password, enter your NEW
    one and tick "Save this user name and password". If it fails without
    asking, press Windows+R and run this, then type your new password when
    prompted (the * is required):

        rasdial "{VPN_PROFILE}" YOUR.USERNAME *

    Approve the Microsoft Authenticator prompt on your phone.

  STEP 2 -- while the VPN is still connected, press Ctrl+Alt+Delete, choose
    "Lock", then unlock with your NEW password.

Step 2 is the one people skip, and it is the one that makes it stick: it
refreshes the copy of your password held on the laptop. Do step 1 only, and the
VPN works for the rest of the day and then fails again tomorrow morning.

Need help? Reply to this message and it will reach IT.

-- Cirque IT (automated notice from Tracker)
"""

    html = f"""<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222">
<p>Hi {first},</p>
<p style="font-size:15px"><strong>{when}</strong></p>
<p>{lead}</p>
<h3 style="margin-bottom:4px">How to change it</h3>
<p>{how}</p>
<h3 style="margin-bottom:4px">If you use the &ldquo;{VPN_PROFILE}&rdquo; VPN &mdash; two steps, please do both</h3>
<p>Your laptop keeps its own copy of your password, and changing it in the cloud
does not update that copy. Until you do, the VPN will keep failing every time it
reconnects <em>even though your password is correct</em>.</p>
<ol>
  <li><strong>Get connected once.</strong> Connect the &ldquo;{VPN_PROFILE}&rdquo;
      VPN. If it asks for a password, enter your <strong>new</strong> one and tick
      &ldquo;Save this user name and password&rdquo;. If it fails without asking,
      press <kbd>Windows</kbd>+<kbd>R</kbd> and run the following, then type your
      new password when prompted (the <code>*</code> is required):
      <div style="margin:6px 0;padding:8px;background:#f4f4f4;font-family:Consolas,monospace">
      rasdial &quot;{VPN_PROFILE}&quot; YOUR.USERNAME *</div>
      Approve the Microsoft Authenticator prompt on your phone.</li>
  <li><strong>While the VPN is still connected</strong>, press
      <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Delete</kbd>, choose <strong>Lock</strong>,
      then unlock with your <strong>new</strong> password.</li>
</ol>
<p style="color:#555">Step 2 is the one people skip, and it is the one that makes it
stick &mdash; it refreshes the copy of your password held on the laptop. Do step 1
only, and the VPN works for the rest of the day and then fails again tomorrow
morning.</p>
<p>Need help? Reply to this message and it will reach IT.</p>
<p style="color:#888;font-size:12px">Cirque IT &middot; automated notice from Tracker</p>
</body></html>"""

    return _subject(item), text, html


def run(con, mode=None, dry_run=None):
    """Send any due notices. Returns a summary dict.

    mode: 'off' | 'dry' | 'on'. Defaults to Setting `pwd_expiry_notify_users`.
    """
    if mode is None:
        row = con.execute(
            "SELECT value FROM setting WHERE key=?", ('pwd_expiry_notify_users',)
        ).fetchone()
        mode = (row['value'] if row else 'off') or 'off'
    mode = str(mode).strip().lower()
    if dry_run:
        mode = 'dry'

    summary = {'mode': mode, 'due': 0, 'sent': 0, 'skipped_already': 0,
               'skipped_recent': 0, 'failed': 0, 'capped': 0, 'planned': []}

    if mode == 'off':
        return summary

    due = _candidates(con)
    summary['due'] = len(due)

    for item in due:
        if _already_sent(con, item['employee_id'], item['band'], item['pwd_expires_at']):
            summary['skipped_already'] += 1
            continue
        if _recently_notified(con, item['employee_id'], item['pwd_expires_at']):
            summary['skipped_recent'] += 1
            continue
        if summary['sent'] + summary['capped'] >= MAX_PER_RUN:
            summary['capped'] += 1
            continue

        subject, text, html = render(item)
        summary['planned'].append({
            'to': item['email'], 'sam': item['sam'], 'band': item['band'],
            'days_left': round(item['days_left'], 2), 'subject': subject,
        })

        if mode == 'dry':
            summary['sent'] += 1
            continue

        try:
            ok = _send(subject, item['email'], text, html)
        except Exception as exc:
            logger.warning('pwd expiry notice to %s failed: %s', item['email'], exc)
            ok = False

        if ok:
            _record(con, item, item['email'])
            con.commit()
            summary['sent'] += 1
            logger.info('pwd expiry notice sent to %s (band %sd)', item['email'], item['band'])
        else:
            summary['failed'] += 1

    return summary


def main():
    """CLI: default is a dry run that mails nobody."""
    from app import app
    from pg_db import pg_connect

    argv = sys.argv[1:]
    mode = 'dry'
    if '--send' in argv:
        mode = 'on'
    elif '--setting' in argv:
        mode = None

    with app.app_context():
        con = pg_connect()
        try:
            s = run(con, mode=mode)
        finally:
            con.close()

    print(f"mode={s['mode']}  due={s['due']}  would_send/sent={s['sent']}  "
          f"already={s['skipped_already']}  recent={s['skipped_recent']}  "
          f"failed={s['failed']}  capped={s['capped']}")
    for p in s['planned']:
        band = 'EXPIRED' if p['band'] == 0 else f"{p['band']}d"
        print(f"  [{band:>7}] {p['days_left']:>7.2f}d  {p['sam']:<22} {p['to']:<32} {p['subject']}")
    if s['mode'] == 'dry':
        print("\n(dry run -- no mail sent, nothing recorded)")


if __name__ == '__main__':
    main()
