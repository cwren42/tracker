"""Teams bot messaging endpoint (Phase 02 — inbound front door).

POST /api/teams/messages is the Bot Framework messaging endpoint configured on the
Azure Bot. It is intentionally NOT session-authenticated (no @login_required) and
is CSRF-exempt — inbound requests are authenticated by the Bot Framework JWT they
carry (verified by teams_bot.validate_auth). Inert until TEAMS_BOT_APP_ID/SECRET
are set, so it's safe to ship before the Azure Bot is provisioned.
"""
import logging

from flask import Blueprint, request, jsonify

import teams_bot
import teams_intake

log = logging.getLogger(__name__)

bp = Blueprint("teams_bot", __name__, url_prefix="/api/teams")


@bp.route("/messages", methods=["POST"])
def messages():
    # Built-and-ready but inert until the Azure Bot creds are configured.
    if not teams_bot.enabled():
        return ("", 200)

    # Authenticate the inbound activity (genuine Bot Framework token for OUR bot).
    try:
        teams_bot.validate_auth(request.headers.get("Authorization", ""))
    except Exception as e:
        log.warning("teams: rejected unauthenticated activity: %s", e)
        return jsonify(error="unauthorized"), 401

    activity = request.get_json(silent=True) or {}
    atype = activity.get("type")
    _frm = activity.get("from") or {}
    _conv = activity.get("conversation") or {}
    log.info("teams inbound: type=%s convType=%s aad=%s name=%r text=%r",
             atype, _conv.get("conversationType"), _frm.get("aadObjectId"),
             _frm.get("name"), (activity.get("text") or "")[:60])

    try:
        if atype == "message":
            text, card = teams_intake.handle_message(activity)
            ok = teams_bot.send_reply(activity, text=text, card=card)
            log.info("teams reply sent=%s (convType=%s)", ok, _conv.get("conversationType"))
        elif atype == "invoke":
            # Action.Execute callbacks (1-click resolve/escalate) -> handle + return
            # the adaptiveCard/action invoke response directly as the HTTP body.
            if activity.get("name") == "adaptiveCard/action":
                return jsonify(teams_intake.handle_invoke(activity)), 200
            log.info("teams: unhandled invoke name=%s", activity.get("name"))
        # conversationUpdate / typing / etc. -> silent 200
    except Exception:
        log.exception("teams: activity handling failed (type=%s)", atype)

    # Always 200 so Teams doesn't retry-storm; the reply went via the connector.
    return ("", 200)


@bp.route("/outgoing", methods=["POST"])
def outgoing():
    """Teams Outgoing Webhook endpoint — NOT a bot. Teams POSTs the @mention here
    (HMAC-signed) and we reply synchronously in the HTTP response. Different
    delivery path than the bot, so it sidesteps the tenant's bot-message routing."""
    raw = request.get_data()  # raw bytes required for HMAC
    if not teams_bot.verify_outgoing_hmac(raw, request.headers.get("Authorization", "")):
        log.warning("teams outgoing: HMAC verification failed")
        return jsonify(type="message", text="Unauthorized."), 401
    payload = request.get_json(silent=True) or {}
    log.info("teams outgoing inbound: from=%r text=%r",
             (payload.get("from") or {}).get("name"), (payload.get("text") or "")[:60])
    try:
        reply = teams_intake.respond_text(payload.get("text"), payload.get("from") or {})
    except Exception:
        log.exception("teams outgoing: handling failed")
        reply = "Sorry — something went wrong on our side. Please try again."
    return jsonify(type="message", text=reply)
