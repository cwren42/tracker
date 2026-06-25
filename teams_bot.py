"""Teams bot protocol layer (Agentic IT OS roadmap, Phase 02 — inbound front door).

Implements the minimal Bot Framework v3 protocol WITHOUT the heavy botbuilder SDK,
using libs already in the venv (PyJWT + cryptography for inbound auth, MSAL for the
outbound connector token):

  * validate_auth(header)  -> verify the Bearer JWT on an inbound activity is a
    genuine Bot Framework token addressed to OUR bot (signature via the BF JWKS,
    issuer = api.botframework.com, audience = our app id). Rejects forgeries so the
    public /api/teams/messages endpoint can't be driven by anyone.
  * send_reply(activity, text=None, card=None) -> POST a reply activity back to the
    originating serviceUrl, authenticated with an app-credentials connector token.

CONFIG-GATED + DEFAULT OFF: reads TEAMS_BOT_APP_ID / TEAMS_BOT_APP_SECRET (and
optional TEAMS_BOT_TENANT) from the environment. enabled() is False until those are
set, so the endpoint is built-and-ready but inert until the Azure Bot is provisioned.
"""
import os
import json
import logging
import urllib.request
import urllib.error

import jwt  # PyJWT

log = logging.getLogger(__name__)

_BF_OPENID = "https://login.botframework.com/v1/.well-known/openidconfiguration"
_BF_ISSUER = "https://api.botframework.com"
_CONNECTOR_SCOPE = "https://api.botframework.com/.default"

_jwk_client = None      # cached PyJWKClient
_msal_app = None        # cached MSAL ConfidentialClientApplication


def _cfg():
    return (os.environ.get("TEAMS_BOT_APP_ID", "").strip(),
            os.environ.get("TEAMS_BOT_APP_SECRET", "").strip(),
            os.environ.get("TEAMS_BOT_TENANT", "botframework.com").strip())


def enabled():
    app_id, secret, _ = _cfg()
    return bool(app_id and secret)


# ---- inbound auth -----------------------------------------------------------
def _jwks():
    global _jwk_client
    if _jwk_client is None:
        cfg = json.loads(urllib.request.urlopen(_BF_OPENID, timeout=8).read())
        _jwk_client = jwt.PyJWKClient(cfg["jwks_uri"])  # caches keys internally
    return _jwk_client


def validate_auth(auth_header):
    """Return the verified JWT claims, or raise. auth_header is the raw
    'Authorization' value ('Bearer <jwt>')."""
    app_id, _, _ = _cfg()
    if not app_id:
        raise PermissionError("teams bot not configured")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise PermissionError("missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    signing_key = _jwks().get_signing_key_from_jwt(token).key
    return jwt.decode(
        token, signing_key, algorithms=["RS256"],
        audience=app_id, issuer=_BF_ISSUER,
        options={"require": ["exp", "iss", "aud"]},
    )


# ---- outbound reply ---------------------------------------------------------
def _connector_token():
    global _msal_app
    app_id, secret, tenant = _cfg()
    if not (app_id and secret):
        raise PermissionError("teams bot not configured")
    if _msal_app is None:
        import msal
        _msal_app = msal.ConfidentialClientApplication(
            client_id=app_id, client_credential=secret,
            authority=f"https://login.microsoftonline.com/{tenant}")
    res = _msal_app.acquire_token_for_client(scopes=[_CONNECTOR_SCOPE])
    if "access_token" not in res:
        raise RuntimeError(f"connector token failed: {res.get('error_description', res)}")
    return res["access_token"]


def _reply_activity(text=None, card=None):
    act = {"type": "message"}
    if card is not None:
        act["attachments"] = [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": card,
        }]
        if text:
            act["text"] = text
    else:
        act["text"] = text or ""
    return act


def send_reply(incoming_activity, text=None, card=None):
    """POST a reply to the conversation the incoming activity came from. Best-effort:
    logs and returns False on failure (never raises into the request handler)."""
    try:
        service_url = (incoming_activity.get("serviceUrl") or "").rstrip("/")
        conv = (incoming_activity.get("conversation") or {}).get("id")
        act_id = incoming_activity.get("id")
        if not (service_url and conv):
            return False
        url = f"{service_url}/v3/conversations/{conv}/activities"
        if act_id:
            url += f"/{act_id}"  # reply-to threads under the user's message
        body = _reply_activity(text=text, card=card)
        # Full reply envelope — the connector 400s without from/recipient/conversation.
        body["conversation"] = incoming_activity.get("conversation")
        body["from"] = incoming_activity.get("recipient")   # the bot
        body["recipient"] = incoming_activity.get("from")   # the user
        if act_id:
            body["replyToId"] = act_id
        token = _connector_token()
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except urllib.error.HTTPError as he:
        try:
            detail = he.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        log.error("teams send_reply HTTP %s: %s", he.code, detail)
        return False
    except Exception:
        log.exception("teams send_reply failed")
        return False
