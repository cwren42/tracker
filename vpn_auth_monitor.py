"""Ingest CHR SSTP/RADIUS auth outcomes from syslog and diagnose failures.

WHY THIS EXISTS
    Between 2026-09-03 and 09-09 the VPN produced 23 auth failures across five
    users. IT learned about every single one from the user. The CHR logs each
    failure within seconds and ships it to /var/log/chr/usa-chr.log by syslog --
    nothing read it. This module reads it, works out the LIKELY CAUSE from data
    Tracker already holds, and records an event the alert evaluator turns into a
    notification.

    The diagnosis matters more than the detection. "steve.austin auth failed" is
    not actionable; "auth failed and AD says his password expired 4 days ago, he
    can self-serve at aka.ms/sspr" is.

TIMEZONES
    The CHR is America/Denver and its syslog lines carry a UTC offset. Events are
    stored in **UTC** for consistency with alert_log and audit_trail, and rendered
    back to Mountain for humans. Getting this backwards has caused real confusion
    before, so both directions are explicit here.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

LOG_PATHS = ['/var/log/chr/usa-chr.log', '/var/log/chr/usa-chr.log.1']

# Must be a real zone, NOT a fixed offset. Denver is -0600 (MDT) today but
# -0700 (MST) from 2026-11-01, and a hardcoded -6 would silently render every
# winter timestamp an hour early -- the exact class of error that has already
# caused confusion when quoting times back.
MOUNTAIN = ZoneInfo('America/Denver')

_TS = r'(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2})'

RE_FAIL = re.compile(
    _TS + r'.*?sstp,ppp,error\s*:\s*user\s+(?P<user>\S+)\s+authentication failed\s*(?:-\s*(?P<reason>.*?))?\s*$')
RE_LOGIN = re.compile(
    _TS + r'.*?sstp,ppp,info,account\s+(?P<user>\S+)\s+logged in,\s*(?P<assigned>\S+)\s+from\s+(?P<client>\S+)')
RE_LOGOUT = re.compile(
    _TS + r'.*?sstp,ppp,info,account\s+(?P<user>\S+)\s+logged out.*?from\s+(?P<client>\S+)')


def _parse_ts(raw):
    """CHR timestamp -> naive UTC datetime."""
    try:
        return datetime.fromisoformat(raw).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def to_mountain(dt_utc):
    """Naive-UTC -> 'MM-DD HH:MM MDT' for humans."""
    if dt_utc is None:
        return '?'
    local = dt_utc.replace(tzinfo=timezone.utc).astimezone(MOUNTAIN)
    return local.strftime('%m-%d %H:%M ') + local.tzname()


def parse_log(paths=None):
    """Yield dicts for every auth outcome found in the CHR syslog files."""
    for path in (paths or LOG_PATHS):
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', errors='replace') as fh:
                lines = fh.readlines()
        except OSError as exc:
            logger.warning('vpn_auth_monitor: cannot read %s (%s)', path, exc)
            continue
        for line in lines:
            line = line.rstrip('\n')
            m = RE_FAIL.search(line)
            if m:
                yield {'occurred_at': _parse_ts(m.group('ts')), 'username': m.group('user'),
                       'outcome': 'failed', 'reason': (m.group('reason') or '').strip() or None,
                       'client_ip': None, 'assigned_ip': None, 'raw_line': line}
                continue
            m = RE_LOGIN.search(line)
            if m:
                yield {'occurred_at': _parse_ts(m.group('ts')), 'username': m.group('user'),
                       'outcome': 'success', 'reason': None,
                       'client_ip': m.group('client'), 'assigned_ip': m.group('assigned').rstrip(','),
                       'raw_line': line}
                continue
            m = RE_LOGOUT.search(line)
            if m:
                yield {'occurred_at': _parse_ts(m.group('ts')), 'username': m.group('user'),
                       'outcome': 'logout', 'reason': None,
                       'client_ip': m.group('client'), 'assigned_ip': None, 'raw_line': line}


def diagnose(event, emp_row):
    """Turn a raw failure into an actionable cause.

    emp_row is the matching `employee` row (or None) -- pwd_expires_at and
    pwd_last_set come from the AD sync, so no extra directory round-trip.
    Every branch names the remedy, because an alert without one just relays
    the user's complaint back to IT.
    """
    if event['outcome'] != 'failed':
        return None

    reason = (event['reason'] or '').lower()

    if 'radius timeout' in reason:
        return ('NPS/RADIUS did not answer in time. This is NOT the user. Azure MFA needs '
                'the RADIUS timeout >= 30s because the push waits on a human -- check '
                '/radius print on the CHR (must not be 3s) and that NPS on LOTHAL is '
                'responding. Users typically retry and get in, which masks it.')

    now = datetime.utcnow()
    if emp_row is not None:
        expires = emp_row.get('pwd_expires_at')
        last_set = emp_row.get('pwd_last_set')
        if expires and expires < now:
            days = (now - expires).days
            return (f'AD password EXPIRED {days} day(s) ago. Primary auth cannot succeed, so '
                    f'the MFA step is never reached. Self-serve at aka.ms/sspr (writeback is '
                    f'enabled); no helpdesk reset needed.')
        if last_set and (now - last_set) < timedelta(days=4):
            # Proven on STEVEA-MSI 2026-09-09. The profile had AutoLogon=0, so the
            # connection carried its own saved password; `rasdial <name> *`
            # authenticates but never persists, so the auto-reconnect task kept
            # submitting the pre-rotation password (err=691 / 4776 0xC000006A).
            # 28 of 33 US boxes run AutoLogon=1 and cannot fail this way at all.
            return ('Password changed within the last 4 days -- this is a STALE SAVED '
                    'CREDENTIAL on the VPN profile, not a wrong password. Check '
                    'AutoLogon in the all-user rasphone.pbk: if it is 0 the profile '
                    'stores its own password and has gone stale. Durable fix is the '
                    'fleet standard -- Set-VpnConnection -AllUserConnection '
                    '-UseWinlogonCredential $true, which removes the stored password '
                    'entirely so a rotation can never desync it. One-off fix is '
                    'rasphone -d "<profile>" with "Save this user name and password" '
                    'TICKED; a bare `rasdial <profile> *` does NOT persist and the '
                    'failure returns on the next reconnect.')
        if expires:
            return (f'Primary auth rejected while the password is valid (expires '
                    f'{expires:%Y-%m-%d}). Check the DC for 4776 err=0xC000006A (wrong '
                    f'password -> stale saved credential somewhere) or 4740 (lockout).')

    return ('Primary auth rejected -- NPS never reached the MFA step. Check the DC Security log '
            'for 4776 (0xC0000071 = expired, 0xC000006A = wrong password) or 4740 (lockout).')


def _dedup_key(event):
    """Stable identity for one log line.

    The CHR can emit the same second twice (retries), so the outcome and the
    client IP are part of the key. Column has a UNIQUE index, and ingest also
    pre-checks, so re-reading the whole file is always safe and idempotent --
    which matters because logrotate means we re-scan .log and .log.1 each pass.
    """
    ts = event['occurred_at'].strftime('%Y%m%dT%H%M%S') if event['occurred_at'] else 'na'
    return f"{ts}|{(event['username'] or '?').lower()}|{event['outcome']}|{event.get('client_ip') or '-'}"


def _employee_map(con):
    """sam/UPN-prefix -> employee row, lowercased. CHR usernames vary in case."""
    rows = con.execute(
        """SELECT sam_account_name, email, name, pwd_expires_at, pwd_last_set
             FROM employee
            WHERE sam_account_name IS NOT NULL"""
    ).fetchall()
    out = {}
    for r in rows:
        row = {'name': r['name'], 'email': r['email'],
               'pwd_expires_at': r['pwd_expires_at'], 'pwd_last_set': r['pwd_last_set']}
        sam = (r['sam_account_name'] or '').strip().lower()
        if sam:
            out[sam] = row
        # Steve's sam is Steve.Austin but his UPN is saustin@ -- the CHR has been
        # seen using either, so index both spellings.
        email = (r['email'] or '').strip().lower()
        if '@' in email:
            out.setdefault(email.split('@', 1)[0], row)
    return out


def ingest(con, paths=None, since_hours=72):
    """Read the CHR syslog into vpn_auth_event. Returns a summary dict.

    Only events newer than `since_hours` are considered, so the first run after
    a long gap cannot backfill a flood of stale alerts.
    """
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)
    emp = _employee_map(con)

    inserted, failures, seen = 0, [], 0
    for event in parse_log(paths):
        if not event['occurred_at'] or event['occurred_at'] < cutoff:
            continue
        seen += 1
        key = _dedup_key(event)
        if con.execute("SELECT id FROM vpn_auth_event WHERE dedup_key=?", (key,)).fetchone():
            continue

        emp_row = emp.get((event['username'] or '').strip().lower())
        diag = diagnose(event, emp_row)
        con.execute(
            """INSERT INTO vpn_auth_event
                   (occurred_at, username, outcome, reason, diagnosis,
                    client_ip, assigned_ip, raw_line, dedup_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event['occurred_at'], event['username'], event['outcome'], event['reason'],
             diag, event.get('client_ip'), event.get('assigned_ip'),
             event['raw_line'][:2000], key))
        inserted += 1
        if event['outcome'] == 'failed':
            failures.append({'username': event['username'], 'at': event['occurred_at'],
                             'reason': event['reason'], 'diagnosis': diag})

    con.commit()
    return {'scanned': seen, 'inserted': inserted, 'new_failures': failures}


