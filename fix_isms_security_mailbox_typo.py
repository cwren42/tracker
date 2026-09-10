#!/usr/bin/env python3
"""Correct security@cirq.com -> security@cirque.com in the published ISMS documents.

The reporting mailbox given to staff was misspelled. Verified in Exchange
Online 2026-09-10: the real mailbox is security@cirque.com ("Security",
UserMailbox); cirq.com is not a domain here at all, so mail to the address in
the documents does not arrive anywhere.

This is not cosmetic. IS-CIRQ-PR-020-G is the Incident Response Procedure that
staff are directed to, and the imminent security-reporting announcement points
at it. Anyone following the procedure would report an incident to an address
that bounces -- which defeats CC-025 Incident Response: Employee Responsibility
and would look far worse in an audit than the typo itself.

Version history shows no previous attempt: isms-manual has only imports plus a
May validation edit, and PR-020-G is at v1 from today's import. Note also that
isms-manual embeds the Incident Report Form (IS-CIRQ-F-005) text inline, which
is where its copy of the address lives.

Goes through the app's own _create_version so each fix is a real, diffable
version with a change summary and an audit record -- not a silent UPDATE on a
published policy.

    python fix_isms_security_mailbox_typo.py            # dry run
    python fix_isms_security_mailbox_typo.py --apply
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import ISMSDocument

TYPO = 'security@cirq.com'
FIXED = 'security@cirque.com'
SUMMARY = ('Corrected the security reporting mailbox from security@cirq.com to '
           'security@cirque.com. The misspelled address does not exist (verified in '
           'Exchange Online 2026-09-10); cirq.com is not an accepted domain, so '
           'incident reports sent to it were never delivered. Text-only correction '
           'to a contact detail -- no change to policy, scope or requirements.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='write new versions (default: dry run)')
    args = ap.parse_args()

    with app.app_context():
        # imported here so the module-level app context exists first
        from blueprints.isms import _create_version, _log_action

        docs = ISMSDocument.query.all()
        hits = []
        for d in docs:
            v = d.current_version
            if v and v.markdown_body and TYPO in v.markdown_body:
                hits.append((d, v, v.markdown_body.count(TYPO)))

        if not hits:
            print("Nothing to do: no published document contains the typo.")
            return

        print(f"{'APPLYING' if args.apply else 'DRY RUN --'} {len(hits)} document(s)\n")
        for d, v, n in hits:
            print(f"  {d.slug:<22} v{v.version_number} {d.status:<10} {n} occurrence(s)  {d.title[:40]}")
            for m in re.finditer(re.escape(TYPO), v.markdown_body):
                s = max(0, m.start() - 70)
                snippet = v.markdown_body[s:m.end() + 40].replace('\n', ' ')
                print(f"      ...{snippet}...")

        if not args.apply:
            print("\n(dry run -- nothing changed. Re-run with --apply)")
            return

        for d, v, n in hits:
            new_body = v.markdown_body.replace(TYPO, FIXED)
            nv = _create_version(d, new_body, SUMMARY)
            _log_action(d, 'isms_correction',
                        f'{TYPO} -> {FIXED} ({n} occurrence(s)); version '
                        f'{v.version_number} -> {nv.version_number}')
            print(f"  {d.slug}: v{v.version_number} -> v{nv.version_number}")
        db.session.commit()

        print("\nverification:")
        for d in ISMSDocument.query.all():
            cv = d.current_version
            if cv and cv.markdown_body and TYPO in cv.markdown_body:
                print(f"  STILL PRESENT: {d.slug}")
        remaining = sum(1 for d in ISMSDocument.query.all()
                        if d.current_version and d.current_version.markdown_body
                        and TYPO in d.current_version.markdown_body)
        print(f"  documents still containing the typo: {remaining}")


if __name__ == '__main__':
    main()
