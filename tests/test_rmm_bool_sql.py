"""Guards against the PostgreSQL type mismatches that broke the asset-page RMM
tools on 2026-06-09 (incident: "RustDesk code invalid / shell auth failed /
Services missing"). The Tracker moved from SQLite (where 1/0 == true/false) to
PostgreSQL (strict typing), so these int-vs-bool slips raise at runtime and
never surfaced in the DB-free test suite. These are pure source-parsing checks:
no app import, no DB — they run in the existing CI job.

Two distinct slips were fixed:

1. blueprints/rmm_agent_ingest.py — the auto-register INSERT into rmm_agent bound
   the literal `1` to the BOOLEAN column `enabled`, so EVERY new agent's ingest
   upsert failed with:
       column "enabled" is of type boolean but expression is of type integer
   New agents never got an rmm_agent row → no RMM tools on their asset page.

2. blueprints/rmm.py — the Eagle Eyes report scheduler compared the BIGINT column
   rmm_eagle_report_schedule.enabled with `= true`, firing every 15 min with:
       operator does not exist: bigint = boolean
   (rmm_eagle_report_schedule.enabled is bigint, unlike the boolean `enabled` on
   every sibling rmm_* table — a migration artifact. Code must compare `= 1`.)
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_rmm_agent_insert_uses_boolean_for_enabled():
    """The rmm_agent auto-register INSERT must bind a real SQL boolean to the
    boolean `enabled` column, never the integer literal 1/0."""
    src = _read("blueprints/rmm_agent_ingest.py")
    # Find the INSERT INTO rmm_agent (... enabled ...) VALUES (...) statement.
    m = re.search(
        r"INSERT INTO rmm_agent\s*\((?P<cols>[^)]*)\)\s*VALUES\s*\((?P<vals>[^)]*)\)",
        src, re.IGNORECASE,
    )
    assert m, "rmm_agent INSERT statement not found — did the ingest upsert move?"
    cols = [c.strip() for c in m.group("cols").split(",")]
    vals = [v.strip() for v in m.group("vals").split(",")]
    assert "enabled" in cols, "rmm_agent INSERT no longer sets `enabled`"
    enabled_val = vals[cols.index("enabled")].lower()
    assert enabled_val in ("true", "false"), (
        f"rmm_agent.enabled is BOOLEAN in PostgreSQL; the INSERT binds "
        f"{enabled_val!r}. Use the SQL literal `true`/`false`, not 1/0 — "
        f"integer literals raise DatatypeMismatch on every new-agent ingest."
    )


def test_eagle_report_schedule_enabled_not_compared_to_boolean():
    """rmm_eagle_report_schedule.enabled is a bigint; comparing it to `true`/
    `false` raises 'operator does not exist: bigint = boolean'. The scheduler
    query must compare against an integer (e.g. `enabled = 1`)."""
    src = _read("blueprints/rmm.py")
    bad = re.findall(
        r"rmm_eagle_report_schedule\s+WHERE\s+enabled\s*=\s*(?:true|false)",
        src, re.IGNORECASE,
    )
    assert not bad, (
        "rmm_eagle_report_schedule.enabled is BIGINT — comparing it to a SQL "
        "boolean raises 'operator does not exist: bigint = boolean' and kills "
        "the Eagle report scheduler every 15 min. Compare `enabled = 1`."
    )
