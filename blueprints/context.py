"""Context Layer API (Agentic IT OS roadmap, Phase 0).

Read-only endpoints that return the unified per-entity context object assembled by
context_service. Admin-gated, like the other internal routes. The UI, ticket
enrichment, and (later) playbooks all read from here so there's one source of
truth for "everything we know about this person / device".
"""
import logging

from flask import Blueprint, jsonify
from flask_login import login_required

from utils import admin_required
import context_service

logger = logging.getLogger(__name__)

bp = Blueprint("context", __name__, url_prefix="/context")

_KINDS = {
    "person": context_service.get_person_context,
    "device": context_service.get_device_context,
}


@bp.route("/<kind>/<path:ident>")
@login_required
@admin_required
def get_context(kind, ident):
    """Return the context object for a person (id / email / sam) or device
    (id / name / serial / asset_tag). Read-only."""
    fn = _KINDS.get((kind or "").lower())
    if fn is None:
        return jsonify(error="kind must be 'person' or 'device'"), 400
    try:
        ctx = fn(ident)
    except Exception:  # never leak a stack trace to the client
        logger.exception("context assembly failed for %s/%s", kind, ident)
        return jsonify(error="context assembly failed"), 500
    if ctx is None:
        return jsonify(error=f"{kind} not found: {ident}"), 404
    return jsonify(ctx)
