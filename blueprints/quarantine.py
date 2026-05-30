"""
blueprints/quarantine.py — Exchange Online Quarantine viewer & analysis.

Routes:
  GET  /quarantine                — list + search
  POST /quarantine/sync           — pull fresh data from Exchange
  GET  /quarantine/<msg_id>       — detail / header analysis
  POST /quarantine/<msg_id>/release — release to inbox
  POST /quarantine/<msg_id>/delete  — permanent delete
  GET  /quarantine/campaigns      — campaign grouping view
  GET  /quarantine/export/iocs    — CSV export of IOCs
  GET  /quarantine/api/stats      — JSON stats for dashboard widget
"""
import csv
import io
import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, Response, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case, func, or_, text

from extensions import db
from models import (AzureIntegrationConfig, QuarantineIOC, QuarantineMessage,
                    now_mst)
from utils import admin_required, email_access_required

logger = logging.getLogger(__name__)

bp = Blueprint("quarantine", __name__)

# ─── Sync state: stored in Setting table so all gunicorn workers see the same state ──
_SYNC_STATE_KEY_PREFIX = "quarantine_sync_state"
_SYNC_CURSOR_KEY_PREFIX = "quarantine_sync_cursor"
_DEFAULT_SYNC_LOOKBACK_DAYS = 30
_SYNC_CURSOR_OVERLAP_MINUTES = 10

def _sync_scope_key(recipient_address: str | None = None) -> str:
    if recipient_address:
        return recipient_address.strip().lower()
    return "tenant"


def _sync_state_key(recipient_address: str | None = None) -> str:
    return f"{_SYNC_STATE_KEY_PREFIX}:{_sync_scope_key(recipient_address)}"


def _sync_cursor_key(recipient_address: str | None = None) -> str:
    return f"{_SYNC_CURSOR_KEY_PREFIX}:{_sync_scope_key(recipient_address)}"


def _read_sync_state(recipient_address: str | None = None) -> dict:
    from models import Setting
    try:
        row = Setting.query.filter_by(key=_sync_state_key(recipient_address)).first()
        if row and row.value:
            return json.loads(row.value)
    except Exception:
        pass
    return {"running": False, "message": "Idle", "level": "info", "added": 0, "updated": 0, "started_at": None}


def _write_sync_state(app, state: dict, recipient_address: str | None = None):
    """Write sync state within a fresh app context (safe from background threads)."""
    with app.app_context():
        from models import Setting
        row = Setting.query.filter_by(key=_sync_state_key(recipient_address)).first()
        if row:
            row.value = json.dumps(state)
        else:
            db.session.add(Setting(key=_sync_state_key(recipient_address), value=json.dumps(state)))
        db.session.commit()


def _read_sync_cursor(recipient_address: str | None = None) -> datetime | None:
    from models import Setting
    try:
        row = Setting.query.filter_by(key=_sync_cursor_key(recipient_address)).first()
        if not row or not row.value:
            return None
        return datetime.fromisoformat(row.value.replace("Z", "+00:00"))
    except Exception:
        return None


def _write_sync_cursor(app, cursor_at: datetime, recipient_address: str | None = None):
    with app.app_context():
        from models import Setting
        row = Setting.query.filter_by(key=_sync_cursor_key(recipient_address)).first()
        value = cursor_at.astimezone(timezone.utc).isoformat()
        if row:
            row.value = value
        else:
            db.session.add(Setting(key=_sync_cursor_key(recipient_address), value=value))
        db.session.commit()


def _get_sync_window(recipient_address: str | None = None, days: int = _DEFAULT_SYNC_LOOKBACK_DAYS) -> tuple[datetime, datetime]:
    window_end = datetime.now(timezone.utc)
    cursor = _read_sync_cursor(recipient_address)
    if cursor:
        if cursor.tzinfo is None:
            cursor = cursor.replace(tzinfo=timezone.utc)
        else:
            cursor = cursor.astimezone(timezone.utc)
        window_start = cursor - timedelta(minutes=_SYNC_CURSOR_OVERLAP_MINUTES)
    else:
        window_start = window_end - timedelta(days=max(days, 1))
    return window_start, window_end


