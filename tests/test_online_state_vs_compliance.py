"""Guards the online_state vs Intune-compliance separation fixed on 2026-06-09.

Incident: the Intune asset sync wrote `complianceState`
(compliant / noncompliant / unknown) into `asset.online_state`, the LIVE
CONNECTIVITY field. ~65 assets showed "compliant" as their online status while
actually being offline (e.g. SALUTE-ASUS: RMM agent dead since 2026-03-17, only
Intune's compliance check-in was fresh). The operator lost trust in the online
indicator.

Rule enforced here:
  * online_state means LIVE CONNECTIVITY only — 'Online' / 'Offline'.
    Live writers: rmm_agent_ingest.py, rmm_sync_routes.py ('Online'),
    unifi_service.py (Online/Offline). The Intune sync must NOT write a
    compliance string into it.
  * Intune compliance lives in `intune_compliance_state`.
  * Compliance READERS (assets.py noncompliant filter, dashboard.py
    noncompliant count) must read `intune_compliance_state`, not online_state.

These are pure source-parsing checks: no app import, no DB — they run in the
existing CI job (`pytest -ra`).
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_COMPLIANCE_STRINGS = ("compliant", "noncompliant", "unknown")


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_intune_sync_never_writes_compliance_into_online_state():
    """blueprints/assets_intune.py must never assign a compliance value to
    online_state. The Intune sync owns intune_compliance_state, not the live
    connectivity field."""
    src = _read("blueprints/assets_intune.py")

    # Every assignment / keyword-binding of online_state in the Intune sync.
    # Catches both `asset.online_state = <x>` and `online_state=<x>` (Asset(...)).
    assigns = re.findall(r"online_state\s*=\s*([^\n,]+)", src)
    assert assigns, "online_state assignment not found in assets_intune.py — did it move?"

    for expr in assigns:
        e = expr.strip()
        low = e.lower()
        # Must not pull from complianceState / the `compliance` local.
        assert "compliancestate" not in low, (
            f"assets_intune.py binds online_state to {e!r} — that is Intune "
            f"compliance, not connectivity. Write it to intune_compliance_state."
        )
        assert not re.match(r"compliance\b", low), (
            f"assets_intune.py binds online_state to the `compliance` local "
            f"({e!r}); compliance is not connectivity."
        )
        # Any string literal it is set to must be a connectivity value.
        lit = re.match(r"['\"]([^'\"]+)['\"]", e)
        if lit:
            val = lit.group(1).lower()
            assert val not in _COMPLIANCE_STRINGS, (
                f"assets_intune.py sets online_state to compliance string "
                f"{val!r}; online_state is live connectivity (Online/Offline)."
            )


def test_intune_sync_still_writes_compliance_state():
    """The compliance value must still be persisted — to intune_compliance_state."""
    src = _read("blueprints/assets_intune.py")
    assert re.search(r"intune_compliance_state\s*=", src), (
        "assets_intune.py no longer sets intune_compliance_state — the "
        "noncompliant filter/dashboard read that field."
    )


def test_noncompliant_filter_reads_compliance_field_not_online_state():
    """assets.py noncompliant quick-filter must read intune_compliance_state."""
    src = _read("blueprints/assets.py")
    assert "intune_compliance_state == 'noncompliant'" in src, (
        "assets.py noncompliant filter must read intune_compliance_state."
    )
    assert "online_state == 'noncompliant'" not in src, (
        "assets.py still filters noncompliant off online_state — that is the "
        "connectivity field, not compliance."
    )


def test_dashboard_noncompliant_count_reads_compliance_field():
    """dashboard.py noncompliant count must filter on intune_compliance_state."""
    src = _read("blueprints/dashboard.py")
    assert re.search(
        r"filter_by\(\s*intune_compliance_state\s*=\s*'noncompliant'", src
    ), "dashboard.py noncompliant count must filter on intune_compliance_state."
    assert not re.search(r"filter_by\(\s*online_state\s*=\s*'noncompliant'", src), (
        "dashboard.py still counts noncompliant off online_state."
    )
