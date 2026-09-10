#!/usr/bin/env python3
"""Rename the RMM triage tables so they cannot be mistaken for incident response.

Tracker has TWO unrelated subsystems whose tables shared the `incident_` prefix:

  incidents / incident_timeline / incident_evidence / incident_actions / ...
      the SOC 2 incident-response module (security incidents, notification
      decisions, evidence, playbooks)

  agent_incident / incident_message / incident_fix_outcome
      the RMM auto-remediation stream (an agent raises a signal, the triage
      agent reasons about it in a chat thread, the outcome feeds a learning loop)

The two middle tables were named as if they belonged to the first group, and
their FK column was called `incident_id` while actually referencing
agent_incident(id). On 2026-09-10, clearing seed data out of the incident-
response module, that naming came within one query of destroying real
remediation history -- `DELETE FROM incident_*` or trusting the prefix would
have taken 30 rows of genuine agent triage with it.

The schema itself was never wrong: incident_message.incident_id has always had
an FK to agent_incident(id) ON DELETE CASCADE. Only the names lied. This renames
them to match what they are:

    incident_message      -> agent_incident_message
    incident_fix_outcome  -> agent_incident_fix_outcome
    .incident_id          -> .agent_incident_id      (on both)

plus the indexes and constraints, so nothing is left carrying the old name.

Note agent_incident_fix_outcome has NO foreign key (only a pkey), which is why
it holds orphaned ids from long-deleted signals. Adding one would delete
history, so it is left alone deliberately.

Idempotent: every step checks first, so re-running is a no-op.

    python migrate_rename_agent_incident_tables.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import text

RENAMES = [
    ('table',  'incident_message',                       'agent_incident_message'),
    ('column', 'agent_incident_message.incident_id',     'agent_incident_id'),
    ('index',  'idx_incident_message_incident',          'idx_agent_incident_message_incident'),
    ('index',  'incident_message_pkey',                  'agent_incident_message_pkey'),
    ('fk',     'agent_incident_message.incident_message_incident_id_fkey',
               'agent_incident_message_agent_incident_id_fkey'),
    ('table',  'incident_fix_outcome',                   'agent_incident_fix_outcome'),
    ('column', 'agent_incident_fix_outcome.incident_id', 'agent_incident_id'),
    ('index',  'idx_incident_fix_outcome_signal_action',
               'idx_agent_incident_fix_outcome_signal_action'),
    ('index',  'uq_incident_fix_outcome_incident',       'uq_agent_incident_fix_outcome_incident'),
    ('index',  'incident_fix_outcome_pkey',              'agent_incident_fix_outcome_pkey'),
]


def exists_table(name):
    return db.session.execute(text("SELECT to_regclass(:n)"), {'n': name}).scalar() is not None


def exists_column(table, col):
    return db.session.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name=:t AND column_name=:c"), {'t': table, 'c': col}).scalar() is not None


def exists_index(name):
    return db.session.execute(text(
        "SELECT 1 FROM pg_class WHERE relname=:n AND relkind IN ('i','I')"),
        {'n': name}).scalar() is not None


def exists_constraint(name):
    return db.session.execute(text(
        "SELECT 1 FROM pg_constraint WHERE conname=:n"), {'n': name}).scalar() is not None


def main():
    with app.app_context():
        for kind, old, new in RENAMES:
            try:
                if kind == 'table':
                    if not exists_table(old):
                        print(f"  skip table    {old} (absent)"); continue
                    db.session.execute(text(f'ALTER TABLE {old} RENAME TO {new}'))
                    print(f"  table         {old} -> {new}")
                elif kind == 'column':
                    tbl, col = old.split('.')
                    if not exists_table(tbl) or not exists_column(tbl, col):
                        print(f"  skip column   {old} (absent)"); continue
                    db.session.execute(text(f'ALTER TABLE {tbl} RENAME COLUMN {col} TO {new}'))
                    print(f"  column        {old} -> {new}")
                elif kind == 'index':
                    if not exists_index(old):
                        print(f"  skip index    {old} (absent)"); continue
                    db.session.execute(text(f'ALTER INDEX {old} RENAME TO {new}'))
                    print(f"  index         {old} -> {new}")
                elif kind == 'fk':
                    tbl, con = old.split('.')
                    if not exists_constraint(con):
                        print(f"  skip fk       {con} (absent)"); continue
                    db.session.execute(text(
                        f'ALTER TABLE {tbl} RENAME CONSTRAINT {con} TO {new}'))
                    print(f"  constraint    {con} -> {new}")
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                print(f"  FAILED {kind} {old}: {exc}")
                raise

        print("\nverification:")
        for t in ('agent_incident', 'agent_incident_message', 'agent_incident_fix_outcome',
                  'incidents', 'incident_timeline', 'incident_evidence'):
            n = db.session.execute(text(f'SELECT count(*) FROM {t}')).scalar() \
                if exists_table(t) else 'ABSENT'
            print(f"  {t:<32} {n}")
        for old in ('incident_message', 'incident_fix_outcome'):
            print(f"  {old:<32} {'STILL PRESENT (bad)' if exists_table(old) else 'gone'}")


if __name__ == '__main__':
    main()