def perform_quarantine_sync(flask_app, recipient_address: str | None = None, days: int = _DEFAULT_SYNC_LOOKBACK_DAYS) -> dict:
    scope_label = recipient_address or "all mailboxes"
    started_at = datetime.now(timezone.utc)

    with flask_app.app_context():
        svc = _get_qsvc()

    window_start, window_end = _get_sync_window(recipient_address, days=days)
    _write_sync_state(flask_app, {
        "running": True,
        "started_at": started_at.isoformat(),
        "message": f"Querying Defender from {window_start.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC for {scope_label}…",
        "level": "info",
        "added": 0,
        "updated": 0,
    }, recipient_address)

    try:
        messages = svc.get_quarantine_messages_via_hunting(
            days=days,
            start_time=window_start,
            end_time=window_end,
            recipient_address=recipient_address,
        )

        _write_sync_state(flask_app, {
            "running": True,
            "started_at": started_at.isoformat(),
            "message": f"Writing {len(messages)} messages for {scope_label}…",
            "level": "info",
            "added": 0,
            "updated": 0,
        }, recipient_address)

        with flask_app.app_context():
            added, updated = _upsert_messages(messages) if messages else (0, 0)

        _write_sync_cursor(flask_app, window_end, recipient_address)

        if messages:
            message = f"Sync complete — {added} new, {updated} updated."
        else:
            message = "Sync complete — no new messages found."

        result = {
            "running": False,
            "started_at": started_at.isoformat(),
            "message": message,
            "level": "success",
            "added": added,
            "updated": updated,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
        _write_sync_state(flask_app, result, recipient_address)
        return result
    except Exception as exc:
        logger.exception("Background quarantine sync failed")
        result = {
            "running": False,
            "started_at": started_at.isoformat(),
            "message": f"Sync failed: {exc}",
            "level": "danger",
            "added": 0,
            "updated": 0,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
        _write_sync_state(flask_app, result, recipient_address)
        return result

# ─── Helpers ─────────────────────────────────────────────────────────────────

_MST = timezone(timedelta(hours=-7))


def _get_qsvc():
    """Instantiate QuarantineService from stored Azure config."""
    from quarantine_service import QuarantineService
    from m365_config import get_m365_credentials
    tenant_id, client_id, client_secret = get_m365_credentials()
    if not (tenant_id and client_id and client_secret):
        raise ValueError("M365 credentials not configured. Set them in /var/www/tracker/.secrets.env.")
    return QuarantineService(tenant_id, client_id, client_secret)


def _upsert_messages(messages: list[dict]) -> tuple[int, int]:
    """Upsert a list of normalized message dicts. Returns (added, updated)."""
    from quarantine_service import QuarantineService
    added = updated = 0
    for m in messages:
        mid = m.get("message_id")
        if not mid:
            continue

        existing = QuarantineMessage.query.filter_by(message_id=mid).first()

        # Parse received_time
        rt = m.get("received_time")
        if isinstance(rt, str):
            try:
                rt = datetime.fromisoformat(rt.replace("Z", "+00:00"))
            except ValueError:
                rt = None

        if existing:
            # Update mutable fields only
            existing.last_synced = datetime.utcnow()
            existing.release_status = m.get("release_status", existing.release_status)
            existing.spf_result = m.get("spf_result", existing.spf_result)
            existing.dkim_result = m.get("dkim_result", existing.dkim_result)
            existing.dmarc_result = m.get("dmarc_result", existing.dmarc_result)
            existing.sender_ip = m.get("sender_ip") or existing.sender_ip
            existing.email_direction = m.get("email_direction") or existing.email_direction
            updated += 1
        else:
            qm = QuarantineMessage(
                message_id=mid,
                internet_message_id=m.get("internet_message_id"),
                sender_address=m.get("sender_address"),
                sender_display_name=m.get("sender_display_name"),
                sender_domain=m.get("sender_domain"),
                recipient_address=m.get("recipient_address"),
                subject=m.get("subject"),
                received_time=rt,
                expiry_time=m.get("expiry_time"),
                quarantine_reason=m.get("quarantine_reason"),
                policy_type=m.get("policy_type"),
                threat_type=m.get("threat_type"),
                spf_result=m.get("spf_result"),
                dkim_result=m.get("dkim_result"),
                dmarc_result=m.get("dmarc_result"),
                sender_ip=m.get("sender_ip"),
                email_direction=m.get("email_direction"),
                release_status=m.get("release_status", "Quarantined"),
                url_count=m.get("url_count", 0),
                attachment_count=m.get("attachment_count", 0),
                campaign_id=m.get("sender_domain"),
                last_synced=datetime.utcnow(),
            )
            db.session.add(qm)
            db.session.flush()  # get ID before IOC insert

            # Extract and store IOCs
            iocs = QuarantineService.extract_iocs(m)
            for ioc in iocs:
                # Deduplicate: increment seen_count if already exists
                existing_ioc = QuarantineIOC.query.filter_by(
                    message_id=mid,
                    ioc_type=ioc["ioc_type"],
                    ioc_value=ioc["ioc_value"],
                ).first()
                if not existing_ioc:
                    db.session.add(QuarantineIOC(
                        message_id=mid,
                        ioc_type=ioc["ioc_type"],
                        ioc_value=ioc["ioc_value"],
                        threat_label=ioc.get("threat_label"),
                    ))
            added += 1

    db.session.commit()
    return added, updated


# ─── Routes ──────────────────────────────────────────────────────────────────

@bp.route("/quarantine")
@login_required
@email_access_required
def quarantine_list():
    page = request.args.get("page", 1, type=int)
    per_page = 50
    is_admin = current_user.role == "admin"
    sync_scope_email = None if is_admin else (current_user.email or "").strip().lower()
    # Only admins see all mail; all other roles see only their own.
    can_see_all_mail = is_admin
    search        = request.args.get("q", "").strip()
    threat_filter = request.args.get("threat", "").strip()
    policy_filter = request.args.get("policy", "").strip()
    status_filter = request.args.get("status", "").strip()
    days_filter   = request.args.get("days", "30")
    view_filter   = request.args.get("view", "quarantined")  # all|delivered|quarantined|blocked|junk|threats

    # Base date-bounded query for stats
    base_q = QuarantineMessage.query
    # Restrict to own emails for non-admin users.
    if not can_see_all_mail:
        base_q = base_q.filter(QuarantineMessage.recipient_address == current_user.email)
    if days_filter.isdigit():
        cutoff = datetime.utcnow() - timedelta(days=int(days_filter))
        base_q = base_q.filter(QuarantineMessage.received_time >= cutoff)

    # ── Tab counts ────────────────────────────────────────────────────────────
    tab_counts = {
        "all":         base_q.count(),
        "delivered":   base_q.filter(QuarantineMessage.release_status.in_(["Delivered", "Junk"])).count(),
        "quarantined": base_q.filter(QuarantineMessage.release_status.in_(["Quarantined", "Released", "Deleted"])).count(),
        "blocked":     base_q.filter(QuarantineMessage.release_status == "Blocked").count(),
        "junk":        base_q.filter(QuarantineMessage.release_status == "Junk").count(),
        "threats":     base_q.filter(QuarantineMessage.threat_type.in_(["Phish", "Malware"])).count(),
    }

    # ── Build display query ───────────────────────────────────────────────────
    q = base_q
    # If status_filter is set explicitly, it takes full precedence over the tab's
    # view_filter restriction — otherwise the two can conflict and return 0 rows.
    if status_filter:
        q = q.filter(QuarantineMessage.release_status == status_filter)
    else:
        if view_filter == "delivered":
            q = q.filter(QuarantineMessage.release_status.in_(["Delivered", "Junk"]))
        elif view_filter == "quarantined":
            q = q.filter(QuarantineMessage.release_status.in_(["Quarantined", "Released", "Deleted"]))
        elif view_filter == "blocked":
            q = q.filter(QuarantineMessage.release_status == "Blocked")
        elif view_filter == "junk":
            q = q.filter(QuarantineMessage.release_status == "Junk")
        elif view_filter == "threats":
            q = q.filter(QuarantineMessage.threat_type.in_(["Phish", "Malware"]))
        # view_filter == "all" → no additional status filter

    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            QuarantineMessage.sender_address.ilike(like),
            QuarantineMessage.sender_domain.ilike(like),
            QuarantineMessage.subject.ilike(like),
            QuarantineMessage.recipient_address.ilike(like),
        ))
    if threat_filter:
        q = q.filter(QuarantineMessage.threat_type == threat_filter)
    if policy_filter:
        q = q.filter(QuarantineMessage.policy_type == policy_filter)
    if status_filter:
        q = q.filter(QuarantineMessage.release_status == status_filter)

    q = q.order_by(QuarantineMessage.received_time.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    messages = pagination.items

    # ── Stat cards (always full-period, all statuses) ─────────────────────────
    delivered_count   = tab_counts["delivered"]
    quarantine_count  = tab_counts["quarantined"]
    blocked_count     = tab_counts["blocked"]
    threat_count      = tab_counts["threats"]
    phish_count       = base_q.filter(QuarantineMessage.threat_type == "Phish").count()
    malware_count     = base_q.filter(QuarantineMessage.threat_type == "Malware").count()

    last_sync = _read_sync_cursor(sync_scope_email)

    return render_template(
        "quarantine.html",
        messages=messages,
        pagination=pagination,
        search=search,
        threat_filter=threat_filter,
        policy_filter=policy_filter,
        status_filter=status_filter,
        days_filter=days_filter,
        view_filter=view_filter,
        tab_counts=tab_counts,
        delivered_count=delivered_count,
        quarantine_count=quarantine_count,
        blocked_count=blocked_count,
        threat_count=threat_count,
        phish_count=phish_count,
        malware_count=malware_count,
        last_sync=last_sync,
        now=datetime.utcnow(),
        is_admin=is_admin,
    )


@bp.route("/quarantine/sync", methods=["POST"])
@login_required
@email_access_required
def quarantine_sync():
    """Kick off a background sync and redirect immediately."""
    recipient_address = None if current_user.role == "admin" else (current_user.email or "").strip().lower()
    if current_user.role != "admin" and not recipient_address:
        flash("Your account does not have an email address configured for mailbox sync.", "danger")
        return redirect(url_for("quarantine.quarantine_list"))

    state = _read_sync_state(recipient_address)
    if state.get("running"):
        flash("Sync is already running. Check the status indicator.", "warning")
        return redirect(url_for("quarantine.quarantine_list"))

    days_raw = request.form.get("days", "30")
    days = int(days_raw) if days_raw.isdigit() else 30

    try:
        _get_qsvc()  # validate config before spawning thread
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("quarantine.quarantine_list"))

    from flask import current_app
    app = current_app._get_current_object()

    def _run():
        perform_quarantine_sync(app, recipient_address=recipient_address, days=days)

    t = threading.Thread(target=_run, daemon=True, name="quarantine-sync")
    t.start()

    flash(
        "Sync started in background — results will appear automatically."
        if current_user.role == "admin"
        else "Your mailbox sync started in background — results will appear automatically.",
        "info",
    )
    return redirect(url_for("quarantine.quarantine_list"))


