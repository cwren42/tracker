#!/usr/bin/env python3
"""Link SOC 2 controls to the ISMS document that authorises them.

WHY: soc2_control.isms_document_id drives the Statement of Applicability and
the "which policy backs this control" column an auditor reads. 22 of 55 were
null because the documents only entered Tracker on 2026-09-10.

The mapping is written out EXPLICITLY rather than fuzzy-matched. A wrong link
is worse than a null one: it asserts to an auditor that a control is governed
by a document that does not actually cover it. Two sources of confidence:

  EVIDENCED  - soc2_control.authoritative_docs already names the document by
               title (its own IS-A**-CIRQ** coding scheme differs from the
               IS-CIRQ-* codes, but the parenthetical titles match exactly).
  EXACT      - the control name and the document subject are the same thing,
               e.g. "Change Management: Infrastructure" -> the Change
               Management Policy: IT Infrastructure written for that control.

Controls with NO governing ISMS document are deliberately left null and listed
by --report. Most are HR/legal artifacts (background checks, NDAs, code of
conduct, org chart) that the ISMS document set does not cover. That is a real
documentation gap and should be visible, not papered over with a wrong link.

    python link_soc2_controls_to_isms.py            # dry run
    python link_soc2_controls_to_isms.py --apply
    python link_soc2_controls_to_isms.py --report    # unlinked + why
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import ISMSDocument, AuditTrail, User

APPROVER = 'cjwren'

# control_id -> (document slug, confidence, rationale)
MAPPING = {
    60:  ('is-cirq-p-019-g',  'EVIDENCED', 'authoritative_docs names "Acceptable Use Policy"'),
    66:  ('is-cirq-p-015-g',  'EVIDENCED', 'authoritative_docs names "Information Security Continuity Policy"'),
    106: ('is-cirq-pr-008-g', 'EVIDENCED', 'authoritative_docs names "Access Control Procedure"'),
    111: ('is-cirq-p-013-g',  'EVIDENCED', 'authoritative_docs names "Supplier Relationships Policy"'),
    67:  ('is-cirq-pr-026-g', 'EXACT',     'Internal Software Change Management Procedure is this control'),
    69:  ('is-cirq-p-028-g',  'EXACT',     'P-028-G was written for the auditor\'s IT Infrastructure change control'),
    85:  ('is-cirq-p-014-g',  'EXACT',     'Information Security Incident Management Policy; /report-security-concern implements it'),
    110: ('is-cirq-pr-019-g', 'EXACT',     'Supplier Security Review Procedure is vendor due diligence'),
}

# Proposed but NOT applied -- one plausible document each, needs a human nod.
PROPOSED = {
    68:  ('is-cirq-pr-013-g', 'Change Management Procedure (emergency path not called out separately)'),
    71:  ('is-cirq-pr-013-g', 'Change Management Procedure (separation of duties clause)'),
    72:  ('is-cirq-pr-013-g', 'Change Management Procedure (ticketing as the change record)'),
    76:  ('is-cirq-p-013-g',  'Supplier Relationships Policy covers contracts'),
    89:  ('is-cirq-p-002-g',  'Roles, Responsibilities and Authorities Policy / D-004 Roles Matrix'),
    91:  ('is-cirq-p-022-g',  'Management Review Policy'),
    107: ('is-cirq-pr-019-g', 'Supplier Security Review Procedure covers third-party SOC 2 review'),
    113: ('is-cirq-p-013-g',  'Supplier Relationships Policy; register itself is generated at /compliance/vendor-risk-register'),
}

# No ISMS document governs these today.
NO_DOCUMENT = {
    65:  'Background Check -- HR control, no ISMS document covers pre-employment screening',
    73:  'Code of Conduct -- HR/legal document, not in the ISMS set',
    74:  'Collection: Reliable Source -- control intent unclear; needs scoping before linking',
    82:  'Employee Performance -- HR process, not an information-security document',
    94:  'Non Disclosure Agreement -- legal template, not in the ISMS set',
    95:  'Organizational Chart -- HR artifact, not an ISMS document',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='write the links (default: dry run)')
    ap.add_argument('--report', action='store_true', help='show unlinked controls and why')
    args = ap.parse_args()

    with app.app_context():
        approver = User.query.filter_by(username=APPROVER).first()
        slugs = {d.slug: d for d in ISMSDocument.query.all()}

        if args.report:
            rows = db.session.execute(db.text(
                "SELECT id, control_name FROM soc2_control "
                "WHERE isms_document_id IS NULL ORDER BY id")).fetchall()
            print(f"{len(rows)} control(s) still unlinked\n")
            for cid, name in rows:
                if cid in PROPOSED:
                    tgt, why = PROPOSED[cid]
                    print(f"  {cid:>3} {name[:38]:<38} PROPOSED -> {tgt:<18} {why[:52]}")
                elif cid in NO_DOCUMENT:
                    print(f"  {cid:>3} {name[:38]:<38} NO DOC   -- {NO_DOCUMENT[cid][:60]}")
                else:
                    print(f"  {cid:>3} {name[:38]:<38} UNCLASSIFIED")
            return

        print(f"{'APPLYING' if args.apply else 'DRY RUN --'} {len(MAPPING)} link(s)\n")
        applied = 0
        for cid, (slug, confidence, rationale) in sorted(MAPPING.items()):
            doc = slugs.get(slug)
            row = db.session.execute(db.text(
                "SELECT control_name, isms_document_id FROM soc2_control WHERE id=:i"),
                {'i': cid}).fetchone()
            if not row:
                print(f"  {cid:>3} SKIP -- no such control"); continue
            if not doc:
                print(f"  {cid:>3} SKIP -- no document {slug}"); continue
            if row[1] is not None:
                print(f"  {cid:>3} SKIP -- already linked to {row[1]}"); continue

            print(f"  {cid:>3} {row[0][:36]:<36} -> {slug:<18} [{confidence}]")
            if args.apply:
                db.session.execute(db.text(
                    "UPDATE soc2_control SET isms_document_id=:d, updated_at=NOW() WHERE id=:i"),
                    {'d': doc.id, 'i': cid})
                db.session.add(AuditTrail(
                    entity_type='SOC2Control', entity_id=cid, action='soc2_control_link_isms',
                    changes=json.dumps({'control': row[0], 'isms_document': slug,
                                        'confidence': confidence, 'rationale': rationale,
                                        'approver': APPROVER}),
                    user_id=approver.id if approver else 1))
                applied += 1

        if args.apply:
            db.session.commit()
            linked = db.session.execute(db.text(
                "SELECT count(*) FROM soc2_control WHERE isms_document_id IS NOT NULL")).scalar()
            total = db.session.execute(db.text("SELECT count(*) FROM soc2_control")).scalar()
            print(f"\nApplied {applied}. Controls linked to an ISMS document: {linked}/{total}")
        else:
            print("\n(dry run -- nothing changed. Re-run with --apply)")


if __name__ == '__main__':
    main()
