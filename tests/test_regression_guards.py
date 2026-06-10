"""Regression guards for the classes of bug that reached production on 2026-06-09/10.

Each test locks in a failure mode that CI did NOT previously catch, so it can't
silently come back. All DB-free (conftest sets dummy env; CI has no Postgres).

  1. CSP must allow the RMM gateway WebSocket origins  (the shell "WebSocket
     connection failed" — connect-src 'self' silently blocked the gateway WS).
  2. The agent + linux-agent modules must import cleanly on Linux  (report_engine
     import-time path, ConPTY HRESULT typo, pywinpty else-block capturing win32
     structs, missing psutil/websockets dev-deps — all blew up at import on CI).
  3. view_asset tab partials must be div-balanced  (a stray </div> in _tab_unifi
     closed #assetMainContent early, pushing RMM panes outside .tab-content).
"""
import glob
import importlib.util
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1. CSP allows the gateway WebSocket origins ───────────────────────────────
def test_csp_connect_src_allows_gateway_ws(client):
    """connect-src must include a wss:// gateway origin, not just 'self'.
    If it regresses to 'self' only, the browser blocks the Open Shell WebSocket."""
    csp = client.get('/').headers.get('Content-Security-Policy', '')
    m = re.search(r"connect-src([^;]*);", csp)
    assert m, "no connect-src directive in CSP"
    connect_src = m.group(1)
    assert 'wss://' in connect_src, (
        f"connect-src has no wss:// gateway origin (shell WS would be blocked): {connect_src!r}")


# ── 2. Agent + linux modules import cleanly on Linux ──────────────────────────
@pytest.mark.parametrize("relpath", [
    "rmm_agent/agent_client.py",         # fleet agent
    "rmm_agent/canary/agent_client.py",  # canary agent
    "linux_agent/agent.py",              # linux agent
])
def test_agent_module_imports_on_linux(relpath):
    """These run on Windows but CI loads them on Linux for tests. win32-only code
    must be guarded; module-level imports (psutil/websockets) must resolve."""
    path = os.path.join(ROOT, relpath)
    assert os.path.exists(path), f"missing {relpath}"
    name = "agentmod_" + relpath.replace('/', '_').replace('.', '_')
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # raises if the module can't import on Linux


# ── 3. view_asset tab partials are div-balanced ───────────────────────────────
def _strip_jinja_comments(s: str) -> str:
    # {# ... #} comments can contain literal </div> in prose — drop them first.
    return re.sub(r"\{#.*?#\}", "", s, flags=re.DOTALL)


@pytest.mark.parametrize("path", sorted(
    glob.glob(os.path.join(ROOT, "templates/partials/view_asset/_tab_*.html"))))
def test_view_asset_tab_partial_div_balanced(path):
    """Each tab partial must open and close its own <div>s. A net-negative count
    means it closes a parent container (the #assetMainContent regression)."""
    src = _strip_jinja_comments(open(path).read())
    opens = len(re.findall(r"<div\b", src))
    closes = len(re.findall(r"</div>", src))
    assert opens == closes, (
        f"{os.path.basename(path)} div imbalance: {opens} <div> vs {closes} </div> "
        f"(net {opens - closes}); a partial must not close a container it didn't open")