@bp.route("/quarantine/sync/status")
@login_required
@email_access_required
def quarantine_sync_status():
    """JSON endpoint polled by the list page to show live sync progress."""
    recipient_address = None if current_user.role == "admin" else (current_user.email or "").strip().lower()
    return jsonify(_read_sync_state(recipient_address))


@bp.route("/quarantine/<path:message_id>", methods=["GET"])
@login_required
@email_access_required
def quarantine_detail(message_id):
    msg = QuarantineMessage.query.filter_by(message_id=message_id).first_or_404()
    can_see_all_mail = current_user.role == "admin"
    # Non-admin users may only view their own messages.
    if not can_see_all_mail and msg.recipient_address != current_user.email:
        flash("Access denied.", "danger")
        return redirect(url_for("quarantine.quarantine_list"))
    iocs = QuarantineIOC.query.filter_by(message_id=message_id).all()

    # Attempt to fetch raw headers on-demand if not cached
    if not msg.raw_headers:
        try:
            svc = _get_qsvc()
            headers = svc.get_message_headers(message_id)
            if headers:
                msg.raw_headers = headers
                db.session.commit()
        except Exception:
            pass  # Headers are optional

    # Parse raw headers into key/value list for display
    parsed_headers = _parse_headers(msg.raw_headers or "")

    # Domain frequency: how many times has this domain appeared?
    domain_count = QuarantineMessage.query.filter_by(sender_domain=msg.sender_domain).count()

    # Related messages from same campaign
    related = (
        QuarantineMessage.query
        .filter_by(campaign_id=msg.campaign_id)
        .filter(QuarantineMessage.message_id != message_id)
        .order_by(QuarantineMessage.received_time.desc())
        .limit(10)
        .all()
    ) if (current_user.role == "admin" and msg.campaign_id) else []

    return render_template(
        "quarantine_detail.html",
        msg=msg,
        iocs=iocs,
        parsed_headers=parsed_headers,
        domain_count=domain_count,
        related=related,
        now=datetime.utcnow(),
        is_admin=(current_user.role == "admin"),
    )


