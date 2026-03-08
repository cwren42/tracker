"""pg_db.py — Thin psycopg2 shim shared by background service modules.

Exposes pg_connect() which returns a _Conn object that mimics the
sqlite3.Connection / sqlite3.Row API so that background services
(alert_service, ai_engine, workflow_engine, report_engine, api_system)
require minimal code changes for the SQLite → PostgreSQL migration.
"""

import re
import datetime as _dt

import psycopg2
import psycopg2.extras

DB_DSN = "postgresql://tracker_user:tracker_secure_2026@localhost/tracker"


# ─── Strip timezone from all TIMESTAMPTZ reads ────────────────────────────────
# pgloader migrated SQLite text columns to PostgreSQL TIMESTAMP WITH TIME ZONE.
# The app was written for SQLite which returns naive datetimes; to avoid
# changing every datetime.utcnow() call we strip tzinfo globally on read.
#
# psycopg2 calls new_type adapters with the RAW TEXT string from PostgreSQL,
# not a pre-parsed object.  We set timezone=UTC on every connection so PG
# always sends "YYYY-MM-DD HH:MM:SS[.ffffff]+00" — then we just strip the
# offset suffix instead of doing tz conversion arithmetic.
#
# OIDs: 1184 = timestamptz, 1185 = timestamptz[] (array)
_TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(\.\d+)?')

def _strip_tz(val, cur):
    """Parse raw PostgreSQL TIMESTAMPTZ string into naive UTC datetime."""
    if val is None:
        return None
    m = _TS_RE.match(val)
    if not m:
        return None
    base = m.group(1)
    frac = m.group(2) or ''
    fmt = '%Y-%m-%d %H:%M:%S' + ('.%f' if frac else '')
    return _dt.datetime.strptime(base + frac, fmt)

try:
    _NAIVE_TS = psycopg2.extensions.new_type(
        (1184, 1185), 'NAIVE_TIMESTAMPTZ', _strip_tz
    )
    psycopg2.extensions.register_type(_NAIVE_TS)
except Exception as _e:
    import logging as _log
    _log.getLogger('pg_db').warning('TIMESTAMPTZ adapter failed: %s', _e)


# ─── SQL dialect converter ────────────────────────────────────────────────────

def _fix_sql(sql: str) -> str:
    """Translate SQLite-isms to PostgreSQL equivalents."""

    def _interval_sub(m):
        sign = '+' if m.group(1)[0] == '+' else '-'
        n    = abs(int(m.group(1)))
        unit = m.group(2)
        return f"NOW() {sign} INTERVAL '{n} {unit}'"

    # 1. datetime('now', '±N unit') → NOW() ± INTERVAL 'N unit'
    sql = re.sub(
        r"(?i)datetime\('now',\s*'([+-]\d+)\s+(\w+)'\)",
        _interval_sub, sql
    )
    # 2. date('now', '±N unit') → same form
    sql = re.sub(
        r"(?i)date\('now',\s*'([+-]\d+)\s+(\w+)'\)",
        _interval_sub, sql
    )
    # 3. datetime('now', ?) → NOW() + ?::interval  (before ? → %s conversion)
    sql = re.sub(
        r"(?i)datetime\('now',\s*\?\)",
        "NOW() + ?::interval",
        sql
    )
    # 4. bare datetime('now') → NOW()
    sql = re.sub(r"(?i)datetime\('now'\)", "NOW()", sql)
    # 5. Escape bare % in SQL (LIKE wildcards) so psycopg2 won't misread them
    #    Replace % with %% — but only when it is NOT already %% and NOT %s
    sql = re.sub(r"(?<!%)%(?![%s(])", "%%", sql)
    # 6. ? → %s (positional param placeholder)
    sql = sql.replace("?", "%s")
    # 7. NOW() + ?::interval (from step 3) is now NOW() + %s::interval — correct
    # 8. Auto-add RETURNING id to bare INSERTs so lastrowid works
    stripped = sql.strip()
    if (stripped.upper().startswith("INSERT")
            and "RETURNING" not in stripped.upper()):
        sql = stripped.rstrip(";") + " RETURNING id"
    return sql


# ─── Row wrapper ─────────────────────────────────────────────────────────────

class _Row(dict):
    """Dict subclass that also supports positional iteration (like sqlite3.Row).

    list(row)   → list of values in column order (for CSV/table export).
    row['col']  → value by column name.
    dict(row)   → plain dict copy.
    """
    def __iter__(self):
        return iter(self.values())


# ─── Cursor wrapper ───────────────────────────────────────────────────────────

class _Cursor:
    """Wraps a psycopg2 RealDictCursor to mimic the sqlite3 cursor API."""

    def __init__(self, pg_cur):
        self._c = pg_cur

    def execute(self, sql, params=()):
        self._c.execute(_fix_sql(sql), params or None)
        return self

    def fetchall(self):
        return [_Row(r) for r in self._c.fetchall()]

    def fetchone(self):
        r = self._c.fetchone()
        return _Row(r) if r else None

    @property
    def lastrowid(self):
        r = self._c.fetchone()
        return r["id"] if r else None

    def __iter__(self):
        return (_Row(r) for r in self._c)


# ─── Connection wrapper ───────────────────────────────────────────────────────

class _Conn:
    """Wraps a psycopg2 connection to mimic the sqlite3.Connection API."""

    def __init__(self):
        self._conn = psycopg2.connect(DB_DSN, options="-c timezone=UTC")

    def cursor(self) -> _Cursor:
        return _Cursor(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

    def execute(self, sql, params=()) -> _Cursor:
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ─── Public helper ────────────────────────────────────────────────────────────

def pg_connect() -> _Conn:
    """Return a new PostgreSQL connection that mimics sqlite3.Connection."""
    return _Conn()
