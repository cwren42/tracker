"""Guards for the daily M365 employee-status reconcile.

Background: employee.m365_account_enabled / m365_validated_at are CACHED flags. They were
only written by the AD-driven employee sync, which (a) silently no-ops the M365 half when the
headless Graph fetch fails, and (b) never touches employees no longer in AD (offboarded users
like Jon Li) — so the flags drifted org-wide for ~3 months and an offboarded user still showed
M365-enabled=True. The fix adds a daily reconcile that refreshes the authoritative M365User
table and copies M365User.account_enabled -> employee.m365_account_enabled for is_current rows.

These tests are deliberately DB-free / Graph-free (CI has no Postgres) — they assert on the
scheduler wiring and the reconcile function's shape via source inspection, matching the
existing test suite's constraints.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _module_source(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _funcs(src):
    return {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}


def test_scheduler_registers_reconcile_job():
    """The scheduler must register the new job under a DISABLE_* guard, with a 24h-default
    interval and its own lock path — consistent with the other scheduled jobs."""
    src = _module_source("sync_scheduler.py")
    assert "DISABLE_M365_EMPLOYEE_RECONCILE" in src
    assert "M365_EMPLOYEE_RECONCILE_INTERVAL_HOURS" in src
    assert "TRACKER_M365_EMPLOYEE_RECONCILE_LOCK_PATH" in src
    assert "id='m365_employee_reconcile'" in src
    assert "run_m365_employee_reconcile_job" in src
    # default cadence is daily
    assert "'M365_EMPLOYEE_RECONCILE_INTERVAL_HOURS', '24'" in src


def test_reconcile_job_uses_its_own_lock_and_is_resilient():
    """The job must acquire its own file lock and must NOT abort the flag reconcile if the
    Graph refresh fails — Graph is best-effort, the reconcile off existing data is the point."""
    src = _module_source("sync_scheduler.py")
    funcs = _funcs(src)
    assert "run_m365_employee_reconcile_job" in funcs
    body = ast.get_source_segment(src, funcs["run_m365_employee_reconcile_job"])
    assert "M365_EMPLOYEE_RECONCILE_LOCK_PATH" in body
    assert "reconcile_employee_m365_flags" in body
    # the Graph sync is wrapped so a failure does not skip the reconcile
    assert "sync_m365_users" in body
    assert "resolve_identity_links" in body


def test_reconcile_function_exists_and_targets_cached_flags():
    """employees.reconcile_employee_m365_flags must write the two cached flags from the
    authoritative M365User table, scoped to is_current linked rows, and never raise."""
    src = _module_source("blueprints/employees.py")
    funcs = _funcs(src)
    assert "reconcile_employee_m365_flags" in funcs
    body = ast.get_source_segment(src, funcs["reconcile_employee_m365_flags"])
    assert "m365_account_enabled" in body
    assert "m365_validated_at" in body
    assert "is_current" in body
    assert "employee_id" in body
    # idempotent: only writes when the value actually differs
    assert "is not enabled" in body or "IS DISTINCT" in body or "!=" in body
    # never raises — has a rollback in the failure path
    assert "rollback" in body
