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

    try:
        if atype == "message":
            text, card = teams_intake.handle_message(activity)
            teams_bot.send_reply(activity, text=text, card=card)
        elif atype == "invoke":
            # Action.Execute callbacks (1-click fix/approve) land here — wired next.
            log.info("teams: invoke received (name=%s) — not yet handled",
                     activity.get("name"))
        # conversationUpdate / typing / etc. -> silent 200
    except Exception:
        log.exception("teams: activity handling failed (type=%s)", atype)

    # Always 200 so Teams doesn't retry-storm; the reply went via the connector.
    return ("", 200)
