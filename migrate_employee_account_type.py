"""Separate service accounts and shared objects from people.

`employee.is_visible = False` had become a grab-bag: real departures, AD
service accounts (gitlab-runner, Scans, Darth Vader...) and cloud-only M365
objects all shared the flag. Nothing could tell them apart, so "who has left?"
queries returned robots, and the ISMS worker ledger had to guess.

Adds `employee.account_type`:
    person   - a human being
    service  - an AD/M365 account that runs something
    shared   - a shared mailbox, alias or non-person directory object
    unknown  - not yet classified; needs a human decision

Auto-classification is deliberately conservative. Only names that are plainly
not people are marked `service`; anything with a human name is left `unknown`
rather than guessed at, because getting this wrong misrepresents a person in an
audited record.

Idempotent. Run: venv/bin/python migrate_employee_account_type.py
"""
import os
import psycopg2


def _dsn() -> str:
    dsn = os.environ.get('DATABASE_URL')
    if dsn:
        return dsn
    with open('/var/www/tracker/.secrets.env') as fh:
        for line in fh:
            if line.strip().startswith('DATABASE_URL='):
                return line.split('=', 1)[1].strip().strip('"').strip("'")
    raise RuntimeError('DATABASE_URL not set')


# Names that are unambiguously not people.
KNOWN_SERVICE = [
    'gitlab-runner', 'Scans', 'ptptest user', 'Capex', 'Darth Vader',
    'Han Solo', 'Chewy', 'Project Admin',
]


def migrate():
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM information_schema.columns "
                "WHERE table_name='employee' AND column_name='account_type'")
    if cur.fetchone():
        print("employee.account_type already present")
    else:
        cur.execute("ALTER TABLE employee ADD COLUMN account_type VARCHAR(20)")
        print("employee.account_type added")

    # Anyone visible and not offboarded is a person -- they are in the worker
    # population today, which is the strongest signal available.
    cur.execute("""UPDATE employee SET account_type='person'
                    WHERE account_type IS NULL
                      AND is_visible = TRUE AND offboarded_at IS NULL""")
    print(f"  marked person (active roster)      : {cur.rowcount}")

    # Formally offboarded records are people who left.
    cur.execute("""UPDATE employee SET account_type='person'
                    WHERE account_type IS NULL AND offboarded_at IS NOT NULL""")
    print(f"  marked person (offboarded)         : {cur.rowcount}")

    cur.execute("""UPDATE employee SET account_type='service'
                    WHERE account_type IS NULL AND name = ANY(%s)""", (KNOWN_SERVICE,))
    print(f"  marked service (known non-people)  : {cur.rowcount}")

    # Hidden, AD-joined, and carrying a department: a person who left without
    # being offboarded. Real humans, so classify them as such -- the missing
    # offboard date is a separate problem.
    cur.execute("""UPDATE employee SET account_type='person'
                    WHERE account_type IS NULL
                      AND ad_dn IS NOT NULL AND department IS NOT NULL""")
    print(f"  marked person (AD + department)    : {cur.rowcount}")

    cur.execute("UPDATE employee SET account_type='unknown' WHERE account_type IS NULL")
    print(f"  left unknown (needs a decision)    : {cur.rowcount}")

    conn.commit()
    cur.execute("SELECT account_type, COUNT(*) FROM employee GROUP BY 1 ORDER BY 2 DESC")
    print("\nfinal distribution:")
    for kind, count in cur.fetchall():
        print(f"  {kind:10s} {count}")
    cur.execute("SELECT name, email FROM employee WHERE account_type='unknown' ORDER BY name")
    rows = cur.fetchall()
    if rows:
        print("\nunclassified - human names but no AD account or department:")
        for name, email in rows:
            print(f"  {name:20s} {email or ''}")
    cur.close()
    conn.close()


if __name__ == '__main__':
    migrate()
