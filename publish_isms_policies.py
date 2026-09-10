#!/usr/bin/env python3
"""Bulk-publish approved ISMS policies to the employee library.

WHY THIS EXISTS
---------------
`/isms/policies` serves only documents with status='published' (SOC 2 CC.2.2 --
policies must be communicated to personnel). The importer deliberately lands
everything as 'draft', because making a document company policy is an approval
act under IS-CIRQ-P-006-G Documented Information Control, not a side effect of
running an import script.

That leaves a gap: approving 30 documents one at a time through the UI is not
realistic, but publishing them with an unlogged UPDATE leaves no evidence of who
approved what. This does the bulk change AND writes one audit_trail row per
document naming the approver, so the action is one command with 30 separately
defensible records.

DEFAULT IS A DRY RUN. Pass --publish to actually change anything.

    python publish_isms_policies.py                      # show what would change
    python publish_isms_policies.py --publish            # policies only
    python publish_isms_policies.py --publish --type procedure
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import ISMSDocument, AuditTrail, User

DEFAULT_APPROVER = 'cjwren'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--publish', action='store_true',
                    help='actually publish (default is a dry run)')
    ap.add_argument('--type', default='policy',
                    help="doc_type to publish (default: policy)")
    ap.add_argument('--approver', default=DEFAULT_APPROVER,
                    help=f'username recorded as approver (default: {DEFAULT_APPROVER})')
    ap.add_argument('--exclude', default='',
                    help='comma-separated slugs to hold back (e.g. unfilled TEMPLATE documents, '
                         'which must never reach staff as approved policy)')
    ap.add_argument('--note', default='Approved for publication to the employee policy library',
                    help='text stored on each audit_trail row')
    args = ap.parse_args()

    with app.app_context():
        docs = (ISMSDocument.query
                .filter(ISMSDocument.doc_type == args.type)
                .filter(ISMSDocument.status != 'published')
                .order_by(ISMSDocument.slug.asc())
                .all())

        held = {x.strip() for x in args.exclude.split(',') if x.strip()}
        if held:
            skipped = [d for d in docs if (d.slug or '') in held]
            docs = [d for d in docs if (d.slug or '') not in held]
            for d in skipped:
                print(f"  HELD BACK  {(d.slug or '?'):<22} {(d.title or '')[:56]}")
            if skipped:
                print()

        if not docs:
            print(f"Nothing to do: no {args.type} documents in a non-published state.")
            return

        approver = User.query.filter_by(username=args.approver).first()
        if not approver:
            print(f"ERROR: no Tracker user named '{args.approver}'. "
                  f"AuditTrail.user_id is NOT NULL with an FK to user, so the "
                  f"approver must be a real account.")
            return
        approver_id = approver.id

        print(f"{'PUBLISHING' if args.publish else 'DRY RUN --'} "
              f"{len(docs)} {args.type} document(s), approver={args.approver}\n")
        for d in docs:
            print(f"  {(d.slug or '?'):<22} {(d.status or 'draft'):<8} -> published   {(d.title or '')[:56]}")

        if not args.publish:
            print("\n(dry run -- nothing changed. Re-run with --publish)")
            return

        for d in docs:
            previous = d.status or 'draft'
            d.status = 'published'
            db.session.add(AuditTrail(
                entity_type='ISMSDocument',
                entity_id=d.id,
                action='isms_publish',
                changes=json.dumps({
                    'document': d.slug, 'title': d.title,
                    'status_from': previous, 'status_to': 'published',
                    'approver': args.approver, 'note': args.note,
                }),
                user_id=approver_id,
            ))
        db.session.commit()

        published = ISMSDocument.query.filter_by(status='published').count()
        print(f"\nPublished {len(docs)} document(s).")
        print(f"/isms/policies now shows {published} document(s).")


if __name__ == '__main__':
    main()
