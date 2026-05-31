"""Event bus — the connective tissue (nervous system) of the Agentic IT-OS.

Subsystems publish events to an outbox (`event_outbox`); a single leader-elected
worker dispatches them to subscribers — currently the workflow engine's `fire_trigger`.
This is what lets the brain REACT to the world instead of only acting when a human
clicks a button. Built on Postgres (no broker) + the same cross-process file lock the
SLA/sync threads use, so only ONE of the 5 gunicorn workers dispatches at a time.

Delivery is AT-LEAST-ONCE, not exactly-once: leader election prevents concurrent
double-dispatch, but a crash between firing a subscriber and marking the row dispatched
will replay the event. Subscribers MUST be idempotent. The approval engine gates any
risky action a triggered workflow plans, so an event firing a workflow can never itself
cause an unapproved destructive change (and a replay = at most a duplicate parked
approval, not a duplicate destructive action).

Design for THIS server: the app is created at import time and runs in 5 workers, so an
in-process queue can't span them — the outbox lives in Postgres and the dispatcher is
leader-elected via flock. See docs/AGENTIC_IT_OS_GAMEPLAN.md.
"""
import json, logging, os, re, threading, time
from datetime import datetime

from pg_db import pg_connect

log = logging.getLogger("event_bus")

_DISPATCH_INTERVAL = 5     # seconds between dispatch passes
_BATCH = 50                # max events handled per pass
_MAX_ATTEMPTS = 5          # give up (status='error') after this many failures
_RETENTION_DAYS = 30       # prune dispatched/error rows older than this
_PRUNE_EVERY = 720         # ~ every hour (720 * 5s); prune runs on the leader only
_REINDEX_EVERY = 120960    # ~ weekly (7d * 86400 / 5s); knowledge auto-reindex, leader only
_started = False           # per-process guard (mirrors sync_scheduler/workflow_engine)

# Defense-in-depth: never let a publisher persist an obvious secret in cleartext JSONB.
_SENSITIVE_KEY = re.compile(r"(pass|pwd|secret|token|api[_-]?key|credential|private[_-]?key)", re.I)


def _redact(d):
    if not isinstance(d, dict):
        return d
    out = {}
    for k, v in d.items():
        if isinstance(k, str) and _SENSITIVE_KEY.search(k) and v not in (None, "", "[redacted]"):
            out[k] = "[redacted]"
        elif isinstance(v, dict):
            out[k] = _redact(v)
        else:
            out[k] = v
    return out


