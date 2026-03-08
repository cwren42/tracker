#!/usr/bin/env python3
"""Migration: workflows, AI, reports"""
import sqlite3, sys, os

DB = os.path.join(os.path.dirname(__file__), 'assets.db')
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

MIGRATIONS = [
    # ── Workflow definitions (visual canvas config stored as JSON) ──────────
    """CREATE TABLE IF NOT EXISTS workflow_definitions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT,
        trigger_type TEXT NOT NULL,
        trigger_config TEXT DEFAULT '{}',
        nodes       TEXT NOT NULL DEFAULT '[]',
        edges       TEXT NOT NULL DEFAULT '[]',
        enabled     INTEGER DEFAULT 1,
        created_by  TEXT,
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    )""",

    # ── Workflow run history ────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS workflow_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id  INTEGER NOT NULL,
        trigger_data TEXT DEFAULT '{}',
        status       TEXT DEFAULT 'running',
        started_at   TEXT DEFAULT (datetime('now')),
        completed_at TEXT,
        error        TEXT,
        FOREIGN KEY (workflow_id) REFERENCES workflow_definitions(id)
    )""",

    # ── Per-step results within a run ──────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS workflow_run_steps (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id       INTEGER NOT NULL,
        node_id      TEXT NOT NULL,
        node_type    TEXT,
        node_label   TEXT,
        status       TEXT DEFAULT 'pending',
        started_at   TEXT,
        completed_at TEXT,
        input_data   TEXT DEFAULT '{}',
        output_data  TEXT DEFAULT '{}',
        error        TEXT,
        FOREIGN KEY (run_id) REFERENCES workflow_runs(id)
    )""",

    # ── AI ticket suggestions ───────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS ai_ticket_suggestions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id   INTEGER NOT NULL,
        model       TEXT DEFAULT 'gpt-4o',
        suggestion  TEXT NOT NULL,
        confidence  REAL,
        category    TEXT,
        auto_mode   INTEGER DEFAULT 0,
        status      TEXT DEFAULT 'pending',
        created_at  TEXT DEFAULT (datetime('now')),
        reviewed_by TEXT,
        reviewed_at TEXT,
        sent_at     TEXT
    )""",

    # ── AI security summaries (periodic posture reports) ───────────────────
    """CREATE TABLE IF NOT EXISTS ai_security_summaries (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        summary              TEXT NOT NULL,
        vuln_count           INTEGER DEFAULT 0,
        critical_count       INTEGER DEFAULT 0,
        patch_compliance_pct INTEGER DEFAULT 0,
        action_items         TEXT DEFAULT '[]',
        raw_data             TEXT DEFAULT '{}',
        created_at           TEXT DEFAULT (datetime('now'))
    )""",

    # ── Report templates ────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS report_templates (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT,
        report_type TEXT NOT NULL,
        config      TEXT DEFAULT '{}',
        is_builtin  INTEGER DEFAULT 0,
        created_by  TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    )""",

    # ── Generated report instances ──────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS report_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id  INTEGER,
        name         TEXT,
        report_type  TEXT NOT NULL,
        config       TEXT DEFAULT '{}',
        status       TEXT DEFAULT 'pending',
        file_pdf     TEXT,
        file_csv     TEXT,
        row_count    INTEGER DEFAULT 0,
        generated_by TEXT,
        generated_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT,
        error        TEXT
    )""",
]

SEED_TEMPLATES = [
    ("Vulnerability Report",       "vulnerability",    "All open CVEs by severity with affected assets and remediation status"),
    ("Patch Compliance Report",    "patch_compliance", "Per-device patch status, compliance rate, and missing patches"),
    ("Asset Inventory Report",     "asset_inventory",  "Full asset list with lifecycle, warranty, and assignment data"),
    ("Ticket Summary Report",      "tickets",          "Ticket volume, resolution time, and category breakdown"),
    ("Alert History Report",       "alerts",           "Alert frequency by type and device over selected period"),
    ("User Activity Report",       "user_activity",    "Logins, config changes, and admin actions audit log"),
    ("RMM Agent Status Report",    "rmm_status",       "Agent online/offline status, last seen, patch job history"),
]

def run():
    print("Running migrations…")
    for sql in MIGRATIONS:
        table = sql.split("EXISTS")[1].split("(")[0].strip()
        db.execute(sql)
        print(f"  ✓ {table}")
    db.commit()

    print("Seeding built-in report templates…")
    for name, rtype, desc in SEED_TEMPLATES:
        exists = db.execute(
            "SELECT id FROM report_templates WHERE name=? AND is_builtin=1", (name,)
        ).fetchone()
        if not exists:
            db.execute(
                "INSERT INTO report_templates (name, description, report_type, is_builtin) VALUES (?,?,?,1)",
                (name, desc, rtype)
            )
            print(f"  ✓ {name}")
        else:
            print(f"  — {name} (already exists)")
    db.commit()
    print("Done.")

if __name__ == '__main__':
    run()
