"""
Migration: seed the `onboard_license_group` Setting.

Onboarding provisions a new hire in on-prem AD and adds them to the AD security
groups IT picks — but the group that grants the M365 license (`Cirque-Users` →
Microsoft 365 Business Premium) is a CLOUD-ONLY Entra group, unreachable from the
on-prem add path. So greenfield hires landed with NO license. _action_onboard_
employee now adds the user to this cloud group via Graph (group-based licensing);
this Setting holds the target Entra group id so it isn't hardcoded.

Value = the Entra object id of `Cirque-Users`. Change it here (or in the Settings
table) if the license group ever changes.

Idempotent — inserts only if absent. Safe to re-run.
Run once: venv/bin/python migrate_seed_onboard_license_group.py
"""
import os
import psycopg2

CIRQUE_USERS_GROUP_ID = "63f45fbf-454b-47ad-834d-275b86d51bd5"


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


def migrate():
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM setting WHERE key = 'onboard_license_group'")
    if cur.fetchone():
        print("setting onboard_license_group already present.")
    else:
        cur.execute("INSERT INTO setting (key, value) VALUES (%s, %s)",
                    ('onboard_license_group', CIRQUE_USERS_GROUP_ID))
        print(f"Seeded onboard_license_group = {CIRQUE_USERS_GROUP_ID} (Cirque-Users).")
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    migrate()
