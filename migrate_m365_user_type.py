"""Tag B2B guests in the M365 user table so they stop counting as our users.

115 of 227 synced M365 accounts are external B2B guests — Alps Alpine, Liteon,
Lenovo, Dell and other partners invited into the tenant. They are legitimately
in M365 and legitimately not Cirque users, but nothing distinguished them, so
every "user count" in the SOC 2 evidence and dashboards was roughly double the
real headcount.

Adds `m365_user.user_type`, populated from Graph's `userType` (Member/Guest) on
the next sync. This migration backfills from the UPN shape (`#EXT#`) so the
distinction is available immediately without waiting for a sync.

Guests are tagged rather than deleted: they hold real tenant access, so they
belong in the record — just not in the count of our own people.

Idempotent. Run: venv/bin/python migrate_m365_user_type.py
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


def migrate():
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM information_schema.columns "
                "WHERE table_name='m365_user' AND column_name='user_type'")
    if cur.fetchone():
        print("m365_user.user_type already present")
    else:
        cur.execute("ALTER TABLE m365_user ADD COLUMN user_type VARCHAR(20)")
        print("m365_user.user_type added")

    # A UPN containing #EXT# is a B2B guest whose home tenant is elsewhere.
    cur.execute("""UPDATE m365_user SET user_type='Guest'
                    WHERE user_type IS DISTINCT FROM 'Guest'
                      AND user_principal_name ILIKE '%%#EXT#%%'""")
    print(f"  tagged Guest  : {cur.rowcount}")

    cur.execute("""UPDATE m365_user SET user_type='Member'
                    WHERE user_type IS NULL""")
    print(f"  tagged Member : {cur.rowcount}")

    conn.commit()
    cur.execute("""SELECT user_type, COUNT(*),
                          COUNT(*) FILTER (WHERE is_admin),
                          COUNT(*) FILTER (WHERE account_enabled)
                     FROM m365_user WHERE is_current GROUP BY 1 ORDER BY 2 DESC""")
    print("\ncurrent rows by type (total / admin / enabled):")
    for kind, total, admins, enabled in cur.fetchall():
        print(f"  {kind:8s} {total:>4}  admin={admins:<3} enabled={enabled}")
    cur.close()
    conn.close()


if __name__ == '__main__':
    migrate()
