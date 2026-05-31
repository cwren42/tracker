---
name: safedb
description: Query the Tracker's production PostgreSQL safely. Use for investigating data, counts, schema, or one-off reads. Read-only by default; writes require explicit user consent and run transactionally.
allowed-tools: Bash, Read
argument-hint: "[what to look up]"
---
# Safe production DB access

The Tracker uses **PostgreSQL** via Flask-SQLAlchemy (+ a `pg_db.py` SQLite→PG shim).
`DATABASE_URL` lives in `.secrets.env`. `psycopg2` is in the project venv (not system python).

## Read pattern (default — always allowed)
```
cd /var/www/tracker
set -a; . ./.secrets.env 2>/dev/null; set +a
venv/bin/python - <<'PY'
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL']); cur = conn.cursor()
cur.execute("SELECT ...")          # SELECT / read only
print(cur.fetchall())
conn.close()
PY
```
- Use `venv/bin/python` (system `python3` lacks psycopg2).
- Never print secret values (passwords, tokens, encryption keys). For secret columns report `<set>`/`<empty>` only.
- Secrets in the `setting` table may be Fernet-encrypted (`enc:v1:` prefix) — decrypt via `secret_store.decrypt_secret` only when genuinely needed, never echo plaintext.

## Write pattern (requires EXPLICIT user consent for the specific change)
Do NOT write to prod without the user clearly approving *that* change. When approved:
- Take a backup first for anything destructive.
- Wrap in a transaction; verify the row counts / post-state BEFORE `conn.commit()`; `rollback()` on any mismatch.
- Keep it tightly scoped (named keys/ids), and print before/after.

The auto-mode safety classifier will (correctly) block prod writes and history rewrites
done on vague consent — get specific approval, then proceed.

## Useful references
- Models: `models.py`, `soc2_models.py` (class → `__tablename__`).
- RMM tables are raw-SQL (`rmm_agent`, `rmm_telemetry`, `rmm_commands`, `rmm_eagle_*`, …).
