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
_COLLECT_EVERY = 17283     # ~ daily (offset so it doesn't collide with prune/reindex ticks)
_EAGLE_PURGE_EVERY = 17299 # ~ daily (prime-ish offset so it doesn't collide with the others)

# Eagle Eyes activity-event retention. rmm_eagle_event is an unbounded surveillance store
# (2M+ rows); without a bound it grows forever. Retention is TIME-BASED and fleet-wide — we
# delete strictly by AGE, never by enable/exclude state (an enabled-but-excluded test box's
# data is kept while it's inside the window). Configurable via Setting 'eagle_event_retention_days'.
_EAGLE_RETENTION_DEFAULT_DAYS = 90
_EAGLE_PURGE_BATCH = 50000   # bounded chunk size so a large delete doesn't hold a long lock
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
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # local time (TZ=America/Denver), see now_mst


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


def _eagle_retention_days():
    """Read Setting 'eagle_event_retention_days' (fallback 90). Raw SQL on the shim
    connection — keeps this leader-loop step out of the Flask app context, matching _prune."""
    try:
        db = _db()
        try:
            row = db.execute(
                "SELECT value FROM setting WHERE key = ?", ("eagle_event_retention_days",)
            ).fetchone()
        finally:
            db.close()
        if row is not None:
            v = row[0] if not isinstance(row, dict) else row.get("value")
            n = int(str(v).strip())
            if n > 0:
                return n
    except Exception:
        log.exception("eagle retention-days read failed; using default")
    return _EAGLE_RETENTION_DEFAULT_DAYS


def _purge_eagle_events():
    """Delete rmm_eagle_event rows older than the retention window (captured_at < now - N days).

    TIME-BASED + fleet-wide: deletes ONLY by age, never by enable/exclude state. Batched so a
    large backlog delete doesn't hold a long table lock. captured_at is indexed
    (idx_17063_idx_rmm_eagle_event_date), so the age filter is index-supported. Leader-only,
    best-effort. rmm_eagle_current is one-row-per-agent live state (not history) — intentionally
    left untouched. Returns total rows deleted."""
    days = _eagle_retention_days()
    total = 0
    try:
        while True:
            db = _db()
            try:
                # Bounded chunk: delete the oldest matching ids, batch at a time.
                cur = db.execute(
                    "DELETE FROM rmm_eagle_event WHERE id IN ("
                    "  SELECT id FROM rmm_eagle_event"
                    "  WHERE captured_at < NOW() - ?::interval"
                    "  ORDER BY captured_at ASC LIMIT ?)",
                    (f"{days} days", _EAGLE_PURGE_BATCH),
                )
                n = cur.rowcount or 0
                db.commit()
            finally:
                db.close()
            total += n
            if n < _EAGLE_PURGE_BATCH:
                break
        if total:
            log.info("eagle event purge: deleted %s rows older than %s days", total, days)
        else:
            log.info("eagle event purge: 0 rows older than %s days (nothing to delete)", days)
    except Exception:
        log.exception("eagle event purge failed")
    return total


def _collect_all(flask_app):
    """Nightly refresh of Layer-3 live facts: run every read-only collector on each system
    that has a host asset. Actions (e.g. entra_sync) are NOT auto-run. Leader-only, best-effort."""
    try:
        import collectors
        from models import ITSystem
        with flask_app.app_context():
            systems = ITSystem.query.filter(ITSystem.asset_id.isnot(None)).all()
            for s in systems:
                for p in collectors.PROBES:
                    if p['kind'] == 'collect' and p['applies'](s):
                        try:
                            collectors.run_probe(s.id, p['key'], user='scheduler')
                        except Exception:
                            log.exception("scheduled collect failed: system %s / %s", s.id, p['key'])
            # After refreshing facts, let the brain flag anomalies + open tickets.
            try:
                import anomalies
                anomalies.scan(create_tickets=True, actor='scheduler')
            except Exception:
                log.exception("scheduled anomaly scan failed")
    except Exception:
        log.exception("scheduled collector refresh failed")


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
                # Built-in subscriber: the Knowledge Agent's "Learn" step. This is the
                # SINGLE place a resolved ticket is distilled into a runbook — every
                # close path (UI status change, close button, bulk close, AND the brain
                # auto-resolve in workflow_engine) publishes ticket.resolved, so wiring
                # Learn here covers them all and can't drift. learn_from_ticket upserts
                # by source_id='ticket:<id>' and returns None when not runbook-worthy, so
                # at-least-once delivery just refreshes — safe to (re)run.
                if etype == "ticket.resolved":
                    tid = payload.get("ticket_id")
                    if tid is not None:
                        try:
                            import knowledge_agent
                            knowledge_agent.learn_from_ticket(int(tid))
                        except Exception:
                            log.exception("ticket.resolved: learn_from_ticket failed (ticket=%s)", tid)
                # Built-in subscriber: the "Apply" reflex — auto-scoop. A NEW ticket is
                # matched against the vetted fix library; a confident match on a tested
                # fix + reachable device PARKS a 1-click apply_fix at /approvals (gated by
                # the ticket_autoscoop_enabled setting). scoop() is fail-safe & idempotent.
                if etype == "ticket.created":
                    tid = payload.get("ticket_id")
                    if tid is not None:
                        try:
                            import ticket_autoscoop
                            ticket_autoscoop.scoop(int(tid))
                        except Exception:
                            log.exception("ticket.created: auto-scoop failed (ticket=%s)", tid)
                # Built-in subscriber: the email-security half of "Learn". A human-approved
                # quarantine release (the release_quarantine action) publishes email.released;
                # distill it into an email-triage runbook. Upserts by source_id='qmsg:<id>',
                # returns None when not generalizable — safe under at-least-once delivery.
                if etype == "email.released":
                    mid = payload.get("message_id")
                    if mid:
                        try:
                            import knowledge_agent
                            knowledge_agent.learn_from_email_decision(mid, "released", payload.get("actor"))
                        except Exception:
                            log.exception("email.released: learn_from_email_decision failed (msg=%s)", mid)
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
                        if ticks % _COLLECT_EVERY == 0:
                            _collect_all(flask_app)
                        if ticks % _EAGLE_PURGE_EVERY == 0:
                            _purge_eagle_events()
            except Exception:
                log.exception("event dispatcher loop error")

    t = threading.Thread(target=_loop, daemon=True, name="event-dispatcher")
    t.start()
    log.info("Event dispatcher started")
    return t
