"""Email security AI agent.

Shared helpers + capabilities for the Email Security / quarantine subsystem. The
prompt-builder and OpenAI wrapper used to live inline in blueprints/quarantine.py;
they're consolidated here so single-message analysis, bulk/campaign analysis, the
structured verdict, and future capabilities all share one OpenAI path and one
message-summary builder.

Important provenance note for prompts/UI: for Quarantined/Blocked mail we do NOT have
the message body (bodies come from Graph Mail.Read and only for mailbox-resident mail).
Triage is therefore based on envelope + signals: threat type, SPF/DKIM/DMARC, sender
domain/IP, URL/attachment counts, IOCs, and the risk score.

Guardrails: recommendations are advisory only — nothing here releases/blocks/deletes or
writes to the tenant. Secrets (the OpenAI key) are fetched server-side and never returned.
"""
import json

import requests as _http

from models import Setting, QuarantineMessage
from secret_store import decrypt_secret

# Statuses where a copy lives in a mailbox (so a body/preview can be fetched via Graph).
MAILBOX_MESSAGE_STATUSES = {"Delivered", "Junk", "Released"}


def get_openai_config():
    """(api_key, model) from the Setting table. Raises ValueError if no key configured."""
    api_key_row = Setting.query.filter_by(key="openai_api_key").first()
    api_key = decrypt_secret(api_key_row.value) if api_key_row else None
    if not api_key:
        raise ValueError("OpenAI API key not configured — add it in Settings → AI")
    model_row = Setting.query.filter_by(key="openai_model").first()
    model = (model_row.value if model_row and model_row.value else None) or "gpt-4o"
    return api_key, model


def build_message_summary(msg: QuarantineMessage, include_headers: bool = True) -> str:
    """Flatten a quarantine message into prompt text (no body — see module note)."""
    lines = [
        f"Subject: {msg.subject or '(none)'}",
        f"From: {msg.sender_address or '(unknown)'}",
        f"To: {msg.recipient_address or '(unknown)'}",
        f"Direction: {msg.email_direction or 'Unknown'}",
        f"Disposition: {msg.release_status or 'Unknown'}",
        f"Threat type: {msg.threat_type or 'None'}",
        f"Detection policy: {msg.policy_type or 'None'}",
        f"SPF: {msg.spf_result or 'none'}, DKIM: {msg.dkim_result or 'none'}, DMARC: {msg.dmarc_result or 'none'}",
        f"Sender IP: {msg.sender_ip or 'Unknown'}",
        f"URLs: {msg.url_count or 0}, Attachments: {msg.attachment_count or 0}",
        f"Risk score: {msg.risk_score or 'N/A'} ({msg.risk_label or 'N/A'})",
    ]
    if include_headers:
        raw_headers = (msg.raw_headers or "").strip()
        lines.append("Raw headers excerpt:")
        lines.append(raw_headers[:4000] if raw_headers else "Not available")
    return "\n".join(lines)


