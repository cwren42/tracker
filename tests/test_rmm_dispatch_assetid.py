"""Guards that RMM command dispatch is keyed on the STABLE asset_id, not the
drift-prone agent_id string.

Background: renamed / re-enrolled / mis-cased boxes (asset "Ken-Lenovo" ->
agent_id "KEN-DELL"; "ChrisHome" -> "CHRISHOME") kept their original enrollment
agent_id while the asset name changed. Dispatch used to match agent_id EXACTLY,
so those boxes' commands were stranded as undeliverable 'queued' orphans. The fix
re-keys every queue match onto asset_id across all 3 command tables while the WS
still handshakes as agent_id.

Pure source-parsing checks (like tests/test_rmm_bool_sql.py): no app import, no
FastAPI/httpx, no DB — they run in the DB-free CI job.
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _norm(s: str) -> str:
    """Collapse all runs of whitespace to a single space so assertions are robust
    to reformatting/line-wrapping of the SQL."""
    return re.sub(r"\s+", " ", s)


def _func_body(src: str, name: str) -> str:
    """Return the source text of an async def <name>(...) up to (but not including)
    the next top-level `async def`/`def` at column 0."""
    m = re.search(rf"^async def {re.escape(name)}\b", src, re.MULTILINE)
    assert m, f"function {name} not found in rmm_gateway/main.py"
    start = m.start()
    nxt = re.search(r"^(?:async def|def) ", src[m.end():], re.MULTILINE)
    end = m.end() + nxt.start() if nxt else len(src)
    return src[start:end]


def test_flush_remediation_queue_keys_on_asset_id():
    """_flush_remediation_queue's SELECT from rmm_remediation_queue must filter on
    asset_id, not agent_id."""
    body = _norm(_func_body(_read("rmm_gateway/main.py"), "_flush_remediation_queue"))
    assert "FROM rmm_remediation_queue WHERE asset_id=%s" in body, (
        "_flush_remediation_queue must select queued rows by asset_id"
    )
    assert "FROM rmm_remediation_queue WHERE agent_id=%s" not in body, (
        "_flush_remediation_queue must NOT still filter rmm_remediation_queue by agent_id"
    )


def test_dispatch_next_product_filters_on_asset_id():
    """_dispatch_next_product's cve_patch_job SELECTs must filter on asset_id, never
    agent_id (which drifts on rename/re-enroll)."""
    body = _norm(_func_body(_read("rmm_gateway/main.py"), "_dispatch_next_product"))
    assert "j.asset_id=%s AND j.status='queued'" in body, (
        "_dispatch_next_product must filter cve_patch_job on j.asset_id"
    )
    assert "WHERE asset_id=%s AND status='queued' AND id=%s" in body, (
        "_dispatch_next_product single-job SELECT must filter on asset_id"
    )
    assert "j.agent_id=%s" not in body and "WHERE agent_id=%s" not in body, (
        "_dispatch_next_product must NOT filter cve_patch_job by agent_id anymore"
    )


def test_cve_sibling_close_is_asset_only():
    """The CVE sibling-close bulk UPDATE must drop the agent_id predicate and key on
    asset_id only (agent_id drifts; asset_id is stable)."""
    src = _norm(_read("rmm_gateway/main.py"))
    assert "agent_id=%s AND asset_id=%s" not in src, (
        "CVE sibling-close must no longer AND agent_id with asset_id — asset-only now"
    )


def test_asset_agents_map_and_live_ws_helper_exist():
    """The asset_id->agent_id reverse map and the _live_ws_for_asset helper (used by
    enqueue's live-push) must exist."""
    src = _read("rmm_gateway/main.py")
    assert re.search(r"^asset_agents\s*:\s*Dict\[int,\s*str\]\s*=", src, re.MULTILINE), (
        "asset_agents: Dict[int, str] map must be defined at module scope"
    )
    assert re.search(r"^def _live_ws_for_asset\(", src, re.MULTILINE), (
        "_live_ws_for_asset helper must be defined"
    )