@bp.route("/quarantine/<path:message_id>/release", methods=["POST"])
@login_required
@admin_required
def quarantine_release(message_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    msg = QuarantineMessage.query.filter_by(message_id=message_id).first_or_404()
    if msg.release_status != "Quarantined":
        if is_ajax:
            return jsonify({"success": False, "error": "Message is not in Quarantined status."})
        flash("Message is not in Quarantined status.", "warning")
        return redirect(url_for("quarantine.quarantine_detail", message_id=message_id))
    try:
        svc = _get_qsvc()
        result = svc.release_message(message_id, msg.recipient_address or "")
        if result.get("success"):
            msg.release_status = "Released"
            msg.released_by = current_user.username
            msg.released_at = datetime.utcnow()
            db.session.commit()
            if is_ajax:
                return jsonify({"success": True})
            flash(f"Message released to {msg.recipient_address}.", "success")
        else:
            err = result.get("error", "Unknown error")
            if is_ajax:
                return jsonify({"success": False, "error": err})
            flash(f"Release failed: {err}", "danger")
    except ValueError as e:
        if is_ajax:
            return jsonify({"success": False, "error": str(e)})
        flash(str(e), "danger")
    except Exception as e:
        logger.exception("Release failed")
        if is_ajax:
            return jsonify({"success": False, "error": str(e)})
        flash(f"Release failed: {e}", "danger")
    return redirect(url_for("quarantine.quarantine_detail", message_id=message_id))


@bp.route("/quarantine/<path:message_id>/delete", methods=["POST"])
@login_required
@admin_required
def quarantine_delete(message_id):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    msg = QuarantineMessage.query.filter_by(message_id=message_id).first_or_404()
    try:
        svc = _get_qsvc()
        result, success_message = _delete_email_message(svc, msg)
        if result.get("success"):
            msg.release_status = "Deleted"
            msg.released_by = current_user.username
            msg.released_at = datetime.utcnow()
            db.session.commit()
            if is_ajax:
                return jsonify({"success": True})
            flash(success_message, "success")
        else:
            err = result.get("error", "Unknown error")
            if is_ajax:
                return jsonify({"success": False, "error": err})
            flash(f"Delete failed: {err}", "danger")
    except ValueError as e:
        if is_ajax:
            return jsonify({"success": False, "error": str(e)})
        flash(str(e), "danger")
    except Exception as e:
        logger.exception("Delete failed")
        if is_ajax:
            return jsonify({"success": False, "error": str(e)})
        flash(f"Delete failed: {e}", "danger")
    if is_ajax:
        return jsonify({"success": False, "error": "Unknown error"})
    return redirect(url_for("quarantine.quarantine_list"))


@bp.route("/quarantine/<path:message_id>/preview", methods=["GET"])
@login_required
@email_access_required
def quarantine_preview(message_id):
    """JSON endpoint — returns full preview payload: meta + body + URLs + attachments."""
    msg = QuarantineMessage.query.filter_by(message_id=message_id).first_or_404()
    can_see_all_mail = current_user.role == "admin"
    # Non-admin users may only view their own messages.
    if not can_see_all_mail and msg.recipient_address != current_user.email:
        return jsonify({"error": "Access denied"}), 403
    is_admin = current_user.role == "admin"
    try:
        svc = _get_qsvc()
        preview = svc.get_email_preview(
            message_id,
            internet_message_id=msg.internet_message_id,
            recipient=msg.recipient_address,
            release_status=msg.release_status,
        )
    except Exception as e:
        preview = {"body_html": None, "body_available": False, "urls": [], "attachments": [], "error": str(e)}
    # Strip body from non-admin responses
    if not is_admin:
        preview["body_html"] = None
        preview["body_preview"] = None
        preview["body_available"] = False
        preview["body_permission_needed"] = False
    return jsonify({
        "message_id": message_id,
        "subject": msg.subject,
        "sender": msg.sender_address,
        "sender_display_name": msg.sender_display_name,
        "recipient": msg.recipient_address,
        "received": msg.received_time.isoformat() if msg.received_time else None,
        "threat_type": msg.threat_type,
        "policy_type": msg.policy_type,
        "spf": msg.spf_result,
        "dkim": msg.dkim_result,
        "dmarc": msg.dmarc_result,
        "sender_ip": msg.sender_ip,
        "email_direction": msg.email_direction,
        "risk_score": msg.risk_score,
        "risk_label": msg.risk_label,
        "release_status": msg.release_status,
        "url_count": msg.url_count,
        "attachment_count": msg.attachment_count,
        **preview,
    })

def _get_openai_email_config():
    from models import Setting

    from secret_store import decrypt_secret
    api_key_row = Setting.query.filter_by(key="openai_api_key").first()
    api_key = decrypt_secret(api_key_row.value) if api_key_row else None
    if not api_key:
        raise ValueError("OpenAI API key not configured — add it in Settings -> AI")

    model_row = Setting.query.filter_by(key="openai_model").first()
    model = (model_row.value if model_row and model_row.value else None) or "gpt-4o"
    return api_key, model


def _build_ai_message_summary(msg: QuarantineMessage, include_headers: bool = True) -> str:
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
        if raw_headers:
            lines.append("Raw headers excerpt:")
            lines.append(raw_headers[:4000])
        else:
            lines.append("Raw headers excerpt: Not available")
    return "\n".join(lines)


def _run_openai_email_analysis(system_prompt: str, user_prompt: str, max_tokens: int = 700):
    import requests as _http

    api_key, model = _get_openai_email_config()
    resp = _http.post(
        "https://api.openai.com/v1/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
        },
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    analysis = resp.json()["choices"][0]["message"]["content"].strip()
    return analysis, model


_MAILBOX_MESSAGE_STATUSES = {"Delivered", "Junk", "Released"}


def _delete_email_message(svc, msg: QuarantineMessage):
    if msg.release_status == "Quarantined":
        return svc.delete_message(msg.message_id), "Message permanently deleted from quarantine."
    if msg.release_status in _MAILBOX_MESSAGE_STATUSES:
        return (
            svc.delete_mailbox_message(msg.recipient_address or "", msg.internet_message_id or ""),
            "Message deleted from mailbox.",
        )
    return (
        {"success": False, "error": f"Message is not deletable from status: {msg.release_status or 'Unknown'}."},
        None,
    )


@bp.route("/quarantine/<path:message_id>/ai-analyze", methods=["POST"])
@login_required
@email_access_required
def quarantine_ai_analyze(message_id):
    """AI security analysis of an email using OpenAI. Admin only."""
    if current_user.role != "admin":
        return jsonify({"error": "AI analysis is only available to admins."}), 403
    msg = QuarantineMessage.query.filter_by(message_id=message_id).first_or_404()

    system_prompt = (
        "You are a SOC analyst AI assistant specializing in email security. "
        "Analyze the provided email metadata and give a structured security assessment. "
        "Cover: (1) Threat Indicators, (2) Authentication Assessment, "
        "(3) Likely Classification (phishing / spam / BEC / legitimate / etc), "
        "(4) Recommended Action. "
        "Use the raw headers excerpt when present. "
        "Be concise — 2-4 sentences per section. Use Markdown with ### headers."
    )
    try:
        analysis, model = _run_openai_email_analysis(
            system_prompt,
            "Analyze this email:\n\n" + _build_ai_message_summary(msg, include_headers=True),
            max_tokens=700,
        )
        return jsonify({"analysis": analysis, "model": model})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"AI analysis failed: {str(e)}"}), 500


