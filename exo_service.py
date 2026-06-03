"""Exchange Online app-only automation — the enforcement arm of the email-security
unblock/whitelist flow.

Auth: certificate-based app-only (no user, no secret in flight). The Tracker's Entra app
("Asset Tracker SOC2") holds the EXO RBAC "Transport Rules" management role (granted via
New-ServicePrincipal + New-ManagementRoleAssignment), so it can read and modify mail-flow
rules and nothing else. Connection settings come from .secrets.env:

    EXO_APP_ID, EXO_ORG, EXO_PFX_PATH, EXO_PFX_PASSWORD

Every write here is meant to run behind the approval engine (a gated action), never
unattended — releasing/whitelisting mail is a live change.

SECURITY: values that land in a PowerShell command (domain, rule name) are passed to pwsh
as ENVIRONMENT VARIABLES and referenced as $env:VAR inside the script — they are never
interpolated into the command text. Callers must still validate inputs (safe_domain /
known rule names) as defense in depth.
"""
import json
import logging
import os
import re
import subprocess

log = logging.getLogger("exo_service")

PWSH = "/usr/bin/pwsh"
_DEFAULT_TIMEOUT = 150

# A transport-rule name we will touch must look like the real ones ("SECURITY - Blacklist
# Domains B", "Content Rules"). Conservative allowlist of characters.
_RULE_RE = re.compile(r"^[A-Za-z0-9 ._\-/()]{1,128}$")
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9.\-]{0,251}[a-z0-9])?$")

# The central SCL=-1 allow rule (added to as a belt-and-suspenders whitelist).
ALLOW_RULE = "SECURITY - Allow Trusted Domains"


class ExoError(RuntimeError):
    pass


def safe_domain(domain: str) -> str:
    """Lowercase + validate a sender domain. Raises on anything that isn't a bare domain."""
    d = (domain or "").strip().lower().lstrip("@")
    if not _DOMAIN_RE.match(d) or ".." in d:
        raise ExoError(f"unsafe/invalid domain: {domain!r}")
    return d


def _safe_rule(name: str) -> str:
    n = (name or "").strip()
    if not _RULE_RE.match(n):
        raise ExoError(f"unsafe/invalid rule name: {name!r}")
    return n


def _conn_env() -> dict:
    need = ("EXO_APP_ID", "EXO_ORG", "EXO_PFX_PATH", "EXO_PFX_PASSWORD")
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise ExoError(f"missing EXO config in env: {', '.join(missing)}")
    return {k: os.environ[k] for k in need}


# Connect once per pwsh invocation, run the caller's script, always disconnect. The caller's
# script should emit a single JSON object on stdout via `_emit` so Python can parse it.
_PREAMBLE = r"""
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
function _emit($obj){ $obj | ConvertTo-Json -Depth 6 -Compress }
try {
  Import-Module ExchangeOnlineManagement -ErrorAction Stop
  $pw = ConvertTo-SecureString $env:EXO_PFX_PASSWORD -AsPlainText -Force
  Connect-ExchangeOnline -AppId $env:EXO_APP_ID -CertificateFilePath $env:EXO_PFX_PATH `
      -CertificatePassword $pw -Organization $env:EXO_ORG -ShowBanner:$false -ErrorAction Stop | Out-Null
} catch {
  _emit @{ ok=$false; stage="connect"; error=$_.Exception.Message }
  exit 0
}
try {
"""

_POSTAMBLE = r"""
} catch {
  _emit @{ ok=$false; stage="run"; error=$_.Exception.Message }
} finally {
  Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
}
"""


