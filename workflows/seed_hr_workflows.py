#!/usr/bin/env python3
"""
Seed HR onboarding and offboarding workflows into the workflow_definitions table.

Usage:
    python workflows/seed_hr_workflows.py

Run from /var/www/tracker. Safe to run multiple times — skips workflows that
already exist by name.
"""
import json
import os
import sys
from datetime import datetime

# Allow importing pg_db from the tracker root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pg_db import pg_connect

WORKFLOWS_DIR = os.path.dirname(os.path.abspath(__file__))
FILES = [
    "employee_onboarding.json",
    "employee_offboarding.json",
]

NOW = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def seed():
    conn = pg_connect()
    created = 0
    skipped = 0

    for fname in FILES:
        path = os.path.join(WORKFLOWS_DIR, fname)
        with open(path) as f:
            wf = json.load(f)

        name = wf["name"]
        existing = conn.execute(
            "SELECT id FROM workflow_definitions WHERE name = %s LIMIT 1", (name,)
        ).fetchone()

        if existing:
            print(f"  SKIP  '{name}' (already exists, id={existing['id']})")
            skipped += 1
            continue

        conn.execute(
            """INSERT INTO workflow_definitions
               (name, description, trigger_type, trigger_config, nodes, edges,
                enabled, created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                name,
                wf.get("description", ""),
                wf.get("trigger_type", "manual"),
                json.dumps(wf.get("trigger_config", {})),
                json.dumps(wf.get("nodes", [])),
                json.dumps(wf.get("edges", [])),
                bool(wf.get("enabled", True)),
                "system",
                NOW,
                NOW,
            ),
        )
        print(f"  CREATE '{name}'")
        created += 1

    conn.commit()
    conn.close()
    print(f"\nDone — {created} created, {skipped} skipped.")


if __name__ == "__main__":
    seed()
