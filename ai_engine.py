"""
Cirque RMM — AI Engine
Wraps OpenAI API for ticket assistant and security monitoring.
API key stored in setting table (key='openai_api_key').
"""
import json, logging, re, urllib.request, urllib.error
from datetime import datetime

from pg_db import pg_connect

log = logging.getLogger("ai_engine")


def _db():
    return pg_connect()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # local time (TZ=America/Denver), see now_mst


def _get_api_key() -> str | None:
    # Delegates to the central provider config so it works for OpenAI AND on-site Ollama.
    # Returns a usable bearer when AI is configured (a dummy for keyless local endpoints),
    # else None so the "is AI available?" gates behave correctly.
    import ai_config
    return ai_config.api_key() if ai_config.ready() else None


def _get_setting(key: str, default: str = "") -> str:
    db = _db()
    row = db.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    db.close()
    return row["value"] if row else default


def _loads_lenient(raw):
    """Parse JSON from a model response that may be wrapped in ```json fences or have prose
    around it. Returns the parsed object, or None if nothing parseable is found."""
    if not raw:
        return None
    s = raw.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)  # fenced block
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    m = re.search(r"(\{.*\}|\[.*\])", s, re.S)  # first {...} or [...]
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


def _openai_chat(messages: list, model: str = None, max_tokens: int = 800) -> str:
    """Call the configured chat endpoint (OpenAI or on-site Ollama). Raises on failure."""
    import ai_config
    if not ai_config.ready():
        raise ValueError("AI not configured — set an OpenAI key or point ai_base_url at Ollama (Settings → AI)")
    api_key = ai_config.api_key()
    model = model or ai_config.chat_model()
    payload = json.dumps({
        "model":      model,
        "messages":   messages,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(
        ai_config.base_url() + "/chat/completions",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    return resp["choices"][0]["message"]["content"].strip()


def openai_chat_tools(messages: list, tools: list = None, model: str = None,
                      max_tokens: int = 900, temperature: float = 0.2) -> dict:
    """Call the configured chat endpoint with OpenAI tool/function-calling support.

    Returns the raw assistant `message` dict (so the caller can inspect
    `tool_calls` and `content`). Used by the agentic triage loop (triage_agent.py).
    gpt-4o supports parallel tool calls; we let the model decide which read-only
    tools to invoke and iterate. Raises on transport failure (caller fail-safes).

    NB: passes `tools` straight through to /chat/completions. Ollama models vary
    in tool-call support — triage_agent gates on ai_config and degrades when the
    model returns no usable tool_calls."""
    import ai_config
    if not ai_config.ready():
        raise ValueError("AI not configured")
    api_key = ai_config.api_key()
    model = model or ai_config.chat_model()
    body = {
        "model":       model,
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        ai_config.base_url() + "/chat/completions",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    choice = resp.get("choices", [{}])[0]
    msg = choice.get("message", {}) or {}
    # Attach usage so the loop can enforce a token budget.
    msg["_usage"] = resp.get("usage", {}) or {}
    return msg


# ──────────────────────────────────────────────────────────────────────────────
# Ticket assistant
# ──────────────────────────────────────────────────────────────────────────────
def _resolved_history(category: str, exclude_ticket_id: int = 0, limit: int = 5) -> list:
    """
    Return up to `limit` recently-closed tickets in `category`, each with the
    most useful human resolution note attached. Resolution content lives in
    ticket_note rows (there is no resolution column). We prefer reply-to-user
    notes (what the user was actually told), then any internal/plain note,
    and skip auto-generated SLA/ALERT system lines.

    Returns: [{"id", "subject", "resolution"}]  (resolution may be None).
    """
    if not category:
        return []
    db = _db()
    try:
        rows = db.execute(
            """
            SELECT t.id AS id, t.subject AS subject,
                   (
                     SELECT n.content FROM ticket_note n
                     WHERE n.ticket_id = t.id
                       AND n.content NOT LIKE '[SLA]%'
                       AND n.content NOT LIKE '[ALERT]%'
                       AND trim(n.content) <> ''
                     ORDER BY (CASE WHEN n.is_reply THEN 0 ELSE 1 END), n.created_at DESC
                     LIMIT 1
                   ) AS resolution
            FROM support_ticket t
            WHERE t.status IN ('Closed', 'Merged')
              AND t.category = ?
              AND t.id <> ?
            ORDER BY t.closed_at DESC NULLS LAST, t.id DESC
            LIMIT ?
            """,
            (category, exclude_ticket_id, limit),
        ).fetchall()
    finally:
        db.close()

    out = []
    for r in rows:
        d = dict(r)
        res = (d.get("resolution") or "").strip()
        if res and len(res) > 600:
            res = res[:600] + "…"
        out.append({"id": d["id"], "subject": d["subject"], "resolution": res or None})
    return out


def suggest_ticket_resolution(ticket_id: int) -> dict:
    """
    Generate an AI resolution suggestion for a ticket.
    Returns the saved suggestion dict or raises on error.
    """
    db = _db()
    ticket = db.execute("SELECT * FROM support_ticket WHERE id=?", (ticket_id,)).fetchone()
    db.close()
    if not ticket:
        raise ValueError(f"Ticket {ticket_id} not found")

    # Check if we already have a recent pending suggestion
    db2 = _db()
    existing = db2.execute(
        "SELECT * FROM ai_ticket_suggestions WHERE ticket_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
        (ticket_id,)
    ).fetchone()
    db2.close()
    if existing:
        out = dict(existing)
        out["suggestion_id"] = out["id"]
        try:
            out["informed_by"] = json.loads(out["suggestion"]).get("informed_by", [])
        except Exception:
            out["informed_by"] = []
        return out

    # ── Resolved-history context ───────────────────────────────────────────
    # Pull recently-closed tickets in the SAME category and the most useful
    # human resolution note from each, so the model can ground its suggestion
    # in how we actually resolved similar tickets before.
    history = _resolved_history(ticket["category"], exclude_ticket_id=ticket_id, limit=5)

    # ── Knowledge grounding (learned runbooks + ISMS policy + system docs) ──
    # Semantic retrieval over the Knowledge Agent corpus. A runbook learned from a
    # past resolved ticket will surface here — this is the Learn -> Apply cycle.
    knowledge_hits = []
    try:
        import knowledge_agent
        if knowledge_agent.count():
            knowledge_hits = knowledge_agent.search(
                f"{ticket['subject']} {ticket['description'] or ''}".strip(), k=4)
    except Exception:
        log.exception("knowledge grounding failed (non-fatal)")

    # ── Live entity context (Phase 0 context layer) ────────────────────────
    # Ground the diagnosis in WHO reported it and WHICH device: live telemetry,
    # Intune compliance, AD region, account status, and derived risk flags.
    # Best-effort — context is enrichment, never a hard dependency.
    live_context = ""
    try:
        import context_service
        live_context = context_service.context_block_for_ticket(
            asset_id=ticket["asset_id"], reporter_email=ticket["reporter_email"])
    except Exception:
        log.exception("live context enrichment failed (non-fatal)")

    system_prompt = (
        "You are an expert IT support engineer for an internal IT/RMM helpdesk. "
        "Diagnose the ticket and propose a resolution. Ground your answer in the provided "
        "knowledge base (runbooks we wrote from past resolutions, plus policy & system docs) "
        "and prior resolved tickets in the same category — prefer a matching runbook, it's how "
        "we solved this before. When LIVE CONTEXT is provided, use it: the reporter's account "
        "state and the device's live telemetry, compliance, and risk flags often point straight "
        "at the cause (e.g. disk full, non-compliant, offboarded). Respond with ONLY a JSON object "
        "(no markdown, no prose):\n"
        '{"diagnosis": "...", "resolution_steps": ["step1","step2",...], '
        '"can_auto_resolve": true/false, "category": "hardware|software|network|email|account|security|other", '
        '"confidence": 0.0-1.0, "estimated_minutes": N}\n'
        "Set can_auto_resolve=true only for routine issues with a well-established fix; "
        "lower the confidence when the history is thin or the issue is ambiguous."
    )

    history_block = "No prior resolved tickets in this category were found."
    if history:
        lines = []
        for h in history:
            res = h["resolution"] or "(no resolution note recorded)"
            lines.append(f"- Ticket #{h['id']} [{h['subject']}]\n  Resolution: {res}")
        history_block = (
            "How we resolved similar past tickets in this category "
            f"(category: {ticket['category'] or 'N/A'}):\n" + "\n".join(lines)
        )

    knowledge_block = ""
    if knowledge_hits:
        klines = [f"- [{h['source_type']}] {h['title']}\n  {h['content'][:500]}"
                  for h in knowledge_hits]
        knowledge_block = ("\n\nRelevant knowledge base entries (prefer a matching runbook):\n"
                           + "\n".join(klines))

    context_block = ""
    if live_context:
        context_block = "\n\nLIVE CONTEXT (reporter + device, current):\n" + live_context

    user_prompt = (
        f"NEW TICKET #{ticket_id}\n"
        f"Subject: {ticket['subject']}\n"
        f"Description: {ticket['description'] or 'N/A'}\n"
        f"Priority: {ticket['priority']}\n"
        f"Category: {ticket['category'] or 'N/A'}\n"
        f"{context_block}\n\n"
        f"{history_block}"
        f"{knowledge_block}"
    )

    model = _get_setting("openai_model", "gpt-4o")
    raw = _openai_chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user",   "content": user_prompt}],
        model=model,
        max_tokens=600
    )

    # Parse JSON (model should return JSON, but may wrap it in ```json fences)
    parsed = _loads_lenient(raw)
    if parsed is None:
        parsed = {"diagnosis": raw, "resolution_steps": [], "can_auto_resolve": False,
                  "category": "other", "confidence": 0.5, "estimated_minutes": None}

    # Record which past tickets + knowledge entries informed the suggestion (provenance).
    informed_by = [{"id": h["id"], "subject": h["subject"]} for h in history]
    parsed["informed_by"] = informed_by
    parsed["knowledge_used"] = [
        {"type": h["source_type"], "title": h["title"], "score": h["score"]}
        for h in knowledge_hits
    ]

    suggestion_text = json.dumps(parsed)
    auto_mode = _get_setting("ai_ticket_auto_mode") == "1"  # boolean (auto_mode is a bool column)

    db3 = _db()
    cur = db3.execute(
        """INSERT INTO ai_ticket_suggestions
           (ticket_id, model, suggestion, confidence, category, auto_mode, status, created_at)
           VALUES (?,?,?,?,?,?,'pending',?)""",
        (ticket_id, model, suggestion_text,
         parsed.get("confidence", 0.5),
         parsed.get("category", "other"),
         auto_mode, _now())
    )
    sug_id = cur.lastrowid
    db3.commit()
    row = db3.execute("SELECT * FROM ai_ticket_suggestions WHERE id=?", (sug_id,)).fetchone()
    db3.close()

    # Auto-apply if mode is on and confidence is high. This adds an advisory
    # note only — it never closes the ticket. Off by default (ai_ticket_auto_mode).
    if auto_mode and parsed.get("confidence", 0) >= 0.85 and parsed.get("can_auto_resolve"):
        try:
            apply_ticket_suggestion(sug_id, auto=True)
        except Exception:
            log.exception("Auto-apply failed for suggestion %s", sug_id)

    out = dict(row)
    out["suggestion_id"] = sug_id
    out["informed_by"] = informed_by
    return out


def apply_ticket_suggestion(suggestion_id: int, auto: bool = False) -> bool:
    """Mark suggestion as applied and add a note to the ticket."""
    db = _db()
    sug = db.execute("SELECT * FROM ai_ticket_suggestions WHERE id=?", (suggestion_id,)).fetchone()
    if not sug:
        db.close()
        raise ValueError("Suggestion not found")
    parsed = json.loads(sug["suggestion"])
    ticket_id = sug["ticket_id"]

    note_text = f"**AI Resolution Suggestion** {'(auto-applied)' if auto else '(tech-approved)'}\n\n"
    note_text += f"**Diagnosis:** {parsed.get('diagnosis','')}\n\n"
    if parsed.get("resolution_steps"):
        note_text += "**Steps:**\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(parsed["resolution_steps"]))

    db.execute(
        "INSERT INTO ticket_note (ticket_id, content, created_by, created_at) VALUES (?,?,?,?)",
        (ticket_id, note_text, "AI Assistant", _now())
    )
    reviewer = "system" if auto else "technician"
    db.execute(
        "UPDATE ai_ticket_suggestions SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?",
        (reviewer, _now(), suggestion_id)
    )
    db.commit(); db.close()
    return True


def dismiss_ticket_suggestion(suggestion_id: int) -> bool:
    db = _db()
    db.execute("UPDATE ai_ticket_suggestions SET status='rejected', reviewed_at=? WHERE id=?",
               (_now(), suggestion_id))
    db.commit(); db.close()
    return True


def triage_teams_message(text: str, context_text: str = "") -> dict:
    """Front-door triage for a Teams message: ANSWER directly (self-service) or open a
    TICKET. Runs on a cheap model (openai_triage_model, default gpt-4.1-mini) since it's
    high-volume + lightweight. Returns {action, reply, subject, priority, category}.
    Fail-safe: anything unparseable or uncertain -> ticket, so a request is never dropped."""
    model = _get_setting("openai_triage_model", "gpt-4.1-mini")
    system = (
        "You are the IT helpdesk front door in Microsoft Teams. Choose ONE action:\n"
        "ANSWER — only for general how-to / informational questions you can FULLY resolve in "
        "chat (e.g. 'how do I set an out-of-office?', 'where's the VPN guide?').\n"
        "TICKET — anything needing access/account/password changes, hardware, software installs, "
        "on-site work, anything device-specific, or anything you can't fully resolve yourself.\n"
        "If LIVE CONTEXT shows a relevant problem (e.g. a near-full disk), reference it in your reply.\n"
        "Keep the reply friendly and concise. Respond with ONLY a JSON object:\n"
        '{"action":"answer"|"ticket","reply":"<message to the user>",'
        '"subject":"<short ticket title if ticket>","priority":"Low|Normal|High|Urgent",'
        '"category":"hardware|software|network|email|account|security|other"}'
    )
    user = f"USER MESSAGE:\n{text}"
    if context_text:
        user += f"\n\nLIVE CONTEXT:\n{context_text}"
    try:
        raw = _openai_chat([{"role": "system", "content": system},
                            {"role": "user", "content": user}], model=model, max_tokens=400)
        data = _loads_lenient(raw) or {}
    except Exception:
        log.exception("triage_teams_message failed")
        data = {}
    if data.get("action") not in ("answer", "ticket"):
        data["action"] = "ticket"           # fail-safe: never drop a request
    if not data.get("reply"):
        data["reply"] = "Thanks — I've opened a ticket and a tech will follow up."
    return data


def _keyword_score(text: str, keywords: str) -> tuple:
    """Score a fix's comma-separated `fix_keywords` against the ticket text.
    Returns (n_matches, [matched phrases]). Substring match, so multi-word
    phrases like 'pointer not working' match too."""
    if not keywords:
        return 0, []
    hits = []
    for kw in keywords.split(","):
        kw = kw.strip().lower()
        if kw and kw in text:
            hits.append(kw)
    return len(hits), hits


def match_ticket_to_fix(ticket_id: int) -> dict:
    """Match a ticket to the single best automated fix in the library
    (rmm_script_library where is_fix=true), or none.

    Two-stage and cheap-first: a deterministic keyword prefilter over
    `fix_keywords` builds a candidate set; only if there are candidates do we
    spend an LLM call to rank them. Falls back to the keyword score when no AI
    is configured. NEVER raises — returns fix_id=None on any failure so the
    auto-scoop subscriber degrades safely.

    Returns {"fix_id": int|None, "fix_name": str, "confidence": float,
             "reason": str, "is_tested": bool, "source": "rules"|"ai"|"none",
             "candidates": [{"fix_id","name","score"}]}.
    """
    none = {"fix_id": None, "fix_name": "", "confidence": 0.0, "reason": "",
            "is_tested": False, "source": "none", "candidates": []}
    try:
        db = _db()
        trow = db.execute(
            "SELECT subject, description FROM support_ticket WHERE id=?", (ticket_id,)
        ).fetchone()
        fixes = db.execute(
            "SELECT id, name, fix_keywords, is_tested FROM rmm_script_library "
            "WHERE is_fix=true AND is_active=true"
        ).fetchall()
        db.close()
    except Exception:
        log.exception("match_ticket_to_fix: db read failed (ticket=%s)", ticket_id)
        return none
    if not trow or not fixes:
        return none

    text = f"{trow['subject']} {trow['description'] or ''}".lower()

    # Stage 1 — keyword prefilter -> candidates (best score first)
    scored = []
    for f in fixes:
        n, hits = _keyword_score(text, f["fix_keywords"] or "")
        if n > 0:
            scored.append({"fix_id": f["id"], "name": f["name"],
                           "is_tested": bool(f["is_tested"]), "score": n, "hits": hits})
    scored.sort(key=lambda c: c["score"], reverse=True)
    if not scored:
        return none
    candidates = [{"fix_id": c["fix_id"], "name": c["name"], "score": c["score"]} for c in scored]

    # Stage 2 — LLM rank over the candidate set (only when AI is configured)
    if _get_api_key():
        try:
            clines = "\n".join(
                f"- fix_id={c['fix_id']}: {c['name']} (keywords: {f['fix_keywords']})"
                for c, f in [(c, next(x for x in fixes if x["id"] == c["fix_id"])) for c in scored]
            )
            raw = _openai_chat([
                {"role": "system", "content":
                 "You match an IT support ticket to the single best automated fix from a list, "
                 "or to none. A fix should be chosen ONLY if it clearly resolves the ticket's "
                 "root problem. Respond with ONLY JSON (no prose): "
                 '{"fix_id": <id or null>, "confidence": 0.0-1.0, "reason": "one sentence"}. '
                 "confidence is how sure you are this exact fix resolves THIS ticket."},
                {"role": "user", "content":
                 f"TICKET #{ticket_id}\nSubject: {trow['subject']}\n"
                 f"Description: {trow['description'] or '(none)'}\n\n"
                 f"Candidate fixes:\n{clines}"}
            ], max_tokens=120)
            parsed = _loads_lenient(raw) or {}
            fid = parsed.get("fix_id")
            fid = int(fid) if fid not in (None, "", "null") else None
            valid_ids = {c["fix_id"] for c in scored}
            if fid in valid_ids:
                conf = float(parsed.get("confidence", 0.5) or 0.5)
                conf = max(0.0, min(1.0, conf))
                chosen = next(c for c in scored if c["fix_id"] == fid)
                return {"fix_id": fid, "fix_name": chosen["name"], "confidence": conf,
                        "reason": str(parsed.get("reason", ""))[:300],
                        "is_tested": chosen["is_tested"], "source": "ai",
                        "candidates": candidates}
            # model declined (fix_id null / not a candidate) -> no confident match
            return {**none, "candidates": candidates,
                    "reason": str(parsed.get("reason", ""))[:300]}
        except Exception:
            log.exception("match_ticket_to_fix: LLM rank failed (ticket=%s), using keyword fallback", ticket_id)

    # Fallback — keyword-only confidence (caps below high-confidence auto-park
    # unless the keyword overlap is strong: 1 hit=0.55, 2=0.70, 3+=0.80).
    top = scored[0]
    conf = {1: 0.55, 2: 0.70}.get(top["score"], 0.80)
    return {"fix_id": top["fix_id"], "fix_name": top["name"], "confidence": conf,
            "reason": f"Keyword match: {', '.join(top['hits'][:4])}",
            "is_tested": top["is_tested"], "source": "rules", "candidates": candidates}


# ──────────────────────────────────────────────────────────────────────────────
# Security monitor
# ──────────────────────────────────────────────────────────────────────────────
def generate_security_summary() -> dict:
    """
    Analyse current vulnerability + patch posture and return an AI summary.
    """
    db = _db()

    # Collect raw data
    vuln_rows = db.execute(
        """SELECT severity, status, COUNT(*) as cnt
           FROM device_vulnerability GROUP BY severity, status"""
    ).fetchall()
    patch_rows = db.execute(
        """SELECT COUNT(*) as total,
           SUM(CASE WHEN status='installed' THEN 1 ELSE 0 END) as installed
           FROM rmm_patch"""
    ).fetchone()
    offline_agents = db.execute(
        "SELECT COUNT(*) as cnt FROM rmm_agent WHERE online=0"
    ).fetchone()
    open_tickets = db.execute(
        "SELECT COUNT(*) as cnt FROM support_ticket WHERE status='open'"
    ).fetchone()
    recent_alerts = db.execute(
        "SELECT rule_name, COUNT(*) as cnt FROM alert_log GROUP BY rule_name ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    db.close()

    # Summarise numbers
    vuln_by_sev = {}
    total_open  = 0
    for row in vuln_rows:
        sev = (row["severity"] or "unknown").lower()
        if row["status"] == "open":
            vuln_by_sev[sev] = vuln_by_sev.get(sev, 0) + row["cnt"]
            total_open += row["cnt"]

    total_patches  = patch_rows["total"]    if patch_rows else 0
    installed_p    = patch_rows["installed"] if patch_rows else 0
    compliance_pct = int(installed_p / total_patches * 100) if total_patches else 100
    critical_count = vuln_by_sev.get("critical", 0)

    raw_data = {
        "vuln_by_severity": vuln_by_sev,
        "total_open_vulns": total_open,
        "critical_vulns": critical_count,
        "patch_compliance_pct": compliance_pct,
        "offline_agents": offline_agents["cnt"] if offline_agents else 0,
        "open_tickets": open_tickets["cnt"] if open_tickets else 0,
        "top_alerts": [dict(r) for r in recent_alerts],
    }

    prompt = (
        "You are a cybersecurity analyst reviewing an IT environment. "
        "Analyse this data and return a JSON object:\n"
        '{"summary": "2-3 sentence executive summary", '
        '"risk_level": "low|medium|high|critical", '
        '"action_items": ["item1","item2",...], '
        '"positive_notes": ["thing going well",...], '
        '"priority_cves_note": "any commentary on critical CVEs"}\n\n'
        f"Data: {json.dumps(raw_data)}"
    )

    model = _get_setting("openai_model", "gpt-4o")
    raw = _openai_chat(
        [{"role": "user", "content": prompt}],
        model=model,
        max_tokens=500
    )
    parsed = _loads_lenient(raw)
    if parsed is None:
        parsed = {"summary": raw, "risk_level": "unknown", "action_items": [], "positive_notes": []}

    db2 = _db()
    db2.execute(
        """INSERT INTO ai_security_summaries
           (summary, vuln_count, critical_count, patch_compliance_pct, action_items, raw_data)
           VALUES (?,?,?,?,?,?)""",
        (json.dumps(parsed), total_open, critical_count, compliance_pct,
         json.dumps(parsed.get("action_items", [])), json.dumps(raw_data))
    )
    db2.commit(); db2.close()
    return {**parsed, **raw_data}


def get_latest_security_summary() -> dict | None:
    db = _db()
    row = db.execute(
        "SELECT * FROM ai_security_summaries ORDER BY id DESC LIMIT 1"
    ).fetchone()
    db.close()
    if not row:
        return None
    result = dict(row)
    result["parsed"] = json.loads(result["summary"])
    result["action_items"] = json.loads(result["action_items"] or "[]")
    result["raw_data"] = json.loads(result["raw_data"] or "{}")
    return result


# ── Workflow generation ────────────────────────────────────────────────────────

_WF_SYSTEM = """You are an IT automation workflow designer for a corporate IT helpdesk and RMM system.
Given a plain-English description, output a JSON object with "nodes" and "edges" arrays
that represent an automation workflow.

Available node types and actions:

TRIGGERS (always use as first node):
- type="trigger" — config: {trigger_type: manual|schedule|ticket_created|ticket_updated|vulnerability_detected|patch_failed|user_offboarded|alert_triggered}

TICKET MANAGEMENT:
- type="action", action="create_ticket"    config: {title, description, priority (Low|Medium|High|Critical)}
- type="action", action="update_ticket"    config: {status, note, assigned_to}
- type="action", action="close_ticket"     config: {resolution}
- type="action", action="assign_ticket"    config: {assigned_to}

NOTIFICATIONS:
- type="action", action="send_notification" config: {message, user}
- type="action", action="send_email"        config: {to, subject, body}
- type="action", action="send_teams"        config: {webhook_url, title, message}
- type="action", action="send_slack"        config: {webhook_url, channel, message}

ACTIVE DIRECTORY / IDENTITY:
- type="action", action="create_user"       config: {username, first_name, last_name, password, ou}
- type="action", action="disable_ad_user"   config: {username}
- type="action", action="enable_ad_user"    config: {username}
- type="action", action="reset_password"    config: {username, new_password}
- type="action", action="unlock_account"    config: {username}
- type="action", action="add_to_group"      config: {username, group_name}
- type="action", action="remove_from_group" config: {username, group_name}
- type="action", action="azure_sync"        config: {}

DEVICE MANAGEMENT (requires RMM agent online):
- type="action", action="deploy_software"    config: {asset_id, method (chocolatey|winget|msi|exe), package, args}
- type="action", action="uninstall_software" config: {asset_id, method, package}
- type="action", action="run_script"         config: {asset_id, language (powershell|bash), script}
- type="action", action="deploy_patch"       config: {cve_id, asset_id}
- type="action", action="reboot_device"      config: {asset_id, delay_seconds}
- type="action", action="shutdown_device"    config: {asset_id}
- type="action", action="lock_device"        config: {asset_id, mode (lock|bitlocker)}
- type="action", action="apply_gpo"          config: {asset_id, force (true|false)}

INTEGRATION / FLOW:
- type="action", action="webhook"            config: {url, method (POST|GET), body}
- type="action", action="http_request"       config: {url, method (GET|POST|PUT|PATCH|DELETE), headers, body}
- type="action", action="ai_suggest"         config: {}
- type="action", action="wait"               config: {seconds}
- type="condition"                           config: {field, operator (==|!=|>|<|contains|not_contains), value}
    condition nodes have output ports "true" and "false"

Layout rules:
- First node (trigger) at x=80, y=200
- Space nodes ~250px apart horizontally, ~150px vertically for branches
- Each node needs a unique id like "n1","n2",...
- Each edge needs id "e1","e2",... fromNode, fromPort (out|true|false), toNode
- Use {{username}}, {{asset_id}}, {{ticket_id}}, {{cve_id}} for dynamic context values

Return ONLY valid JSON (no markdown fences). Structure:
{
  "name": "Descriptive workflow name",
  "trigger_type": "alert_triggered",
  "nodes": [
    {"id":"n1","type":"trigger","action":"","label":"Trigger","x":80,"y":200,"config":{"trigger_type":"alert_triggered"}},
    {"id":"n2","type":"action","action":"create_ticket","label":"Create Ticket","x":330,"y":200,"config":{"title":"Alert: {{cve_id}}","priority":"High"}}
  ],
  "edges": [
    {"id":"e1","fromNode":"n1","fromPort":"out","toNode":"n2","label":""}
  ]
}"""


def generate_workflow(prompt: str) -> dict:
    """Ask the AI to generate a workflow from a plain-English prompt."""
    try:
        raw = _openai_chat([
            {"role": "system",  "content": _WF_SYSTEM},
            {"role": "user",    "content": prompt},
        ], max_tokens=2000)
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        wf = json.loads(raw.strip())
        return {"ok": True, "workflow": wf}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"AI generation failed: {e}"}


# ── Cross-module AI Ask ────────────────────────────────────────────────────────

def ask_ai(question: str) -> dict:
    """
    Answer a natural language question by pulling live context from all modules:
    assets, employees, tickets, licenses, monitoring alerts, and backup health.
    Returns {"answer": str, "sources": [str]} or raises on error.
    """
    db = _db()

    # --- Assets ---
    asset_total = db.execute("SELECT COUNT(*) FROM asset").fetchone()[0]
    asset_in_use = db.execute("SELECT COUNT(*) FROM asset WHERE status='In Use'").fetchone()[0]
    asset_offline = db.execute(
        "SELECT COUNT(*) FROM asset WHERE intune_last_sync IS NOT NULL AND "
        "intune_last_sync < datetime('now', '-7 days')"
    ).fetchone()[0]
    asset_low_storage = db.execute(
        "SELECT COUNT(*) FROM asset WHERE hardware_storage_total_gb > 0 AND "
        "CAST(hardware_storage_free_gb AS REAL) / CAST(hardware_storage_total_gb AS REAL) < 0.20"
    ).fetchone()[0]
    low_storage_hosts = [r[0] for r in db.execute(
        "SELECT name FROM asset WHERE hardware_storage_total_gb > 0 AND "
        "CAST(hardware_storage_free_gb AS REAL) / CAST(hardware_storage_total_gb AS REAL) < 0.20 "
        "AND name IS NOT NULL LIMIT 10"
    ).fetchall()]

    # --- Employees ---
    emp_total = db.execute("SELECT COUNT(*) FROM employee").fetchone()[0]
    departments = [r[0] for r in db.execute(
        "SELECT DISTINCT department FROM employee WHERE department IS NOT NULL ORDER BY department"
    ).fetchall()]

    # --- Tickets ---
    tickets_open = db.execute(
        "SELECT COUNT(*) FROM support_ticket WHERE status='Open'"
    ).fetchone()[0]
    tickets_inprog = db.execute(
        "SELECT COUNT(*) FROM support_ticket WHERE status='In Progress'"
    ).fetchone()[0]
    tickets_unassigned = db.execute(
        "SELECT COUNT(*) FROM support_ticket WHERE status IN ('Open','In Progress') "
        "AND assigned_to_user_id IS NULL"
    ).fetchone()[0]
    urgent_tickets = [dict(r) for r in db.execute(
        "SELECT id, subject, priority, created_at FROM support_ticket "
        "WHERE status IN ('Open','In Progress') AND priority='Urgent' LIMIT 5"
    ).fetchall()]

    # --- Licenses ---
    licenses_active = db.execute("SELECT COUNT(*) FROM license WHERE status='Active'").fetchone()[0]
    licenses_expired = db.execute("SELECT COUNT(*) FROM license WHERE status='Expired'").fetchone()[0]
    licenses_expiring = db.execute(
        "SELECT COUNT(*) FROM license WHERE status='Active' AND expiry_date IS NOT NULL "
        "AND expiry_date <= date('now', '+30 days')"
    ).fetchone()[0]
    annual_cost = db.execute(
        "SELECT COALESCE(SUM(annual_cost), 0) FROM license WHERE annual_cost IS NOT NULL"
    ).fetchone()[0]

    # --- Monitoring alerts ---
    alerts_critical = db.execute(
        "SELECT COUNT(*) FROM monitoring_alert WHERE status='open' AND severity='critical'"
    ).fetchone()[0]
    alerts_warning = db.execute(
        "SELECT COUNT(*) FROM monitoring_alert WHERE status='open' AND severity='warning'"
    ).fetchone()[0]
    top_alerts = [dict(r) for r in db.execute(
        "SELECT message, severity, triggered_at FROM monitoring_alert "
        "WHERE status='open' ORDER BY triggered_at DESC LIMIT 5"
    ).fetchall()]

    # --- Backup health ---
    pools_total = db.execute("SELECT COUNT(*) FROM proxmox_zfs_pool").fetchone()[0]
    pools_degraded = db.execute(
        "SELECT COUNT(*) FROM proxmox_zfs_pool WHERE health != 'ONLINE'"
    ).fetchone()[0]
    vms_stale = db.execute(
        "SELECT COUNT(*) FROM proxmox_backup_job WHERE "
        "last_snapshot_time IS NULL OR last_snapshot_time < NOW() - INTERVAL '25 hours'"
    ).fetchone()[0]

    db.close()

    context = {
        "assets": {
            "total": asset_total, "in_use": asset_in_use,
            "offline_7d": asset_offline, "low_storage_count": asset_low_storage,
            "low_storage_hosts": low_storage_hosts,
        },
        "employees": {"total": emp_total, "departments": departments},
        "tickets": {
            "open": tickets_open, "in_progress": tickets_inprog,
            "unassigned": tickets_unassigned, "urgent": urgent_tickets,
        },
        "licenses": {
            "active": licenses_active, "expired": licenses_expired,
            "expiring_in_30d": licenses_expiring, "annual_cost_usd": annual_cost,
        },
        "monitoring_alerts": {
            "critical_open": alerts_critical, "warning_open": alerts_warning,
            "recent": top_alerts,
        },
        "backups": {
            "zfs_pools_total": pools_total, "zfs_pools_degraded": pools_degraded,
            "vm_backups_stale": vms_stale,
        },
    }

    system_prompt = (
        "You are an expert IT administrator assistant for Cirque Corporation's internal IT tracker. "
        "You have access to real-time data from all IT modules: assets, employees, tickets, licenses, "
        "monitoring alerts, and backup health. Answer questions clearly and concisely. "
        "If specific device names or ticket IDs are relevant, include them. "
        "Format your answer in plain text suitable for display in a web UI — "
        "use numbered lists or bullet points where helpful but avoid markdown headers."
    )
    user_prompt = (
        f"Current IT environment data:\n{json.dumps(context, default=str, indent=2)}\n\n"
        f"Question: {question}"
    )

    answer = _openai_chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        max_tokens=600
    )

    sources = []
    ctx_lower = question.lower()
    if any(w in ctx_lower for w in ["asset", "device", "computer", "laptop", "storage", "offline"]):
        sources.append("Assets")
    if any(w in ctx_lower for w in ["ticket", "issue", "request", "help", "support"]):
        sources.append("Tickets")
    if any(w in ctx_lower for w in ["employee", "staff", "user", "department", "person"]):
        sources.append("Employees")
    if any(w in ctx_lower for w in ["license", "software", "cost", "expir"]):
        sources.append("Licenses")
    if any(w in ctx_lower for w in ["alert", "monitor", "warning", "critical"]):
        sources.append("Monitoring")
    if any(w in ctx_lower for w in ["backup", "zfs", "proxmox", "pool", "vm"]):
        sources.append("Backups")
    if not sources:
        sources = ["Assets", "Tickets", "Licenses", "Monitoring", "Backups"]

    return {"answer": answer, "sources": sources}


def generate_daily_briefing() -> dict:
    """Build a prioritized 'what needs attention today' briefing from live ops data.

    Reuses ask_ai()'s cross-module context gathering with a fixed briefing prompt.
    Returns {"answer": str, "sources": [...]}.
    """
    prompt = (
        "You are giving me, the IT administrator, my morning operations briefing. "
        "From the current environment data, list ONLY the items that genuinely need "
        "attention today, in priority order (most urgent first). Consider: offline or "
        "at-risk devices, low-storage devices, open critical/warning monitoring alerts, "
        "urgent or unassigned tickets, degraded backups, and licenses expiring soon. "
        "For each item give a short one-line recommended action. Use concise bullet "
        "points (max ~10). If everything looks healthy, say so in one line and note the "
        "overall fleet/ticket counts. Do not invent data that isn't present."
    )
    return ask_ai(prompt)


# ──────────────────────────────────────────────────────────────────────────────
# Predictive failure analysis + ticket auto-triage
# ──────────────────────────────────────────────────────────────────────────────
def predict_asset_failures() -> dict:
    """
    Analyse metrics, CVE counts, age, storage and monitoring alerts to surface
    at-risk assets. Returns rule-based risk scores enriched with AI narrative if
    an OpenAI key is available.
    """
    db = _db()

    # Gather signals
    assets = db.execute("""
        SELECT a.id, a.asset_tag, a.name, a.purchase_date, a.status,
               a.hardware_storage_total_gb, a.hardware_storage_free_gb,
               a.hardware_ram_gb, a.warranty_expiry,
               (SELECT COUNT(*) FROM device_vulnerability v
                WHERE v.asset_id = a.id AND v.severity IN ('Critical','High')
                  AND v.status != 'resolved') AS crit_cves,
               (SELECT COUNT(*) FROM monitoring_alert m
                WHERE m.asset_id = a.id AND m.status = 'open') AS open_alerts
        FROM asset a
        WHERE a.status != 'Retired'
    """).fetchall()

    at_risk = []
    today = datetime.utcnow().date()

    for row in assets:
        risk_score = 0
        reasons = []

        # Age signal
        if row["purchase_date"]:
            import datetime as _dt
            pd = _dt.date.fromisoformat(str(row["purchase_date"]))
            age_years = (today - pd).days / 365.25
            if age_years >= 5:
                risk_score += 30
                reasons.append(f"Age {age_years:.1f}yr ≥ 5yr EOL threshold")
            elif age_years >= 4:
                risk_score += 15
                reasons.append(f"Age {age_years:.1f}yr approaching EOL")

        # Warranty signal
        if row["warranty_expiry"]:
            import datetime as _dt
            we = _dt.date.fromisoformat(str(row["warranty_expiry"]))
            days_left = (we - today).days
            if days_left < 0:
                risk_score += 25
                reasons.append("Warranty expired")
            elif days_left <= 30:
                risk_score += 15
                reasons.append(f"Warranty expiring in {days_left}d")

        # Storage signal
        if row["hardware_storage_total_gb"] and row["hardware_storage_total_gb"] > 0:
            free_pct = (row["hardware_storage_free_gb"] or 0) / row["hardware_storage_total_gb"]
            if free_pct < 0.10:
                risk_score += 25
                reasons.append(f"Storage critically low ({free_pct*100:.0f}% free)")
            elif free_pct < 0.20:
                risk_score += 10
                reasons.append(f"Storage low ({free_pct*100:.0f}% free)")

        # CVE signal
        if row["crit_cves"] and row["crit_cves"] > 0:
            cve_score = min(30, row["crit_cves"] * 2)
            risk_score += cve_score
            reasons.append(f"{row['crit_cves']} unresolved Critical/High CVEs")

        # Monitoring alerts
        if row["open_alerts"] and row["open_alerts"] > 0:
            alert_score = min(20, row["open_alerts"] * 5)
            risk_score += alert_score
            reasons.append(f"{row['open_alerts']} open monitoring alert(s)")

        if risk_score > 0:
            at_risk.append({
                "asset_id":  row["id"],
                "asset_tag": row["asset_tag"],
                "name":      row["name"],
                "risk_score": min(100, risk_score),
                "reasons":   reasons,
            })

    db.close()

    # Sort by descending risk
    at_risk.sort(key=lambda x: x["risk_score"], reverse=True)
    top = at_risk[:20]

    # AI narrative (optional, skipped if no API key)
    narrative = None
    try:
        if top and _get_api_key():
            summary_lines = [
                f"  - {a['name']} (score {a['risk_score']}): {'; '.join(a['reasons'])}"
                for a in top[:10]
            ]
            narrative = _openai_chat([
                {"role": "system", "content":
                    "You are a senior IT infrastructure analyst. Provide a concise executive summary "
                    "(3-5 sentences) of hardware risk across the fleet and prioritised action items."},
                {"role": "user", "content":
                    f"Top at-risk assets:\n" + "\n".join(summary_lines)}
            ], max_tokens=300)
    except Exception as _ai_err:
        log.debug(f"AI narrative skipped: {_ai_err}")

    return {
        "at_risk": top,
        "total_flagged": len(at_risk),
        "narrative": narrative,
        "generated_at": _now(),
    }


def auto_triage_ticket(ticket_id: int) -> dict:
    """
    Rule-based ticket triage: suggest priority, category and assignee based on
    keywords. Falls back to AI if a key is available.
    Returns {"priority", "category", "suggested_assignee", "reason"}.
    """
    db = _db()
    row = db.execute(
        "SELECT subject, description, priority, source FROM support_ticket WHERE id=?",
        (ticket_id,)
    ).fetchone()
    db.close()

    if not row:
        return {"error": "Ticket not found"}

    text = f"{row['subject']} {row['description'] or ''}".lower()

    # Keyword-based rules
    priority = row["priority"] or "Normal"
    category = "General"
    reason = "Keyword match"

    if any(w in text for w in ["ransomware", "breach", "hack", "phish", "malware", "virus"]):
        priority, category = "Urgent", "Security Incident"
        reason = "Security threat keywords detected"
    elif any(w in text for w in ["down", "outage", "offline", "unreachable", "not working", "crash"]):
        priority = "High"
        category = "Outage / Downtime"
        reason = "Outage/downtime keywords"
    elif any(w in text for w in ["slow", "performance", "lag", "freeze"]):
        priority = "Normal"
        category = "Performance"
        reason = "Performance keywords"
    elif any(w in text for w in ["password", "locked out", "login", "access denied"]):
        priority = "High"
        category = "Access / Authentication"
        reason = "Access problem keywords"
    elif any(w in text for w in ["printer", "print", "scanner"]):
        priority = "Low"
        category = "Peripheral"
        reason = "Printer/peripheral keywords"

    result = {"priority": priority, "category": category, "reason": reason, "source": "rules"}

    # Enhance with AI if available
    try:
        if _get_api_key():
            ai_resp = _openai_chat([
                {"role": "system", "content":
                    "You are an IT help-desk triage specialist. Given a support ticket, respond with "
                    "JSON only: {\"priority\": \"Low|Normal|High|Urgent\", \"category\": \"string\", "
                    "\"reason\": \"one sentence\"}"},
                {"role": "user", "content":
                    f"Subject: {row['subject']}\nDescription: {row['description'] or '(none)'}"}
            ], max_tokens=120)
            parsed = json.loads(ai_resp)
            result.update(parsed)
            result["source"] = "ai"
    except Exception:
        pass  # Use rule-based result

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Asset health analysis
# ──────────────────────────────────────────────────────────────────────────────
def analyze_asset_health(asset_id: int) -> dict:
    """
    Gather all available data for a single asset and ask OpenAI to produce
    a structured health report with issues and recommended actions.
    """
    db = _db()

    asset = db.execute(
        "SELECT id, name, device_type, status, last_seen, online_state FROM asset WHERE id=?",
        (asset_id,)
    ).fetchone()
    if not asset:
        db.close()
        raise ValueError(f"Asset {asset_id} not found")

    # RMM telemetry (most recent row for this asset)
    tel = db.execute(
        """SELECT hostname, os_name, os_version, cpu_percent, ram_percent,
                  ram_total_gb, ram_available_gb, disk_json, logged_in_user,
                  uptime_seconds, security_json, captured_at
           FROM rmm_telemetry WHERE asset_id=? ORDER BY captured_at DESC LIMIT 1""",
        (asset_id,)
    ).fetchone()

    # Agent row (for agent_id and last_seen_at)
    agent = db.execute(
        "SELECT agent_id, last_seen_at FROM rmm_agent WHERE asset_id=? AND enabled=true LIMIT 1",
        (asset_id,)
    ).fetchone()

    # Open CVEs grouped by severity
    vuln_rows = db.execute(
        """SELECT severity, COUNT(*) as cnt FROM device_vulnerability
           WHERE asset_id=? AND status='Open' GROUP BY severity""",
        (asset_id,)
    ).fetchall()
    vuln_by_sev = {}
    for r in vuln_rows:
        sev = (r['severity'] or 'unknown').lower()
        vuln_by_sev[sev] = vuln_by_sev.get(sev, 0) + r['cnt']

    # Pending patches + reboot flag
    patch_data = db.execute(
        """SELECT COUNT(*) as cnt, BOOL_OR(rpu.reboot_required) as needs_reboot
           FROM rmm_pending_update rpu
           JOIN rmm_agent ra ON ra.agent_id = rpu.agent_id
           WHERE ra.asset_id=?""",
        (asset_id,)
    ).fetchone()

    # Open support tickets
    tickets = db.execute(
        "SELECT COUNT(*) as cnt FROM support_ticket WHERE asset_id=? AND status NOT IN ('resolved','closed')",
        (asset_id,)
    ).fetchone()

    # Active monitoring alerts
    alert_rows = db.execute(
        """SELECT message, severity FROM monitoring_alert
           WHERE asset_id=? AND resolved_at IS NULL ORDER BY triggered_at DESC LIMIT 5""",
        (asset_id,)
    ).fetchall()

    db.close()

    # Parse disk JSON for low-space drives
    disk_issues = []
    if tel and tel['disk_json']:
        try:
            disks = json.loads(tel['disk_json']) if isinstance(tel['disk_json'], str) else tel['disk_json']
            for d in (disks if isinstance(disks, list) else []):
                free_pct = d.get('free_percent') or d.get('free_pct')
                if free_pct is None and d.get('total_gb') and d.get('free_gb'):
                    free_pct = round(d['free_gb'] / d['total_gb'] * 100, 1)
                if free_pct is not None and float(free_pct) < 15:
                    label = d.get('device') or d.get('path') or d.get('drive', 'Disk')
                    disk_issues.append(f"{label}: {float(free_pct):.0f}%% free")
        except Exception:
            pass

    # Parse security JSON
    security_info = {}
    if tel and tel['security_json']:
        try:
            security_info = json.loads(tel['security_json']) if isinstance(tel['security_json'], str) else (tel['security_json'] or {})
        except Exception:
            pass

    raw_data = {
        "asset_name": asset['name'],
        "device_type": asset['device_type'] or 'Unknown',
        "asset_status": asset['status'],
        "online_state": asset['online_state'],
        "os": f"{tel['os_name']} {tel['os_version']}" if tel else None,
        "cpu_percent": tel['cpu_percent'] if tel else None,
        "ram_percent": tel['ram_percent'] if tel else None,
        "logged_in_user": tel['logged_in_user'] if tel else None,
        "disk_low_free": disk_issues,
        "uptime_hours": round(tel['uptime_seconds'] / 3600, 1) if tel and tel.get('uptime_seconds') else None,
        "av_status": (security_info.get('antivirus') or {}).get('state') if security_info else None,
        "firewall_enabled": (security_info.get('firewall') or {}).get('enabled') if security_info else None,
        "pending_reboot": bool(patch_data['needs_reboot']) if patch_data else False,
        "open_vulns": vuln_by_sev,
        "pending_patches": patch_data['cnt'] if patch_data else 0,
        "open_tickets": tickets['cnt'] if tickets else 0,
        "active_alerts": [{"rule": r['message'], "severity": r['severity']} for r in alert_rows],
    }

    prompt = (
        "You are an IT system health analyst. Analyse this Windows/Linux device data and return ONLY valid JSON.\n"
        "Be specific and actionable. Skip checks where data is None/missing.\n\n"
        '{"health_status": "healthy|warning|critical", '
        '"health_score": <integer 0-100>, '
        '"summary": "<1-2 sentence plain-English summary>", '
        '"issues": [{"severity": "critical|warning|info", "title": "<short title>", "detail": "<detail>"}], '
        '"recommended_actions": ["<action 1>", "<action 2>"], '
        '"positive_notes": ["<what is good>"]}\n\n'
        f"Device data: {json.dumps(raw_data)}"
    )

    raw = _openai_chat([{"role": "user", "content": prompt}], max_tokens=800)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except Exception:
                parsed = {}
        else:
            parsed = {}
        if not parsed:
            parsed = {
                "health_status": "unknown", "health_score": 50,
                "summary": raw, "issues": [], "recommended_actions": [], "positive_notes": []
            }

    return {
        **parsed,
        "raw_data": raw_data,
        "agent_id": agent['agent_id'] if agent else None,
        "is_online": (asset['online_state'] or '').lower() == 'online',
        "has_agent": bool(agent),
        "asset_id": asset_id,
        "asset_name": asset['name'],
    }