def run_chat(system_prompt: str, user_prompt: str, max_tokens: int = 700, json_mode: bool = False):
    """Single OpenAI chat call. Returns (content_str, model). json_mode forces a JSON object."""
    api_key, model = get_openai_config()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    resp = _http.post(
        "https://api.openai.com/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip(), model


# ── Capability prompts ──────────────────────────────────────────────────────
SINGLE_SYSTEM_PROMPT = (
    "You are a SOC analyst AI assistant specializing in email security. "
    "Analyze the provided email metadata and give a structured security assessment. "
    "Cover: (1) Threat Indicators, (2) Authentication Assessment, "
    "(3) Likely Classification (phishing / spam / BEC / legitimate / etc), "
    "(4) Recommended Action. Use the raw headers excerpt when present. "
    "Note: the message body is not available for quarantined/blocked mail — assess from "
    "envelope + signals. Be concise — 2-4 sentences per section. Use Markdown with ### headers."
)

BULK_SYSTEM_PROMPT = (
    "You are a SOC analyst AI assistant specializing in email security. "
    "Analyze this selected group of emails and provide a concise campaign-level assessment. "
    "Cover: (1) Overall Pattern, (2) Common Threat Indicators, (3) Notable Outliers, "
    "(4) Recommended Bulk Action. Use Markdown with ### headers."
)

_VERDICTS = {"phishing", "spam", "bec", "malware", "bulk", "legitimate", "uncertain"}
_ACTIONS = {"release", "block", "delete", "investigate", "none"}

VERDICT_SYSTEM_PROMPT = (
    "You are a SOC analyst AI for email security. From the email metadata + signals "
    "(envelope, SPF/DKIM/DMARC, sender domain/IP, URL/attachment counts, threat type, risk "
    "score, raw-header excerpt), classify the message. You do NOT have the body for "
    "quarantined/blocked mail — do not assume it. Respond with ONLY a JSON object: "
    '{"verdict": one of phishing|spam|bec|malware|bulk|legitimate|uncertain, '
    '"confidence": number 0..1, "indicators": [short strings], '
    '"auth_assessment": short string about SPF/DKIM/DMARC, '
    '"recommended_action": one of release|block|delete|investigate|none, '
    '"rationale": one or two sentences}. '
    "recommended_action is ADVISORY only — a human will decide."
)


def analyze_single(msg: QuarantineMessage):
    """Free-text SOC analysis of one message → (markdown, model)."""
    return run_chat(SINGLE_SYSTEM_PROMPT, "Analyze this email:\n\n" + build_message_summary(msg), 700)


def analyze_bulk(messages):
    """Campaign-level analysis of a set of messages → (markdown, model)."""
    user = "Analyze these selected emails as a set:\n\n" + "\n\n---\n\n".join(
        f"Email {i}:\n{build_message_summary(m, include_headers=False)}"
        for i, m in enumerate(messages, start=1)
    )
    return run_chat(BULK_SYSTEM_PROMPT, user, 900)


LANDSCAPE_SYSTEM_PROMPT = (
    "You are a SOC analyst summarizing an organization's inbound email threat landscape "
    "from aggregate quarantine stats. Write a brief executive summary with these Markdown "
    "sections: ### Posture (one or two sentences on overall risk), ### Notable (top threats, "
    "lookalike/abusive sender domains, DMARC-failure or phishing spikes), ### Recommended "
    "actions (2-4 short bullets). Be specific to the numbers given; do not invent data. Concise."
)


def gather_landscape_stats(days: int = 30) -> dict:
    """Compact aggregates over quarantine_message for the period (for the AI landscape)."""
    from datetime import datetime, timedelta
    from sqlalchemy import func, case
    from extensions import db

    cutoff = datetime.utcnow() - timedelta(days=days)
    in_window = QuarantineMessage.received_time >= cutoff

    total = db.session.query(func.count(QuarantineMessage.id)).filter(in_window).scalar() or 0
    by_threat = dict(
        db.session.query(QuarantineMessage.threat_type, func.count(QuarantineMessage.id))
        .filter(in_window).group_by(QuarantineMessage.threat_type).all()
    )
    by_status = dict(
        db.session.query(QuarantineMessage.release_status, func.count(QuarantineMessage.id))
        .filter(in_window).group_by(QuarantineMessage.release_status).all()
    )
    dmarc_fail = db.session.query(func.count(QuarantineMessage.id)).filter(
        in_window, QuarantineMessage.dmarc_result == "fail"
    ).scalar() or 0
    top_domains = (
        db.session.query(
            QuarantineMessage.sender_domain,
            func.count(QuarantineMessage.id).label("c"),
            func.count(case((QuarantineMessage.dmarc_result == "fail", 1))).label("dmarc_fail"),
        )
        .filter(in_window, QuarantineMessage.sender_domain.isnot(None))
        .group_by(QuarantineMessage.sender_domain)
        .order_by(func.count(QuarantineMessage.id).desc())
        .limit(10).all()
    )
    return {
        "days": days,
        "total_messages": int(total),
        "by_threat_type": {(k or "Unknown"): int(v) for k, v in by_threat.items()},
        "by_disposition": {(k or "Unknown"): int(v) for k, v in by_status.items()},
        "dmarc_failures": int(dmarc_fail),
        "top_sender_domains": [
            {"domain": d, "messages": int(c), "dmarc_fails": int(df)} for d, c, df in top_domains
        ],
    }


def summarize_landscape(days: int = 30):
    """Threat-landscape summary for the period → (markdown, model, stats_dict)."""
    stats = gather_landscape_stats(days)
    if not stats["total_messages"]:
        return ("No email security activity recorded in this period.", None, stats)
    user_prompt = (
        f"Email security aggregates for the last {days} days (JSON):\n"
        + json.dumps(stats, default=str)
    )
    md, model = run_chat(LANDSCAPE_SYSTEM_PROMPT, user_prompt, max_tokens=600)
    return md, model, stats


def analyze_verdict(msg: QuarantineMessage):
    """Structured triage verdict for one message → (dict, model).

    dict keys: verdict, confidence, indicators[], auth_assessment, recommended_action, rationale.
    Always returns a well-formed dict (falls back to 'uncertain'/'investigate' on parse failure).
    """
    raw, model = run_chat(
        VERDICT_SYSTEM_PROMPT,
        "Assess this email:\n\n" + build_message_summary(msg),
        max_tokens=500,
        json_mode=True,
    )
    try:
        data = json.loads(raw)
    except Exception:
        data = {}

    verdict = str(data.get("verdict", "uncertain")).lower().strip()
    if verdict not in _VERDICTS:
        verdict = "uncertain"
    action = str(data.get("recommended_action", "investigate")).lower().strip()
    if action not in _ACTIONS:
        action = "investigate"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    indicators = data.get("indicators") or []
    if not isinstance(indicators, list):
        indicators = [str(indicators)]
    indicators = [str(x) for x in indicators][:8]

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "indicators": indicators,
        "auth_assessment": str(data.get("auth_assessment", "") or "")[:300],
        "recommended_action": action,
        "rationale": str(data.get("rationale", "") or (raw[:400] if not data else ""))[:600],
    }, model