@bp.route("/quarantine/ai-analyze-bulk", methods=["POST"])
@login_required
@admin_required
def quarantine_bulk_ai_analyze():
    """AI analysis for multiple selected emails. Admin only."""
    data = request.get_json(silent=True) or {}
    ids = [str(message_id).strip() for message_id in (data.get("ids") or []) if str(message_id).strip()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return jsonify({"error": "No emails selected."}), 400
    if len(ids) > 10:
        return jsonify({"error": "Select 10 emails or fewer for AI analysis."}), 400

    found = {
        msg.message_id: msg
        for msg in QuarantineMessage.query.filter(QuarantineMessage.message_id.in_(ids)).all()
    }
    ordered_messages = [found[message_id] for message_id in ids if message_id in found]
    if not ordered_messages:
        return jsonify({"error": "Selected emails were not found."}), 404

    system_prompt = (
        "You are a SOC analyst AI assistant specializing in email security. "
        "Analyze this selected group of emails and provide a concise campaign-level assessment. "
        "Cover: (1) Overall Pattern, (2) Common Threat Indicators, (3) Notable Outliers, "
        "(4) Recommended Bulk Action. "
        "Use Markdown with ### headers."
    )
    user_prompt = "Analyze these selected emails as a set:\n\n" + "\n\n---\n\n".join(
        f"Email {index}:\n{_build_ai_message_summary(msg, include_headers=False)}"
        for index, msg in enumerate(ordered_messages, start=1)
    )
    try:
        analysis, model = _run_openai_email_analysis(system_prompt, user_prompt, max_tokens=900)
        return jsonify({"analysis": analysis, "model": model, "count": len(ordered_messages)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Bulk AI analysis failed: {str(e)}"}), 500

@bp.route("/quarantine/bulk", methods=["POST"])
@login_required
@admin_required
def quarantine_bulk():
    """Bulk release or delete. Delete targets quarantine or mailbox copies based on status."""
    data = request.get_json() or {}
    action = data.get("action")
    ids    = data.get("ids", [])
    if not ids or action not in ("release", "delete"):
        return jsonify({"error": "Invalid request — provide action and ids"}), 400
    try:
        svc = _get_qsvc()
    except ValueError as e:
        return jsonify({"error": str(e)}), 500

    ok, failed = [], []
    for mid in ids[:100]:  # hard cap at 100 per request
        msg = QuarantineMessage.query.filter_by(message_id=mid).first()
        if not msg:
            failed.append({"id": mid, "error": "Not found"})
            continue
        try:
            if action == "release":
                r = svc.release_message(mid, msg.recipient_address or "")
            else:
                r, _ = _delete_email_message(svc, msg)
            if r.get("success"):
                msg.release_status = "Released" if action == "release" else "Deleted"
                msg.released_by = current_user.username
                msg.released_at = datetime.utcnow()
                ok.append(mid)
            else:
                failed.append({"id": mid, "error": r.get("error", "API error")})
        except Exception as e:
            failed.append({"id": mid, "error": str(e)})

    db.session.commit()
    return jsonify({"ok": ok, "failed": failed, "ok_count": len(ok), "fail_count": len(failed)})




# Reporting routes split into a sibling module (registered on bp above)
from blueprints import quarantine_reports  # noqa: E402,F401
from blueprints.quarantine_reports import _parse_headers  # noqa: E402,F401  re-export for preview route
