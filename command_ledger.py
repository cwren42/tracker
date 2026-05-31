"""Command ledger — write helpers for the action audit/trust spine.

Every attempted agent/automation action is recorded here: who/what requested it, which agent
planned it, the tool that executed it, the object it changed, approval + risk, before/after
state (for rollback), and verification result. This is the audit trail, debugging layer, training
data, and trust layer of the Agentic IT-OS. Reads/Mission-Control come later; this is the spine
everything writes to. See docs/AGENTIC_IT_OS_GAMEPLAN.md.
"""
from extensions import db
from models import CommandLedger, now_mst


def log_action(tool, action_type, *, object_type=None, object_id=None, requested_by="system",
               planned_by=None, risk_tier="low", approval_status="auto", before_state=None,
               correlation_id=None, status="planned", rollback_available=False):
    """Record an attempted action. Returns the CommandLedger row (commit it owns)."""
    row = CommandLedger(
        tool=tool, action_type=action_type, object_type=object_type,
        object_id=(str(object_id) if object_id is not None else None),
        requested_by=requested_by, planned_by=planned_by, risk_tier=risk_tier,
        approval_status=approval_status, before_state=before_state,
        correlation_id=correlation_id, status=status, rollback_available=rollback_available,
    )
    db.session.add(row)
    db.session.commit()
    return row


def mark_result(row, status, *, after_state=None, verification_status=None,
                verification_detail=None):
    """Update an action's lifecycle outcome (succeeded/failed) + verification."""
    row.status = status
    if after_state is not None:
        row.after_state = after_state
    if verification_status:
        row.verification_status = verification_status
    if verification_detail:
        row.verification_detail = verification_detail
    row.completed_at = now_mst()
    db.session.commit()
    return row
