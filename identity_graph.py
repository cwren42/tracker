"""IT graph — identity link resolution (Track B of the Agentic IT-OS gameplan).

Connects the synced "islands" to the core people/asset records so the world model is
queryable end-to-end:
    M365User.employee_id  -> Employee   (match: m365_id, else UPN == email)
    IntuneDevice.asset_id -> Asset      (match: serial_number, else azure_ad_device_id)

Idempotent + non-destructive: only fills FKs that are currently NULL — it never overwrites
an existing link. Safe to run repeatedly. Use it two ways:
  1. one-time backfill of existing rows, and
  2. at the end of each M365/Intune sync, so newly-synced rows link automatically.

See docs/AGENTIC_IT_OS_GAMEPLAN.md (Track B). No external writes — pure DB linkage.
"""
from extensions import db
from models import Employee, Asset
from soc2_models import M365User, IntuneDevice


def _norm(s):
    return (s or "").strip().lower()


def resolve_identity_links(commit: bool = True) -> dict:
    """Fill M365User.employee_id and IntuneDevice.asset_id where NULL. Returns counts."""
    stats = {
        "m365_total": 0, "m365_linked_now": 0, "m365_linked_total": 0, "m365_unmatched": 0,
        "m365_via_email": 0,
        "intune_total": 0, "intune_linked_now": 0, "intune_linked_total": 0, "intune_unmatched": 0,
    }

    # ── M365User -> Employee (current rows only) ─────────────────────────────
    # Only link is_current rows — historical/deprovisioned snapshots must not poison the
    # live graph (a departed user's stale row could link to a recycled address/employee).
    emps = Employee.query.all()
    by_m365 = {e.m365_id: e for e in emps if e.m365_id}
    by_email = {_norm(e.email): e for e in emps if e.email}
    for u in M365User.query.filter_by(is_current=True):
        stats["m365_total"] += 1
        if u.employee_id:
            stats["m365_linked_total"] += 1
            continue
        # m365_id (Graph object id) is the trustworthy key. Only fall back to UPN==email
        # when there is no m365_id at all — a present-but-unmatched id means the employee
        # isn't synced, NOT that email is a safe proxy.
        if u.m365_id:
            emp = by_m365.get(u.m365_id)
            via_email = False
        else:
            emp = by_email.get(_norm(u.user_principal_name))
            via_email = bool(emp)
        if emp:
            u.employee_id = emp.id
            stats["m365_linked_now"] += 1
            stats["m365_linked_total"] += 1
            if via_email:
                stats["m365_via_email"] += 1
        else:
            stats["m365_unmatched"] += 1

    # ── IntuneDevice -> Asset (current rows only) ────────────────────────────
    assets = Asset.query.all()
    by_serial = {_norm(a.serial_number): a for a in assets if a.serial_number}
    by_aad = {a.azure_ad_device_id: a for a in assets if a.azure_ad_device_id}
    for d in IntuneDevice.query.filter_by(is_current=True):
        stats["intune_total"] += 1
        if d.asset_id:
            stats["intune_linked_total"] += 1
            continue
        asset = None
        if d.serial_number:
            asset = by_serial.get(_norm(d.serial_number))
        if not asset and d.azure_ad_device_id:
            asset = by_aad.get(d.azure_ad_device_id)
        if asset:
            d.asset_id = asset.id
            stats["intune_linked_now"] += 1
            stats["intune_linked_total"] += 1
        else:
            stats["intune_unmatched"] += 1

    if commit:
        db.session.commit()
    return stats