def _run_ps(body: str, extra_env: dict | None = None, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    """Run `body` (which must call _emit with a result object) inside a connected EXO session.
    Returns the parsed JSON dict. Raises ExoError on transport/timeouts/parse failure."""
    env = {**os.environ, **_conn_env(), **(extra_env or {})}
    script = _PREAMBLE + body + _POSTAMBLE
    try:
        proc = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                              env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ExoError(f"EXO command timed out after {timeout}s")
    out = (proc.stdout or "").strip()
    if not out:
        raise ExoError(f"no output from pwsh (rc={proc.returncode}): {(proc.stderr or '')[:400]}")
    # _emit may be preceded by stray warnings; take the last JSON line.
    line = next((l for l in reversed(out.splitlines()) if l.strip().startswith("{")), None)
    if not line:
        raise ExoError(f"unparseable pwsh output: {out[:400]}")
    try:
        return json.loads(line)
    except Exception as e:
        raise ExoError(f"bad JSON from pwsh: {e}: {line[:400]}")


# ── Read ops ────────────────────────────────────────────────────────────────────
def health() -> dict:
    """Connect + count transport rules. Returns {ok, count} or {ok:false, error}."""
    return _run_ps('_emit @{ ok=$true; count=@(Get-TransportRule -ResultSize Unlimited).Count }')


def get_rule(name: str) -> dict:
    rule = _safe_rule(name)
    body = r"""
  $r = Get-TransportRule -Identity $env:TARGET_RULE
  _emit @{ ok=$true; name=$r.Name; state=$r.State.ToString(); priority=$r.Priority;
           deleteMessage=$r.DeleteMessage;
           senderDomainIs=@($r.SenderDomainIs);
           exceptIfSenderDomainIs=@($r.ExceptIfSenderDomainIs) }
"""
    return _run_ps(body, {"TARGET_RULE": rule})


# ── Write ops (run behind approval) ───────────────────────────────────────────────
def add_domain_exception(rule_name: str, domain: str) -> dict:
    """Append `domain` to a blocking rule's ExceptIfSenderDomainIs so future mail from that
    domain bypasses the block. Idempotent (no-op if already present). Reads the current array
    and sets the full array back — Set-TransportRule overwrites multivalued props."""
    rule = _safe_rule(rule_name)
    dom = safe_domain(domain)
    body = r"""
  $r = Get-TransportRule -Identity $env:TARGET_RULE
  $existing = @()
  if ($r.ExceptIfSenderDomainIs) { $existing = @($r.ExceptIfSenderDomainIs | ForEach-Object { "$_" } | Where-Object { $_ }) }
  if ($existing -contains $env:TARGET_DOMAIN) {
    _emit @{ ok=$true; changed=$false; rule=$r.Name; domain=$env:TARGET_DOMAIN; note="already excepted" }
  } else {
    $new = [string[]]($existing + $env:TARGET_DOMAIN)
    Set-TransportRule -Identity $env:TARGET_RULE -ExceptIfSenderDomainIs $new
    _emit @{ ok=$true; changed=$true; rule=$r.Name; domain=$env:TARGET_DOMAIN; count=$new.Count }
  }
"""
    return _run_ps(body, {"TARGET_RULE": rule, "TARGET_DOMAIN": dom})


def remove_from_blacklist(rule_name: str, domain: str) -> dict:
    """Remove `domain` from a blacklist rule's SenderDomainIs. Idempotent."""
    rule = _safe_rule(rule_name)
    dom = safe_domain(domain)
    body = r"""
  $r = Get-TransportRule -Identity $env:TARGET_RULE
  $cur = @()
  if ($r.SenderDomainIs) { $cur = @($r.SenderDomainIs | ForEach-Object { "$_" } | Where-Object { $_ }) }
  if ($cur -notcontains $env:TARGET_DOMAIN) {
    _emit @{ ok=$true; changed=$false; rule=$r.Name; domain=$env:TARGET_DOMAIN; note="not present" }
  } else {
    $new = [string[]]@($cur | Where-Object { $_ -ne $env:TARGET_DOMAIN })
    Set-TransportRule -Identity $env:TARGET_RULE -SenderDomainIs $new
    _emit @{ ok=$true; changed=$true; rule=$r.Name; domain=$env:TARGET_DOMAIN; count=$new.Count }
  }
"""
    return _run_ps(body, {"TARGET_RULE": rule, "TARGET_DOMAIN": dom})


def add_to_allowlist(domain: str) -> dict:
    """Append `domain` to the central SCL=-1 allow rule (SECURITY - Allow Trusted Domains)."""
    return add_domain_exception_to_allow(ALLOW_RULE, domain)


def add_domain_exception_to_allow(rule_name: str, domain: str) -> dict:
    """Append `domain` to a rule's SenderDomainIs (used for the allow rule, where membership
    of SenderDomainIs is what grants SCL=-1)."""
    rule = _safe_rule(rule_name)
    dom = safe_domain(domain)
    body = r"""
  $r = Get-TransportRule -Identity $env:TARGET_RULE
  $cur = @()
  if ($r.SenderDomainIs) { $cur = @($r.SenderDomainIs | ForEach-Object { "$_" } | Where-Object { $_ }) }
  if ($cur -contains $env:TARGET_DOMAIN) {
    _emit @{ ok=$true; changed=$false; rule=$r.Name; domain=$env:TARGET_DOMAIN; note="already allowed" }
  } else {
    $new = [string[]]($cur + $env:TARGET_DOMAIN)
    Set-TransportRule -Identity $env:TARGET_RULE -SenderDomainIs $new
    _emit @{ ok=$true; changed=$true; rule=$r.Name; domain=$env:TARGET_DOMAIN; count=$new.Count }
  }
"""
    return _run_ps(body, {"TARGET_RULE": rule, "TARGET_DOMAIN": dom})
