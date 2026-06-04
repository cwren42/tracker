"""Approval engine — the risk-scored, human-in-the-loop gate for agent/automation actions.

Before any planned workflow action runs, it passes through ``decide()``. Low-risk actions
auto-execute; medium/high-risk actions park the run as a *pending approval* (a
``command_ledger`` row, ``approval_status='pending'`` / ``status='awaiting_approval'``) until
a human approves it from the Approvals queue, at which point the exact action is replayed.

The gate lives at the single action-dispatch choke point in ``workflow_engine._run_workflow``,
so it covers EVERY action type (device scripts AND directory/identity changes), not just
agent dispatch. This module is intentionally pure Python — no Flask/app-context or DB — so it
is safe to call from workflow_engine's context-less daemon threads. It only makes a decision.

Conservative by design for v1: nothing medium+ runs without a person, and any unmapped action
defaults to 'medium' → 'require' (fail safe). As the master brain accumulates approve/deny
telemetry, that labeled stream is what it learns from to auto-promote safe actions later.
See docs/AGENTIC_IT_OS_GAMEPLAN.md.
"""

# Canonical risk tier per workflow action_type (keys MUST match workflow_engine.ACTION_MAP).
# An explicit per-node `risk_tier` in the action config overrides this map.
RISK_TIERS = {
    # ── Records & notifications — no real-world side effect → auto ──
    "create_ticket":     "low",
    "update_ticket":     "low",
    "close_ticket":      "low",
    "assign_ticket":     "low",
    "send_notification": "low",
    "send_email":        "low",
    "send_teams":        "low",
    "send_slack":        "low",
    "ai_suggest":        "low",
    "wait":              "low",

    # ── Directory / identity changes — privilege & access impact ──
    "create_user":       "high",   # provisions a new identity
    "disable_ad_user":   "high",
    "enable_ad_user":    "medium",
    "reset_password":    "high",
    "unlock_account":    "medium",
    "add_to_group":      "high",    # group membership = privilege grant (could be Domain Admins)
    "remove_from_group": "medium",
    "onboard_employee":  "high",    # provisions a new identity + grants group access (new-hire)
    "azure_sync":        "low",   # idempotent AAD-Connect delta sync — safe to auto-run

    # ── Endpoint actions — act on a live machine ──
    "run_script":        "high",    # arbitrary code execution on an endpoint
    "deploy_software":   "medium",
    "uninstall_software": "medium",
    "install_local_tool": "medium",  # runs a user-staged installer from C:\ITTOOLS as SYSTEM
    "apply_fix":          "medium",  # runs a vetted one-click fix script (by id) as SYSTEM
    "deploy_patch":      "medium",
    "apply_gpo":         "medium",
    "reboot_device":     "high",
    "shutdown_device":   "high",
    "lock_device":       "high",    # bitlocker/device lock

    # ── External calls with side effects ──
    "webhook":           "medium",
    "http_request":      "medium",

    # ── Email security ──
    "release_quarantine": "medium",  # delivers a quarantined/blocked message — human signs off
}

# Tiers permitted to execute automatically, with no human in the loop.
AUTO_TIERS = {"low"}

# Human-facing ordering weight for the queue (high first).
TIER_RANK = {"high": 3, "medium": 2, "low": 1}


def risk_tier_for(action_type, explicit=None):
    """Resolve the effective risk tier: an explicit per-node tier wins, else the map,
    else 'medium' (so an unknown/new action fails safe — it requires approval)."""
    if explicit:
        return explicit
    return RISK_TIERS.get(action_type, "medium")


def decide(action_type, risk_tier=None):
    """Return (decision, tier, reason). decision is 'auto' (run now) or 'require' (hold)."""
    tier = risk_tier_for(action_type, risk_tier)
    if tier in AUTO_TIERS:
        return "auto", tier, f"{tier}-risk action auto-approved by policy"
    return "require", tier, f"{tier}-risk action requires human approval"