def _db():
    return pg_connect()


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def ensure_schema():
    """Create the outbox table if absent. Additive + idempotent (safe at startup)."""
    try:
        db = _db()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS event_outbox ("
                " id SERIAL PRIMARY KEY,"
                " event_type VARCHAR(120) NOT NULL,"
                " payload JSONB,"
                " source VARCHAR(80),"
                " status VARCHAR(20) DEFAULT 'pending',"
                " attempts INTEGER DEFAULT 0,"
                " last_error TEXT,"
                " created_at TIMESTAMP,"
                " dispatched_at TIMESTAMP)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_event_outbox_pending "
                "ON event_outbox (status, id) WHERE status='pending'"
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("event_outbox schema ensure failed")


def publish(event_type, payload=None, source="app"):
    """Record an event in the outbox. Best-effort — never raises into the caller.

    DESIGN DECISION (assessed 2026-05-31): this inserts in its OWN connection, post-commit,
    not the caller's business transaction. A true same-txn outbox would close the tiny
    crash-between-commit-and-publish window, but it's deliberately NOT done here: (1) the
    only window is a process death in the ~ms between the business commit and this insert;
    (2) every subscriber is recoverable on a miss — a dropped ticket.created just means that
    one ticket has no auto-suggestion (the manual Suggest button still works), and a dropped
    ticket.resolved skips one auto-runbook; nothing destructive is lost; (3) a true outbox
    needs the row id BEFORE commit (e.g. ticket.id), forcing a flush+restructure of each
    caller for marginal gain. So: publish AFTER the business change commits, accept
    at-least-once, keep subscribers idempotent. Revisit only if a critical, non-recoverable
    event type is added.
    """
    try:
        db = _db()
        try:
            cur = db.execute(
                "INSERT INTO event_outbox (event_type, payload, source, status, attempts, created_at) "
                "VALUES (?,?,?,'pending',0,?)",
                (event_type, json.dumps(_redact(payload or {}), default=str), source, _now()),
            )
            eid = cur.lastrowid
            db.commit()
            log.info("event published: %s (#%s)", event_type, eid)
            return eid
        finally:
            db.close()
    except Exception:
        log.exception("event publish failed (%s)", event_type)
        return None


def _mark_dispatched(eid):
    db = _db()
    try:
        db.execute("UPDATE event_outbox SET status='dispatched', dispatched_at=? WHERE id=?",
                   (_now(), eid))
        db.commit()
    finally:
        db.close()


def _mark_failed(eid, err):
    db = _db()
    try:
        # Bump attempts; flip to terminal 'error' once we hit the cap, else leave 'pending'.
        db.execute(
            "UPDATE event_outbox SET attempts=attempts+1, last_error=?, "
            "status=CASE WHEN attempts+1 >= ? THEN 'error' ELSE 'pending' END WHERE id=?",
            (str(err)[:500], _MAX_ATTEMPTS, eid),
        )
        db.commit()
    finally:
        db.close()


def _prune():
    """Delete terminal (dispatched/error) rows older than the retention window. Keeps the
    outbox and the /events count query small. Leader-only, best-effort."""
    try:
        db = _db()
        try:
            db.execute(
                "DELETE FROM event_outbox WHERE status IN ('dispatched','error') "
                "AND created_at < NOW() - ?::interval",
                (f"{_RETENTION_DAYS} days",),
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("event_outbox prune failed")


def _dispatch_once(flask_app):
    """One dispatch pass — fan pending events out to subscribers. Caller holds the lock."""
    db = _db()
    try:
        rows = db.execute(
            "SELECT id, event_type, payload FROM event_outbox "
            "WHERE status='pending' AND attempts < ? ORDER BY id ASC LIMIT ?",
            (_MAX_ATTEMPTS, _BATCH),
        ).fetchall()
    finally:
        db.close()
    if not rows:
        return 0

    import workflow_engine
    dispatched = 0
    for r in rows:
        eid, etype, raw = r["id"], r["event_type"], r["payload"]
        if isinstance(raw, (str, bytes, bytearray)):
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                payload = {}
        else:
            payload = raw or {}          # JSONB comes back already parsed
        try:
            # Subscriber: the workflow engine. fire_trigger finds enabled workflows whose
            # trigger_type matches and runs them (risky actions park for approval).
            with flask_app.app_context():
                workflow_engine.fire_trigger(etype, payload)
            _mark_dispatched(eid)
            dispatched += 1
        except Exception as e:
            _mark_failed(eid, e)
            log.exception("event dispatch failed (id=%s type=%s)", eid, etype)
    return dispatched


def start_event_dispatcher(flask_app):
    """Start the leader-elected dispatch loop. One thread per worker; only the flock
    winner dispatches. Idempotent per process (guards against a double start)."""
    global _started
    if _started:
        return None
    _started = True
    ensure_schema()

    def _loop():
        from sync_scheduler import _file_lock
        lock_path = os.environ.get("TRACKER_EVENT_LOCK_PATH", "/tmp/tracker_event_dispatch.lock")
        ticks = 0
        while True:
            try:
                time.sleep(_DISPATCH_INTERVAL)
                ticks += 1
                with _file_lock(lock_path) as got_lock:
                    if got_lock:
                        _dispatch_once(flask_app)
                        if ticks % _PRUNE_EVERY == 0:
                            _prune()
                        if ticks % _REINDEX_EVERY == 0:
                            try:
                                import knowledge_agent
                                with flask_app.app_context():
                                    knowledge_agent.reindex()
                            except Exception:
                                log.exception("scheduled knowledge reindex failed")
            except Exception:
                log.exception("event dispatcher loop error")

    t = threading.Thread(target=_loop, daemon=True, name="event-dispatcher")
    t.start()
    log.info("Event dispatcher started")
    return t
