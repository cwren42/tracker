#!/usr/bin/env python3
"""Create the SOC 2 controls that exist in the auditor's SoA but not in Tracker.

The auditor works from a 58-row Statement of Applicability; Tracker tracked 55.
The three missing rows were CC-022, CC-024 and CC-028. A Tracker-generated SoA
that silently omits a control the auditor lists reads as an oversight, so each
one needs a row that either answers it or records why it does not apply.

CC-022 Encryption at Rest is handled separately -- it is a Not Applicable
decision (compensating control CC-057 Antivirus, agreed with the auditor
2026-09-03) and asserting that needs an explicit human sign-off, not a script
default.

IMPORTANT -- these are created with control_progress='Not In Place'.
Both controls are *implemented*; what is missing in each case is the specific
artifact the auditor asks for:

  CC-024 Firewall Rules      -- Tracker reads the UniFi zone firewall live, so
                                the ruleset is exportable, but there is no
                                PERIODIC REVIEW RECORD (someone reviewing the
                                rules on a cadence and signing off).
  CC-028 Incidents External  -- incident response is built and in use (11
                                incidents, 13 timeline entries, 9 evidence
                                items), but incident_communications -- the
                                table shaped exactly for external notification
                                decisions, with approved_by/approval_date -- has
                                ZERO rows. Nothing records the notify /
                                do-not-notify decision per incident.

Marking either "In Place" would be a false claim. They go in honest and get
promoted when the artifact exists.

    python add_missing_soa_controls.py            # dry run
    python add_missing_soa_controls.py --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import AuditTrail, User
from sqlalchemy import text

APPROVER = 'cjwren'
OWNER = 'chris.wren@cirque.com'   # the value 33 of 55 existing controls already use

CONTROLS = [
    {
        'soa_control_ref': 'CC-022',
        'control_name': 'Encryption at Rest',
        'control_description': (
            'Encrypts data stored in databases, file systems, and backups. SIDELINED with the '
            'auditor 2026-09-03: the compensating control is CC-057 Antivirus, which is deployed '
            'and evidenced across the fleet (95 of 96 devices active in a 14-day window report an '
            'AV product). Recorded as Not Applicable rather than omitted, so the SoA answers the '
            'auditor row explicitly. Note CC-019 Disk Encryption remains a separate, ACTIVE '
            'control and is In Place -- only the databases/file-systems/backups scope is set '
            'aside here.'),
        'control_frequency': 'As Needed',
        'audit_alignment': 'SOC2:2022.CC.6.1',
        'iso27001_clause': 'A.10.2.1',
        'applicability': 'Not Applicable',
        'applicability_justification': (
            'Not Applicable -- sidelined by agreement with the auditor 2026-09-03. Compensating '
            'control: CC-057 Antivirus, deployed fleet-wide and evidenced. Confirmed by Chris '
            'Wren 2026-09-10.'),
        'isms_slug': 'is-cirq-p-009-g',   # Cryptography Policy
        'gap': 'None -- Not Applicable by agreement. Do NOT promote without re-agreeing with the '
               'auditor.',
        'not_applicable': True,
    },
    {
        'soa_control_ref': 'CC-024',
        'control_name': 'Firewall Rules',
        'control_description': (
            'Controls network traffic; restricts access to systems based on security policy. '
            'Implemented as the UniFi zone-based firewall (zones, policies and address/port '
            'groups), which Tracker reads live. Evidence required by the auditor is the current '
            'ruleset AND a periodic review record.'),
        'control_frequency': 'Quarterly',
        'audit_alignment': 'SOC2:2022.CC.6.6',
        'iso27001_clause': 'A.13.1.3',
        'applicability': 'Applicable',
        'applicability_justification': (
            'Controls network traffic; restricts access to systems based on security policy.'),
        'isms_slug': 'is-cirq-pr-016-g',   # Network Security Management Procedure
        'gap': 'No periodic firewall-rule review record exists. Ruleset itself is exportable '
               'from the UniFi controller.',
    },
    {
        'soa_control_ref': 'CC-028',
        'control_name': 'Incidents External',
        'control_description': (
            'Procedures for reporting incidents to external parties (customers, regulators). '
            'Incident handling is implemented in Tracker (incidents, timeline, evidence, '
            'messages, fix outcomes). Evidence required by the auditor is the external '
            'notification decision log per incident.'),
        'control_frequency': 'As Needed',
        'audit_alignment': 'SOC2:2022.CC.7.3',
        'iso27001_clause': 'A.16.1.5',
        'applicability': 'Applicable',
        'applicability_justification': (
            'Procedures for reporting incidents to external parties (customers, regulators).'),
        'isms_slug': 'is-cirq-pr-020-g',   # Incident Response Procedure (Global Core)
        'gap': 'incident_communications has 0 rows, so no notify / do-not-notify decision is '
               'recorded against any of the 11 incidents.',
    },
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='create the rows (default: dry run)')
    args = ap.parse_args()

    with app.app_context():
        approver = User.query.filter_by(username=APPROVER).first()
        print(f"{'CREATING' if args.apply else 'DRY RUN --'} {len(CONTROLS)} control(s)\n")

        for c in CONTROLS:
            existing = db.session.execute(text(
                "SELECT id, control_name FROM soc2_control "
                "WHERE soa_control_ref = :r OR control_name = :n"),
                {'r': c['soa_control_ref'], 'n': c['control_name']}).fetchone()
            if existing:
                print(f"  SKIP {c['soa_control_ref']} -- already present as id {existing[0]} "
                      f"({existing[1]})")
                continue

            doc = db.session.execute(text(
                "SELECT id, title FROM isms_document WHERE slug = :s"),
                {'s': c['isms_slug']}).fetchone()
            if not doc:
                print(f"  SKIP {c['soa_control_ref']} -- governing document "
                      f"{c['isms_slug']} not found")
                continue

            print(f"  {c['soa_control_ref']}  {c['control_name']}")
            print(f"      iso={c['iso27001_clause']}  cc={c['audit_alignment']}  "
                  f"freq={c['control_frequency']}  applicability={c['applicability']}  "
                  f"is_active={not c.get('not_applicable', False)}")
            print(f"      governed by: {doc[1]}")
            print(f"      GAP: {c['gap']}")

            if not args.apply:
                continue

            row = db.session.execute(text("""
                INSERT INTO soc2_control
                    (control_name, control_description, control_frequency, control_owner,
                     control_progress, is_active, audit_alignment, automation_enabled,
                     isms_document_id, iso27001_clause, applicability,
                     applicability_justification, soa_control_ref, created_at, updated_at)
                VALUES
                    (:name, :desc, :freq, :owner, :progress, :active, :align, FALSE,
                     :docid, :iso, :appl, :just, :ref, NOW(), NOW())
                RETURNING id"""), {
                'name': c['control_name'], 'desc': c['control_description'],
                'freq': c['control_frequency'], 'owner': OWNER,
                'progress': 'Not In Place' if not c.get('not_applicable') else 'Not In Place',
                'active': not c.get('not_applicable', False),
                'align': c['audit_alignment'], 'docid': doc[0],
                'iso': c['iso27001_clause'], 'appl': c['applicability'],
                'just': c['applicability_justification'], 'ref': c['soa_control_ref'],
            }).fetchone()

            db.session.add(AuditTrail(
                entity_type='SOC2Control', entity_id=row[0], action='soc2_control_created',
                changes=json.dumps({
                    'control': c['control_name'], 'soa_ref': c['soa_control_ref'],
                    'reason': 'present in auditor SoA, absent from Tracker',
                    'progress': 'Not In Place', 'gap': c['gap'],
                    'governing_document': c['isms_slug'], 'approver': APPROVER}),
                user_id=approver.id if approver else 1))
            print(f"      created as id {row[0]}")

        if args.apply:
            db.session.commit()
            total = db.session.execute(text("SELECT count(*) FROM soc2_control")).scalar()
            withref = db.session.execute(text(
                "SELECT count(*) FROM soc2_control WHERE soa_control_ref IS NOT NULL")).scalar()
            print(f"\nsoc2_control rows: {total}  (with an SoA ref: {withref})")
        else:
            print("\n(dry run -- nothing created. Re-run with --apply)")


if __name__ == '__main__':
    main()
