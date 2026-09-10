#!/usr/bin/env python3
"""Add Statement-of-Applicability fields to soc2_control, and backfill them.

WHY: a Statement of Applicability needs, per control, the ISO 27001 clause it
maps to, whether the control is Applicable, and the justification for that
decision. Tracker held none of those -- so it could track control ownership and
progress but could not produce an SoA.

That data DID exist, in
  archive/isms-manual-preimport-2026-09-10/StrikeGraph Upload/08-Control-Mappings/
      SOC2-Statement-of-Applicability.csv
which was written ~2026-03 and never brought into Tracker. It is populated for
all 58 rows. This migration adds the columns and backfills from that CSV before
the folder is archived, so the information is not lost with it.

Name matching is exact-on-control_name with one explicit alias: the CSV's
"Board Oversight OR Management Oversight" is Tracker's "Management Oversight".

Additive and idempotent: columns use IF NOT EXISTS, and the backfill only fills
values that are currently NULL, so re-running never overwrites later edits.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import text

CSV_PATH = ('archive/isms-manual-preimport-2026-09-10/StrikeGraph Upload/08-Control-Mappings/'
            'SOC2-Statement-of-Applicability.csv')

ALIASES = {
    'board oversight or management oversight': 'management oversight',
}

DDL = [
    "ALTER TABLE soc2_control ADD COLUMN IF NOT EXISTS iso27001_clause VARCHAR(64)",
    "ALTER TABLE soc2_control ADD COLUMN IF NOT EXISTS applicability VARCHAR(32)",
    "ALTER TABLE soc2_control ADD COLUMN IF NOT EXISTS applicability_justification TEXT",
    "ALTER TABLE soc2_control ADD COLUMN IF NOT EXISTS soa_control_ref VARCHAR(32)",
]


def main():
    with app.app_context():
        for stmt in DDL:
            print("  " + stmt)
            db.session.execute(text(stmt))
        db.session.commit()

        if not os.path.exists(CSV_PATH):
            print(f"\nCSV not found at {CSV_PATH} -- columns added, no backfill.")
            return

        rows = list(csv.DictReader(open(CSV_PATH, encoding='utf-8-sig')))
        ctrls = db.session.execute(text(
            "SELECT id, control_name FROM soc2_control")).fetchall()
        by_name = {(c[1] or '').strip().lower(): c[0] for c in ctrls}

        filled, unmatched = 0, []
        for r in rows:
            name = (r.get('Control Name') or '').strip().lower()
            name = ALIASES.get(name, name)
            cid = by_name.get(name)
            if not cid:
                unmatched.append(f"{r.get('Control ID')}  {r.get('Control Name')}")
                continue
            db.session.execute(text("""
                UPDATE soc2_control SET
                    iso27001_clause = COALESCE(iso27001_clause, :iso),
                    applicability = COALESCE(applicability, :app),
                    applicability_justification = COALESCE(applicability_justification, :just),
                    soa_control_ref = COALESCE(soa_control_ref, :ref)
                WHERE id = :i"""), {
                'iso': (r.get('ISO 27001:2022') or '').strip() or None,
                'app': (r.get('Applicability') or '').strip() or None,
                'just': (r.get('Justification') or '').strip() or None,
                'ref': (r.get('Control ID') or '').strip() or None,
                'i': cid})
            filled += 1
        db.session.commit()

        print(f"\nBackfilled {filled} control(s) from the SoA CSV.")
        for c in ('iso27001_clause', 'applicability', 'applicability_justification'):
            n = db.session.execute(text(
                f"SELECT count(*) FROM soc2_control WHERE {c} IS NOT NULL")).scalar()
            print(f"  {c:<32} populated {n}/55")

        if unmatched:
            print(f"\n{len(unmatched)} SoA row(s) with NO matching Tracker control "
                  f"-- these controls are in the auditor's SoA but untracked:")
            for u in unmatched:
                print(f"    {u}")


if __name__ == '__main__':
    main()
