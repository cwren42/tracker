"""Agentic diagnosis for the Teams front door (roadmap 'M' moat — closed loop).

When the bot opens a ticket for a device-related issue, this runs ASYNC: it probes
the reporter's actual machine with triage_agent's read-only diagnostics (live volumes,
disk hogs, stopped auto-services, recent event-log errors), has the AI reason over the
real data to a likely cause + next step, then posts that back into the same Teams thread
— with a 1-click Apply-fix when a tested library fix matches. Detect -> diagnose ->
(offer) remediate, right where the user asked. Best-effort; never raises into the caller.
"""
import os
import json
import logging
import threading

import psycopg2

log = logging.getLogger(__name__)

# Live diagnostics for a generic device complaint (read-only). (key, arg) — keys
# must match triage_agent.DIAGNOSTICS; recent_errors takes an event-log name.
_DIAGS = [("volumes", None), ("disk_hogs", None),
          ("services_stopped", None), ("recent_errors", "System")]


def _agent_id_for_asset(asset_id):
    """Resolve the RMM agent_id for an asset (telemetry first, then rmm_agent)."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT agent_id FROM rmm_telemetry WHERE asset_id=%s "
                    "ORDER BY last_seen DESC NULLS LAST LIMIT 1", (asset_id,))
        r = cur.fetchone()
        if r and r[0]:
            return r[0]
        cur.execute("SELECT agent_id FROM rmm_agent WHERE asset_id=%s AND enabled=true LIMIT 1", (asset_id,))
        r = cur.fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def _run_diagnostics(agent_id, asset_id):
    """Run the read-only diagnostics on the device via triage_agent's gateway tools."""
    import triage_agent
    out = {}
    con = triage_agent._db()
    try:
        for k, arg in _DIAGS:
            try:
                res = triage_agent.tool_run_readonly_diagnostic(con, agent_id, asset_id, k, arg)
                out[k] = res
            except Exception:
                log.exception("teams diagnose: diagnostic %s failed", k)
    finally:
        try:
            con.close()
        except Exception:
            pass
    return out


def _summarize_diag(res):
    """Pull the human-useful text out of a diagnostic tool result for the prompt."""
    if not isinstance(res, dict):
        return str(res)[:500]
    if res.get("error"):
        return f"(error: {res['error']})"
    for key in ("stdout", "result", "output", "rows", "data"):
        if res.get(key):
            return str(res[key])[:600]
    return json.dumps({k: v for k, v in res.items()
                       if k not in ("diag_key", "label")}, default=str)[:600]


def _ai_diagnosis(problem_text, diags, ctx_block):
    import ai_engine
    blocks = []
    for k, res in (diags or {}).items():
        blocks.append(f"[{k}]\n{_summarize_diag(res)}")
    diag_text = "\n\n".join(blocks) or "(no diagnostics returned)"
    system = (
        "You are an IT support engineer triaging a live machine. You are given the user's "
        "complaint, the device's known context, and the results of READ-ONLY diagnostics run "
        "on that exact machine just now. Identify the most likely cause and the single best "
        "next action, grounded ONLY in the data shown (don't invent). Be concise and specific "
        "— 2-3 sentences, written to the end user. If the data doesn't reveal a clear cause, "
        "say a tech will investigate."
    )
    user = f"USER COMPLAINT:\n{problem_text}\n\n"
    if ctx_block:
        user += f"DEVICE CONTEXT:\n{ctx_block}\n\n"
    user += f"LIVE DIAGNOSTICS (run just now on the device):\n{diag_text}"
    try:
        return ai_engine._openai_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=350).strip()
    except Exception:
        log.exception("teams diagnose: AI diagnosis failed")
        return ""


def _diag_card(ticket_id, diagnosis, fix):
    body = [
        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "color": "accent",
         "wrap": True, "text": f"🔎 I looked at your machine — ticket #{ticket_id}"},
        {"type": "TextBlock", "wrap": True, "text": diagnosis},
    ]
    actions = []
    if fix:
        body.append({"type": "TextBlock", "wrap": True, "spacing": "Small", "color": "good",
                     "text": f"I can apply a tested fix: **{fix['fix_name']}**."})
        actions.append({"type": "Action.Execute", "title": "🔧 Apply fix", "verb": "apply_fix",
                        "data": {"ticket_id": ticket_id, "fix_id": fix["fix_id"],
                                 "asset_id": fix["asset_id"]}})
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4", "body": body, "actions": actions,
    }


def _work(conv_ref, ticket_id, asset_id, problem_text, ctx_block):
    try:
        import teams_bot, teams_intake
        agent_id = _agent_id_for_asset(asset_id)
        if not agent_id:
            return  # no agent on the box -> nothing to probe; the ticket already carries context
        diags = _run_diagnostics(agent_id, asset_id)
        diagnosis = _ai_diagnosis(problem_text, diags, ctx_block)
        if not diagnosis:
            return
        # record the diagnosis on the ticket too (internal note)
        try:
            conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.autocommit = True
            conn.cursor().execute(
                "INSERT INTO ticket_note (ticket_id, user_id, content, is_internal, is_reply, created_at) "
                "VALUES (%s, NULL, %s, TRUE, FALSE, now())",
                (ticket_id, "[agentic-diagnosis] " + diagnosis)); conn.close()
        except Exception:
            log.exception("teams diagnose: note write failed")
        fix = teams_intake._match_offerable_fix(ticket_id, asset_id)
        teams_bot.send_reply(conv_ref, text=None, card=_diag_card(ticket_id, diagnosis, fix))
    except Exception:
        log.exception("teams diagnose: work failed (ticket=%s)", ticket_id)


def kick_off(conv_ref, ticket_id, asset_id, problem_text, ctx_block):
    """Spawn the async diagnose+followup. Returns immediately so the bot's first
    reply isn't delayed by the live diagnostics."""
    if not asset_id:
        return
    threading.Thread(target=_work, daemon=True,
                     name=f"teams-diagnose-{ticket_id}",
                     args=(conv_ref, ticket_id, asset_id, problem_text, ctx_block)).start()
