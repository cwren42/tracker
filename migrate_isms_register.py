"""ISMS ledger register: capture surfaces + compliance reminders.

The ALAP ISMS Management Ledgers are filed several times a year, and a number
of their required columns have no home in Tracker — so every filing they get
hand-typed and drift. This adds the places to record them, plus the alert rules
that chase the periodic obligations behind them.

1. soc2_vendor gains the F06B fields: xNDA-007 execution date, ISMS
   announcement date, the partner's liaison department, on-site access scope,
   required availability, training requirement, data return/deletion terms, and
   a terms-and-conditions review date.
2. license gains `version`, for F08A's Software Version column.
3. New isms_information_asset table — the F02B register. Tracker has no
   information-asset concept today, which is why 89 of its 90 rows are blank in
   the columns ALAP added for FY26.
4. Seeds a `compliance` alert category. Nothing watched vendor reviews before
   this, which is how a vendor review ran 28 days past due unnoticed.

Idempotent — safe to re-run.
Run once: venv/bin/python migrate_isms_register.py
"""
import os
import psycopg2


def _dsn() -> str:
    dsn = os.environ.get('DATABASE_URL')
    if dsn:
        return dsn
    with open('/var/www/tracker/.secrets.env') as fh:
        for line in fh:
            line = line.strip()
            if line.startswith('DATABASE_URL='):
                return line.split('=', 1)[1].strip().strip('"').strip("'")
    raise RuntimeError('DATABASE_URL not set')


VENDOR_COLUMNS = [
    ('nda_executed_date', 'DATE'),                  # F06B I  xNDA-007 execution
    ('isms_notified_date', 'DATE'),                 # F06B J  ISMS announcement
    ('terms_reviewed_date', 'DATE'),                # T&C review of record
    ('terms_next_review_date', 'DATE'),             # risk-tiered, see alert rules
    ('contact_department', 'VARCHAR(200)'),         # F06B D
    ('onsite_access_scope', 'TEXT'),                # F06B G
    ('required_availability', 'INTEGER'),           # F06B W  (3/2/1)
    ('training_required', 'VARCHAR(10)'),           # F06B X  Yes/No
    ('data_return_on_termination', 'TEXT'),         # F06B Y
]

# F02B columns ALAP added for FY26 that no FY25 carry-over covers.
INFORMATION_ASSET_DDL = """
CREATE TABLE IF NOT EXISTS isms_information_asset (
    id                          SERIAL PRIMARY KEY,
    asset_name                  VARCHAR(400) NOT NULL,
    required_protect_class      VARCHAR(100),
    critical_classification     VARCHAR(200),   -- F02B E
    customer_name               VARCHAR(200),   -- F02B F (blank when not on ALAP's list)
    information_category        VARCHAR(200),   -- F02B G
    information_category_fy25   VARCHAR(20),    -- F02B H, carried
    asset_manager               VARCHAR(200),
    owning_department           VARCHAR(200),
    business_area               VARCHAR(200),   -- F02B K
    purpose                     TEXT,
    media_form                  VARCHAR(100),   -- F02B M
    media_form_fy25             VARCHAR(100),
    stored_on                   VARCHAR(400),   -- support asset / F02-A link
    viewing_authority           VARCHAR(20),
    permitted_scope_of_use      TEXT,           -- F02B Q
    other_requirements          TEXT,
    confidentiality             INTEGER,
    integrity                   INTEGER,
    availability                INTEGER,
    threat_class                INTEGER,
    vulnerability_class         INTEGER,
    usage_start_date            DATE,
    usage_stop_date             DATE,
    remarks                     TEXT,
    is_active                   BOOLEAN DEFAULT TRUE,
    source                      VARCHAR(50) DEFAULT 'manual',
    created_at                  TIMESTAMPTZ DEFAULT now(),
    updated_at                  TIMESTAMPTZ DEFAULT now(),
    created_by                  VARCHAR(120)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_isms_information_asset_name
    ON isms_information_asset (lower(asset_name));
"""

# (category, alert_type, label, enabled, auto_ticket, email_notify, threshold, unit)
COMPLIANCE_RULES = [
    ('compliance', 'vendor_review_due',      'Vendor Review Due (30 days)',              True,  False, True,  30, 'days'),
    ('compliance', 'vendor_review_overdue',  'Vendor Review Overdue',                    True,  True,  True,  0,  'days'),
    ('compliance', 'vendor_terms_due',       'Vendor Terms & Conditions Review Due',     True,  False, True,  30, 'days'),
    ('compliance', 'vendor_nda_missing',     'Business Partner Has No xNDA-007 On File', True,  False, True,  0,  ''),
    ('compliance', 'isms_training_due',      'ISMS Training Due (annual, per worker)',   True,  False, True,  30, 'days'),
    ('compliance', 'isms_training_overdue',  'ISMS Training Overdue',                    True,  True,  True,  0,  'days'),
    ('compliance', 'asset_inspection_due',   'Asset Security Inspection Due',            True,  False, True,  14, 'days'),
    ('compliance', 'ledger_filing_due',      'ISMS Ledger Filing Due',                   True,  False, True,  30, 'days'),
]


def migrate():
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()

    # 1. soc2_vendor columns
    for name, coltype in VENDOR_COLUMNS:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='soc2_vendor' AND column_name=%s", (name,))
        if cur.fetchone():
            print(f"  soc2_vendor.{name} already present")
        else:
            cur.execute(f"ALTER TABLE soc2_vendor ADD COLUMN {name} {coltype}")
            print(f"  soc2_vendor.{name} added")

    # 2. license.version
    cur.execute("SELECT 1 FROM information_schema.columns "
                "WHERE table_name='license' AND column_name='version'")
    if cur.fetchone():
        print("  license.version already present")
    else:
        cur.execute("ALTER TABLE license ADD COLUMN version VARCHAR(120)")
        print("  license.version added")

    # 3. information asset register
    cur.execute(INFORMATION_ASSET_DDL)
    print("  isms_information_asset ensured")

    # 4. compliance alert rules
    for (cat, atype, label, enabled, auto_ticket, email, threshold, unit) in COMPLIANCE_RULES:
        cur.execute("SELECT 1 FROM alert_rule WHERE alert_type=%s", (atype,))
        if cur.fetchone():
            print(f"  alert_rule {atype} already present")
            continue
        cur.execute(
            """INSERT INTO alert_rule
                 (category, alert_type, label, threshold_value, threshold_unit,
                  enabled, auto_ticket, ticket_priority, email_notify,
                  teams_notify, cooldown_minutes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (cat, atype, label, threshold, unit, enabled, auto_ticket,
             'Normal', email, False, 10080))   # weekly cooldown: these are slow obligations
        print(f"  alert_rule {atype} seeded")

    conn.commit()
    cur.close()
    conn.close()
    print("done.")


if __name__ == '__main__':
    migrate()