def failure_digest(failures, limit=4):
    """One alert message for a batch of failures, leading with the diagnosis."""
    if not failures:
        return None
    users = sorted({f['username'] for f in failures})
    who = ', '.join(users[:6]) + (' and others' if len(users) > 6 else '')
    head = (f"{len(failures)} VPN authentication failure(s) on USA-CHR affecting "
            f"{len(users)} user(s): {who}.")
    lines = []
    for f in failures[-limit:]:
        lines.append(f"  - {f['username']} at {to_mountain(f['at'])}: "
                     f"{f['diagnosis'] or f['reason'] or 'rejected'}")
    tail = ('' if len(failures) <= limit
            else f"\n  ...{len(failures) - limit} earlier failure(s) not shown.")
    return head + "\n" + "\n".join(lines) + tail


def main():
    """CLI: python vpn_auth_monitor.py [--ingest]"""
    import sys as _sys
    from app import app
    from pg_db import pg_connect

    with app.app_context():
        con = pg_connect()
        try:
            if '--ingest' in _sys.argv[1:]:
                s = ingest(con)
                print(f"scanned={s['scanned']} inserted={s['inserted']} "
                      f"new_failures={len(s['new_failures'])}")
                msg = failure_digest(s['new_failures'])
                if msg:
                    print("\n--- alert message would be ---\n" + msg)
            else:
                events = [e for e in parse_log() if e['occurred_at']]
                by = {}
                for e in events:
                    by[e['outcome']] = by.get(e['outcome'], 0) + 1
                print(f"parsed {len(events)} events: " +
                      ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
                emp = _employee_map(con)
                for e in events:
                    if e['outcome'] == 'failed':
                        print(f"  {to_mountain(e['occurred_at'])}  {e['username']}")
                        print(f"      -> {diagnose(e, emp.get((e['username'] or '').lower()))}")
        finally:
            con.close()


if __name__ == '__main__':
    main()
