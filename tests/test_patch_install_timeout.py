"""Guard the patch-install hang fix (fix/patch-install-hang).

Root cause: the WUA COM Install() runs on the agent's command-executor thread and
can block effectively forever on a broken/large Windows Update backlog, wedging the
whole executor (agent stays WS-connected but executes nothing). The fix runs the
blocking WUA work in a CHILD PROCESS with a hard wall-clock deadline; on timeout the
process tree is killed and a structured failed/timeout result is returned so the
gateway records a real failure (not silent stuck-deploying) and the executor frees.

These tests are import-only (no PowerShell/COM invoked) and verify the
timeout-handling contract of _install_patches_wua without a live Windows box:
  * on timeout -> structured failed result (timed_out, "timed out" error text,
    installed=0) and the function RETURNS cleanly (executor thread freed),
  * on success -> the normal result is passed through unchanged.

The gateway (rmm_gateway/main.py patch_install_result handler) already classifies
an error containing "timed out"/"timed_out" as status='failed', so this error text
is load-bearing for the wire contract.
"""
import importlib.util
import os
import sys

_AGENT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "rmm_agent", "canary", "agent_client.py",
)


def _load_agent():
    spec = importlib.util.spec_from_file_location("cagent_under_test", _AGENT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["cagent_under_test"] = m
    spec.loader.exec_module(m)
    return m


def test_install_patches_timeout_reports_failed_and_returns():
    m = _load_agent()
    # Force the child-process WUA call to behave as if it hit the hard deadline.
    m._ps_json_proc = lambda script, timeout=15: m._PS_TIMEOUT
    res = m._install_patches_wua(update_ids=["abc"], kb_ids=["5031234"], titles=[])
    assert res["installed"] == 0
    assert res["reboot_required"] is False
    assert res.get("timed_out") is True
    # The gateway matches "timed out"/"timed_out" -> status='failed'; keep that contract.
    assert "timed out" in res["error"].lower()


def test_install_patches_success_passthrough():
    m = _load_agent()
    m._ps_json_proc = lambda script, timeout=15: {
        "installed": 2, "reboot_required": True, "result_code": 2, "still_pending": 0, "error": "",
    }
    res = m._install_patches_wua(update_ids=["abc"], kb_ids=["5031234"], titles=[])
    assert res["installed"] == 2
    assert res["reboot_required"] is True
    assert res.get("timed_out") is None
    assert res["error"] == ""


def test_patch_install_timeout_default_and_override(monkeypatch):
    m = _load_agent()
    monkeypatch.delenv("CIRQUE_PATCH_INSTALL_TIMEOUT", raising=False)
    assert m._patch_install_timeout() == 35 * 60
    monkeypatch.setenv("CIRQUE_PATCH_INSTALL_TIMEOUT", "600")
    assert m._patch_install_timeout() == 600
    # Below the 60s floor falls back to default.
    monkeypatch.setenv("CIRQUE_PATCH_INSTALL_TIMEOUT", "5")
    assert m._patch_install_timeout() == 35 * 60
