"""Anomaly flagger — the brain inspects live collector facts and opens a ticket when it finds
a real problem (security misconfig, policy != documented, expiring cert, replication failure).

Each finding has a stable marker [anomaly:<system_id>:<key>] embedded in the ticket body so a
re-scan never duplicates an open finding. New tickets publish ticket.created so the auto-triage
workflow grounds a suggested fix from the knowledge base. See docs/AGENTIC_IT_OS_GAMEPLAN.md.
"""
import logging

from extensions import db
from models import SupportTicket, ITSystem

log = logging.getLogger("anomalies")

_PRIO = {'high': 'High', 'medium': 'Normal', 'low': 'Low'}


def _ad_rules(s, f):
    out = []
    if f.get('pwd_reversible_encryption') is True:
        out.append(('reversible_encryption', 'high', 'Security',
            'AD: reversible password encryption is ENABLED',
            'The Default Domain Policy stores passwords using reversible encryption — a recoverable '
            '(≈plaintext) form. This should be DISABLED unless a specific legacy app requires it (and '
            'then scoped via a fine-grained password policy, not domain-wide). Verify why it is on; '
            'disable "Store passwords using reversible encryption" if not required.'))
    mx = f.get('pwd_max_age_days')
    if isinstance(mx, int) and mx > 90:
        out.append(('pwd_maxage', 'medium', 'Security',
            f'AD: password max age is {mx} days (documented policy is 90)',
            f'The default domain maximum password age is {mx} days, but the documented Password Policy '
            'specifies 90 days (standard) / 60 (privileged) — and there are no fine-grained password '
            'policies (PSOs) enforcing it. Reconcile: implement the 90-day policy (PSO/GPO) or update the doc.'))
    if f.get('pwd_min_age_days') == 0:
        out.append(('pwd_minage', 'low', 'Security',
            'AD: minimum password age is 0 (history can be bypassed)',
            'With minimum password age 0 and a history of 5, a user can change their password repeatedly '
            'to cycle back to a previous one. Set minimum password age to 1 day.'))
    da = f.get('domain_admins')
    if isinstance(da, int) and da > 5:
        out.append(('domain_admins', 'medium', 'Security', f'AD: {da} Domain Admins (review least privilege)',
            f'{da} accounts are in Domain Admins. Review membership and remove standing privilege where possible.'))
    ea = f.get('enterprise_admins')
    if isinstance(ea, int) and ea > 3:
        out.append(('enterprise_admins', 'medium', 'Security', f'AD: {ea} Enterprise Admins (review)',
            f'{ea} accounts are in Enterprise Admins. This group should be near-empty outside forest changes.'))
    rf = f.get('replication_failures')
    if isinstance(rf, int) and rf > 0:
        out.append(('repl_fail', 'high', 'General', f'AD replication failures: {rf} (writable DCs)',
            'One or more writable DCs report replication failures. Investigate with `repadmin /showrepl` / '
            '`repadmin /replsummary`.'))
    return out


def _cert_rules(s, f):
    se = f.get('soon_expiring')
    if isinstance(se, int) and se > 0:
        return [('cert_expiry', 'high', 'Security',
                 f'{se} certificate(s) expiring within 30 days on {s.name}',
                 'Certificates on this host expire within 30 days. Renew before expiry to avoid outages — '
                 'see the certificate "Live facts" doc on the system for the list.')]
    return []


_RULES = [_ad_rules, _cert_rules]


def scan(create_tickets=True, actor='brain'):
    """Run all anomaly rules over current system facts. Opens a ticket per new finding
    (deduped by marker). Returns {findings, created:[(id,title)]}."""
    created, found = [], 0
    for s in ITSystem.query.all():
        facts = s.facts or {}
        for rule in _RULES:
            for key, sev, cat, title, detail in rule(s, facts):
                found += 1
                marker = f"[anomaly:{s.id}:{key}]"
                exists = (SupportTicket.query
                          .filter(SupportTicket.status.notin_(['Closed', 'Merged']))
                          .filter(SupportTicket.description.like(f"%{marker}%")).first())
                if exists or not create_tickets:
                    continue
                t = SupportTicket(
                    subject=title[:200],
                    description=(f"{detail}\n\n— Detected by the IT brain from live collector facts on "
                                 f"**{s.name}**.\n\n{marker}"),
                    priority=_PRIO.get(sev, 'Normal'), category=cat, status='Open',
                    source='brain', reporter_name='IT Brain')
                db.session.add(t)
                db.session.commit()
                created.append((t.id, title))
                # Let the auto-triage workflow ground a suggested fix from the knowledge base.
                try:
                    import event_bus
                    event_bus.publish('ticket.created', {
                        'ticket_id': t.id, 'subject': t.subject, 'category': cat,
                        'priority': _PRIO.get(sev, 'Normal'), 'submitted_by': actor}, source='anomaly')
                except Exception:
                    log.exception("publish ticket.created failed for anomaly ticket %s", t.id)
    log.info("anomaly scan: %d findings, %d new tickets", found, len(created))
    return {'findings': found, 'created': created}
