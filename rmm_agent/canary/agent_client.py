#!/usr/bin/env python3
"""Cirque RMM Agent - full telemetry + streaming shell + screenshot.

Env vars:
  RMM_GATEWAY_URL         wss://rmm.corp.cirque.com        (primary: internal LAN)
  RMM_GATEWAY_URL_PUBLIC  wss://rmm.cirquetools.com        (fallback: Cloudflare)
  RMM_TRACKER_URL         https://tracker.corp.cirque.com  (primary: internal LAN)
  RMM_TRACKER_URL_PUBLIC  https://tracker.cirquetools.com  (fallback: Cloudflare)
  RMM_AGENT_TOKEN    token from enroll script
  RMM_AGENT_ID       optional (defaults to hostname)
  RMM_SCREENSHOT     1 = enable screenshot capture (default: 0)

Connection strategy: on startup and after every disconnect the agent probes
tracker.corp.cirque.com:443 via TCP (2.5s timeout). If reachable, it uses the
internal LAN endpoints. If unreachable (off-network, or LAN gateway down), it
falls back to the public Cloudflare tunnel endpoints automatically.
"""

AGENT_VERSION = "2.9.39"

import asyncio
import base64
import hashlib
import io
import json
import os
import platform
import socket
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, Optional

import psutil
import websockets

# Force UTF-8 stdout/stderr so non-ASCII chars (software names, winget output, etc.)
# don't crash the service with a 'charmap' UnicodeEncodeError when running on Windows.
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def _patch_install_timeout() -> int:
    """Hard wall-clock budget (seconds) for one WUA install pass, after which the
    child process is killed and the job reported as failed/timeout. Default 35 min;
    override with the CIRQUE_PATCH_INSTALL_TIMEOUT env var (seconds, min 60)."""
    try:
        v = int(os.environ.get("CIRQUE_PATCH_INSTALL_TIMEOUT", "") or 0)
        if v >= 60:
            return v
    except Exception:
        pass
    return 35 * 60


def _ssl_ctx():
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    except Exception:
        return None


def _verifying_ssl_ctx():
    """Best-effort *chain-validating* SSL context for update fetches (R1).

    Validates the server certificate chain but tolerates a hostname mismatch
    (the internal cert may not match the URL we dial). This is defense-in-depth
    only: the RSA signature verification on the payload is the real integrity
    guard, so callers MUST fall back to the unverified _ssl_ctx() on any cert
    error rather than breaking updates for agents whose internal CA isn't
    chain-trusted. Returns None on failure (caller falls back)."""
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx
    except Exception:
        return None


def _open_update_url(req, timeout):
    """Open an update-related request, preferring a chain-verifying TLS context
    and falling back to the unverified context on any SSL/cert error (R1).

    Confidentiality is best-effort here; payload integrity is enforced
    separately by RSA signature verification. We therefore never let a cert
    failure block a LAN update."""
    import ssl as _ssl
    vctx = _verifying_ssl_ctx()
    if vctx is not None:
        try:
            return urllib.request.urlopen(req, context=vctx, timeout=timeout)
        except _ssl.SSLError as e:
            print(f"[update] verifying TLS fetch failed ({e}); falling back to unverified (sig still enforced)", flush=True)
        except urllib.error.URLError as e:
            # URLError wraps SSLCertVerificationError on most stacks
            if isinstance(getattr(e, "reason", None), _ssl.SSLError):
                print(f"[update] verifying TLS fetch failed ({e.reason}); falling back to unverified (sig still enforced)", flush=True)
            else:
                raise
    return urllib.request.urlopen(req, context=_ssl_ctx(), timeout=timeout)


# ---------------------------------------------------------------------------
# Deadlock-safe script runner (R: run_script child-handle deadlock)
# ---------------------------------------------------------------------------
# Root cause we are fixing: subprocess.run(..., capture_output=True) wires the
# child's stdout/stderr to OS pipes whose READ ends communicate() drains until
# EOF. If the launched script spawns a LONG-LIVED grandchild (e.g. cloudflared,
# a service restart, a detached helper) that INHERITS those pipe write handles,
# the pipes never hit EOF even after the direct child exits -> communicate()
# blocks forever -> the run_in_executor future never completes. Worse, on
# TimeoutExpired subprocess.run kills only the direct child and then RE-CALLS
# communicate() during cleanup, which re-blocks on the inherited handles. The
# WS receive loop awaits that future and stops accepting commands fleet-wide.
#
# Fix: never give the child an inheritable pipe. Redirect stdout/stderr to
# regular TEMP FILES, give the child its own process group / detached console,
# and close_fds so grandchildren inherit nothing of ours. We wait() (no pipe
# drain) and read the files back, so a lingering grandchild can NEVER stall us.
def _run_script_capture(argv, timeout: int):
    """Run argv, capture stdout/stderr via temp files (not inheritable pipes).

    Returns (returncode, stdout_text, stderr_text). On timeout the WHOLE process
    tree is killed (taskkill /T on Windows) and TimeoutExpired is raised. A
    lingering grandchild that inherits nothing of ours cannot block the wait.
    """
    import tempfile as _tmp, time as _time
    creationflags = 0
    if sys.platform == "win32":
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP -> the child (and anything
        # it detaches) is in its own group with no console handle of ours.
        creationflags = 0x08000000 | 0x00000200
    out_f = _tmp.NamedTemporaryFile(prefix="cirque_rs_out_", suffix=".txt", delete=False)
    err_f = _tmp.NamedTemporaryFile(prefix="cirque_rs_err_", suffix=".txt", delete=False)
    out_path, err_path = out_f.name, err_f.name
    out_f.close(); err_f.close()
    proc = None
    try:
        of = open(out_path, "wb"); ef = open(err_path, "wb")
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,   # no inherited stdin
                stdout=of, stderr=ef,        # files, NOT pipes -> no EOF dependency
                close_fds=True,              # grandchildren inherit none of our fds
                creationflags=creationflags,
            )
        finally:
            of.close(); ef.close()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the entire tree so the file handles get released and we don't
            # leak a runaway. Then re-raise so the caller reports a timeout.
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=15,
                                   creationflags=0x08000000)
                else:
                    proc.kill()
            except Exception:
                pass
            raise
        rc = proc.returncode
        try:
            stdout = open(out_path, "r", encoding="utf-8", errors="replace").read()
        except Exception:
            stdout = ""
        try:
            stderr = open(err_path, "r", encoding="utf-8", errors="replace").read()
        except Exception:
            stderr = ""
        return rc, stdout, stderr
    finally:
        for p in (out_path, err_path):
            try:
                os.unlink(p)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Self-update payload signature verification (CANARY only)
# Server signs the served agent_client.py / tray.py with RSA-3072 PKCS1v15
# SHA-256; we verify the detached hex signature before swapping any payload.
# Fail-closed: no sig / bad sig / verify error => do NOT apply the update.
# ---------------------------------------------------------------------------
_UPDATE_PUB_E = 65537
_UPDATE_PUB_N = int("c60971a6afa233df8868968ff69b438104eb928ae33c79089eb855d96f148a43db05216929f2534beea5ccbfcfd3f905fb4c8f259d91958dd955ea3c6be1118f3ffea3c72ebeb5a6d1b1ec6a88ab959406f78e38c801d88909874fd7497b08772b987c5c7652ded9ef928b17544a4bfc7d52e1889f20cf8293e778cf64eabac8c3c85600d0af5874270be30a049fc0fecc50e1584a7899976398bba82dcacd82211fbe210012de186be7bbd496f1eef3d7a1cd7f8f3165bf00eab9fa60f4b25af628f54b9b7790258a93cd4edf6c7274e31e8708ae4a83511d9bf69bdd22e4794f1ec11fa378c777300ab238b93bba5283d629259ead16413df74a3b7318804d11f7a93c2af48b157e73bb780059cbd4fc0d0fa55a7ea65c03ba496336e6fc86e2101221f44945980cc9cbc5a9a0c9a28da0f34d2852f219e22e461e33ffda1c922351e738d633adf82b4f9f29f2f5a2fa859e6aa4b49d64ab3c5896bbcfd0e554b191cfcd4c83ec7b730b66e9f988df75ca3ef69def7ad8307ec3d1a958a91f", 16)
_SHA256_DER = bytes.fromhex("3031300d060960864801650304020105000420")  # PKCS#1 v1.5 SHA-256 DigestInfo


def _verify_update_sig(data: bytes, sig_hex: str) -> bool:
    try:
        sig = bytes.fromhex(sig_hex)
        k = (_UPDATE_PUB_N.bit_length() + 7) // 8
        if len(sig) != k:
            return False
        m = pow(int.from_bytes(sig, "big"), _UPDATE_PUB_E, _UPDATE_PUB_N)
        em = m.to_bytes(k, "big")
        if em[0:2] != b"\x00\x01":
            return False
        try:
            sep = em.index(b"\x00", 2)
        except ValueError:
            return False
        if sep < 10 or any(b != 0xFF for b in em[2:sep]):
            return False
        return em[sep + 1:] == _SHA256_DER + hashlib.sha256(data).digest()
    except Exception:
        return False


def _fetch_update_sig(sig_url: str, ctx=None, timeout: int = 15) -> str:
    """Fetch a detached signature from a *-sig endpoint. Returns the hex sig
    string, or '' on any error / missing sig (caller fails closed).

    Uses the verifying-with-fallback TLS opener (R1); the legacy `ctx` arg is
    ignored and kept only for call-site compatibility."""
    try:
        req = urllib.request.Request(sig_url, headers={"User-Agent": f"CirqueRMM/{AGENT_VERSION}"})
        with _open_update_url(req, timeout) as r:
            obj = json.loads(r.read())
        return str(obj.get("sig") or "")
    except Exception as e:
        print(f"[update] signature fetch failed: {e}", flush=True)
        return ""


def _ps_json(script: str, timeout: int = 15):
    """Run a PowerShell one-liner and return parsed JSON, or None on failure."""
    try:
        # Decode with errors='replace' so a single byte that's undefined in the
        # locale codepage doesn't raise UnicodeDecodeError -> None -> the caller
        # seeing EMPTY. That silently dropped software inventory on big boxes
        # (BRIAN-MSI, 616 apps: a ~90KB dump is far likelier to contain an odd
        # publisher/name byte). NOTE: do NOT set [Console]::OutputEncoding here --
        # the agent launches powershell with CREATE_NO_WINDOW (no console), so the
        # setter throws "handle is invalid" and kills the whole script. Plain
        # text=True + errors='replace' decodes with the locale codepage and never
        # raises, which is what we want.
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, errors="replace", timeout=timeout,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        out = (r.stdout or "").strip()
        if r.returncode != 0 and not out:
            err = r.stderr.strip()
            if err:
                print(f'[ps_json] stderr: {err[:300]}', flush=True)
        if out and out != "null":
            return json.loads(out)
    except Exception:
        pass
    return None


# Sentinel returned by _ps_json_proc when the child process exceeds its hard
# deadline and is killed. Distinguished from None (no/garbage output) so callers
# can report a structured "timeout" failure rather than a generic error.
_PS_TIMEOUT = object()


def _kill_proc_tree(proc) -> None:
    """Kill *proc* and every descendant. WUA's Install() runs the actual work in
    out-of-process workers (TrustedInstaller / wuauserv-spawned helpers) that a
    plain proc.kill() on powershell.exe would orphan, so we walk the tree via
    psutil. Best-effort: any failure is swallowed — the goal is only to free the
    executor thread, not to guarantee the WU worker dies."""
    try:
        parent = psutil.Process(proc.pid)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return
    procs = []
    try:
        procs = parent.children(recursive=True)
    except Exception:
        pass
    procs.append(parent)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    try:
        psutil.wait_procs(procs, timeout=5)
    except Exception:
        pass


def _ps_json_proc(script: str, timeout: int = 15):
    """Run a PowerShell script in a CHILD PROCESS with a HARD wall-clock deadline.

    Unlike _ps_json (which relies on subprocess.run's own timeout), this launches
    the process with Popen + communicate(timeout=) and KILLS THE WHOLE PROCESS
    TREE on expiry. This matters for the Windows Update COM Install() path: that call
    can block effectively forever on a broken/large WU backlog, and subprocess.run's
    timeout-kill does not reliably reap the out-of-process WU workers, so the calling
    (executor) thread can stay wedged far longer than the nominal timeout. Killing the
    tree here guarantees this thread returns within ~timeout seconds no matter what
    Windows Update is doing, which is what frees the agent's command executor.

    Returns:
      * parsed JSON dict on success,
      * _PS_TIMEOUT if the deadline was hit (child killed),
      * None on any other failure / unparseable output.
    """
    proc = None
    try:
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception as e:
        print(f"[ps_proc] launch failed: {e}", flush=True)
        return None
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"[ps_proc] hard timeout after {timeout}s -- killing process tree "
                  f"(pid={proc.pid})", flush=True)
            _kill_proc_tree(proc)
            # Drain pipes so the killed child's handles are released and this
            # thread does not block on a half-read pipe.
            try:
                proc.communicate(timeout=10)
            except Exception:
                pass
            return _PS_TIMEOUT
        out = (out or "").strip()
        if proc.returncode != 0 and not out:
            e = (err or "").strip()
            if e:
                print(f"[ps_proc] stderr: {e[:300]}", flush=True)
        if out and out != "null":
            try:
                return json.loads(out)
            except Exception:
                return None
        return None
    finally:
        # Guarantee no lingering handle on the executor thread.
        if proc is not None and proc.poll() is None:
            _kill_proc_tree(proc)


# Internal LAN hostnames -- only resolvable via corporate internal DNS.
# External DNS has no record for these, so a successful TCP connect proves we're on LAN.
_LAN_GATEWAY_HOST = "rmm.corp.cirque.com"
_LAN_TRACKER_URL  = "https://tracker.corp.cirque.com"
_LAN_GATEWAY_URL  = "wss://rmm.corp.cirque.com"


def _can_reach(host: str, port: int = 443, timeout: float = 2.5) -> bool:
    """Return True only if TCP connects AND the TLS certificate is valid for *host*.

    A raw TCP-only probe is insufficient: external hosts (e.g. Squarespace) may
    respond on port 443 for corp.cirque.com domain names because the public DNS
    record points there. We verify the TLS cert so we only return True when the
    genuine internal server (which holds a cert issued for that hostname) answers.
    """
    try:
        import ssl
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host):
                return True
    except OSError:
        return False
    except Exception:
        # Any TLS error (hostname mismatch, expired cert, etc.) means it's not the real host
        return False


def _resolve_urls(fallback_tracker: str, fallback_gateway: str) -> tuple:
    """Probe the internal LAN gateway hostname directly.

    Uses a TLS-verified probe so that external hosts that respond on TCP:443
    (e.g. Squarespace serving the public cirque.com website) are not mistaken
    for the internal gateway. Only the genuine corp server has a cert that
    matches rmm.corp.cirque.com.
    """
    if _can_reach(_LAN_GATEWAY_HOST):
        print(f"[agent] LAN reachable ({_LAN_GATEWAY_HOST}) -- using internal endpoints", flush=True)
        return _LAN_TRACKER_URL, _LAN_GATEWAY_URL
    print(f"[agent] LAN unreachable -- falling back to Cloudflare endpoints", flush=True)
    return fallback_tracker, fallback_gateway


# ---------------------------------------------------------------------------
# Self-update
# ---------------------------------------------------------------------------

def sync_rustdesk_id(tracker_url: str, agent_id: str, token: str) -> None:
    """Read the local RustDesk peer ID and send it to the tracker so
    asset.rustdesk_id is kept up-to-date automatically.
    Silently ignores all errors (RustDesk may not be installed)."""
    try:
        import re as _re
        import glob as _glob
        # Candidate config paths (Windows + Linux/Mac)
        candidates = []
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(os.path.join(appdata, "RustDesk", "config", "RustDesk.toml"))
        # Also try LocalAppData / programdata layouts
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            candidates.append(os.path.join(localappdata, "RustDesk", "config", "RustDesk.toml"))
        # SYSTEM / service account profiles (always check these regardless of env)
        candidates += [
            # MSI service runs as LocalService
            r'C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config\RustDesk.toml',
            r'C:\Windows\ServiceProfiles\NetworkService\AppData\Roaming\RustDesk\config\RustDesk.toml',
            r'C:\Windows\System32\config\systemprofile\AppData\Roaming\RustDesk\config\RustDesk.toml',
            r'C:\ProgramData\RustDesk\config\RustDesk.toml',
            r'C:\Windows\ServiceProfiles\LocalSystem\AppData\Roaming\RustDesk\config\RustDesk.toml',
        ]
        # All user profiles (covers interactive sessions)
        for p in _glob.glob(r'C:\Users\*\AppData\Roaming\RustDesk\config\RustDesk.toml'):
            candidates.append(p)
        # Linux / Mac
        home = os.path.expanduser("~")
        candidates.append(os.path.join(home, ".config", "rustdesk", "RustDesk.toml"))
        candidates.append("/etc/rustdesk/RustDesk.toml")

        peer_id = None
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                m = _re.search(r'^id\s*=\s*["\']?([0-9a-zA-Z_\-]+)["\']?', content, _re.MULTILINE)
                if m:
                    peer_id = m.group(1).strip()
                    break
            except Exception:
                continue

        # Fallback 1: check cached peer ID file (avoids repeated --get-id on every start)
        if not peer_id and os.path.isfile(_RUSTDESK_PEER_ID_FILE):
            try:
                cached = open(_RUSTDESK_PEER_ID_FILE, 'r', encoding='utf-8').read().strip()
                if _re.match(r'^[0-9a-zA-Z_\-]+$', cached):
                    peer_id = cached
            except Exception:
                pass

        # Fallback 2: ask the running RustDesk process via --get-id
        # Only run if TOML not found AND cache is empty/missing
        if not peer_id:
            try:
                exe = _rustdesk_exe()
                if exe and os.path.isfile(exe):
                    import subprocess as _sp
                    r = _sp.run([exe, "--get-id"], capture_output=True, text=True, timeout=10)
                    out = (r.stdout or "").strip()
                    if _re.match(r'^[0-9a-zA-Z_\-]+$', out):
                        peer_id = out
                        # Cache it so we don't need to call --get-id again next restart
                        try:
                            os.makedirs(os.path.dirname(_RUSTDESK_PEER_ID_FILE), exist_ok=True)
                            open(_RUSTDESK_PEER_ID_FILE, 'w').write(peer_id)
                        except Exception:
                            pass
                        print(f"[rustdesk] Got peer ID via --get-id: {peer_id} (cached)", flush=True)
            except Exception as ge:
                print(f"[rustdesk] --get-id fallback failed: {ge}", flush=True)

        if not peer_id:
            return  # RustDesk not installed or ID not yet assigned

        # Get (or create) the permanent access password and include it in sync
        rd_password = _ensure_rustdesk_password()

        url = f"{tracker_url}/api/rmm/rustdesk-sync/{agent_id}?token={token}"
        import urllib.request as _ur
        sync_payload: dict = {"rustdesk_id": peer_id}
        if rd_password:
            sync_payload["rustdesk_password"] = rd_password
        payload = json.dumps(sync_payload).encode()
        req = _ur.Request(url, data=payload, headers={"Content-Type": "application/json", "User-Agent": f"CirqueRMM/{AGENT_VERSION}"}, method="POST")
        with _ur.urlopen(req, timeout=10, context=_ssl_ctx()) as resp:
            body = json.loads(resp.read())
            if body.get("changed"):
                print(f"[rustdesk] Synced peer ID {peer_id} to tracker (asset {body.get('asset_id')})", flush=True)
    except Exception as e:
        print(f"[rustdesk] sync skipped: {e}", flush=True)


# RustDesk relay server + key are NOT hardcoded in the agent. They live only in
# server env (.secrets.env) and are fetched at runtime over the authenticated
# agent endpoint, cached once per process here.
_RUSTDESK_RELAY_CACHE = None   # None = not yet fetched; (server, key) once resolved


def _get_rustdesk_relay(tracker_url: str, agent_id: str, token: str):
    """Fetch the RustDesk relay (server, key) from the tracker, authenticated by
    agent_id + token. Cached per-process so we only hit the server once.

    URL failover: tries the resolved tracker_url first (LAN primary), then the
    public Cloudflare fallback. Returns (server, key) on success, or (None, None)
    on any failure / empty config so callers can skip RustDesk configuration."""
    global _RUSTDESK_RELAY_CACHE
    if _RUSTDESK_RELAY_CACHE is not None:
        return _RUSTDESK_RELAY_CACHE

    import urllib.request as _ur
    # Candidate base URLs: resolved primary, then public fallback (dedup, keep order).
    fallback = os.environ.get("RMM_TRACKER_URL_PUBLIC", "https://tracker.cirquetools.com").rstrip("/")
    bases = []
    for b in (tracker_url, fallback):
        b = (b or "").rstrip("/")
        if b and b not in bases:
            bases.append(b)

    for base in bases:
        try:
            url = f"{base}/api/rmm/rustdesk-config/{agent_id}?token={token}"
            req = _ur.Request(url, headers={"User-Agent": f"CirqueRMM/{AGENT_VERSION}"})
            with _ur.urlopen(req, timeout=10, context=_ssl_ctx()) as resp:
                body = json.loads(resp.read())
            server = (body.get("server") or "").strip()
            key = (body.get("key") or "").strip()
            if server and key:
                _RUSTDESK_RELAY_CACHE = (server, key)
                print(f"[rustdesk] relay config fetched from {base} (server={server})", flush=True)
                return _RUSTDESK_RELAY_CACHE
            # Reachable but env unset server-side -- don't keep retrying other bases.
            print("[rustdesk] relay config unavailable -- skipping", flush=True)
            _RUSTDESK_RELAY_CACHE = (None, None)
            return _RUSTDESK_RELAY_CACHE
        except Exception as e:
            print(f"[rustdesk] relay config fetch failed from {base}: {e}", flush=True)
            continue

    # All bases failed -- don't cache (transient network); retry next cycle.
    return (None, None)


# File on disk where we persist the plaintext password so it survives restarts.
_RUSTDESK_PASS_FILE    = r'C:\CirqueRMM\rustdesk_pass.txt'
_RUSTDESK_PEER_ID_FILE = r'C:\CirqueRMM\rustdesk_peer_id.txt'  # cached so --get-id only runs once

# Tray app API key (create_tickets scope) -- baked in at build time
_TRAY_API_KEY = 'crmm_tray_60bb6c2cfc8e5bb56cd27eafcc766044609271533237fcf8'
_tray_setup_done     = False  # only run _setup_tray once per agent process
_rustdesk_setup_done = False  # only do full rustdesk ensure once per process
_periodic_update_started = False  # only spawn the 4h self-update task once per process
_warp_logged_state = None  # last logged state string, to avoid log spam each connect
_warp_worker_launched_at = 0.0  # monotonic ts of last SYSTEM-worker launch (cooldown)
_WARP_WORKER_COOLDOWN = 1800.0  # don't relaunch the enroll worker more than once / 30 min
_WARP_CONFIG_CACHE = None  # None=not fetched; (org, client_id, client_secret) or (None,...)

# Per-agent behaviour flags pushed by server on connect via agent_config message.
# Servers set these to True so neither RustDesk nor the systray are installed.
_disable_rustdesk = False
_disable_tray     = False
_TRAY_PY_PATH = r'C:\CirqueRMM\tray.py'
_TRAY_CFG_PATH = r'C:\CirqueRMM\tray_config.json'


def _setup_tray(tracker_url: str, agent_id: str, token: str) -> None:
    """Download tray.py, write tray_config.json, install pip deps, and create
    a per-user Startup shortcut so the tray runs at login.
    Safe to call repeatedly -- skips steps already done."""
    import subprocess as _sp, glob as _glob
    try:
        agent_dir = os.path.dirname(os.path.abspath(__file__))

        # 1. Download / refresh tray.py from tracker
        tray_url = f"{tracker_url}/rmm/agent/tray?agent_id={agent_id}&token={token}"
        import urllib.request as _ur, ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        try:
            req = _ur.Request(tray_url, headers={"User-Agent": f"CirqueRMM/{AGENT_VERSION}"})
            with _open_update_url(req, 20) as resp:
                new_src = resp.read()
            # Only write if changed
            existing = b''
            if os.path.isfile(_TRAY_PY_PATH):
                with open(_TRAY_PY_PATH, 'rb') as fh:
                    existing = fh.read()
            if new_src != existing:
                # Signature gate (CANARY): verify the served tray.py before writing.
                # Fail-closed -- no sig / bad sig leaves the existing tray.py in place.
                tray_sig_url = f"{tracker_url}/rmm/agent/tray-sig?agent_id={agent_id}&token={token}"
                tray_sig = _fetch_update_sig(tray_sig_url, ctx)
                if not tray_sig:
                    print('[tray] No signature returned for tray.py -- skipping update (fail-closed)', flush=True)
                elif not _verify_update_sig(new_src, tray_sig):
                    print('[tray] Signature verification FAILED for tray.py -- skipping update (fail-closed)', flush=True)
                else:
                    os.makedirs(os.path.dirname(_TRAY_PY_PATH), exist_ok=True)
                    with open(_TRAY_PY_PATH, 'wb') as fh:
                        fh.write(new_src)
                    print('[tray] Signature verified -- tray.py updated', flush=True)
        except Exception as dl_err:
            print(f'[tray] download failed: {dl_err}', flush=True)

        # 2. Get asset info for the config (asset_id, asset_tag)
        asset_id_num = ''
        asset_tag    = ''
        it_contact   = 'IT Support'
        try:
            info_url = f"{tracker_url}/api/rmm/agent-info/{agent_id}?token={token}"
            req2 = _ur.Request(info_url, headers={"User-Agent": f"CirqueRMM/{AGENT_VERSION}"})
            with _ur.urlopen(req2, timeout=10, context=ctx) as r2:
                info = json.loads(r2.read())
            asset_id_num = str(info.get('asset_id') or '')
            asset_tag    = str(info.get('asset_tag') or '')
        except Exception:
            pass

        # 3. Write tray_config.json
        cfg = {
            'tracker_url':  tracker_url,
            'tray_api_key': _TRAY_API_KEY,
            'agent_id':     agent_id,
            'asset_id':     asset_id_num,
            'asset_tag':    asset_tag,
            'it_contact':   it_contact,
        }
        os.makedirs(os.path.dirname(_TRAY_CFG_PATH), exist_ok=True)
        with open(_TRAY_CFG_PATH, 'w', encoding='utf-8') as fh:
            json.dump(cfg, fh, indent=2)

        # 4. Install pystray + pillow into the user-accessible Python
        #    Use the same Python the agent runs under, then also try a user-level install
        pip_targets = [
            [sys.executable, '-m', 'pip', 'install', '--quiet', 'pystray', 'pillow'],
        ]
        # Find Python installs for interactive users (C:\Users\*\AppData\Local\Programs\Python)
        import glob as _glob
        for py in _glob.glob(r'C:\Users\*\AppData\Local\Programs\Python\Python*\python.exe'):
            pip_targets.append([py, '-m', 'pip', 'install', '--quiet', 'pystray', 'pillow'])
        for cmd in pip_targets:
            try:
                _sp.run(cmd, capture_output=True, timeout=120)
            except Exception:
                pass

        # 5. Create Startup shortcut and (re-)launch tray now
        _create_startup_shortcut_task()

    except Exception as e:
        print(f'[tray] _setup_tray error: {e}', flush=True)


def _tray_pids(_sp):
    """Return a list of PIDs for running pythonw.exe processes whose command
    line references tray.py. Returns [] on any failure (treat as 'none found')."""
    try:
        _r = _sp.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             'Get-WmiObject Win32_Process | Where-Object { $_.Name -eq "pythonw.exe" '
             '-and $_.CommandLine -like "*tray.py*" } | '
             'ForEach-Object { $_.ProcessId }'],
            capture_output=True, text=True, timeout=15,
        )
        if _r.returncode != 0:
            return []
        return [int(x) for x in (_r.stdout or '').split() if x.strip().isdigit()]
    except Exception:
        return []


def _create_startup_shortcut_task():
    """Write tray startup entries to All-Users and per-user Startup folders,
    then (re-)launch the tray immediately in the current interactive user's session.

    HARDENING (BRIAN-MSI): we never kill the running tray before we've confirmed a
    viable relaunch path. Order is: (1) resolve a usable pythonw.exe + tray.py and
    confirm both exist, (2) ALWAYS (re)write the Startup VBS so a future login is
    armed, (3) only if a viable launch path exists do we kill the old tray and start
    the new one, (4) verify the new tray actually came up. If no usable interpreter /
    tray.py is found we leave any running tray untouched -- we never end trayless."""
    import subprocess as _sp, glob as _glob, time as _time

    _tray_py = r'C:\CirqueRMM\tray.py'

    # -- 1. Resolve a usable pythonw.exe -------------------------------------
    pythonw_path = ''
    _candidate = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    if os.path.isfile(_candidate):
        pythonw_path = _candidate
    if not pythonw_path:
        for _py in _glob.glob(r'C:\Users\*\AppData\Local\Programs\Python\Python*\pythonw.exe'):
            pythonw_path = _py; break
    if not pythonw_path:
        for _py in _glob.glob(r'C:\Program Files\Python*\pythonw.exe'):
            pythonw_path = _py; break
    if not pythonw_path:
        for _py in _glob.glob(r'C:\Python*\pythonw.exe'):
            pythonw_path = _py; break
    if not pythonw_path:
        try:
            _r = _sp.run(['where', 'pythonw.exe'], capture_output=True, text=True, timeout=5)
            if _r.returncode == 0:
                for _line in _r.stdout.strip().splitlines():
                    _line = _line.strip()
                    if _line and os.path.isfile(_line):
                        pythonw_path = _line; break
        except Exception:
            pass

    # A launch is "viable" only if BOTH the interpreter and tray.py exist on disk.
    _launch_viable = bool(pythonw_path) and os.path.isfile(pythonw_path) and os.path.isfile(_tray_py)
    if not _launch_viable:
        if not pythonw_path:
            print('[tray] No pythonw.exe found -- NOT killing running tray; arming Startup VBS only', flush=True)
        elif not os.path.isfile(_tray_py):
            print(f'[tray] tray.py missing at {_tray_py} -- NOT killing running tray; arming Startup VBS only', flush=True)

    # -- 2. ALWAYS (re)write the Startup VBS so login-relaunch is armed -------
    # The VBS resolves pythonw.exe at LAUNCH time (robust to a stale interpreter
    # path that moved after write time, e.g. an MSI/embedded Python relocation).
    # It prefers our known-current interpreter (passed as a hint) and falls back
    # to the same discovery order the agent uses, so a moved path can't no-op it.
    _vbs_hint = (pythonw_path or '').replace('"', '""')
    _vbs = _build_tray_vbs(_vbs_hint, _tray_py)
    # All-Users startup
    _common_startup = r'C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup'
    try:
        os.makedirs(_common_startup, exist_ok=True)
        with open(os.path.join(_common_startup, 'CirqueTray.vbs'), 'w', encoding='utf-8') as _fh:
            _fh.write(_vbs)
        print('[tray] Wrote VBS to CommonStartup', flush=True)
    except Exception as _e:
        print(f'[tray] CommonStartup VBS write failed: {_e}', flush=True)
    # Per-user startup folders
    _skip = {'All Users', 'Default', 'Default User', 'Public'}
    for _profile in _glob.glob(r'C:\Users\*'):
        if not os.path.isdir(_profile):  # skip files like desktop.ini
            continue
        _uname = os.path.basename(_profile)
        if _uname in _skip:
            continue
        _user_startup = os.path.join(
            _profile,
            r'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'
        )
        try:
            os.makedirs(_user_startup, exist_ok=True)
            with open(os.path.join(_user_startup, 'CirqueTray.vbs'), 'w', encoding='utf-8') as _fh:
                _fh.write(_vbs)
            print(f'[tray] Wrote VBS to startup for user: {_uname}', flush=True)
        except Exception as _e:
            print(f'[tray] User startup write failed ({_uname}): {_e}', flush=True)

    # If we can't launch, STOP here. The currently-running tray (if any) stays
    # alive, and the Startup VBS we just wrote will (re)launch on next login.
    if not _launch_viable:
        print('[tray] Relaunch not viable -- left running tray (if any) in place; Startup VBS armed', flush=True)
        return

    # -- 3. Kill the old tray, THEN launch the new one -----------------------
    # Only reached when we have a viable interpreter + tray.py, so we never end
    # up trayless: a replacement launch immediately follows the kill.
    _pre_pids = set(_tray_pids(_sp))
    try:
        _sp.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             'Get-WmiObject Win32_Process | Where-Object { $_.Name -eq "pythonw.exe" '
             '-and $_.CommandLine -like "*tray.py*" } | '
             'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass
    _time.sleep(2)

    # -- 4. Immediate launch in the active interactive user's session ---------
    # Use New-ScheduledTaskPrincipal with the actual logged-in username --
    # more reliable than generic InteractiveToken when called from session 0.
    _py_escaped = pythonw_path.replace("'", "''")
    ps_launch = (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        # 1) Console interactive user via WMI (returns domain\\user, but is NULL
        #    over RDP -- it only reflects the physical console session).
        "$username = (Get-WmiObject -Class Win32_ComputerSystem).UserName\n"
        # 2) RDP-aware fallback: parse `query user` (a.k.a. quser). We accept a
        #    session in state 'Active' OR 'Disc' (Disconnected) that has a real
        #    username, so installing over RDP -- including a disconnected /
        #    reconnecting RDP session -- still targets the right user instead of
        #    logging 'No interactive user found'. quser columns are fixed-width;
        #    the leading '>' marks the current session and USERNAME is col 1.
        "if (-not $username) {\n"
        "    $qu = & query user 2>$null\n"
        "    if (-not $qu) { $qu = & quser 2>$null }\n"
        "    $cand = $null\n"
        "    foreach ($ln in ($qu | Select-Object -Skip 1)) {\n"
        "        $row = ($ln -replace '^>','').Trim()\n"
        "        if (-not $row) { continue }\n"
        "        $cols = $row -split '\\s+'\n"
        "        $u = $cols[0]\n"
        "        if (-not $u -or $u -eq 'USERNAME') { continue }\n"
        "        # State is 'Active'/'Disc'; it shifts column when SESSIONNAME is\n"
        "        # blank (disconnected). Match the state token anywhere in the row.\n"
        "        if ($row -match '\\bActive\\b') { $cand = $u; break }\n"
        "        if (-not $cand -and $row -match '\\bDisc\\b') { $cand = $u }\n"
        "    }\n"
        "    if ($cand) {\n"
        "        # quser shows the bare SAM name; resolve to domain\\user so the\n"
        "        # task principal works on a domain box. Fall back to bare name.\n"
        "        $dom = $env:USERDOMAIN\n"
        "        if ($dom) { $username = \"$dom\\$cand\" } else { $username = $cand }\n"
        "    }\n"
        "}\n"
        "if (-not $username) { Write-Host 'No interactive user found'; exit 1 }\n"
        "Write-Host \"Targeting user: $username\"\n"
        "$action   = New-ScheduledTaskAction -Execute '" + _py_escaped + "' "
        "-Argument 'C:\\CirqueRMM\\tray.py' -WorkingDirectory 'C:\\CirqueRMM'\n"
        "$prin     = New-ScheduledTaskPrincipal -UserId $username "
        "-LogonType Interactive -RunLevel Limited\n"
        "$regErr = $null\n"
        "Register-ScheduledTask -TaskName 'CirqueTrayLaunch' "
        "-Action $action -Principal $prin -Force -ErrorVariable regErr | Out-Null\n"
        "if ($regErr) { Write-Host \"Tray task error: $regErr\" } else {\n"
        "  Start-ScheduledTask -TaskName 'CirqueTrayLaunch' -ErrorAction SilentlyContinue\n"
        "  Write-Host 'Tray launch task started'\n"
        "}\n"
    )
    _result = _sp.run(
        ['powershell', '-NoProfile', '-Command', ps_launch],
        capture_output=True, text=True, timeout=25,
    )
    _out = (_result.stdout or '').strip()
    _err = (_result.stderr or '').strip()
    if _out:
        print(f'[tray] Launch: {_out}', flush=True)
    if _err and _result.returncode != 0:
        print(f'[tray] Launch stderr: {_err[:300]}', flush=True)

    # -- 5. Verify the relaunch actually came up (poll up to ~5s) ------------
    # A new tray PID (not in the pre-kill set) means the interactive launch
    # succeeded. If none appears we log it; the Startup VBS armed in step 2
    # is the safety net for the next login.
    _came_up = False
    for _ in range(5):
        _time.sleep(1)
        _now = set(_tray_pids(_sp))
        if _now - _pre_pids:
            _came_up = True
            break
        if _now:  # any tray at all (e.g. relaunch reused detection) counts as alive
            _came_up = True
            break
    if _came_up:
        print('[tray] Relaunch confirmed: tray.py process is running', flush=True)
    else:
        print('[tray] WARNING: relaunch not confirmed within ~5s -- '
              'Startup VBS is armed to recover on next login', flush=True)


def _build_tray_vbs(pythonw_hint: str, tray_py: str) -> str:
    """Build a MINIMAL Startup VBScript that launches the tray silently (no console).

    Deliberately tiny — single-line statements only, no Option Explicit / Dim /
    Array / nested loops / line-continuations. A prior version did launch-time
    path-probing with all of those and compiled to INVALID VBScript (WSH "syntax
    error" popup on every login/reboot). The agent resolves a valid pythonw at
    write time (pythonw_hint) and rewrites this file on every reconnect, so a
    stale path self-heals on the next agent run; we keep one simple bare-pythonw
    fallback for safety."""
    _hint = (pythonw_hint or 'pythonw.exe').replace('"', '""')
    _tray_for_vbs = tray_py.replace('"', '""')
    return (
        'Set oShell = CreateObject("WScript.Shell")\r\n'
        'Set oFSO = CreateObject("Scripting.FileSystemObject")\r\n'
        f'sPy = "{_hint}"\r\n'
        'If Not oFSO.FileExists(sPy) Then sPy = "pythonw.exe"\r\n'
        f'oShell.Run Chr(34) & sPy & Chr(34) & " ""{_tray_for_vbs}""", 0, False\r\n'
    )

def _ensure_rustdesk_password() -> str:
    """Return the permanent RustDesk access password for this machine.
    If no password has been set yet, generate a random 12-char one,
    save it to disk, and configure RustDesk to use it.
    Always returns the plaintext password string (or '' on failure)."""
    import string as _string, secrets as _secrets, subprocess as _sp
    try:
        # 1. Try to read an existing password from the local file
        pw = ''
        if os.path.isfile(_RUSTDESK_PASS_FILE):
            with open(_RUSTDESK_PASS_FILE, 'r', encoding='utf-8') as fh:
                pw = fh.read().strip()

        # 2. Generate a new password if missing / too short
        if len(pw) < 8:
            alphabet = _string.ascii_letters + _string.digits
            pw = ''.join(_secrets.choice(alphabet) for _ in range(12))
            # Persist before calling --password so we don't lose it
            os.makedirs(os.path.dirname(_RUSTDESK_PASS_FILE), exist_ok=True)
            with open(_RUSTDESK_PASS_FILE, 'w', encoding='utf-8') as fh:
                fh.write(pw)
            print(f'[rustdesk] Generated new access password', flush=True)

        # 3. Set the password in RustDesk (idempotent -- safe to call every time)
        exe = _rustdesk_exe()
        if exe and os.path.isfile(exe):
            _sp.run([exe, '--password', pw], capture_output=True, timeout=10)

        return pw
    except Exception as e:
        print(f'[rustdesk] _ensure_rustdesk_password error: {e}', flush=True)
        return ''

_RUSTDESK_IDENTITY_PATHS = [
    r'C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config\RustDesk.toml',
    r'C:\Windows\ServiceProfiles\NetworkService\AppData\Roaming\RustDesk\config\RustDesk.toml',
    r'C:\Windows\System32\config\systemprofile\AppData\Roaming\RustDesk\config\RustDesk.toml',
    r'C:\ProgramData\RustDesk\config\RustDesk.toml',
]


def _fix_rustdesk_identity(rd_server: str) -> bool:
    """If RustDesk registered with the public server instead of ours,
    delete the identity file and restart the service so it re-registers
    with our private server.  Returns True if a reset was performed.

    rd_server is the fetched relay host; if empty/None we cannot tell which
    server is "ours", so we skip the identity check entirely."""
    import re as _re, subprocess as _sp, time as _time
    if not rd_server:
        print('[rustdesk] relay config unavailable -- skipping identity check', flush=True)
        return False
    for path in _RUSTDESK_IDENTITY_PATHS:
        if not os.path.isfile(path):
            continue
        try:
            content = open(path, encoding='utf-8', errors='replace').read()
            # Find the [keys_confirmed] section
            m = _re.search(r'\[keys_confirmed\](.*?)(?:\[|\Z)', content, _re.DOTALL)
            if m:
                confirmed = m.group(1).strip()
                if not confirmed:
                    # Empty = service just started, hasn't confirmed any server yet
                    # Don't delete -- just wait for it to confirm
                    return False
                # Has our server confirmed? First segment of the relay host (e.g. 'rust').
                our_name = rd_server.split('.')[0].lower()
                if our_name in confirmed.lower():
                    return False  # already confirmed against our server
                # Has a FOREIGN server (e.g. rs-ny from public RustDesk)
                print(f'[rustdesk] Foreign server in identity at {path}: {confirmed.strip()} -- resetting', flush=True)
            else:
                # No [keys_confirmed] section at all -- new file, leave it alone
                return False
            os.remove(path)
            # Restart the service so it regenerates identity against our server
            _sp.run(['sc.exe', 'stop', 'RustDesk'], capture_output=True, timeout=10)
            _time.sleep(3)
            _sp.run(['sc.exe', 'start', 'RustDesk'], capture_output=True, timeout=10)
            _time.sleep(10)
            print('[rustdesk] Identity reset -- service restarted', flush=True)
            return True
        except Exception as e:
            print(f'[rustdesk] identity check error: {e}', flush=True)
    return False


def _rustdesk_exe() -> str:
    """Return path to RustDesk.exe if installed, else empty string."""
    pf   = os.environ.get('ProgramFiles', r'C:\Program Files')
    pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
    lad  = os.environ.get('LOCALAPPDATA', '')
    candidates = [
        r'C:\Program Files\RustDesk\RustDesk.exe',           # machine-scope install
        r'C:\Program Files (x86)\RustDesk\RustDesk.exe',
        os.path.join(pf,   'RustDesk', 'RustDesk.exe'),
        os.path.join(pf86, 'RustDesk', 'RustDesk.exe'),
        os.path.join(lad,  'Programs', 'RustDesk', 'RustDesk.exe') if lad else '',
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return ''


def ensure_rustdesk(tracker_url: str, agent_id: str, token: str) -> None:
    """Install RustDesk and configure it to use the internal server if it is
    not present (or has been uninstalled).  Safe to call repeatedly."""
    global _rustdesk_setup_done
    import subprocess as _sp, time as _time
    try:
        # Fetch the relay server/key from the tracker (cached per-process). If
        # unavailable (env unset server-side, or network), the config-write and
        # identity-check below skip gracefully without crashing the flow.
        rd_server, rd_key = _get_rustdesk_relay(tracker_url, agent_id, token)

        if _rustdesk_exe():
            if _rustdesk_setup_done:
                # Already fully set up -- only sync ID (fast, idempotent)
                sync_rustdesk_id(tracker_url, agent_id, token)
                return
            # First run: do full config check
            _write_rustdesk_config(rd_server, rd_key)
            _fix_rustdesk_identity(rd_server)
            _ensure_rustdesk_password()
            sync_rustdesk_id(tracker_url, agent_id, token)
            _rustdesk_setup_done = True
            return  # tray_watchdog handles tray setup separately

        print('[rustdesk] Not installed -- installing...', flush=True)

        # Try 1: winget with --scope machine (works from SYSTEM on most Win10/11)
        _rustdesk_winget_install()

        # Always verify exe exists after winget -- it can exit 0 as SYSTEM
        # without actually installing (stub/redirect issue)
        if not _rustdesk_exe():
            _rustdesk_direct_install()

        _write_rustdesk_config(rd_server, rd_key)

        # Start briefly to trigger peer-ID generation
        exe = _rustdesk_exe()
        if exe:
            try:
                proc = _sp.Popen([exe, '--minimized'], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                _time.sleep(12)
                proc.terminate()
                _time.sleep(1)
            except Exception:
                pass
            sync_rustdesk_id(tracker_url, agent_id, token)
            print('[rustdesk] Install complete.', flush=True)
        else:
            print('[rustdesk] Install failed -- exe not found after attempts.', flush=True)
    except Exception as e:
        print(f'[rustdesk] ensure_rustdesk error: {e}', flush=True)


def _rustdesk_winget_install() -> bool:
    """Try to install RustDesk via winget. Returns True on success."""
    import subprocess as _sp, glob as _glob
    # winget may not be on SYSTEM's PATH -- find it explicitly
    winget = 'winget'
    patterns = [
        r'C:\Program Files\WindowsApps\Microsoft.DesktopAppInstaller_*\winget.exe',
        r'C:\Users\*\AppData\Local\Microsoft\WindowsApps\winget.exe',
    ]
    for pat in patterns:
        matches = _glob.glob(pat)
        if matches:
            winget = matches[0]
            break
    try:
        r = _sp.run(
            [winget, 'install', '--id', 'RustDesk.RustDesk',
             '--silent', '--scope', 'machine',
             '--accept-source-agreements', '--accept-package-agreements'],
            capture_output=True, timeout=180
        )
        print(f'[rustdesk] winget exit={r.returncode}', flush=True)
        return r.returncode == 0
    except Exception as e:
        print(f'[rustdesk] winget failed: {e}', flush=True)
        return False


def _rustdesk_direct_install() -> None:
    """Download RustDesk MSI from GitHub and install silently."""
    import subprocess as _sp, urllib.request as _ur, tempfile as _tmp, os as _os
    MSI_URL = 'https://github.com/rustdesk/rustdesk/releases/download/1.4.6/rustdesk-1.4.6-x86_64.msi'
    try:
        print(f'[rustdesk] Downloading MSI: {MSI_URL}', flush=True)
        tmp = _tmp.NamedTemporaryFile(suffix='.msi', delete=False)
        tmp_path = tmp.name
        tmp.close()

        req = _ur.Request(MSI_URL, headers={'User-Agent': 'CirqueRMM'})
        with _ur.urlopen(req, timeout=120) as resp, open(tmp_path, 'wb') as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)

        size_mb = _os.path.getsize(tmp_path) / 1024 / 1024
        print(f'[rustdesk] Downloaded {size_mb:.1f} MB', flush=True)
        if size_mb < 5:
            print('[rustdesk] Download too small -- aborting', flush=True)
            _os.unlink(tmp_path)
            return

        # msiexec /i <file> /qn = quiet, no UI
        r = _sp.run(
            ['msiexec', '/i', tmp_path, '/qn', '/norestart'],
            capture_output=True, timeout=180
        )
        print(f'[rustdesk] msiexec exit={r.returncode}', flush=True)
        if r.stderr:
            print(f'[rustdesk] msiexec stderr: {r.stderr[:300]}', flush=True)
        try:
            _os.unlink(tmp_path)
        except Exception:
            pass
    except Exception as e:
        print(f'[rustdesk] direct install failed: {e}', flush=True)


def _write_rustdesk_config(rd_server: str, rd_key: str) -> None:
    """Write RustDesk2.toml server config to all relevant paths using the
    relay server/key fetched from the tracker. If either is empty/None, skip
    (the agent must not write a config without a valid relay)."""
    if not rd_server or not rd_key:
        print('[rustdesk] relay config unavailable -- skipping config write', flush=True)
        return
    cfg = (
        f"rendezvous_server = '{rd_server}'\n"
        f"nat_type = 1\nserial = 0\n\n[options]\n"
        f"custom-rendezvous-server = '{rd_server}'\n"
        f"key = '{rd_key}'\n"
        f"relay-server = ''\napi-server = ''\n"
    )
    appdata  = os.environ.get('APPDATA', '')
    progdata = os.environ.get('ProgramData', r'C:\ProgramData')
    dirs = [
        os.path.join(appdata, 'RustDesk', 'config') if appdata else '',
        os.path.join(progdata, 'RustDesk', 'config'),
        # MSI-installed RustDesk runs as NT AUTHORITY\LocalService
        r'C:\Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config',
        r'C:\Windows\System32\config\systemprofile\AppData\Roaming\RustDesk\config',
    ]
    for d in dirs:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, 'RustDesk2.toml'), 'w', encoding='utf-8') as fh:
                fh.write(cfg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Cloudflare WARP bake-in (off-site boxes only)
# ---------------------------------------------------------------------------
# Off-site Windows boxes can't reach the internal RustDesk relay (10.15.0.63)
# directly. Cloudflare WARP private-network routing carries RustDesk's UDP
# rendezvous to the relay over the WARP mesh with zero internet exposure (the
# Cloudflare HTTP/WS tunnel mangles RustDesk's binary frames; only WARP's full
# L3 WireGuard works -- see memory rustdesk-offsite-cloudflare-deadend).
#
# ensure_warp() is idempotent and SAFE TO CALL REPEATEDLY:
#   * On-LAN/site boxes (relay TCP 21116 directly reachable) -> SKIP entirely.
#   * Already enrolled in org 'cirquetools' AND connected -> done.
#   * Otherwise drop a one-shot SYSTEM scheduled task that does ALL the
#     long-lived / connection-bouncing work (WARP install/restart/enroll/connect
#     + RustDesk toml). We NEVER inline-launch those from run_script / the WS
#     loop -- a lingering child would deadlock the command loop (Change 1).
_WARP_CLI = r'C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe'
_WARP_PROGDATA = r'C:\ProgramData\Cloudflare'
_WARP_MSI_URL = 'https://downloads.cloudflareclient.com/v1/download/windows/ga'
_RELAY_PROBE_IP = '10.15.0.63'   # internal RustDesk relay (rust.corp.cirque.com)
_RELAY_PROBE_PORT = 21116


def _warp_is_offsite(on_lan: bool) -> bool:
    """Decide, FAIL-CLOSED toward on-site, whether this box is genuinely off-site
    and therefore needs WARP. LAN/site boxes MUST be classified on-site so they
    never get a switch_locked WARP enrollment.

    on_lan is the connection's OWN already-resolved verdict (True when the agent
    connected to the internal LAN endpoints because rmm.corp.cirque.com was
    TLS-reachable; False when it fell back to the Cloudflare public endpoints).
    This is the authoritative signal and is NOT subject to a fresh transient
    probe failure: a real LAN box always connects on the LAN endpoints.

    We declare off-site ONLY when BOTH hold:
      (1) the connection itself used the Cloudflare fallback (on_lan is False), AND
      (2) a fresh direct TCP probe to the relay 10.15.0.63:21116 also fails.
    If either says on-site, we skip WARP. So a transient probe blip on a LAN box
    cannot trigger enrollment (its on_lan stays True)."""
    if on_lan:
        return False  # connected on internal endpoints -> definitively on-site
    # On Cloudflare fallback: confirm with a direct relay probe before enrolling.
    # If the relay is somehow directly reachable here (e.g. split routing), treat
    # as on-site and skip.
    try:
        with socket.create_connection((_RELAY_PROBE_IP, _RELAY_PROBE_PORT), timeout=2.5):
            return False
    except Exception:
        return True


def _get_warp_config(tracker_url: str, agent_id: str, token: str):
    """Fetch the WARP enrollment (org, client_id, client_secret) from the tracker,
    authenticated by agent_id + token. Cached per-process. LAN-primary then public
    Cloudflare fallback (mirrors _get_rustdesk_relay). Returns (None, None, None)
    on any failure / unset server-side so the caller skips WARP gracefully.

    The secret is held only in memory for the lifetime of one enrollment and is
    written to the (machine-only) MDM store via the SYSTEM task; it is never
    logged or echoed."""
    global _WARP_CONFIG_CACHE
    if _WARP_CONFIG_CACHE is not None:
        return _WARP_CONFIG_CACHE
    import urllib.request as _ur
    fallback = os.environ.get("RMM_TRACKER_URL_PUBLIC", "https://tracker.cirquetools.com").rstrip("/")
    bases = []
    for b in (tracker_url, fallback):
        b = (b or "").rstrip("/")
        if b and b not in bases:
            bases.append(b)
    for base in bases:
        try:
            url = f"{base}/api/rmm/warp-config/{agent_id}?token={token}"
            req = _ur.Request(url, headers={"User-Agent": f"CirqueRMM/{AGENT_VERSION}"})
            with _ur.urlopen(req, timeout=10, context=_ssl_ctx()) as resp:
                body = json.loads(resp.read())
            org = (body.get("organization") or "").strip()
            cid = (body.get("auth_client_id") or "").strip()
            sec = (body.get("auth_client_secret") or "").strip()
            if org and cid and sec:
                _WARP_CONFIG_CACHE = (org, cid, sec)
                print(f"[warp] enrollment config fetched from {base} (org={org})", flush=True)
                return _WARP_CONFIG_CACHE
            print("[warp] enrollment config unavailable -- skipping", flush=True)
            _WARP_CONFIG_CACHE = (None, None, None)
            return _WARP_CONFIG_CACHE
        except Exception as e:
            print(f"[warp] config fetch failed from {base}: {e}", flush=True)
            continue
    return (None, None, None)


def _warp_enrolled_and_connected(org: str) -> bool:
    """True if warp-cli reports registration in *org* AND a Connected status."""
    import subprocess as _sp
    if not os.path.isfile(_WARP_CLI):
        return False
    try:
        reg = _sp.run([_WARP_CLI, "--accept-tos", "registration", "show"],
                      capture_output=True, text=True, errors="replace",
                      timeout=15, creationflags=0x08000000)
        if org.lower() not in (reg.stdout or "").lower():
            return False
        st = _sp.run([_WARP_CLI, "--accept-tos", "status"],
                     capture_output=True, text=True, errors="replace",
                     timeout=15, creationflags=0x08000000)
        return "connected" in (st.stdout or "").lower()
    except Exception:
        return False


def _build_warp_worker_ps1(org: str, relay_host: str, relay_ip: str) -> str:
    """The one-shot SYSTEM worker: restart WARP (re-parse MDM), enroll into *org*
    only if needed, connect, then write a clean internal RustDesk2.toml preserving
    the existing key. Logs to disk. No secrets here (they're already in MDM)."""
    return (
        '$ErrorActionPreference = "SilentlyContinue"\n'
        '$Log   = "C:\\ProgramData\\Cloudflare\\warp_bakein.log"\n'
        '$cli   = "C:\\Program Files\\Cloudflare\\Cloudflare WARP\\warp-cli.exe"\n'
        f'$Org   = "{org}"\n'
        'function L($m){ "$(Get-Date -Format o)  $m" | Out-File $Log -Append }\n'
        '"" | Out-File $Log\n'
        'L "=== warp bakein worker start ==="\n'
        'Restart-Service -Name CloudflareWARP -Force\n'
        'Start-Sleep -Seconds 12\n'
        '$reg = (& $cli --accept-tos registration show 2>&1 | Out-String)\n'
        'if ($reg -notmatch [regex]::Escape($Org)) {\n'
        '    L "registration org != $Org -> re-enrolling"\n'
        '    & $cli --accept-tos registration delete 2>&1 | Out-File $Log -Append\n'
        '    Start-Sleep -Seconds 5\n'
        '    & $cli --accept-tos registration new $Org 2>&1 | Out-File $Log -Append\n'
        '    Start-Sleep -Seconds 6\n'
        '} else { L "registration already in org $Org" }\n'
        '& $cli --accept-tos connect 2>&1 | Out-File $Log -Append\n'
        'Start-Sleep -Seconds 12\n'
        'L ("WARP status: " + ((& $cli --accept-tos status 2>&1) -join " | "))\n'
        '$rdDirs = @(\n'
        '  "C:\\Windows\\ServiceProfiles\\LocalService\\AppData\\Roaming\\RustDesk\\config",\n'
        '  "$env:APPDATA\\RustDesk\\config",\n'
        '  "C:\\ProgramData\\RustDesk\\config"\n'
        ') | Where-Object { Test-Path $_ }\n'
        'foreach ($d in $rdDirs) {\n'
        '    $toml = Join-Path $d "RustDesk2.toml"\n'
        '    $key  = ""\n'
        '    if (Test-Path $toml) {\n'
        '        $m = Select-String -Path $toml -Pattern "^\\s*key\\s*=\\s*\'([^\']*)\'" -EA SilentlyContinue | Select-Object -First 1\n'
        '        if ($m) { $key = $m.Matches[0].Groups[1].Value }\n'
        '    }\n'
        '    $body = "rendezvous_server = \'' + relay_host + '\'`nnat_type = 1`nserial = 0`n`n[options]`ncustom-rendezvous-server = \'' + relay_host + '\'`nrelay-server = \'\'`napi-server = \'\'`nkey = \'$key\'"\n'
        '    Set-Content -Path $toml -Value $body -Encoding UTF8\n'
        '    L "wrote $toml (key preserved: $([bool]$key))"\n'
        '}\n'
        'Restart-Service -Name RustDesk -Force -EA SilentlyContinue\n'
        'Start-Sleep -Seconds 5\n'
        '$st = ((& $cli --accept-tos status 2>&1) -join " | ")\n'
        '$c = New-Object Net.Sockets.TcpClient\n'
        f'$a = $c.BeginConnect("{relay_ip}", {_RELAY_PROBE_PORT}, $null, $null)\n'
        '$ok = $a.AsyncWaitHandle.WaitOne(5000); $relayReach = ($ok -and $c.Connected); $c.Close()\n'
        f'L "VERIFY relay {relay_ip}:{_RELAY_PROBE_PORT} reachable: $relayReach"\n'
        'if (($st -match "Connected") -and $relayReach) { L "RESULT: OK" } else { L "RESULT: FAIL" }\n'
        'L "=== warp bakein worker done ==="\n'
    )


def _warp_log_once(state: str, msg: str) -> None:
    """Print msg only when the WARP state CHANGES, to avoid spamming the log on
    every reconnect (ensure_warp is re-evaluated each connect by design)."""
    global _warp_logged_state
    if _warp_logged_state != state:
        print(msg, flush=True)
        _warp_logged_state = state


def ensure_warp(tracker_url: str, agent_id: str, token: str, on_lan: bool) -> None:
    """Idempotently enroll OFF-SITE boxes into Cloudflare WARP so they can reach
    the internal RustDesk relay. On-LAN/site boxes skip. Safe to call repeatedly
    and RE-EVALUATED every connect, so a laptop that roams LAN->off-site enrolls
    without waiting for a process restart.

    on_lan = the connection's own resolved verdict (True when the agent connected
    on the internal LAN endpoints; False on the Cloudflare fallback). It is the
    fail-closed off-site signal -- a real LAN box always has on_lan=True so it can
    never be misclassified by a transient probe.

    All long-lived steps run inside a one-shot SYSTEM scheduled task that logs to
    disk (deadlock-safe per Change 1); this function only stages files + launches
    the task and returns quickly. Windows-only."""
    global _warp_worker_launched_at
    if sys.platform != "win32":
        return
    import subprocess as _sp, time as _time
    try:
        # 1) OFF-SITE gate (fail-closed toward on-site). LAN/site boxes MUST skip.
        if not _warp_is_offsite(on_lan):
            _warp_log_once("onsite", "[warp] on-LAN/site (relay reachable) -- skipping WARP")
            return

        org_default = 'cirquetools'
        # 2) Idempotent: already enrolled + connected in our org -> done.
        if _warp_enrolled_and_connected(org_default):
            _warp_log_once("enrolled", f"[warp] already enrolled in {org_default} and connected -- ok")
            return

        # 3) A worker we launched may still be converging -- don't re-fire it more
        #    than once per cooldown window. (Re-evaluated, but rate-limited.)
        now = _time.monotonic()
        if (now - _warp_worker_launched_at) < _WARP_WORKER_COOLDOWN:
            _warp_log_once("converging", "[warp] enrollment worker recently launched -- waiting to converge")
            return

        # 4) Fetch enrollment token from the tracker (never hardcoded in source).
        org, cid, sec = _get_warp_config(tracker_url, agent_id, token)
        if not (org and cid and sec):
            print("[warp] no enrollment config available -- deferring", flush=True)
            return
        rd_server, _rd_key = _get_rustdesk_relay(tracker_url, agent_id, token)
        relay_host = rd_server or 'rust.corp.cirque.com'

        os.makedirs(_WARP_PROGDATA, exist_ok=True)

        # Write MDM to the Policies key + mdm.xml so a WARP service restart enrolls
        # headless into OUR org (NOT consumer WARP). switch_locked, onboarding off.
        try:
            import winreg  # type: ignore
            rp = r"SOFTWARE\Policies\Cloudflare\Warp"
            k = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, rp)
            winreg.SetValueEx(k, "organization", 0, winreg.REG_SZ, org)
            winreg.SetValueEx(k, "service_mode", 0, winreg.REG_SZ, "warp")
            winreg.SetValueEx(k, "auto_connect", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(k, "onboarding", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(k, "switch_locked", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "auth_client_id", 0, winreg.REG_SZ, cid)
            winreg.SetValueEx(k, "auth_client_secret", 0, winreg.REG_SZ, sec)
            winreg.CloseKey(k)
        except Exception as e:
            print(f"[warp] MDM registry write failed: {e}", flush=True)

        mdm_xml = (
            "<dict>\n"
            f"  <key>organization</key><string>{org}</string>\n"
            "  <key>service_mode</key><string>warp</string>\n"
            "  <key>auto_connect</key><integer>0</integer>\n"
            f"  <key>auth_client_id</key><string>{cid}</string>\n"
            f"  <key>auth_client_secret</key><string>{sec}</string>\n"
            "  <key>switch_locked</key><true/>\n"
            "  <key>onboarding</key><false/>\n"
            "</dict>\n"
        )
        try:
            with open(os.path.join(_WARP_PROGDATA, "mdm.xml"), "w", encoding="utf-8") as fh:
                fh.write(mdm_xml)
        except Exception as e:
            print(f"[warp] mdm.xml write failed: {e}", flush=True)

        # If WARP isn't installed, install the MSI first (quiet). The SYSTEM task
        # restarts/enrolls after; if the MSI is still landing the next process
        # cycle re-runs and the task picks up the now-present warp-cli.
        if not os.path.isfile(_WARP_CLI):
            print("[warp] warp-cli absent -- installing MSI", flush=True)
            _warp_install_msi()

        # Stage the one-shot SYSTEM worker that does ALL long-lived steps.
        worker = _build_warp_worker_ps1(org, relay_host, _RELAY_PROBE_IP)
        worker_path = os.path.join(_WARP_PROGDATA, "warp_bakein_worker.ps1")
        with open(worker_path, "w", encoding="utf-8") as fh:
            fh.write(worker)

        tn = "CF_WARP_Bakein"
        _sp.run(["schtasks", "/Delete", "/TN", tn, "/F"],
                capture_output=True, timeout=15, creationflags=0x08000000)
        # Backstop trigger: a near-future ONCE time (~2 min ahead) in case the
        # explicit /Run below doesn't take -- so enrollment isn't pinned to a
        # fixed wall-clock that an off-hours/asleep box might never reach.
        st = _time.strftime("%H:%M", _time.localtime(_time.time() + 120))
        cr = _sp.run(["schtasks", "/Create", "/TN", tn, "/TR",
                      f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{worker_path}"',
                      "/SC", "ONCE", "/ST", st, "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"],
                     capture_output=True, text=True, errors="replace",
                     timeout=20, creationflags=0x08000000)
        if cr.returncode != 0:
            print(f"[warp] schtasks /Create failed (rc={cr.returncode}): "
                  f"{(cr.stderr or cr.stdout or '').strip()[:200]}", flush=True)
            return
        rr = _sp.run(["schtasks", "/Run", "/TN", tn],
                     capture_output=True, text=True, errors="replace",
                     timeout=20, creationflags=0x08000000)
        if rr.returncode != 0:
            print(f"[warp] schtasks /Run failed (rc={rr.returncode}); "
                  f"backstop ONCE trigger at {st} will fire it", flush=True)
        # Mark launch time so we don't re-fire within the cooldown window; the
        # next connect after cooldown re-evaluates (retries if the worker failed).
        _warp_worker_launched_at = _time.monotonic()
        _warp_log_once("launched", "[warp] enrollment worker launched (SYSTEM task) -- converging")
    except Exception as e:
        print(f"[warp] ensure_warp error: {e}", flush=True)


def _warp_install_msi() -> None:
    """Download + silently install the Cloudflare WARP MSI. Returns when msiexec
    exits; the SYSTEM worker handles enrollment afterward."""
    import subprocess as _sp, urllib.request as _ur, tempfile as _tmp
    try:
        tmp = _tmp.NamedTemporaryFile(suffix=".msi", delete=False)
        tmp_path = tmp.name
        tmp.close()
        req = _ur.Request(_WARP_MSI_URL, headers={"User-Agent": "CirqueRMM"})
        with _ur.urlopen(req, timeout=200) as resp, open(tmp_path, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
        if os.path.getsize(tmp_path) < 1_000_000:
            print("[warp] MSI download too small -- aborting", flush=True)
            os.unlink(tmp_path)
            return
        r = _sp.run(["msiexec", "/i", tmp_path, "/qn", "/norestart"],
                    capture_output=True, timeout=300, creationflags=0x08000000)
        print(f"[warp] msiexec exit={r.returncode}", flush=True)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    except Exception as e:
        print(f"[warp] MSI install failed: {e}", flush=True)


# ---------------------------------------------------------------------------
# C:\ITTOOLS provisioning (Install-software feature)
# ---------------------------------------------------------------------------
# A sanctioned drop folder for user-staged installers. Lives at the protected
# C:\ root (only SYSTEM/Admins can *run* from it -- the agent does that as
# SYSTEM after admin approval), but BUILTIN\Users get write+read so a non-admin
# user can drop an installer in via Explorer. The systray "Install software"
# picker enumerates this folder and the server runs the chosen file as SYSTEM.
_ITTOOLS_DIR = r"C:\ITTOOLS"
# Sentinel so we provision the ACL exactly once, not on every heartbeat. Its
# presence means "folder + ACL already set"; we still cheaply re-create the
# folder if it's been deleted.
_ITTOOLS_MARKER = os.path.join(_ITTOOLS_DIR, ".provisioned")


def ensure_ittools_dir() -> None:
    """Idempotently ensure C:\\ITTOOLS exists and is user-writable.

    Runs as SYSTEM (this service). Creates the folder and grants BUILTIN\\Users
    modify rights so a non-admin can stage installers, while the folder stays at
    the protected C:\\ root. Heavy ACL work is gated behind a sentinel file so we
    don't re-run icacls every startup/heartbeat (avoids thrashing the ACL).
    """
    if sys.platform != "win32":
        return
    try:
        already = os.path.isdir(_ITTOOLS_DIR) and os.path.isfile(_ITTOOLS_MARKER)
        # Cheap: make sure the folder exists even if the marker says provisioned
        # (folder could have been deleted out from under us).
        os.makedirs(_ITTOOLS_DIR, exist_ok=True)
        if already:
            return
        # Grant BUILTIN\Users modify (M) with object+container inheritance so
        # files dropped later inherit it. Localised-safe via the *S-1-5-32-545*
        # SID for the Users group rather than the (translatable) name.
        subprocess.run(
            ["icacls", _ITTOOLS_DIR, "/grant", "*S-1-5-32-545:(OI)(CI)M"],
            capture_output=True, timeout=30,
            creationflags=0x08000000,
        )
        try:
            with open(_ITTOOLS_MARKER, "w", encoding="utf-8") as _fh:
                _fh.write("provisioned by CirqueRMM agent\n")
            # Hide the sentinel so it doesn't clutter the user's view.
            subprocess.run(["attrib", "+h", _ITTOOLS_MARKER],
                           capture_output=True, timeout=10,
                           creationflags=0x08000000)
        except Exception:
            pass
        print(f"[ittools] Provisioned {_ITTOOLS_DIR} (Users:modify)", flush=True)
    except Exception as e:
        print(f"[ittools] provisioning failed: {e}", flush=True)


def _parse_semver(v) -> tuple:
    """Parse a dotted version string into a tuple of ints for ordered compare.

    Defensive against malformed/missing values: non-numeric or empty
    components become 0, and a missing/None version parses to (0,) so it sorts
    BELOW any real release (and would therefore be refused as a downgrade).
    Used by the refuse-downgrade gate (R9) -- integer-tuple compare, NOT a
    string compare, so 2.9.10 correctly sorts ABOVE 2.9.9."""
    parts = []
    for chunk in str(v or "").split("."):
        chunk = chunk.strip()
        try:
            parts.append(int(chunk))
        except (TypeError, ValueError):
            # tolerate things like "2.9.11-rc1" -> take leading digits, else 0
            digits = ""
            for ch in chunk:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def check_for_update(tracker_url: str, agent_id: str, token: str) -> bool:
    try:
        url = f"{tracker_url}/rmm/agent/version?agent_id={agent_id}&token={token}"
        req = urllib.request.Request(url, headers={"User-Agent": f"CirqueRMM/{AGENT_VERSION}"})
        with _open_update_url(req, 10) as r:
            data = json.loads(r.read())

        server_checksum = data.get("checksum", "")
        current_path = os.path.abspath(__file__)
        current_checksum = hashlib.sha256(open(current_path, "rb").read()).hexdigest()

        if not server_checksum or server_checksum == current_checksum:
            print(f"[update] Up to date ({AGENT_VERSION})", flush=True)
            return False

        # Refuse downgrades (CANARY R9): a stale or malicious OLDER served file
        # must never silently downgrade the agent. Compare served vs local as
        # INTEGER semver tuples (NOT string compare -- "2.9.10" < "2.9.9" as
        # strings would be a bug). Only swap when served is STRICTLY greater.
        # Intentional rollback is performed by releasing a HIGHER version number
        # that contains the desired (older-behaving) code -- never by serving a
        # lower number. This gate is IN ADDITION to the checksum-diff gate and
        # the RSA signature verification below.
        served_ver = _parse_semver(data.get("version"))
        local_ver = _parse_semver(AGENT_VERSION)
        if served_ver < local_ver:
            print(f"[update] refusing downgrade: served {data.get('version')} < running {AGENT_VERSION}", flush=True)
            return False
        if served_ver == local_ver:
            # Same version but checksum differs (e.g. rebuild) -- no version
            # advance, so treat as a no-op to stay consistent with R9 intent.
            print(f"[update] served version equals running ({AGENT_VERSION}) -- no version advance, skipping", flush=True)
            return False

        print(f"[update] New version {data.get('version')} available -- downloading...", flush=True)
        file_url = f"{tracker_url}/rmm/agent/file?agent_id={agent_id}&token={token}"
        req2 = urllib.request.Request(file_url, headers={"User-Agent": f"CirqueRMM/{AGENT_VERSION}"})
        with _open_update_url(req2, 30) as r:
            new_code = r.read()

        if server_checksum and hashlib.sha256(new_code).hexdigest() != server_checksum:
            print("[update] Checksum mismatch -- aborting", flush=True)
            return False

        # Signature gate (CANARY): verify a detached RSA signature of the EXACT
        # bytes we are about to swap in. Fail-closed -- no sig / bad sig aborts.
        sig_url = f"{tracker_url}/rmm/agent/file-sig?agent_id={agent_id}&token={token}"
        sig = _fetch_update_sig(sig_url, _ssl_ctx())
        if not sig:
            print("[update] No signature returned for agent_client.py -- aborting (fail-closed)", flush=True)
            return False
        if not _verify_update_sig(new_code, sig):
            print("[update] Signature verification FAILED for agent_client.py -- aborting (fail-closed)", flush=True)
            return False
        print("[update] Signature verified for agent_client.py", flush=True)

        tmp = current_path + ".new"
        old = current_path + ".old"
        with open(tmp, "wb") as f:
            f.write(new_code)
        try:
            if os.path.exists(old):
                os.remove(old)
            os.rename(current_path, old)
        except Exception:
            pass
        os.rename(tmp, current_path)
        # Keep version.txt in sync so the launcher sees the correct local version on restart
        ver_file = os.path.join(os.path.dirname(current_path), "version.txt")
        try:
            with open(ver_file, "w") as _vf:
                _vf.write(data.get("version", ""))
        except Exception:
            pass
        print("[update] Updated -- restarting", flush=True)
        return True
    except Exception as e:
        print(f"[update] Check failed: {e}", flush=True)
        return False


def _restart_after_update() -> None:
    """Exit with a non-zero code so NSSM restarts the service after an update."""
    sys.exit(7)  # arbitrary non-zero so NSSM always restarts


# ---------------------------------------------------------------------------
# Telemetry collection
# ---------------------------------------------------------------------------

def _query_user_info() -> tuple:
    """Return (username, logon_time_iso) for the current interactive user via query user.
    query user columns: USERNAME  SESSIONNAME  ID  STATE  IDLE TIME  LOGON TIME
    USERNAME is always first; LOGON TIME is last and may contain spaces (e.g. '3/5/2026 8:00 AM').
    Splitting on 2+ spaces keeps the date+time as one token.
    """
    import re as _re
    def _parse_logon(s: str) -> str:
        for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%d/%m/%Y %I:%M %p", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(s.strip(), fmt)
                return dt.isoformat()
            except ValueError:
                pass
        return ""

    try:
        r = subprocess.run(
            ["query", "user"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000,
        )
        lines = r.stdout.splitlines()
        for state in ("Active", "Disc"):
            for line in lines:
                if state not in line:
                    continue
                clean = line.strip().lstrip(">")
                parts = _re.split(r"\s{2,}", clean)
                if not parts or parts[0].lower() in ("username", ""):
                    continue
                user = parts[0]
                logon = _parse_logon(parts[-1]) if len(parts) >= 2 else ""
                return user, logon
    except Exception:
        pass
    # fallback for non-SYSTEM context
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(256)
        size = ctypes.c_ulong(256)
        if ctypes.windll.secur32.GetUserNameExW(3, buf, ctypes.byref(size)):
            name = buf.value
            if not name.endswith("$"):
                user = name.split("\\")[-1] if "\\" in name else name
                return user, ""
    except Exception:
        pass
    return os.environ.get("USERNAME") or "", ""


def _get_windows_username() -> str:
    return _query_user_info()[0]


def _get_domain() -> str:
    # Use GetComputerNameExW(2) = ComputerNameDnsDomain -- returns just the domain suffix
    try:
        import ctypes
        buf  = ctypes.create_unicode_buffer(256)
        size = ctypes.c_ulong(256)
        if ctypes.windll.kernel32.GetComputerNameExW(2, buf, ctypes.byref(size)):
            return buf.value  # e.g. "corp.cirque.com" -- no stripping needed
    except Exception:
        pass
    return os.environ.get("USERDNSDOMAIN", "")


def _get_drive_types() -> dict:
    """Return a map {drive_letter_upper: DriveType_int} from Win32_LogicalDisk.

    Win32_LogicalDisk.DriveType: 2=removable, 3=fixed (local HDD/SSD),
    4=network, 5=optical/mounted-ISO (CD-ROM/mounted image). Windows-only;
    returns {} on non-Windows or failure (callers degrade gracefully).
    Single WMI/CIM round-trip per telemetry cycle -- lightweight.
    """
    if platform.system() != "Windows":
        return {}
    try:
        data = _ps_json(
            "Get-CimInstance Win32_LogicalDisk | "
            "Select-Object DeviceID,DriveType | ConvertTo-Json -Compress",
            timeout=10,
        )
        if not data:
            return {}
        if isinstance(data, dict):
            data = [data]
        out = {}
        for row in data:
            try:
                dev = (row.get("DeviceID") or "").strip().rstrip("\\").upper()
                if dev:
                    out[dev] = int(row.get("DriveType"))
            except Exception:
                continue
        return out
    except Exception:
        return {}


def _get_cpu_name() -> str:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except Exception:
        return platform.processor()


def _get_public_ip() -> str:
    """Return the machine's public/external IP via a lightweight API."""
    for url in (
        "https://ifconfig.me",
        "https://api.ipify.org",
        "https://checkip.amazonaws.com",
        "https://icanhazip.com",
    ):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                ip = r.read().decode().strip()
                if ip:
                    return ip
        except Exception:
            continue
    # Fallback: use system curl (available on Windows 10+)
    try:
        ip = subprocess.check_output(
            ["curl", "-s", "--max-time", "5", "ifconfig.me"],
            timeout=7, stderr=subprocess.DEVNULL
        ).decode().strip()
        if ip:
            return ip
    except Exception:
        pass
    return ""


def _get_wifi_ssids() -> dict:
    """Return a dict of {interface_name: ssid} for all connected WiFi adapters."""
    ssids = {}
    try:
        out = subprocess.check_output(
            ["netsh", "wlan", "show", "interfaces"],
            text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        current_iface = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Name") and ":" in line:
                current_iface = line.split(":", 1)[1].strip()
            elif line.startswith("SSID") and not line.startswith("BSSID") and ":" in line and current_iface:
                ssids[current_iface] = line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ssids


def _get_windows_display_version() -> str:
    """Return the Windows marketing release name e.g. '24H2', '23H2'."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        return winreg.QueryValueEx(key, "DisplayVersion")[0].strip()
    except Exception:
        return ""


def _get_screen_resolutions() -> str:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # Get all monitor resolutions
        screens = []
        monitor_enum = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_long * 4), ctypes.c_double)
        def cb(hMonitor, hdcMonitor, lprcMonitor, dwData):
            r = lprcMonitor.contents
            w = r[2] - r[0]
            h = r[3] - r[1]
            screens.append(f"{w}x{h}")
            return True
        ctypes.windll.user32.EnumDisplayMonitors(None, None, monitor_enum(cb), 0)
        if screens:
            return ", ".join(screens)
    except Exception:
        pass
    try:
        import ctypes
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        return f"{w}x{h}"
    except Exception:
        return ""


def _decode_security_state(state) -> dict:
    """Decode Win32 SecurityCenter2 productState bitmask.
    Byte 1 (positions 2-3): real-time protection
      '10' = enabled (third-party AV)
      '11' = enabled (Windows Defender / built-in)
      '00' or '01' = disabled
    Byte 2 (positions 4-5): definition status
      '00' = up to date, '10' = out of date
    """
    try:
        s = format(int(state), '06x')
        active  = s[2:4] in ("10", "11")
        updated = s[4:6] == "00"
        return {"active": active, "updated": updated}
    except Exception:
        return {"active": False, "updated": False}


def _normalise_ps_list(raw) -> list:
    """ConvertTo-Json returns a dict for 1 item, list for many."""
    if raw is None:
        return []
    return raw if isinstance(raw, list) else [raw]


def _collect_extended() -> dict:
    """
    Collect hardware / OS / security info via PowerShell/WMI.
    Sent once on connect as part of agent_info.
    """
    result = {}

    # -- Computer system (vendor, model; user+domain handled separately below) -
    cs = _ps_json(
        "Get-CimInstance Win32_ComputerSystem | "
        "Select-Object Manufacturer,Model,UserName,Domain | "
        "ConvertTo-Json -Compress"
    )
    if cs:
        result["vendor"]     = cs.get("Manufacturer") or ""
        result["model_name"] = cs.get("Model") or ""

    # -- Logged-in user via query user (Python-parsed, reliable from SYSTEM) ----
    _qu_user, _qu_logon = _query_user_info()
    if _qu_user:
        result["logged_in_user"] = _qu_user
        result["last_login_user"] = _qu_user
        # Query Security event log for the most recent actual logon/unlock event.
        # This shows "today's login", not the session creation time (which stays
        # the same across sleep/wake cycles and can be weeks old).
        ev = _ps_json(
            f"$u='{_qu_user}';"
            "$e=Get-WinEvent -FilterHashtable @{LogName='Security';Id=4624;StartTime=(Get-Date).AddDays(-45)} "
            "-EA SilentlyContinue"
            "|Where-Object{$_.Properties[8].Value -in @(2,7,10) -and $_.Properties[5].Value -eq $u}"
            "|Select-Object -First 1;"
            "if(!$e){"
            "$e=Get-WinEvent -FilterHashtable @{LogName='Security';Id=4801;StartTime=(Get-Date).AddDays(-45)} "
            "-EA SilentlyContinue|Where-Object{$_.Properties[1].Value -eq $u}|Select-Object -First 1"
            "};"
            "if($e){@{time=$e.TimeCreated.ToString('o')}|ConvertTo-Json -Compress}else{'null'}",
            timeout=20,
        )
        result["last_login_time"] = (ev or {}).get("time") or _qu_logon
    elif cs and cs.get("UserName") and not (cs.get("UserName") or "").endswith("$"):
        result["logged_in_user"] = cs["UserName"].split("\\")[-1]

    # -- Domain: resolve from the logged-in user's NETBIOS prefix ------------
    # Win32_ComputerSystem.Domain gives the computer's root domain (e.g. cirque.com)
    # but the user may be in a child domain (e.g. corp.cirque.com).
    # Win32_ComputerSystem.UserName format is "NETBIOS_DOMAIN\username"; we use
    # the NETBIOS prefix to look up the DNS domain via Win32_NTDomain.
    user_domain = (cs or {}).get("Domain") or _get_domain()  # default: computer domain
    raw_cs_user = (cs or {}).get("UserName") or ""
    if "\\" in raw_cs_user and not raw_cs_user.endswith("$"):
        netbios = raw_cs_user.split("\\")[0]
        nd = _ps_json(
            f"Get-CimInstance Win32_NTDomain "
            f"| Where-Object {{$_.DomainName -eq '{netbios}'}} "
            f"| Select-Object -First 1 DnsDomainName "
            f"| ConvertTo-Json -Compress"
        )
        if nd and isinstance(nd, dict) and nd.get("DnsDomainName"):
            user_domain = nd["DnsDomainName"]
    result["domain"] = user_domain

    # -- BIOS ---------------------------------------------------------------
    bios = _ps_json(
        "Get-CimInstance Win32_BIOS | "
        "Select-Object Manufacturer,SMBIOSBIOSVersion,SerialNumber,"
        "@{N='ReleaseDate';E={if($_.ReleaseDate){$_.ReleaseDate.ToString('yyyy-MM-dd')}else{''}}} | "
        "ConvertTo-Json -Compress"
    )
    if bios:
        result["bios_manufacturer"] = bios.get("Manufacturer") or ""
        result["bios_version"]      = bios.get("SMBIOSBIOSVersion") or ""
        result["bios_date"]         = bios.get("ReleaseDate") or ""
        result["serial_number"]     = bios.get("SerialNumber") or ""

    # -- Windows licensing: OEM/embedded product key + edition + activation --
    # OA3xOriginalProductKey is the BIOS-embedded OEM key (blank on pure VL/MAK).
    lic = _ps_json(
        "$s=Get-CimInstance SoftwareLicensingService -EA SilentlyContinue;"
        "$o=Get-CimInstance Win32_OperatingSystem -EA SilentlyContinue;"
        "$p=Get-CimInstance SoftwareLicensingProduct "
        "-Filter \"ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL\" "
        "-EA SilentlyContinue|Select-Object -First 1;"
        "@{key=$s.OA3xOriginalProductKey;edition=$o.Caption;status=[int]$p.LicenseStatus}|ConvertTo-Json -Compress"
    )
    if lic:
        result["windows_product_key"] = lic.get("key") or ""
        result["windows_edition"]     = lic.get("edition") or ""
        _smap = {0: "Unlicensed", 1: "Licensed", 2: "OOB Grace", 3: "OOT Grace",
                 4: "Non-Genuine", 5: "Notification", 6: "Extended Grace"}
        try:
            result["windows_activation"] = _smap.get(int(lic.get("status")), "")
        except Exception:
            result["windows_activation"] = ""

    # -- Motherboard --------------------------------------------------------
    mb = _ps_json(
        "Get-CimInstance Win32_BaseBoard | "
        "Select-Object Manufacturer,Product | ConvertTo-Json -Compress"
    )
    if mb:
        mb_str = " ".join(filter(None, [mb.get("Manufacturer"), mb.get("Product")])).strip()
        result["motherboard"] = mb_str

    # -- OS edition ---------------------------------------------------------
    os_info = _ps_json(
        "Get-CimInstance Win32_OperatingSystem | "
        "Select-Object Caption | ConvertTo-Json -Compress"
    )
    if os_info:
        result["os_edition"] = os_info.get("Caption") or ""

    # -- GPU ----------------------------------------------------------------
    raw_gpu = _ps_json(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"
    )
    gpus = []
    for g in _normalise_ps_list(raw_gpu):
        name = g.get("Name") or ""
        vram = g.get("AdapterRAM")
        gpus.append({
            "name": name,
            "vram_gb": round(int(vram) / (1024**3), 1) if vram and int(vram) > 0 else None,
        })
    if gpus:
        result["gpu"] = gpus

    # -- Sound --------------------------------------------------------------
    raw_snd = _ps_json(
        "Get-CimInstance Win32_SoundDevice | "
        "Select-Object Name | ConvertTo-Json -Compress"
    )
    sounds = [s.get("Name") for s in _normalise_ps_list(raw_snd) if s.get("Name")]
    if sounds:
        result["sound_card"] = ", ".join(sounds)

    # -- Timezone -----------------------------------------------------------
    tz = _ps_json("Get-TimeZone | Select-Object Id,DisplayName | ConvertTo-Json -Compress")
    if tz:
        result["timezone"] = tz.get("DisplayName") or tz.get("Id") or ""

    # -- Security products --------------------------------------------------
    security = {}
    for key, cls in [("av", "AntiVirusProduct"), ("fw", "FirewallProduct"), ("as", "AntiSpywareProduct")]:
        raw = _ps_json(
            f"Get-CimInstance -Namespace root/SecurityCenter2 -ClassName {cls} "
            f"| Select-Object displayName,productState | ConvertTo-Json -Compress"
        )
        products = []
        for p in _normalise_ps_list(raw):
            state = _decode_security_state(p.get("productState", 0))
            products.append({
                "name":    p.get("displayName") or "",
                "active":  state["active"],
                "updated": state["updated"],
            })
        if products:
            security[key] = products

    # Firewall fallback: Windows Defender Firewall doesn't register in
    # SecurityCenter2 FirewallProduct -- query Get-NetFirewallProfile directly.
    if not security.get("fw"):
        try:
            raw_fw = _ps_json(
                "Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json -Compress"
            )
            profiles = _normalise_ps_list(raw_fw)
            if profiles:
                # Summarise: active if ANY enabled profile (Domain/Private/Public)
                enabled = [p for p in profiles if str(p.get("Enabled", "")).lower() in ("true", "1")]
                all_enabled = len(enabled) == len(profiles)
                # Show per-profile status
                detail = ", ".join(
                    f"{p.get('Name')} {'On' if str(p.get('Enabled','')).lower() in ('true','1') else 'Off'}"
                    for p in profiles
                )
                security["fw"] = [{
                    "name":    f"Windows Defender Firewall ({detail})",
                    "active":  len(enabled) > 0,
                    "updated": True,   # Firewall has no definition updates
                }]
        except Exception:
            pass

    # Antispyware fallback: Defender covers antispyware but only registers
    # under AntiVirusProduct on Windows 10/11. If empty, mirror the AV entry.
    if not security.get("as") and security.get("av"):
        defender_av = [p for p in security["av"] if "defender" in p.get("name", "").lower()]
        if defender_av:
            security["as"] = [{
                "name":    "Windows Defender",
                "active":  defender_av[0]["active"],
                "updated": defender_av[0]["updated"],
            }]

    if security:
        # Also pull last scan times directly from Defender so the dashboard
        # always shows the real last scan even without running one via RMM.
        try:
            mp = _ps_json(
                "Get-MpComputerStatus | Select-Object "
                "@{N='QEnd';E={if($_.QuickScanEndTime){$_.QuickScanEndTime.ToString('yyyy-MM-dd HH:mm:ss')}else{''}}},"
                "@{N='FEnd';E={if($_.FullScanEndTime){$_.FullScanEndTime.ToString('yyyy-MM-dd HH:mm:ss')}else{''}}} "
                "| ConvertTo-Json -Compress",
                timeout=12,
            )
            if mp:
                q_end = mp.get("QEnd") or ""
                f_end = mp.get("FEnd") or ""
                # Pick whichever is more recent
                if f_end and (not q_end or f_end > q_end):
                    security["last_scan"] = {"type": "full",  "time": f_end}
                elif q_end:
                    security["last_scan"] = {"type": "quick", "time": q_end}
        except Exception:
            pass
        result["security"] = security

    # -- BitLocker ----------------------------------------------------------
    try:
        raw_bl = _ps_json(
            "Get-BitLockerVolume -EA SilentlyContinue | "
            "Select-Object MountPoint,VolumeStatus,EncryptionMethod,ProtectionStatus,"
            "@{N='Keys';E={"
            "  $_.KeyProtector "
            "  | Where-Object {$_.KeyProtectorType -eq 'RecoveryPassword'} "
            "  | Select-Object -ExpandProperty RecoveryPassword "
            "}} | "
            "ConvertTo-Json -Compress",
            timeout=15,
        )
        blvols = []
        for v in _normalise_ps_list(raw_bl):
            keys = v.get("Keys") or []
            if isinstance(keys, str):
                keys = [keys]
            blvols.append({
                "drive":     v.get("MountPoint") or "",
                "status":    v.get("VolumeStatus") or "",
                "method":    v.get("EncryptionMethod") or "",
                "protected": str(v.get("ProtectionStatus", "")) == "1",
                "keys":      [k for k in keys if k],
            })
        if blvols:
            result["bitlocker"] = blvols
    except Exception:
        pass

    # -- TPM ----------------------------------------------------------------
    try:
        tpm = _ps_json(
            "Get-Tpm -EA SilentlyContinue | "
            "Select-Object TpmPresent,TpmEnabled,TpmActivated,ManufacturerVersion | "
            "ConvertTo-Json -Compress"
        )
        if tpm and isinstance(tpm, dict):
            ver_raw = tpm.get("ManufacturerVersion") or ""
            ver = ver_raw.split("\x00")[0].strip()
            result["tpm"] = {
                "present":   bool(tpm.get("TpmPresent")),
                "enabled":   bool(tpm.get("TpmEnabled")),
                "activated": bool(tpm.get("TpmActivated")),
                "version":   ver,
            }
    except Exception:
        pass

    # -- Windows Activation -------------------------------------------------
    try:
        lic_ps = _ps_json(
            "$l=Get-CimInstance SoftwareLicensingProduct "
            "-Filter \"ApplicationId='55c92734-d682-4d71-983e-d6ec3f16059f' and PartialProductKey <> null\" "
            "-EA SilentlyContinue | Select-Object -First 1 LicenseStatus,Name;"
            "if($l){@{status=$l.LicenseStatus;name=$l.Name}|ConvertTo-Json -Compress}else{'null'}"
        )
        if lic_ps and isinstance(lic_ps, dict):
            result["windows_licensed"] = (lic_ps.get("status") == 1)
    except Exception:
        pass

    # -- Local Administrators -----------------------------------------------
    try:
        raw_adm = _ps_json(
            "Get-LocalGroupMember -Group 'Administrators' -EA SilentlyContinue "
            "| Select-Object Name,ObjectClass | ConvertTo-Json -Compress"
        )
        admins = []
        for a in _normalise_ps_list(raw_adm):
            name = (a.get("Name") or "").split("\\")[-1]
            if name:
                admins.append({"name": name, "type": a.get("ObjectClass") or ""})
        if admins:
            result["local_admins"] = admins
    except Exception:
        pass

    # -- Printers -----------------------------------------------------------
    try:
        raw_prn = _ps_json(
            "Get-Printer -EA SilentlyContinue "
            "| Select-Object Name,Default,PrinterStatus,PortName | ConvertTo-Json -Compress"
        )
        printers = []
        for p in _normalise_ps_list(raw_prn):
            if p.get("Name"):
                printers.append({
                    "name":    p.get("Name") or "",
                    "default": bool(p.get("Default")),
                    "status":  str(p.get("PrinterStatus") or ""),
                    "port":    p.get("PortName") or "",
                })
        if printers:
            result["printers"] = printers
    except Exception:
        pass

    # -- USB / External Devices ---------------------------------------------
    try:
        raw_usb = _ps_json(
            "Get-PnpDevice -Status 'OK' -EA SilentlyContinue "
            "| Where-Object {$_.Class -in @('DiskDrive','Image','Bluetooth','Ports','Camera','Media','AndroidUsbDeviceClass','WPD') "
            "  -or ($_.Class -eq 'USB' -and $_.FriendlyName -notmatch 'Root|Host|Hub|Controller|Enumerator')} "
            "| Where-Object {$_.FriendlyName "
            "  -and $_.FriendlyName -notmatch 'Root Hub|Host Controller|Enumerator|PCI|ACPI|Composite'} "
            "| Select-Object FriendlyName,Class | ConvertTo-Json -Compress",
            timeout=15,
        )
        usb = []
        seen: set = set()
        for d in _normalise_ps_list(raw_usb):
            nm = d.get("FriendlyName") or ""
            if nm and nm not in seen:
                seen.add(nm)
                usb.append({"name": nm, "class": d.get("Class") or ""})
        if usb:
            result["usb_devices"] = usb
    except Exception:
        pass

    # -- Mapped Network Drives ----------------------------------------------
    try:
        raw_drv = _ps_json(
            "Get-PSDrive -PSProvider FileSystem -EA SilentlyContinue "
            "| Where-Object {$_.DisplayRoot -like '\\\\*'} "
            "| Select-Object Name,DisplayRoot | ConvertTo-Json -Compress"
        )
        drives = []
        for d in _normalise_ps_list(raw_drv):
            if d.get("DisplayRoot"):
                drives.append({"letter": (d.get("Name") or "") + ":", "path": d.get("DisplayRoot") or ""})
        if drives:
            result["mapped_drives"] = drives
    except Exception:
        pass

    # -- Startup Programs ---------------------------------------------------
    try:
        raw_su = _ps_json(
            "Get-CimInstance Win32_StartupCommand "
            "| Select-Object Name,Command,Location,User | ConvertTo-Json -Compress"
        )
        startup = []
        _sys_users = {"nt authority\\local service", "nt authority\\network service",
                      "nt authority\\system", "local service", "network service"}
        for s in _normalise_ps_list(raw_su):
            user = (s.get("User") or "").lower()
            if s.get("Name") and user not in _sys_users:
                startup.append({
                    "name":     s.get("Name") or "",
                    "command":  s.get("Command") or "",
                    "location": s.get("Location") or "",
                    "user":     s.get("User") or "",
                })
        if startup:
            result["startup"] = startup
    except Exception:
        pass

    # -- Power Plan ---------------------------------------------------------
    try:
        raw_pp = _ps_json(
            "Get-CimInstance -Namespace root/cimv2/power -ClassName Win32_PowerPlan "
            "| Where-Object {$_.IsActive} "
            "| Select-Object ElementName | ConvertTo-Json -Compress"
        )
        plans = _normalise_ps_list(raw_pp)
        if plans:
            result["power_plan"] = plans[0].get("ElementName") or ""
    except Exception:
        pass

    # -- Last Successful Windows Update ------------------------------------
    try:
        raw_wu = _ps_json(
            "$s=New-Object -ComObject Microsoft.Update.Session;"
            "$h=$s.CreateUpdateSearcher();"
            "$c=$h.GetTotalHistoryCount();"
            "if($c -gt 0){"
            "  $r=$h.QueryHistory(0,[Math]::Min($c,100))"
            "    |Where-Object{$_.ResultCode -eq 2}"
            "    |Sort-Object Date -Descending|Select-Object -First 1 Date,Title;"
            "  if($r){@{date=$r.Date.ToString('yyyy-MM-dd');title=$r.Title}|ConvertTo-Json -Compress}"
            "  else{'null'}}"
            "else{'null'}",
            timeout=20,
        )
        if raw_wu and isinstance(raw_wu, dict):
            result["last_wu"] = {"date": raw_wu.get("date", ""), "title": raw_wu.get("title", "")}
    except Exception:
        pass

    # -- RDP Enabled --------------------------------------------------------
    try:
        rdp_ps = _ps_json(
            "@{enabled=((Get-ItemProperty "
            "'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server' "
            "-Name fDenyTSConnections -EA SilentlyContinue).fDenyTSConnections -eq 0)}"
            "|ConvertTo-Json -Compress"
        )
        if rdp_ps is not None:
            result["rdp_enabled"] = bool(rdp_ps.get("enabled"))
    except Exception:
        pass

    # -- Pending Reboot -----------------------------------------------------
    try:
        reboot_ps = _ps_json(
            "$r=@{};"
            # Windows Update / CBS
            "$r.cbs=[bool](Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing' -Name RebootPending -EA SilentlyContinue);"
            # Pending file rename ops
            "$r.pfro=[bool](Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager' -Name PendingFileRenameOperations -EA SilentlyContinue);"
            # Windows Update AU
            "$r.au=[bool](Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update' -Name RebootRequired -EA SilentlyContinue);"
            "@{pending=($r.cbs -or $r.pfro -or $r.au);reasons=($r|Where-Object{$_.Value}|Select-Object -ExpandProperty Keys)}|ConvertTo-Json -Compress"
        )
        if reboot_ps and isinstance(reboot_ps, dict):
            result["reboot_pending"] = bool(reboot_ps.get("pending"))
    except Exception:
        pass

    # -- Default Browser ------------------------------------------------------
    try:
        brw_ps = _ps_json(
            # Agent runs as SYSTEM so HKCU is wrong; resolve the logged-in user SID
            "$u=(Get-CimInstance Win32_ComputerSystem -EA SilentlyContinue).UserName;"
            "if($u){"
            "  $sid=(New-Object System.Security.Principal.NTAccount($u)).Translate([System.Security.Principal.SecurityIdentifier]).Value;"
            "  New-PSDrive -Name HKU2 -PSProvider Registry -Root HKEY_USERS -EA SilentlyContinue|Out-Null;"
            "  $p=(Get-ItemProperty \"HKU2:\\$sid\\SOFTWARE\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\https\\UserChoice\" -Name ProgId -EA SilentlyContinue).ProgId;"
            "  if($p){$p|ConvertTo-Json -Compress}else{'null'}"
            "}else{'null'}"
        )
        if brw_ps and isinstance(brw_ps, str):
            prog = brw_ps.strip()
            _brw_map = {
                "ChromeHTML": "Google Chrome", "MSEdgeHTM": "Microsoft Edge",
                "FirefoxURL": "Firefox", "BraveHTML": "Brave",
                "OperaStable": "Opera", "SafariHTML": "Safari",
                "IE.HTTP": "Internet Explorer",
            }
            result["default_browser"] = next(
                (v for k, v in _brw_map.items() if k.lower() in prog.lower()), prog
            )
    except Exception:
        pass

    # -- DNS Servers --------------------------------------------------------
    try:
        dns_ps = _ps_json(
            "Get-DnsClientServerAddress -AddressFamily IPv4 -EA SilentlyContinue "
            "| Where-Object {$_.ServerAddresses} "
            "| Select-Object InterfaceAlias,ServerAddresses | ConvertTo-Json -Compress"
        )
        dns_list = []
        seen_dns: set = set()
        for iface in _normalise_ps_list(dns_ps):
            addrs = iface.get("ServerAddresses") or []
            if isinstance(addrs, str):
                addrs = [addrs]
            for a in addrs:
                if a and a not in seen_dns:
                    seen_dns.add(a)
                    dns_list.append(a)
        if dns_list:
            result["dns_servers"] = dns_list
    except Exception:
        pass

    # -- Group Policy Last Refresh ------------------------------------------
    # The core CSE key ({00000000-...}) stores the last machine GP-processing
    # time as a FILETIME in values named EndTimeHi/EndTimeLo. Earlier code read
    # EndTime2High/EndTime2Low, which do NOT exist on this key -> always null ->
    # the field was empty on every domain box. Read the correct names, and fall
    # back to GroupPolicy/Operational event 8004 (machine policy processing
    # complete) when the registry values are absent.
    try:
        gp_ps = _ps_json(
            "$k='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Group Policy\\State\\Machine\\Extension-List\\{00000000-0000-0000-0000-000000000000}';"
            "$v=Get-ItemProperty $k -EA SilentlyContinue;"
            "$dt=$null;"
            "if($v -and $v.EndTimeHi -ne $null -and $v.EndTimeLo -ne $null){"
            "  try{$dt=[datetime]::FromFileTime((([int64]$v.EndTimeHi -shl 32) -bor [uint32]$v.EndTimeLo))}catch{}"
            "}"
            "if(-not $dt -or $dt.Year -lt 2000){"
            "  try{$dt=(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-GroupPolicy/Operational';Id=8004} -MaxEvents 1 -EA SilentlyContinue).TimeCreated}catch{}"
            "}"
            "if($dt -and $dt.Year -ge 2000){@{time=$dt.ToString('yyyy-MM-dd HH:mm:ss')}|ConvertTo-Json -Compress}else{'null'}"
        )
        if gp_ps and isinstance(gp_ps, dict) and gp_ps.get("time"):
            t = gp_ps["time"]
            if not t.startswith("1600") and not t.startswith("1601"):
                result["gp_last_refresh"] = t
    except Exception:
        pass

    # -- Disk SMART Health --------------------------------------------------
    try:
        smart_ps = _ps_json(
            "Get-PhysicalDisk -EA SilentlyContinue "
            "| Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,SerialNumber,"
            "@{N='SizeGB';E={[math]::Round($_.Size/1GB,0)}} "
            "| ConvertTo-Json -Compress",
            timeout=15,
        )
        smart_list = []
        for d in _normalise_ps_list(smart_ps):
            smart_list.append({
                "name":   d.get("FriendlyName") or "",
                "type":   d.get("MediaType") or "",
                "health": d.get("HealthStatus") or "",
                "status": d.get("OperationalStatus") or "",
                "size_gb": d.get("SizeGB"),
                "serial": (d.get("SerialNumber") or "").strip(),
            })
        if smart_list:
            result["disk_health"] = smart_list
    except Exception:
        pass

    # -- Last BSOD / System Crash -------------------------------------------
    try:
        bsod_ps = _ps_json(
            "$e=Get-WinEvent -FilterHashtable @{LogName='System';Id=@(41,1001,6008);StartTime=(Get-Date).AddDays(-30)} "
            "-EA SilentlyContinue | Select-Object -First 5 TimeCreated,Id,Message;"
            "if($e){$e|Select-Object @{N='time';E={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')}},Id,@{N='msg';E={$_.Message.Split(\"`n\")[0].Trim()}}|ConvertTo-Json -Compress}else{'null'}",
            timeout=15,
        )
        bsod_list = []
        for ev in _normalise_ps_list(bsod_ps):
            bsod_list.append({
                "time": ev.get("time") or "",
                "id":   str(ev.get("Id") or ""),
                "msg":  (ev.get("msg") or "")[:120],
            })
        if bsod_list:
            result["last_bsod"] = bsod_list
        else:
            result["last_bsod"] = []  # checked, no crashes found
    except Exception:
        pass

    # -- Monitor Info -------------------------------------------------------
    try:
        mon_ps = _ps_json(
            "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorID -EA SilentlyContinue "
            "| Select-Object @{N='Mfr';E={[System.Text.Encoding]::ASCII.GetString($_.ManufacturerName -ne 0)}},"
            "@{N='Model';E={[System.Text.Encoding]::ASCII.GetString($_.UserFriendlyName -ne 0)}},"
            "@{N='Serial';E={[System.Text.Encoding]::ASCII.GetString($_.SerialNumberID -ne 0)}} "
            "| ConvertTo-Json -Compress",
            timeout=10,
        )
        monitors = []
        for m in _normalise_ps_list(mon_ps):
            model = (m.get("Model") or "").strip().rstrip("\x00").strip()
            if model:
                monitors.append({
                    "model":  model,
                    "mfr":    (m.get("Mfr") or "").strip().rstrip("\x00").strip(),
                    "serial": (m.get("Serial") or "").strip().rstrip("\x00").strip(),
                })
        if monitors:
            result["monitors"] = monitors
    except Exception:
        pass

    # -- Windows Update Channel ---------------------------------------------
    try:
        wu_ch = _ps_json(
            "$b=(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\WindowsSelfHost\\Applicability' "
            "-Name BranchName -EA SilentlyContinue).BranchName;"
            "$r=(Get-ItemProperty 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate' "
            "-Name TargetReleaseVersion,TargetReleaseVersionInfo -EA SilentlyContinue);"
            "@{branch=$b;target=$r.TargetReleaseVersionInfo}|ConvertTo-Json -Compress"
        )
        if wu_ch and isinstance(wu_ch, dict):
            ch = wu_ch.get("branch") or wu_ch.get("target") or ""
            if ch:
                result["wu_channel"] = ch
    except Exception:
        pass

    # -- Screen Lock Timeout --------------------------------------------------
    try:
        lock_ps = _ps_json(
            # Display timeout from power policy (system-wide, no user context needed)
            "$lines=powercfg /query SCHEME_CURRENT SUB_DISPLAY VIDEOIDLE 2>$null;"
            "$m=$lines|Select-String 'Current AC Power Setting Index:\\s+(0x[0-9a-fA-F]+)'|Select-Object -First 1;"
            "$secs=if($m){[Convert]::ToInt32($m.Matches.Groups[1].Value,16)}else{0};"
            "$ss_sec=0;$ss_secure=$false;"
            "$u=(Get-CimInstance Win32_ComputerSystem -EA SilentlyContinue).UserName;"
            "if($u){"
            "  $sid=(New-Object System.Security.Principal.NTAccount($u)).Translate([System.Security.Principal.SecurityIdentifier]).Value;"
            "  New-PSDrive -Name HKU3 -PSProvider Registry -Root HKEY_USERS -EA SilentlyContinue|Out-Null;"
            "  $ss=Get-ItemProperty \"HKU3:\\$sid\\Control Panel\\Desktop\" -Name ScreenSaveTimeOut,ScreenSaverIsSecure -EA SilentlyContinue;"
            "  $ss_sec=[int]($ss.ScreenSaveTimeOut -as [int]);"
            "  $ss_secure=($ss.ScreenSaverIsSecure -eq '1')"
            "};"
            "@{timeout_sec=if($secs -gt 0){$secs}elseif($ss_sec -gt 0){$ss_sec}else{0};secure=$ss_secure}|ConvertTo-Json -Compress"
        )
        if lock_ps and isinstance(lock_ps, dict):
            result["screen_lock"] = {
                "timeout_sec": int(lock_ps.get("timeout_sec") or 0),
                "secure":      bool(lock_ps.get("secure")),
            }
    except Exception:
        pass

    # -- Listening Ports ----------------------------------------------------
    try:
        port_ps = _ps_json(
            "Get-NetTCPConnection -State Listen -EA SilentlyContinue "
            "| Where-Object {$_.LocalAddress -in @('0.0.0.0','::','127.0.0.1','::1') -or $_.LocalAddress -notlike '169.*'} "
            "| Select-Object LocalPort,LocalAddress,"
            "@{N='PID';E={$_.OwningProcess}},"
            "@{N='Proc';E={(Get-Process -Id $_.OwningProcess -EA SilentlyContinue).Name}} "
            "| Sort-Object LocalPort | ConvertTo-Json -Compress",
            timeout=15,
        )
        ports = []
        seen_ports: set = set()
        for p in _normalise_ps_list(port_ps):
            port = p.get("LocalPort")
            if port and port not in seen_ports:
                seen_ports.add(port)
                ports.append({
                    "port":    int(port),
                    "addr":    p.get("LocalAddress") or "",
                    "process": p.get("Proc") or "",
                })
        if ports:
            result["open_ports"] = ports
    except Exception:
        pass

    # -- Security Event Telemetry (servers + all Windows machines) -------------
    # These values feed the Windows Server monitoring profile checks.
    try:
        sec_events_ps = _ps_json(
            # Failed logons (4625) in last hour
            "$failed=(Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625;StartTime=(Get-Date).AddHours(-1)} "
            "-EA SilentlyContinue|Measure-Object).Count;"
            # Security log cleared (1102) in last 24h
            "$logcleared=(Get-WinEvent -FilterHashtable @{LogName='Security';Id=1102;StartTime=(Get-Date).AddHours(-24)} "
            "-EA SilentlyContinue|Measure-Object).Count;"
            # New local user created (4720) in last 24h
            "$newuser=(Get-WinEvent -FilterHashtable @{LogName='Security';Id=4720;StartTime=(Get-Date).AddHours(-24)} "
            "-EA SilentlyContinue|Measure-Object).Count;"
            # Group membership changed (4732) in last 24h
            "$grpchange=(Get-WinEvent -FilterHashtable @{LogName='Security';Id=4732;StartTime=(Get-Date).AddHours(-24)} "
            "-EA SilentlyContinue|Measure-Object).Count;"
            # Service crashes (7034) in last 24h
            "$svccrash=(Get-WinEvent -FilterHashtable @{LogName='System';Id=7034;StartTime=(Get-Date).AddHours(-24)} "
            "-EA SilentlyContinue|Measure-Object).Count;"
            # Firewall: count of disabled profiles
            "$fwoff=(Get-NetFirewallProfile -EA SilentlyContinue|Where-Object{$_.Enabled -ne $true}|Measure-Object).Count;"
            "@{failed_logons_1h=$failed;sec_log_cleared_24h=$logcleared;new_user_24h=$newuser;"
            "admin_group_change_24h=$grpchange;svc_crash_24h=$svccrash;firewall_profiles_disabled=$fwoff}"
            "|ConvertTo-Json -Compress",
            timeout=30,
        )
        if sec_events_ps and isinstance(sec_events_ps, dict):
            result["security_events"] = {
                "failed_logons_1h":         int(sec_events_ps.get("failed_logons_1h") or 0),
                "sec_log_cleared_24h":      int(sec_events_ps.get("sec_log_cleared_24h") or 0),
                "new_user_24h":             int(sec_events_ps.get("new_user_24h") or 0),
                "admin_group_change_24h":   int(sec_events_ps.get("admin_group_change_24h") or 0),
                "svc_crash_24h":            int(sec_events_ps.get("svc_crash_24h") or 0),
                "firewall_profiles_disabled": int(sec_events_ps.get("firewall_profiles_disabled") or 0),
            }
    except Exception:
        pass

    # Pack IT-detail fields into a single sysinfo subdict -> stored as sysinfo_json
    _si_keys = (
        "bitlocker", "tpm", "windows_licensed", "local_admins",
        "printers", "usb_devices", "mapped_drives", "startup",
        "power_plan", "last_wu", "rdp_enabled",
        "reboot_pending", "default_browser", "dns_servers", "gp_last_refresh",
        "disk_health", "last_bsod", "monitors", "wu_channel",
        "screen_lock", "open_ports", "security_events",
    )
    _si = {k: result.pop(k) for k in _si_keys if k in result}
    if _si:
        result["sysinfo"] = _si

    return result


def _collect_session_events(since_hours: int = 48) -> list:
    """Collect logon/logoff/lock/unlock/sleep/wake events from Windows event log.
    Each event type is queried separately so one failure doesn't block others.
    Returns list of {type, user, time} dicts sorted newest-first.
    """
    skip = {"SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE", "ANONYMOUS LOGON"}
    events = []

    def _run(script: str):
        return _ps_json(script, timeout=20)

    since_expr = f"(Get-Date).AddHours(-{since_hours})"

    # Logon: event 4624, Properties[8]=LogonType, Properties[5]=TargetUserName
    raw = _run(
        f"Get-WinEvent -FilterHashtable @{{LogName='Security';Id=4624;StartTime={since_expr}}} "
        "-EA SilentlyContinue -MaxEvents 200"
        "|Where-Object{$_.Properties[8].Value -in @(2,10)"
        " -and $_.Properties[5].Value -notmatch '^(SYSTEM|LOCAL SERVICE|NETWORK SERVICE|ANONYMOUS LOGON|DWM-\\d+|UMFD-\\d+)$'"
        " -and !$_.Properties[5].Value.EndsWith('$')}"
        "|Select-Object -First 50"
        "|ForEach-Object{[pscustomobject]@{t='logon';u=$_.Properties[5].Value;ts=$_.TimeCreated.ToString('o')}}"
        "|ConvertTo-Json -Compress"
    )
    for e in _normalise_ps_list(raw):
        if e.get("u") and e.get("u") not in skip:
            events.append({"type": "logon", "user": e["u"], "time": e["ts"]})

    # Logoff: event 4647, Properties[1]=TargetUserName
    raw = _run(
        f"Get-WinEvent -FilterHashtable @{{LogName='Security';Id=4647;StartTime={since_expr}}} "
        "-EA SilentlyContinue -MaxEvents 200"
        "|Where-Object{$_.Properties[1].Value -notmatch '^(SYSTEM|LOCAL SERVICE|NETWORK SERVICE|ANONYMOUS LOGON|DWM-\\d+|UMFD-\\d+)$'"
        " -and !$_.Properties[1].Value.EndsWith('$')}"
        "|Select-Object -First 50"
        "|ForEach-Object{[pscustomobject]@{t='logoff';u=$_.Properties[1].Value;ts=$_.TimeCreated.ToString('o')}}"
        "|ConvertTo-Json -Compress"
    )
    for e in _normalise_ps_list(raw):
        if e.get("u") and e.get("u") not in skip:
            events.append({"type": "logoff", "user": e["u"], "time": e["ts"]})

    # Lock: event 4800, Properties[1]=TargetUserName
    raw = _run(
        f"Get-WinEvent -FilterHashtable @{{LogName='Security';Id=4800;StartTime={since_expr}}} "
        "-EA SilentlyContinue -MaxEvents 100"
        "|Where-Object{!$_.Properties[1].Value.EndsWith('$')}"
        "|Select-Object -First 50"
        "|ForEach-Object{[pscustomobject]@{t='lock';u=$_.Properties[1].Value;ts=$_.TimeCreated.ToString('o')}}"
        "|ConvertTo-Json -Compress"
    )
    for e in _normalise_ps_list(raw):
        u = e.get("u", "")
        if u not in skip:
            events.append({"type": "lock", "user": u, "time": e["ts"]})

    # Unlock: event 4801, Properties[1]=TargetUserName
    raw = _run(
        f"Get-WinEvent -FilterHashtable @{{LogName='Security';Id=4801;StartTime={since_expr}}} "
        "-EA SilentlyContinue -MaxEvents 100"
        "|Where-Object{!$_.Properties[1].Value.EndsWith('$')}"
        "|Select-Object -First 50"
        "|ForEach-Object{[pscustomobject]@{t='unlock';u=$_.Properties[1].Value;ts=$_.TimeCreated.ToString('o')}}"
        "|ConvertTo-Json -Compress"
    )
    for e in _normalise_ps_list(raw):
        u = e.get("u", "")
        if u not in skip:
            events.append({"type": "unlock", "user": u, "time": e["ts"]})

    # Sleep: Kernel-Power event 42
    raw = _run(
        f"Get-WinEvent -FilterHashtable @{{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=42;StartTime={since_expr}}} "
        "-EA SilentlyContinue -MaxEvents 50"
        "|ForEach-Object{[pscustomobject]@{t='sleep';u='';ts=$_.TimeCreated.ToString('o')}}"
        "|ConvertTo-Json -Compress"
    )
    for e in _normalise_ps_list(raw):
        events.append({"type": "sleep", "user": "", "time": e["ts"]})

    # Wake: Power-Troubleshooter event 1
    raw = _run(
        f"Get-WinEvent -FilterHashtable @{{LogName='System';ProviderName='Microsoft-Windows-Power-Troubleshooter';Id=1;StartTime={since_expr}}} "
        "-EA SilentlyContinue -MaxEvents 50"
        "|ForEach-Object{[pscustomobject]@{t='wake';u='';ts=$_.TimeCreated.ToString('o')}}"
        "|ConvertTo-Json -Compress"
    )
    for e in _normalise_ps_list(raw):
        events.append({"type": "wake", "user": "", "time": e["ts"]})

    # Sort newest-first for UI display
    events.sort(key=lambda x: x.get("time", ""), reverse=True)
    return events


def _collect_software() -> list:
    """Return installed software from registry (HKLM 64-bit, 32-bit, HKCU)."""
    raw = _ps_json(
        "$p=@("
        "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*');"
        "$p|ForEach-Object{Get-ItemProperty $_ -EA SilentlyContinue}"
        "|Where-Object{$_.DisplayName -and $_.DisplayName.Trim()}"
        "|Select-Object DisplayName,DisplayVersion,Publisher,InstallDate"
        "|Sort-Object DisplayName"
        "|ConvertTo-Json -Compress",
        timeout=60,
    )
    # Coerce every field to str before .strip(): registry DisplayVersion/InstallDate
    # are frequently REG_DWORD numbers, which ConvertTo-Json emits as JSON ints, so
    # `(int or "").strip()` raised AttributeError and made the WHOLE collection throw
    # -> the agent reported 0 software. A box only needed ONE such program to lose its
    # entire inventory (hit big boxes like BRIAN-MSI, 616 apps, the hardest).
    def _s(v):
        return str(v).strip() if v is not None else ""
    seen: set = set()
    results = []
    for item in _normalise_ps_list(raw):
        name = _s(item.get("DisplayName"))
        if not name or name in seen:
            continue
        seen.add(name)
        results.append({
            "name":         name,
            "version":      _s(item.get("DisplayVersion")),
            "publisher":    _s(item.get("Publisher")),
            "install_date": _s(item.get("InstallDate")),
        })
    return results


def post_software_inventory(tracker_url: str, agent_id: str, token: str) -> None:
    """Collect installed software and POST it to the tracker HTTP API.
    Called on startup and every 24 hours. Silently ignores errors."""
    try:
        apps = _collect_software()
        url = f"{tracker_url}/api/rmm/{agent_id}/software?token={token}"
        import urllib.request as _ur
        payload = json.dumps(apps).encode()
        req = _ur.Request(url, data=payload,
                          headers={"Content-Type": "application/json", "User-Agent": f"CirqueRMM/{AGENT_VERSION}"},
                          method="POST")
        ssl_ctx = _ssl_ctx()
        with _ur.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            body = json.loads(resp.read())
            print(f"[software] Posted {body.get('count', 0)} apps to tracker", flush=True)
    except Exception as e:
        print(f"[software] post_software_inventory failed: {e}", flush=True)


def _collect_patches() -> list:
    """Return list of installed Windows hotfixes."""
    raw = _ps_json(
        "Get-HotFix | Select-Object HotFixID,Description,"
        "@{N='InstalledOn';E={if($_.InstalledOn){$_.InstalledOn.ToString('yyyy-MM-dd')}else{''}}} | "
        "Sort-Object InstalledOn -Descending | Select-Object -First 100 | "
        "ConvertTo-Json -Compress",
        timeout=20,
    )
    patches = []
    for p in _normalise_ps_list(raw):
        patches.append({
            "hotfix_id":    p.get("HotFixID") or "",
            "description":  p.get("Description") or "",
            "installed_on": p.get("InstalledOn") or "",
        })
    return patches


# ---------------------------------------------------------------------------
# Core telemetry collection
# ---------------------------------------------------------------------------

def _collect_battery_info() -> dict:
    """Battery model + health/wear + cycle count via `powercfg /batteryreport`.

    Returns {} on desktops / any failure. Keys (all optional):
      model, serial, chemistry, health_pct (float), cycles (int),
      design_capacity, full_charge_capacity.

    Uses powercfg (works in the SYSTEM/agent context) rather than
    root\\wmi BatteryStaticData/FullChargedCapacity, which throws
    "Generic failure" under SYSTEM. The XML report's
    BatteryReport.Batteries.Battery node carries Manufacturer, Id (model),
    SerialNumber, Chemistry, DesignCapacity, FullChargeCapacity, CycleCount.
    """
    import tempfile, xml.etree.ElementTree as _ET
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".xml", prefix="cirque_batt_")
        os.close(fd)
        # powercfg writes the XML report to tmp_path. -duration 1 keeps the run
        # short (we only need the static battery node, not usage history).
        proc = subprocess.run(
            ["powercfg", "/batteryreport", "/output", tmp_path, "/xml"],
            capture_output=True, text=True, errors="replace", timeout=60,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) == 0:
            return {}
        with open(tmp_path, "r", encoding="utf-8", errors="replace") as fh:
            xml_text = fh.read()
        if not xml_text.strip():
            return {}
        root = _ET.fromstring(xml_text)
        # The report namespaces everything; strip the namespace by matching on
        # the local tag name so we don't have to hardcode the (versioned) ns URI.
        def _local(tag):
            return tag.rsplit("}", 1)[-1] if "}" in tag else tag

        def _find_first(node, name):
            for el in node.iter():
                if _local(el.tag) == name:
                    return el
            return None

        batt = _find_first(root, "Battery")
        if batt is None:
            return {}

        vals = {}
        for child in batt:
            vals[_local(child.tag)] = (child.text or "").strip()

        def _num(name):
            try:
                return int(float(vals.get(name) or ""))
            except Exception:
                return None

        design = _num("DesignCapacity")
        full   = _num("FullChargeCapacity")
        health = None
        if design and full and design > 0:
            health = round(full / design * 100.0, 1)

        mfr   = vals.get("Manufacturer") or ""
        model = vals.get("Id") or ""
        # Id is the model designation (e.g. "DELL 4M1JN21"); prefix the
        # manufacturer only if it's not already part of the Id string.
        if mfr and model and mfr.lower() not in model.lower():
            model = f"{mfr} {model}"
        elif not model:
            model = mfr

        return {
            "model":                model or "",
            "serial":               vals.get("SerialNumber") or "",
            "chemistry":            vals.get("Chemistry") or "",
            "design_capacity":      design,
            "full_charge_capacity": full,
            "health_pct":           health,
            "cycles":               _num("CycleCount"),
        }
    except Exception as e:
        print(f"[battery] collect failed: {e}", flush=True)
        return {}
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def collect_telemetry(agent_id: str) -> dict:
    """Collect real-time telemetry and return a telemetry_update message dict."""
    cpu_pct  = psutil.cpu_percent(interval=1)
    cpu_freq = psutil.cpu_freq()
    mem      = psutil.virtual_memory()
    batt     = psutil.sensors_battery()

    # Disk partitions
    disks = []
    _drive_types = _get_drive_types()  # {C: 3, D: 5, ...}; {} off-Windows
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            # Match the WMI DeviceID (e.g. "C:") against the mountpoint letter.
            _letter = (part.mountpoint or "").strip().rstrip("\\").rstrip("/").upper()
            disk = {
                "device":     part.device,
                "mountpoint": part.mountpoint,
                "total_gb":   round(usage.total  / (1024 ** 3), 1),
                "free_gb":    round(usage.free   / (1024 ** 3), 1),
                "percent":    usage.percent,
                # NTFS/CDFS/exFAT/... -- CDFS strongly implies optical/mounted-ISO.
                "fstype":     part.fstype,
            }
            if _letter in _drive_types:
                disk["drive_type"] = _drive_types[_letter]
            disks.append(disk)
        except Exception:
            pass

    # Network interfaces (skip loopback)
    networks = []
    _wifi_ssids = _get_wifi_ssids()
    for iface, addrs in psutil.net_if_addrs().items():
        if iface.lower().startswith("loopback"):
            continue
        ips = [a.address for a in addrs if a.family == socket.AF_INET]
        # AF_LINK = 18 on macOS, -1 on some Windows builds; also try 23 (AF_PACKET)
        mac = next(
            (a.address for a in addrs
             if a.family not in (socket.AF_INET, socket.AF_INET6)),
            ""
        )
        if not ips and not mac:
            continue
        entry = {"interface": iface, "ips": ips, "mac": mac}
        if iface in _wifi_ssids:
            entry["ssid"] = _wifi_ssids[iface]
        networks.append(entry)

    uptime = int(datetime.now().timestamp() - psutil.boot_time())

    # Timezone EVERY cycle (Get-TimeZone is also collected in the extended
    # on-connect sysinfo path, but that only lands on ~1 row out of every
    # periodic batch; collecting it here keeps `timezone` populated on every
    # telemetry row -- valuable for the TW/China/US distributed fleet).
    _tz_str = ""
    try:
        _tz = _ps_json("Get-TimeZone | Select-Object Id,DisplayName | ConvertTo-Json -Compress")
        if _tz:
            _tz_str = _tz.get("DisplayName") or _tz.get("Id") or ""
    except Exception:
        pass

    # WiFi adapter product name (e.g. "Intel(R) Wi-Fi 6E AX211 160MHz"). The
    # network_json carries only the interface alias ("Wi-Fi") + MAC + SSID, not
    # the hardware model -- operators want the card type on Hardware Info.
    # Empty string when there's no 802.11 adapter (desktops / Ethernet-only).
    _wifi_adapter = ""
    try:
        _wa = _ps_json(
            "Get-NetAdapter | Where-Object { $_.PhysicalMediaType -match '802.11' } "
            "| Select-Object -First 1 -ExpandProperty InterfaceDescription | ConvertTo-Json -Compress"
        )
        if isinstance(_wa, str):
            _wifi_adapter = _wa.strip()
    except Exception:
        pass

    # Battery model + health (replacement-planning). Collected here so it rides
    # the periodic telemetry. Null/empty on desktops (no battery).
    _batt_info = _collect_battery_info() if batt is not None else {}

    return {
        "type":               "telemetry_update",
        "agent_id":           agent_id,
        "agent_version":      AGENT_VERSION,
        "cpu_percent":        cpu_pct,
        "cpu_cores":          psutil.cpu_count(logical=False),
        "cpu_name":           _get_cpu_name(),
        "cpu_freq":           cpu_freq.current if cpu_freq else None,
        "ram_percent":        mem.percent,
        "ram_total_gb":       round(mem.total     / (1024 ** 3), 2),
        "ram_available_gb":   round(mem.available / (1024 ** 3), 2),
        "battery_present":    batt is not None,
        "battery_percent":    batt.percent          if batt else None,
        "battery_charging":   batt.power_plugged    if batt else None,
        "battery_minutes_left": (
            batt.secsleft / 60
            if batt and not batt.power_plugged and batt.secsleft > 0
            else None
        ),
        "battery_model":      _batt_info.get("model") or None,
        "battery_serial":     _batt_info.get("serial") or None,
        "battery_health_pct": _batt_info.get("health_pct"),
        "battery_cycles":     _batt_info.get("cycles"),
        "battery_chemistry":  _batt_info.get("chemistry") or None,
        "wifi_adapter":       _wifi_adapter,
        "timezone":           _tz_str,
        "disk_json":          disks,
        "network_json":       networks,
        "logged_in_user":     _get_windows_username(),
        "domain":             _get_domain(),
        "uptime_seconds":     uptime,
        "screen_resolution":  _get_screen_resolutions(),
        "os_name":            platform.system() + " " + platform.release(),
        "os_version":         _get_windows_display_version(),
        "os_build":           platform.version(),
        "os_arch":            platform.machine(),
        "public_ip":          _get_public_ip(),
        "captured_at":        datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Patch management -- available updates + remote-triggered install
# ---------------------------------------------------------------------------

def _collect_pending_updates() -> list:
    """Query Windows Update COM API for updates available but not yet installed."""
    script = r"""
try {
    $Session  = New-Object -ComObject Microsoft.Update.Session
    $Searcher = $Session.CreateUpdateSearcher()
    # Trigger an online search first to ensure WU catalog is fresh
    try { $Searcher.Online = $true } catch {}
    $Results  = $Searcher.Search("IsInstalled=0 and IsHidden=0")
    $out = @()
    foreach ($u in $Results.Updates) {
        $kbs = @($u.KBArticleIDs | ForEach-Object {"KB$_"})
        $cat = if ($u.Categories.Count -gt 0) { $u.Categories.Item(0).Name } else { "" }
        $out += @{
            UpdateID       = $u.Identity.UpdateID
            Title          = $u.Title
            Severity       = if ($u.MsrcSeverity) { $u.MsrcSeverity } else { "" }
            KBs            = $kbs
            SizeMB         = [math]::Round($u.MaxDownloadSize / 1MB, 1)
            RebootRequired = ($u.InstallationBehavior.RebootBehavior -gt 0)
            Category       = $cat
        }
    }
    if ($out.Count -eq 0) { "[]" } else { $out | ConvertTo-Json -Compress -Depth 3 }
} catch { "[]" }
""".strip()
    raw = _ps_json(script, timeout=180)  # online search can take a while
    if not raw:
        return []
    items = raw if isinstance(raw, list) else [raw]
    result = []
    for u in items:
        kbs = u.get("KBs") or []
        if isinstance(kbs, str):
            kbs = [kbs]
        result.append({
            "update_id":       u.get("UpdateID") or "",
            "title":           u.get("Title") or "",
            "severity":        u.get("Severity") or "",
            "kb_ids":          kbs,
            "size_mb":         u.get("SizeMB") or 0,
            "reboot_required": bool(u.get("RebootRequired")),
            "category":        u.get("Category") or "",
        })
    return result


# WinForms countdown dialog shown in user's desktop session before reboot
_REBOOT_DIALOG_PS = r"""
param([int]$DurationSeconds = 900)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$remaining = $DurationSeconds
$form = New-Object System.Windows.Forms.Form
$form.Text = "Cirque IT - System Updates Installed"
$form.Size = New-Object System.Drawing.Size(500, 230)
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.TopMost = $true
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$img = New-Object System.Windows.Forms.PictureBox
$img.Image = ([System.Drawing.SystemIcons]::Information).ToBitmap()
$img.Location = New-Object System.Drawing.Point(12, 15)
$img.Size = New-Object System.Drawing.Size(48, 48)
$form.Controls.Add($img)
$lbl = New-Object System.Windows.Forms.Label
$lbl.Text = "Security updates have been installed by your IT department.`nA system restart is required. Please save your work."
$lbl.Location = New-Object System.Drawing.Point(70, 15)
$lbl.Size = New-Object System.Drawing.Size(410, 50)
$lbl.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($lbl)
$tlbl = New-Object System.Windows.Forms.Label
$tlbl.Location = New-Object System.Drawing.Point(70, 78)
$tlbl.Size = New-Object System.Drawing.Size(410, 26)
$tlbl.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$tlbl.ForeColor = [System.Drawing.Color]::DarkRed
$form.Controls.Add($tlbl)
$dBtn = New-Object System.Windows.Forms.Button
$dBtn.Text = "Defer 15 min (once)"
$dBtn.Location = New-Object System.Drawing.Point(70, 155)
$dBtn.Size = New-Object System.Drawing.Size(175, 30)
$form.Controls.Add($dBtn)
$rBtn = New-Object System.Windows.Forms.Button
$rBtn.Text = "Restart Now"
$rBtn.Location = New-Object System.Drawing.Point(260, 155)
$rBtn.Size = New-Object System.Drawing.Size(120, 30)
$form.Controls.Add($rBtn)
$tick = New-Object System.Windows.Forms.Timer
$tick.Interval = 1000
$updateLbl = {
    $m = [math]::Floor($script:remaining / 60)
    $s = $script:remaining % 60
    $tlbl.Text = "Restarting in {0:D2}:{1:D2}" -f $m, $s
    if ($script:remaining -le 60) { $tlbl.ForeColor = [System.Drawing.Color]::Red }
    if ($script:remaining -le 0)  { $tick.Stop(); $form.Close() }
}
& $updateLbl
$tick.Add_Tick({ $script:remaining--; & $script:updateLbl })
$dBtn.Add_Click({ $script:remaining += 900; $dBtn.Enabled = $false; $dBtn.Text = "Defer used" })
$rBtn.Add_Click({ $tick.Stop(); $form.Close() })
$form.Add_Shown({ $tick.Start() })
[void]$form.ShowDialog()
$tick.Stop()
exit 0
"""


def _run_dialog_in_user_session(ps_code: str, timeout_ms: int = 36 * 60 * 1000) -> int:
    """Spawn a PowerShell script in the active user's desktop session.
    Returns the process exit code, or -1 on failure.
    """
    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv  = ctypes.WinDLL("userenv",  use_last_error=True)

    session_id = kernel32.WTSGetActiveConsoleSessionId()
    if session_id == 0xFFFFFFFF:
        return -1
    h_token = wt.HANDLE()
    if not wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(h_token)):
        return -1
    try:
        h_env = ctypes.c_void_p()
        userenv.CreateEnvironmentBlock(ctypes.byref(h_env), h_token, False)
        pub     = os.environ.get("PUBLIC", r"C:\Users\Public")
        ps_path = os.path.join(pub, "_rmm_reboot_dialog.ps1")
        with open(ps_path, "w", encoding="utf-8") as fp:
            fp.write(ps_code)
        ps_exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        cmd    = f'"{ps_exe}" -NoProfile -ExecutionPolicy Bypass -File "{ps_path}"'

        class STARTUPINFO(ctypes.Structure):
            _fields_ = [
                ("cb",              ctypes.c_ulong),  ("lpReserved",  ctypes.c_wchar_p),
                ("lpDesktop",       ctypes.c_wchar_p),("lpTitle",     ctypes.c_wchar_p),
                ("dwX",             ctypes.c_ulong),  ("dwY",         ctypes.c_ulong),
                ("dwXSize",         ctypes.c_ulong),  ("dwYSize",     ctypes.c_ulong),
                ("dwXCountChars",   ctypes.c_ulong),  ("dwYCountChars",ctypes.c_ulong),
                ("dwFillAttribute", ctypes.c_ulong),  ("dwFlags",     ctypes.c_ulong),
                ("wShowWindow",     ctypes.c_ushort), ("cbReserved2", ctypes.c_ushort),
                ("lpReserved2",     ctypes.c_char_p), ("hStdInput",   wt.HANDLE),
                ("hStdOutput",      wt.HANDLE),       ("hStdError",   wt.HANDLE),
            ]
        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", wt.HANDLE), ("hThread",     wt.HANDLE),
                ("dwProcessId", ctypes.c_ulong), ("dwThreadId", ctypes.c_ulong),
            ]
        si = STARTUPINFO()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = "winsta0\\default"
        si.dwFlags   = 0x00000001  # STARTF_USESHOWWINDOW
        si.wShowWindow = 1         # SW_SHOWNORMAL
        pi = PROCESS_INFORMATION()
        # CREATE_UNICODE_ENVIRONMENT -- visible window
        ok = advapi32.CreateProcessAsUserW(
            h_token, None, cmd, None, None, False,
            0x00000400, h_env, None, ctypes.byref(si), ctypes.byref(pi),
        )
        if h_env:
            userenv.DestroyEnvironmentBlock(h_env)
        if not ok:
            return -1
        try:
            WAIT_TIMEOUT = 0x00000102
            ret = kernel32.WaitForSingleObject(pi.hProcess, timeout_ms)
            exit_code = ctypes.c_ulong(0)
            if ret != WAIT_TIMEOUT:
                kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(exit_code))
            else:
                kernel32.TerminateProcess(pi.hProcess, 0)
            return exit_code.value if ret != WAIT_TIMEOUT else 0
        finally:
            kernel32.CloseHandle(pi.hProcess)
            kernel32.CloseHandle(pi.hThread)
            try:
                os.remove(ps_path)
            except Exception:
                pass
    finally:
        kernel32.CloseHandle(h_token)


def _install_patches_wua(update_ids: list, kb_ids=None, titles=None) -> dict:
    """Install specific Windows Updates via the WUA COM API, matching the LIVE pending
    set by UpdateID OR KB article. Defender/signature UpdateIDs rotate (often hourly),
    so a stale snapshot UpdateID frequently no longer matches — but the KB number is
    stable. KBs come from kb_ids and are also parsed out of the titles the server sends.
    After installing, re-scans to report how many targeted updates are still pending
    (post-install verification)."""
    import re as _re
    kbs = set()
    for k in (kb_ids or []):
        m = _re.search(r'(\d{6,7})', str(k))
        if m:
            kbs.add(m.group(1))
    for t in (titles or []):
        for m in _re.findall(r'KB(\d{6,7})', str(t), _re.I):
            kbs.add(m)
    if not update_ids and not kbs:
        return {"installed": 0, "reboot_required": False, "error": "No update IDs or KBs specified"}
    ids_ps = ", ".join(f'"{v}"' for v in (update_ids or []))
    kbs_ps = ", ".join(f'"{v}"' for v in sorted(kbs))
    script = f"""
$ids = @({ids_ps})
$kbs = @({kbs_ps})
function Match-U($u) {{
    if ($ids -contains $u.Identity.UpdateID) {{ return $true }}
    if ($kbs.Count -gt 0) {{ foreach ($kb in $u.KBArticleIDs) {{ if ($kbs -contains [string]$kb) {{ return $true }} }} }}
    return $false
}}
try {{
    $Sess   = New-Object -ComObject Microsoft.Update.Session
    $Search = $Sess.CreateUpdateSearcher()
    $Found  = $Search.Search("IsInstalled=0 and IsHidden=0")
    $coll   = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($u in $Found.Updates) {{ if (Match-U $u) {{ [void]$coll.Add($u) }} }}
    if ($coll.Count -eq 0) {{
        @{{installed=0;reboot_required=$false;still_pending=0;error="No matching pending updates"}} | ConvertTo-Json -Compress
        exit
    }}
    $dl = $Sess.CreateUpdateDownloader(); $dl.Updates = $coll; [void]$dl.Download()
    $inst = $Sess.CreateUpdateInstaller(); $inst.Updates = $coll
    $res  = $inst.Install()
    # post-install verification: re-scan and count targeted updates still pending
    $still = 0
    try {{
        $F2 = $Search.Search("IsInstalled=0 and IsHidden=0")
        foreach ($u in $F2.Updates) {{ if (Match-U $u) {{ $still++ }} }}
    }} catch {{}}
    @{{
        installed       = $coll.Count
        reboot_required = $res.RebootRequired
        result_code     = $res.ResultCode
        still_pending   = $still
        error           = ""
    }} | ConvertTo-Json -Compress
}} catch {{
    @{{installed=0;reboot_required=$false;still_pending=0;error=$_.Exception.Message}} | ConvertTo-Json -Compress
}}
""".strip()
    # Run the (potentially-forever-blocking) WUA download+Install() pass in a child
    # process with a HARD deadline. A stuck Install() can no longer wedge the agent's
    # command executor: on timeout we kill the process tree and return a structured
    # failed/timeout result so the gateway records a real failure (not silent
    # stuck-deploying), and the executor thread is freed for queued commands.
    tmo = _patch_install_timeout()
    result = _ps_json_proc(script, timeout=tmo)
    if result is _PS_TIMEOUT:
        return {
            "installed": 0,
            "reboot_required": False,
            "result_code": None,
            "still_pending": 0,
            "timed_out": True,
            "error": f"Windows Update install timed out after {tmo}s (process killed)",
        }
    if result is None:
        return {"installed": 0, "reboot_required": False, "error": "No output from installer"}
    return {
        "installed":       int(result.get("installed") or 0),
        "reboot_required": bool(result.get("reboot_required")),
        "result_code":     result.get("result_code"),
        "still_pending":   int(result.get("still_pending") or 0),
        "error":           result.get("error") or "",
    }


# Map device_vulnerability.product_name → winget package ID
_WINGET_PRODUCT_MAP: dict[str, str] = {
    # Browsers
    'chrome':                    'Google.Chrome',
    'google_chrome':             'Google.Chrome',
    'firefox':                   'Mozilla.Firefox',
    'mozilla_firefox':           'Mozilla.Firefox',
    'edge_chromium-based':       'Microsoft.Edge',
    'edge':                      'Microsoft.Edge',
    # Developer tools
    'python':                    'Python.Python.3',
    'openssl':                   'ShiningLight.OpenSSL',
    'git':                       'Git.Git',
    'nodejs':                    'OpenJS.NodeJS',
    'node.js':                   'OpenJS.NodeJS',
    # Productivity / collaboration
    'zoom':                      'Zoom.Zoom',
    'teams':                     'Microsoft.Teams',
    'microsoft_teams':           'Microsoft.Teams',
    'slack':                     'SlackTechnologies.Slack',
    'office':                    'Microsoft.Office',
    # Media / creative
    'vlc':                       'VideoLAN.VLC',
    'vlc_media_player':          'VideoLAN.VLC',
    'gimp':                      'GIMP.GIMP',
    # Utilities
    '7-zip':                     '7zip.7zip',
    'notepad++':                 'Notepad++.Notepad++',
    'wireshark':                 'WiresharkTeam.Wireshark',
    '7zip':                      '7zip.7zip',
    'putty':                     'PuTTY.PuTTY',
    'winscp':                    'WinSCP.WinSCP',
    # Java runtimes
    'jre':                       'Oracle.JavaRuntimeEnvironment',
    'java':                      'Oracle.JavaRuntimeEnvironment',
    'java_runtime':              'Oracle.JavaRuntimeEnvironment',
    # PDF / Adobe Reader
    'adobe_acrobat':             'Adobe.Acrobat.Reader.64-bit',
    'acrobat':                   'Adobe.Acrobat.Reader.64-bit',
    'adobe_reader':              'Adobe.Acrobat.Reader.64-bit',
    'reader':                    'Adobe.Acrobat.Reader.64-bit',
    'acrobat_reader_dc':         'Adobe.Acrobat.Reader.64-bit',
    # Databases
    'mariadb':                   'MariaDB.Server',
    # NVIDIA
    'geforce_experience':        'Nvidia.GeForceExperience',
    'geforce':                   'Nvidia.GeForceExperience',
    # Microsoft .NET
    '.net':                      'Microsoft.DotNet.Runtime.9',
    'dotnet':                    'Microsoft.DotNet.Runtime.9',
    'asp.net_core':              'Microsoft.DotNet.AspHostingBundle',
    'aspnetcore':                'Microsoft.DotNet.AspHostingBundle',
    # Meetings / conferencing
    'meetings':                  'Cisco.CiscoWebexMeetings',
    'webex':                     'Cisco.CiscoWebexMeetings',
    'webex_meetings':            'Cisco.CiscoWebexMeetings',
    # Visual Studio (EOL 2017 → upgrade to 2022; 2022 stays current)
    'visual_studio_2022':        'Microsoft.VisualStudio.2022.Community',
    'visual_studio_2019':        'Microsoft.VisualStudio.2019.Community',
    'visual_studio_2017':        'Microsoft.VisualStudio.2022.Community',  # upgrade path
    # Utilities
    'winrar':                    'RARLab.WinRAR',
    'openoffice':                'Apache.OpenOffice',
    'everything':                'voidtools.Everything',
    # Remote access / VPN
    'netextender':               'SonicWall.NetExtender',
    'global_vpn_client':         'SonicWall.GlobalVPNClient',
    # Dell
    'supportassist':             'Dell.SupportAssist',
    # .NET Core / runtime
    '.net_core':                 'Microsoft.DotNet.Runtime.8',
    'dotnet_core':               'Microsoft.DotNet.Runtime.8',
    # JDK (Temurin is free/open, vs Oracle which needs license)
    'jdk':                       'EclipseAdoptium.Temurin.21.JDK',
    'java_development_kit':      'EclipseAdoptium.Temurin.21.JDK',
    # Vim (Windows)
    'vim':                       'vim.vim',
    # Code editors
    'visual_studio_code':        'Microsoft.VisualStudioCode',
    'vscode':                    'Microsoft.VisualStudioCode',
}


def _install_via_winget(package_id: str) -> dict:
    """Install/upgrade a package via winget.
    Uses Windows Task Scheduler to run winget in the logged-in user's interactive
    session.  Task Scheduler resolves the active user session automatically, avoiding
    all the WTS token-handle and session-ID mismatch problems that affect direct
    CreateProcessAsUserW calls from SYSTEM.
    Falls back to SYSTEM-context winget when no user is found.
    Returns a patch-result-compatible dict."""
    import subprocess as _sp
    pub       = os.environ.get("PUBLIC", r"C:\Users\Public")
    inner_ps  = os.path.join(pub, "_rmm_wi.ps1")   # runs AS USER via task scheduler
    outer_ps  = os.path.join(pub, "_rmm_wo.ps1")   # runs AS SYSTEM, creates the task
    out_path  = os.path.join(pub, "_rmm_wr.json")  # result JSON written by inner_ps

    # ── Inner script: executed in the user's own session by Task Scheduler ──────
    # Tries winget upgrade then winget install; writes a compact JSON result.
    inner_script = (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        f"$out = '{out_path}'\n"
        f"$pkg = '{package_id}'\n"
        "$skipCodes = @(-1978335189,-1978334956,-1978335215,-1978335147,-1978334764,-1978335230)\n"
        "if (Test-Path $out) { Remove-Item $out -Force }\n"
        # Sync WinHTTP proxy from WinInet (IE/browser settings) so winget can reach CDNs
        # through corporate proxies. WinHTTP is separate from WinInet and winget uses WinHTTP.
        "try { netsh winhttp import proxy source=ie 2>&1 | Out-Null } catch {}\n"
        "try {\n"
        "  $r1 = winget upgrade --id $pkg --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-String\n"
        "  $e1 = $LASTEXITCODE\n"
        "  if ($e1 -eq 0 -or $r1 -match 'Successfully (installed|upgraded)') {\n"
        "    [pscustomobject]@{installed=1;updates_found=1;reboot_required=$false;error=''} | ConvertTo-Json -Compress | Out-File $out -Encoding utf8; exit\n"
        "  }\n"
        "  $r2 = winget install --id $pkg --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-String\n"
        "  $e2 = $LASTEXITCODE\n"
        "  $all = ($r1 + [char]10 + $r2).Trim()\n"
        "  if ($e2 -eq 0 -or $r2 -match 'Successfully (installed|upgraded)') {\n"
        "    [pscustomobject]@{installed=1;updates_found=1;reboot_required=$false;error=''} | ConvertTo-Json -Compress | Out-File $out -Encoding utf8\n"
        "  } elseif (($skipCodes -contains $e1 -or $r1 -match 'No applicable update|already installed|No newer|No available upgrade') -and\n"
        "            ($skipCodes -contains $e2 -or $r2 -match 'No applicable update|already installed|No newer|No available upgrade')) {\n"
        "    [pscustomobject]@{installed=0;updates_found=0;reboot_required=$false;error='Already up to date'} | ConvertTo-Json -Compress | Out-File $out -Encoding utf8\n"
        "  } else {\n"
        "    $snip = ($all -replace [char]13+[char]10,' ').Trim()\n"
        "    if ($snip.Length -gt 500) { $snip = $snip.Substring(0,500) }\n"
        '    [pscustomobject]@{installed=0;updates_found=0;reboot_required=$false;error="upgrade=$e1 install=$e2: $snip"} | ConvertTo-Json -Compress | Out-File $out -Encoding utf8\n'
        "  }\n"
        "} catch {\n"
        "  [pscustomobject]@{installed=0;updates_found=0;reboot_required=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress | Out-File $out -Encoding utf8\n"
        "}\n"
    )

    # ── Outer script: runs as SYSTEM, schedules the inner PS as the logged-in user ─
    # Uses Task Scheduler LogonType=Interactive so no password is required —
    # Windows uses the user's existing interactive logon token.
    outer_script = (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        f"$innerPs = '{inner_ps}'\n"
        f"$outPath = '{out_path}'\n"
        "$tn = '_RMM_WingetInstall'\n"
        "Unregister-ScheduledTask -TaskName $tn -Confirm:$false -ErrorAction SilentlyContinue\n"
        "if (Test-Path $outPath) { Remove-Item $outPath -Force }\n"
        "\n"
        "# Find the logged-in user (try WMI first, then explorer.exe owner)\n"
        "$user = $null\n"
        "try { $user = (Get-WmiObject Win32_ComputerSystem).UserName } catch {}\n"
        "if (-not $user) {\n"
        "  try {\n"
        "    $exp = Get-WmiObject -Query \"SELECT * FROM Win32_Process WHERE Name='explorer.exe'\" | Select-Object -First 1\n"
        "    if ($exp) { $o = $exp.GetOwner(); if ($o.User) { $user = if ($o.Domain) { \"$($o.Domain)\\$($o.User)\" } else { $o.User } } }\n"
        "  } catch {}\n"
        "}\n"
        "Write-Host \"[winget-task] user=$user\"\n"
        "\n"
        "if ($user) {\n"
        "  $action = New-ScheduledTaskAction -Execute 'powershell.exe' `\n"
        "    -Argument \"-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `\"$innerPs`\"\"\n"
        "  $trigger  = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddSeconds(3))\n"
        "  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest\n"
        "  $settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable $true\n"
        "  Register-ScheduledTask -TaskName $tn -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null\n"
        "  Start-ScheduledTask -TaskName $tn\n"
        "  # Poll until the result file appears (inner PS writes it when done)\n"
        "  $deadline = (Get-Date).AddMinutes(9)\n"
        "  while (-not (Test-Path $outPath) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 3 }\n"
        "  Start-Sleep -Seconds 1  # let the file flush\n"
        "  Unregister-ScheduledTask -TaskName $tn -Confirm:$false -ErrorAction SilentlyContinue\n"
        "  if (-not (Test-Path $outPath)) {\n"
        "    [pscustomobject]@{installed=0;updates_found=0;reboot_required=$false;error='Timeout: no result from user-session winget'} | ConvertTo-Json -Compress | Out-File $outPath -Encoding utf8\n"
        "  }\n"
        "} else {\n"
        "  # No logged-in user -- attempt SYSTEM winget (may fail for user-scoped packages)\n"
        "  Write-Host '[winget-task] no user found; running as SYSTEM'\n"
        "  try { netsh winhttp import proxy source=ie 2>&1 | Out-Null } catch {}\n"
        "  $r1 = winget upgrade --id '{package_id}' --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-String; $e1 = $LASTEXITCODE\n"
        "  $r2 = winget install --id '{package_id}' --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-String; $e2 = $LASTEXITCODE\n"
        "  $all = ($r1 + [char]10 + $r2).Trim()\n"
        "  if ($e1 -eq 0 -or $e2 -eq 0 -or $all -match 'Successfully (installed|upgraded)') {\n"
        "    [pscustomobject]@{installed=1;updates_found=1;reboot_required=$false;error=''} | ConvertTo-Json -Compress | Out-File $outPath -Encoding utf8\n"
        "  } elseif ($all -match 'already installed|No applicable update|No newer|No available upgrade') {\n"
        "    [pscustomobject]@{installed=0;updates_found=0;reboot_required=$false;error='Already up to date'} | ConvertTo-Json -Compress | Out-File $outPath -Encoding utf8\n"
        "  } else {\n"
        "    $snip = ($all -replace [char]13+[char]10,' ').Trim(); if ($snip.Length -gt 400) {$snip=$snip.Substring(0,400)}\n"
        '    [pscustomobject]@{installed=0;updates_found=0;reboot_required=$false;error="SYSTEM winget upgrade=$e1 install=$e2: $snip"} | ConvertTo-Json -Compress | Out-File $outPath -Encoding utf8\n'
        "  }\n"
        "}\n"
    )

    # Write both scripts
    try:
        if os.path.exists(out_path): os.remove(out_path)
        with open(inner_ps, 'w', encoding='utf-8') as f: f.write(inner_script)
        with open(outer_ps, 'w', encoding='utf-8') as f: f.write(outer_script)
    except Exception as e:
        return {"installed": 0, "updates_found": 0, "reboot_required": False,
                "titles": [], "kb_ids": [], "error": f"script write error: {e}"}

    # Run the outer script as SYSTEM (it schedules inner as the user and waits)
    try:
        _sp.run(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
             '-NonInteractive', '-File', outer_ps],
            timeout=620,   # outer script waits up to 9 min; give it 10+
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception as run_err:
        return {"installed": 0, "updates_found": 0, "reboot_required": False,
                "titles": [], "kb_ids": [], "error": f"outer script run error: {run_err}"}

    # Read the JSON result written by the inner script via outer script
    try:
        with open(out_path, 'r', encoding='utf-8-sig') as f:
            result = json.loads(f.read().strip())
        os.remove(out_path)
    except Exception as read_err:
        return {"installed": 0, "updates_found": 0, "reboot_required": False,
                "titles": [], "kb_ids": [], "error": f"no result from winget script: {read_err}"}

    _err_str = (result.get("error") or "").encode("ascii", "replace").decode()
    print(f'[winget] {package_id}: installed={result.get("installed")} error={_err_str!r}', flush=True)
    return {
        "installed":       int(result.get("installed") or 0),
        "updates_found":   int(result.get("updates_found") or 0),
        "reboot_required": bool(result.get("reboot_required")),
        "result_code":     None,
        "titles":          [package_id],
        "kb_ids":          [],
        "error":           result.get("error") or "",
    }


def _find_and_install_cve_patches(cve_ids: list, product_name: str = '') -> dict:
    """Search Windows Update Agent for uninstalled patches that cover the given
    CVE IDs, download and install them.  For products not serviced by Windows
    Update (Edge, OpenSSL, etc.) falls back to winget.  Returns a summary dict
    compatible with the patch_install_result message schema.

    For Windows OS CVEs (windows_10/windows_11), the WUA COM API rarely
    populates $u.CVEIDs on cumulative updates even when the update does address
    those CVEs.  In that case we install ALL pending Security/Critical updates
    which is the correct behaviour (equivalent to 'Install all Windows Updates'
    under Windows Update settings)."""
    if not cve_ids:
        return {"installed": 0, "reboot_required": False, "error": "No CVE IDs provided",
                "updates_found": 0, "titles": [], "kb_ids": []}

    # OS-level products: CVE ID matching via WUA COM is unreliable.
    # Install all pending Security/Critical updates instead.
    _OS_PRODUCTS = {'windows_10', 'windows_11', 'windows_server_2019',
                    'windows_server_2022', 'windows_server_2016', 'windows_server'}
    _pname_lower = (product_name or '').lower().strip()
    _is_os_product = any(op in _pname_lower for op in _OS_PRODUCTS)

    cves_ps = ", ".join(f"'{c}'" for c in cve_ids)

    if _is_os_product:
        # Broad scan: install all pending Security + Critical updates.
        # These cumulative updates address the CVEs but don't expose CVEIDs via COM.
        script = f"""
try {{
    $Sess   = New-Object -ComObject Microsoft.Update.Session
    $Search = $Sess.CreateUpdateSearcher()
    $Found  = $Search.Search("IsInstalled=0 and IsHidden=0 and Type='Software'")
    $coll   = New-Object -ComObject Microsoft.Update.UpdateColl
    $titles = @(); $kbids = @()
    foreach ($u in $Found.Updates) {{
        # MsrcSeverity: Critical, Important, Moderate, Low  (null = non-security)
        $sev = $u.MsrcSeverity
        if ($sev -eq 'Critical' -or $sev -eq 'Important') {{
            [void]$coll.Add($u)
            $titles += $u.Title
            foreach ($kb in $u.KBArticleIDs) {{ $kbids += $kb }}
        }}
    }}
    if ($coll.Count -eq 0) {{
        @{{installed=0;reboot_required=$false;updates_found=0;
           titles=@();kb_ids=@();error="No pending Security/Critical updates found"}} | ConvertTo-Json -Compress
        exit
    }}
    $dl = $Sess.CreateUpdateDownloader()
    $dl.Updates = $coll
    [void]$dl.Download()
    $inst = $Sess.CreateUpdateInstaller()
    $inst.Updates = $coll
    $res  = $inst.Install()
    @{{
        installed       = $coll.Count
        updates_found   = $coll.Count
        reboot_required = $res.RebootRequired
        result_code     = $res.ResultCode
        titles          = $titles
        kb_ids          = $kbids
        error           = ""
    }} | ConvertTo-Json -Compress
}} catch {{
    @{{installed=0;reboot_required=$false;updates_found=0;titles=@();kb_ids=@();
       error=$_.Exception.Message}} | ConvertTo-Json -Compress
}}
""".strip()
    else:
        # Non-OS product: match by CVE ID as before
        script = f"""
$targetCVEs = @({cves_ps})
try {{
    $Sess   = New-Object -ComObject Microsoft.Update.Session
    $Search = $Sess.CreateUpdateSearcher()
    $Found  = $Search.Search("IsInstalled=0 and IsHidden=0")
    $coll   = New-Object -ComObject Microsoft.Update.UpdateColl
    $titles = @(); $kbids = @()
    foreach ($u in $Found.Updates) {{
        $match = $false
        foreach ($cve in $u.CVEIDs) {{
            if ($targetCVEs -contains $cve) {{ $match = $true; break }}
        }}
        if ($match) {{
            [void]$coll.Add($u)
            $titles += $u.Title
            foreach ($kb in $u.KBArticleIDs) {{ $kbids += $kb }}
        }}
    }}
    if ($coll.Count -eq 0) {{
        @{{installed=0;reboot_required=$false;updates_found=0;
           titles=@();kb_ids=@();error="No pending patches found for these CVEs"}} | ConvertTo-Json -Compress
        exit
    }}
    $dl = $Sess.CreateUpdateDownloader()
    $dl.Updates = $coll
    [void]$dl.Download()
    $inst = $Sess.CreateUpdateInstaller()
    $inst.Updates = $coll
    $res  = $inst.Install()
    @{{
        installed       = $coll.Count
        updates_found   = $coll.Count
        reboot_required = $res.RebootRequired
        result_code     = $res.ResultCode
        titles          = $titles
        kb_ids          = $kbids
        error           = ""
    }} | ConvertTo-Json -Compress
}} catch {{
    @{{installed=0;reboot_required=$false;updates_found=0;titles=@();kb_ids=@();
       error=$_.Exception.Message}} | ConvertTo-Json -Compress
}}
""".strip()
    # Same hard-deadline child-process guard as _install_patches_wua: this WUA
    # Install() path can also block forever and would otherwise wedge the agent's
    # command executor. On timeout we kill the tree and surface a timeout error.
    tmo = _patch_install_timeout()
    result = _ps_json_proc(script, timeout=tmo)
    if result is _PS_TIMEOUT:
        # Return BEFORE the winget fallback: that path has no timeout guard and
        # could re-wedge the executor, or report success and let a CVE auto-close
        # on a box whose Windows Update actually hung. Mirror the OS-path timeout
        # return (_install_patches_wua) so the gateway sees timed_out/"timed out".
        return {"installed": 0, "reboot_required": False, "updates_found": 0,
                "titles": [], "kb_ids": [], "timed_out": True,
                "error": f"Windows Update install timed out after {tmo}s (process killed)"}
    elif result is None:
        wua_result = {"installed": 0, "reboot_required": False, "updates_found": 0,
                      "titles": [], "kb_ids": [], "error": "No output from WUA"}
    else:
        def _to_list(v):
            if isinstance(v, list): return v
            if v: return [v]
            return []
        wua_result = {
            "installed":       int(result.get("installed") or 0),
            "updates_found":   int(result.get("updates_found") or 0),
            "reboot_required": bool(result.get("reboot_required")),
            "result_code":     result.get("result_code"),
            "titles":          _to_list(result.get("titles")),
            "kb_ids":          _to_list(result.get("kb_ids")),
            "error":           result.get("error") or "",
        }

    # If WUA found nothing, try product-specific winget upgrade.
    # Skip for OS products — Windows Update is the only valid pathway.
    if wua_result["updates_found"] == 0 and wua_result["installed"] == 0 and not _is_os_product:
        pname_lower = (product_name or '').lower().strip()
        winget_id = None
        for key, pkg in _WINGET_PRODUCT_MAP.items():
            if key in pname_lower or pname_lower in key:
                winget_id = pkg
                break
        if winget_id:
            print(f"[agent] WUA found no patches; trying winget for {winget_id}", flush=True)
            wg = _install_via_winget(winget_id)
            if wg["installed"] > 0 or not wg["error"] or wg["error"] == "Already up to date":
                return wg
            # winget also failed — return winget error (more informative)
            wua_result["error"] = f"WUA: no patch; winget ({winget_id}): {wg['error']}"

    return wua_result


async def _do_reboot_sequence():
    """Show reboot countdown dialog in user's session, then reboot when it closes."""
    loop = asyncio.get_event_loop()
    print("[agent] Showing reboot notification to logged-in user...", flush=True)
    try:
        await loop.run_in_executor(
            None,
            _run_dialog_in_user_session,
            _REBOOT_DIALOG_PS,
            36 * 60 * 1000,  # 36-min timeout (15 base + 15 defer + 6 buffer)
        )
    except Exception as e:
        print(f"[agent] Reboot dialog error: {e} -- rebooting anyway", flush=True)
    print("[agent] Executing system restart for Windows Updates...", flush=True)
    subprocess.run(
        ["shutdown", "/r", "/t", "0", "/c", "Restart for Windows Updates (Cirque IT)"],
        creationflags=0x08000000,
    )
    t = {
        "type": "telemetry_update",
        "agent_id": agent_id,
        "agent_version": AGENT_VERSION,
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "os_name": platform.system(),
        "os_version": platform.version(),
        "os_build": platform.release(),
        "os_arch": platform.machine(),
        "cpu_name": _get_cpu_name(),
        "cpu_cores": os.cpu_count() or 0,
        "cpu_percent": 0.0,
        "ram_total_gb": 0.0,
        "ram_available_gb": 0.0,
        "ram_percent": 0.0,
        "battery_present": False,
        "battery_percent": None,
        "battery_charging": None,
        "battery_minutes_left": None,
        "disks": [],
        "network": [],
        "logged_in_user": _get_windows_username(),
        "domain": _get_domain(),
        "uptime_seconds": 0,
        "screen_resolution": _get_screen_resolutions(),
        "captured_at": datetime.now().isoformat(),
    }

    try:
        import psutil

        t["cpu_percent"] = psutil.cpu_percent(interval=0.5)

        mem = psutil.virtual_memory()
        t["ram_total_gb"] = round(mem.total / (1024 ** 3), 2)
        t["ram_available_gb"] = round(mem.available / (1024 ** 3), 2)
        t["ram_percent"] = mem.percent

        t["uptime_seconds"] = int(datetime.now().timestamp() - psutil.boot_time())

        # Disks
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024 ** 3), 1),
                    "used_gb": round(usage.used / (1024 ** 3), 1),
                    "free_gb": round(usage.free / (1024 ** 3), 1),
                    "percent": usage.percent,
                })
            except Exception:
                pass
        t["disks"] = disks

        # Network interfaces
        net = []
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        for iface, addr_list in addrs.items():
            s = stats.get(iface)
            if not s or not s.isup:
                continue
            ips = [a.address for a in addr_list if a.family == socket.AF_INET]
            macs = [a.address for a in addr_list if a.family == -1 or (hasattr(socket, 'AF_PACKET') and a.family == socket.AF_PACKET)]
            # Windows MAC
            try:
                import psutil
                AF_LINK = psutil.AF_LINK if hasattr(psutil, 'AF_LINK') else 18
                macs = [a.address for a in addr_list if a.family == AF_LINK]
            except Exception:
                pass
            if ips:
                net.append({"interface": iface, "ips": ips, "mac": macs[0] if macs else ""})
        t["network"] = net

        # Battery
        batt = psutil.sensors_battery()
        if batt:
            t["battery_present"] = True
            t["battery_percent"] = round(batt.percent, 1)
            t["battery_charging"] = batt.power_plugged
            if batt.secsleft and batt.secsleft > 0 and not batt.power_plugged:
                t["battery_minutes_left"] = round(batt.secsleft / 60, 0)

    except ImportError:
        pass
    except Exception as e:
        print(f"[telemetry] Error: {e}", flush=True)

    return t


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

# Inline script run inside the active user's session via CreateProcessAsUser.
_CAPTURE_HELPER = r"""
import sys, json, base64, io, os
os.environ.setdefault("DISPLAY", "")
try:
    import mss, mss.tools
    with mss.mss() as sct:
        sct_img = sct.grab(sct.monitors[0])
        try:
            from PIL import Image
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=60)
            data = buf.getvalue()
            fmt = "jpeg"
        except ImportError:
            data = mss.tools.to_png(sct_img.rgb, sct_img.size)
            fmt = "png"
        result = {"data": base64.b64encode(data).decode(), "format": fmt,
                  "width": sct_img.width, "height": sct_img.height}
except Exception as e:
    result = {"error": str(e)}
with open(sys.argv[1], "w") as f:
    json.dump(result, f)
"""


def _capture_in_user_session() -> Optional[dict]:
    """Spawn a capture helper inside the active interactive session.
    Required when the agent runs as SYSTEM (Session 0 isolation).
    """
    import ctypes
    import ctypes.wintypes as wt
    import json as _json

    kernel32  = ctypes.WinDLL("kernel32", use_last_error=True)
    wtsapi32  = ctypes.WinDLL("wtsapi32", use_last_error=True)
    advapi32  = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv   = ctypes.WinDLL("userenv",  use_last_error=True)

    session_id = kernel32.WTSGetActiveConsoleSessionId()
    if session_id == 0xFFFFFFFF:
        return {"error": "No active console session"}

    h_token = wt.HANDLE()
    if not wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(h_token)):
        return {"error": f"WTSQueryUserToken failed (err={ctypes.get_last_error()}); session={session_id}"}

    try:
        # Build the user's full environment block so mss can find the display
        h_env = ctypes.c_void_p()
        userenv.CreateEnvironmentBlock(ctypes.byref(h_env), h_token, False)

        # Use Public folder -- writable by both SYSTEM (writer) and user (reader)
        pub = os.environ.get("PUBLIC", r"C:\Users\Public")
        py_path  = os.path.join(pub, "_rmm_cap_helper.py")
        out_path = os.path.join(pub, "_rmm_cap_result.json")
        err_path = os.path.join(pub, "_rmm_cap_error.txt")

        with open(py_path, "w") as f:
            f.write(_CAPTURE_HELPER)

        python_exe = sys.executable
        cmd = f'"{python_exe}" "{py_path}" "{out_path}"'

        class STARTUPINFO(ctypes.Structure):
            _fields_ = [
                ("cb",              ctypes.c_ulong),
                ("lpReserved",      ctypes.c_wchar_p),
                ("lpDesktop",       ctypes.c_wchar_p),
                ("lpTitle",         ctypes.c_wchar_p),
                ("dwX",             ctypes.c_ulong),
                ("dwY",             ctypes.c_ulong),
                ("dwXSize",         ctypes.c_ulong),
                ("dwYSize",         ctypes.c_ulong),
                ("dwXCountChars",   ctypes.c_ulong),
                ("dwYCountChars",   ctypes.c_ulong),
                ("dwFillAttribute", ctypes.c_ulong),
                ("dwFlags",         ctypes.c_ulong),
                ("wShowWindow",     ctypes.c_ushort),
                ("cbReserved2",     ctypes.c_ushort),
                ("lpReserved2",     ctypes.c_char_p),
                ("hStdInput",       wt.HANDLE),
                ("hStdOutput",      wt.HANDLE),
                ("hStdError",       wt.HANDLE),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess",    wt.HANDLE),
                ("hThread",     wt.HANDLE),
                ("dwProcessId", ctypes.c_ulong),
                ("dwThreadId",  ctypes.c_ulong),
            ]

        si = STARTUPINFO()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = "winsta0\\default"
        pi = PROCESS_INFORMATION()

        # CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT
        flags = 0x08000000 | 0x00000400
        ok = advapi32.CreateProcessAsUserW(
            h_token,
            None,
            cmd,
            None, None,
            False,
            flags,
            h_env,   # user environment block -- critical for display access
            None,
            ctypes.byref(si),
            ctypes.byref(pi),
        )

        if h_env:
            userenv.DestroyEnvironmentBlock(h_env)

        if not ok:
            return {"error": f"CreateProcessAsUser failed (err={ctypes.get_last_error()})"}

        try:
            WAIT_TIMEOUT = 0x00000102
            ret = kernel32.WaitForSingleObject(pi.hProcess, 15000)
            if ret == WAIT_TIMEOUT:
                kernel32.TerminateProcess(pi.hProcess, 1)
                return {"error": "Screenshot helper timed out"}
        finally:
            kernel32.CloseHandle(pi.hProcess)
            kernel32.CloseHandle(pi.hThread)

        if not os.path.exists(out_path):
            # Check if helper wrote an error file
            err_detail = ""
            if os.path.exists(err_path):
                try:
                    err_detail = open(err_path).read().strip()
                    os.remove(err_path)
                except Exception:
                    pass
            return {"error": f"Helper produced no output. detail={err_detail or 'none'}"}

        with open(out_path) as f:
            result = _json.load(f)
        try:
            os.remove(out_path)
        except Exception:
            pass
        return result

    finally:
        kernel32.CloseHandle(h_token)


def capture_screenshot() -> Optional[dict]:
    try:
        return _capture_in_user_session()
    except Exception as e:
        # Fallback: direct capture (works if already running in user session)
        try:
            import mss
            import mss.tools
            with mss.mss() as sct:
                sct_img = sct.grab(sct.monitors[0])
                try:
                    from PIL import Image
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=60)
                    data = buf.getvalue()
                    fmt = "jpeg"
                except ImportError:
                    data = mss.tools.to_png(sct_img.rgb, sct_img.size)
                    fmt = "png"
                b64 = base64.b64encode(data).decode()
                return {"data": b64, "format": fmt,
                        "width": sct_img.width, "height": sct_img.height}
        except Exception as e2:
            return {"error": f"session capture: {e}; direct capture: {e2}"}


# ---------------------------------------------------------------------------
# Shell session — ConPTY (Windows 10 1903+) with raw-pipe fallback
# ---------------------------------------------------------------------------

# pywinpty availability — defaults for non-win32 (and pywinpty-absent); the win32
# block below overrides these. Kept at module scope so importing this file on Linux
# (e.g. CI loading it for tests) never touches the win32-only ctypes structs.
_PYWINPTY_AVAILABLE = False
_PtyProcess = None

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as _wt
    import threading as _threading

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # pywinpty is the PREFERRED Windows interactive-shell backend. The
    # hand-rolled ctypes ConPTY below works by inspection but, in the
    # SYSTEM-service / session-0 context, the child's output pipe never
    # gets written to (ReadFile blocks forever) — confirmed unfixable-by-
    # inspection live on ITWORKBENCH (agent 2.9.25/2.9.26). pywinpty wraps
    # ConPTY correctly (it's what real solutions use), so when its prebuilt
    # wheel is present we use it and skip the broken hand-rolled path.
    try:
        from winpty import PtyProcess as _PtyProcess
        _PYWINPTY_AVAILABLE = True
    except Exception:
        _PtyProcess = None
        _PYWINPTY_AVAILABLE = False

    class _COORD(ctypes.Structure):
        _fields_ = [("X", _wt.SHORT), ("Y", _wt.SHORT)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb",              _wt.DWORD),
            ("lpReserved",      _wt.LPWSTR),
            ("lpDesktop",       _wt.LPWSTR),
            ("lpTitle",         _wt.LPWSTR),
            ("dwX",             _wt.DWORD),
            ("dwY",             _wt.DWORD),
            ("dwXSize",         _wt.DWORD),
            ("dwYSize",         _wt.DWORD),
            ("dwXCountChars",   _wt.DWORD),
            ("dwYCountChars",   _wt.DWORD),
            ("dwFillAttribute", _wt.DWORD),
            ("dwFlags",         _wt.DWORD),
            ("wShowWindow",     _wt.WORD),
            ("cbReserved2",     _wt.WORD),
            ("lpReserved2",     ctypes.POINTER(_wt.BYTE)),
            ("hStdInput",       _wt.HANDLE),
            ("hStdOutput",      _wt.HANDLE),
            ("hStdError",       _wt.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo",    _STARTUPINFOW),
            ("lpAttributeList", ctypes.c_void_p),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess",    _wt.HANDLE),
            ("hThread",     _wt.HANDLE),
            ("dwProcessId", _wt.DWORD),
            ("dwThreadId",  _wt.DWORD),
        ]

    _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    _EXTENDED_STARTUPINFO_PRESENT        = 0x00080000
    _CREATE_UNICODE_ENVIRONMENT          = 0x00000400
    _INVALID_HANDLE_VALUE                = _wt.HANDLE(-1).value
    _STILL_ACTIVE                        = 259

    # HRESULT is a signed 32-bit LONG. NOTE: ctypes.wintypes has NO `HRESULT`
    # attribute (it lives at top-level ctypes.HRESULT on Windows only). Using
    # `_wt.HRESULT` here raised AttributeError at import, which the except below
    # swallowed and set _CONPTY_AVAILABLE=False — silently disabling ConPTY on
    # EVERY Windows box and dropping every web shell into the raw-pipe fallback
    # (no PSReadLine, raw \x7f echoed, no backspace). Use c_long and check the
    # HRESULT ourselves so we control success (S_OK==0) vs failure cleanup.
    _HRESULT = ctypes.c_long
    # Resolve CreatePseudoConsole — only present on Win10 1903+
    try:
        _CreatePseudoConsole = _k32.CreatePseudoConsole
        _CreatePseudoConsole.restype  = _HRESULT
        _CreatePseudoConsole.argtypes = [
            _COORD, _wt.HANDLE, _wt.HANDLE, _wt.DWORD,
            ctypes.POINTER(_wt.HANDLE),
        ]
        _ResizePseudoConsole = _k32.ResizePseudoConsole
        _ResizePseudoConsole.restype  = _HRESULT
        _ResizePseudoConsole.argtypes = [_wt.HANDLE, _COORD]

        _ClosePseudoConsole = _k32.ClosePseudoConsole
        _ClosePseudoConsole.restype  = None
        _ClosePseudoConsole.argtypes = [_wt.HANDLE]

        _CONPTY_AVAILABLE = True
    except AttributeError:
        # CreatePseudoConsole genuinely absent (pre-1903) — fall back to raw pipes.
        _CONPTY_AVAILABLE = False

    _k32.InitializeProcThreadAttributeList.restype  = _wt.BOOL
    _k32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, _wt.DWORD, _wt.DWORD, ctypes.POINTER(ctypes.c_size_t)
    ]
    _k32.UpdateProcThreadAttribute.restype  = _wt.BOOL
    _k32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, _wt.DWORD, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    _k32.DeleteProcThreadAttributeList.restype  = None
    _k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]

    _k32.CreateProcessW.restype  = _wt.BOOL
    _k32.CreateProcessW.argtypes = [
        _wt.LPCWSTR, _wt.LPWSTR,
        ctypes.c_void_p, ctypes.c_void_p,
        _wt.BOOL, _wt.DWORD, _wt.LPVOID, _wt.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOEXW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _k32.CreatePipe.restype  = _wt.BOOL
    _k32.CreatePipe.argtypes = [
        ctypes.POINTER(_wt.HANDLE), ctypes.POINTER(_wt.HANDLE),
        ctypes.c_void_p, _wt.DWORD,
    ]
    _k32.ReadFile.restype  = _wt.BOOL
    _k32.ReadFile.argtypes = [
        _wt.HANDLE, ctypes.c_void_p, _wt.DWORD,
        ctypes.POINTER(_wt.DWORD), ctypes.c_void_p,
    ]
    _k32.WriteFile.restype  = _wt.BOOL
    _k32.WriteFile.argtypes = [
        _wt.HANDLE, ctypes.c_void_p, _wt.DWORD,
        ctypes.POINTER(_wt.DWORD), ctypes.c_void_p,
    ]
    _k32.CloseHandle.restype  = _wt.BOOL
    _k32.CloseHandle.argtypes = [_wt.HANDLE]
    _k32.TerminateProcess.restype  = _wt.BOOL
    _k32.TerminateProcess.argtypes = [_wt.HANDLE, _wt.UINT]
    _k32.GetExitCodeProcess.restype  = _wt.BOOL
    _k32.GetExitCodeProcess.argtypes = [_wt.HANDLE, ctypes.POINTER(_wt.DWORD)]

    def _win32_close(h):
        if h and h != _INVALID_HANDLE_VALUE:
            _k32.CloseHandle(h)

    def _conpty_create(shell: str, cols: int, rows: int):
        """Spawn a shell in a Windows ConPTY. Returns (hpc, hIn, hOut, hProc, hThread)."""
        hPTYin_r  = _wt.HANDLE(_INVALID_HANDLE_VALUE)
        hPTYin_w  = _wt.HANDLE(_INVALID_HANDLE_VALUE)
        hPTYout_r = _wt.HANDLE(_INVALID_HANDLE_VALUE)
        hPTYout_w = _wt.HANDLE(_INVALID_HANDLE_VALUE)

        if not _k32.CreatePipe(ctypes.byref(hPTYin_r),  ctypes.byref(hPTYin_w),  None, 0):
            raise OSError(f"CreatePipe(in) failed: {ctypes.get_last_error()}")
        if not _k32.CreatePipe(ctypes.byref(hPTYout_r), ctypes.byref(hPTYout_w), None, 0):
            _win32_close(hPTYin_r); _win32_close(hPTYin_w)
            raise OSError(f"CreatePipe(out) failed: {ctypes.get_last_error()}")

        hpc = _wt.HANDLE(_INVALID_HANDLE_VALUE)
        hr  = _CreatePseudoConsole(_COORD(X=cols, Y=rows), hPTYin_r, hPTYout_w, 0, ctypes.byref(hpc))
        # ConPTY owns these ends now — close our copies
        _win32_close(hPTYin_r); _win32_close(hPTYout_w)
        if hr != 0:
            _win32_close(hPTYin_w); _win32_close(hPTYout_r)
            raise OSError(f"CreatePseudoConsole failed: HRESULT={hr & 0xFFFFFFFF:#010x}")

        # Build STARTUPINFOEX with the pseudo console attribute
        attr_sz = ctypes.c_size_t(0)
        _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attr_sz))
        attr_buf = (ctypes.c_byte * attr_sz.value)()
        if not _k32.InitializeProcThreadAttributeList(attr_buf, 1, 0, ctypes.byref(attr_sz)):
            _ClosePseudoConsole(hpc); _win32_close(hPTYin_w); _win32_close(hPTYout_r)
            raise OSError(f"InitializeProcThreadAttributeList failed: {ctypes.get_last_error()}")
        if not _k32.UpdateProcThreadAttribute(
            attr_buf, 0, _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            ctypes.addressof(hpc), ctypes.sizeof(hpc), None, None,
        ):
            _k32.DeleteProcThreadAttributeList(attr_buf)
            _ClosePseudoConsole(hpc); _win32_close(hPTYin_w); _win32_close(hPTYout_r)
            raise OSError(f"UpdateProcThreadAttribute failed: {ctypes.get_last_error()}")

        si_ex = _STARTUPINFOEXW()
        si_ex.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        si_ex.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)
        pi  = _PROCESS_INFORMATION()
        exe = "cmd.exe" if shell == "cmd" else "powershell.exe -NoLogo -NoProfile"
        cmd_buf = ctypes.create_unicode_buffer(exe)
        if not _k32.CreateProcessW(
            None, cmd_buf, None, None, False,
            _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
            None, None, ctypes.byref(si_ex), ctypes.byref(pi),
        ):
            err = ctypes.get_last_error()
            _k32.DeleteProcThreadAttributeList(attr_buf)
            _ClosePseudoConsole(hpc); _win32_close(hPTYin_w); _win32_close(hPTYout_r)
            raise OSError(f"CreateProcessW failed: {err}")

        _k32.DeleteProcThreadAttributeList(attr_buf)
        return hpc, hPTYin_w, hPTYout_r, pi.hProcess, pi.hThread

    def _pipe_read_sync(h, size=4096) -> Optional[bytes]:
        """Blocking read from a Win32 pipe handle. Returns None on EOF/error."""
        buf      = (ctypes.c_byte * size)()
        nread    = _wt.DWORD(0)
        ok = _k32.ReadFile(h, buf, size, ctypes.byref(nread), None)
        if not ok or nread.value == 0:
            return None
        return bytes(buf[:nread.value])

    def _pipe_write_sync(h, data: bytes):
        if not data:
            return
        buf      = (ctypes.c_byte * len(data))(*data)
        nwritten = _wt.DWORD(0)
        _k32.WriteFile(h, buf, len(data), ctypes.byref(nwritten), None)


class ShellSession:
    """Interactive shell.

    Windows backend priority:
      1. pywinpty (PtyProcess) — PREFERRED. Wraps ConPTY correctly even under
         the SYSTEM service / session-0 context where the hand-rolled ctypes
         ConPTY relayed zero output (child never wrote its output pipe;
         ReadFile blocked forever — proven live on ITWORKBENCH 2.9.25/26).
      2. raw asyncio pipes — FALLBACK for fleet boxes that don't yet have the
         pywinpty wheel. No PSReadLine niceties, but functional; keeps those
         agents working until pywinpty is rolled out.

    The hand-rolled ctypes ConPTY path is retired (never relays output under
    the service); pywinpty is the Windows interactive path now.

    Non-Windows uses the raw-pipe fallback.
    """

    def __init__(self, session_id: int, shell: str = "powershell",
                 cols: int = 220, rows: int = 50):
        self.session_id = session_id
        self.shell      = shell
        self.cols       = cols
        self.rows       = rows
        # pywinpty backend
        self._pty      = None
        # Raw-pipe fallback
        self.proc: Optional[asyncio.subprocess.Process] = None
        # Output queue fed by background reader thread (pywinpty path)
        self._q: Optional[asyncio.Queue] = None
        self._reader: Optional["_threading.Thread"] = None
        self._alive    = False
        self._use_pty  = False
        self._loop     = None

    def _reader_fn(self):
        """Background thread: drain pywinpty output into the asyncio queue.

        pywinpty's read() is blocking with an internal pump, so it runs in a
        dedicated daemon thread and hands chunks to the event loop via the
        queue. Hardened: any exception here is caught + logged, then we signal
        EOF (None) and clear _alive so shell_output_loop exits instead of
        spinning forever on an empty queue.
        """
        try:
            while self._alive:
                try:
                    chunk = self._pty.read(4096)
                except EOFError:
                    break
                except Exception as e:
                    print(f"[shell] pty read error: {e}", flush=True)
                    break
                if chunk is None or chunk == "":
                    # isalive() == False after the child exits; read() returns
                    # "" at EOF. Stop if the process is gone.
                    try:
                        if not self._pty.isalive():
                            break
                    except Exception:
                        break
                    continue
                # pywinpty.read() returns str (already UTF-8 decoded)
                text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
                try:
                    asyncio.run_coroutine_threadsafe(self._q.put(text), self._loop)
                except Exception as e:
                    print(f"[shell] reader enqueue error: {e}", flush=True)
                    break
        except Exception as e:
            print(f"[shell] reader thread crashed: {e}", flush=True)
        finally:
            self._alive = False
            try:
                asyncio.run_coroutine_threadsafe(self._q.put(None), self._loop)
            except Exception:
                pass

    async def start(self) -> bool:
        self._loop = asyncio.get_event_loop()
        # Preferred Windows path: pywinpty
        if sys.platform == "win32" and _PYWINPTY_AVAILABLE:
            try:
                spawn_cmd = "cmd.exe" if self.shell == "cmd" else "powershell.exe -NoLogo -NoProfile"
                self._pty = await self._loop.run_in_executor(
                    None, _PtyProcess.spawn, spawn_cmd
                )
                try:
                    # pywinpty setwinsize signature is (rows, cols)
                    self._pty.setwinsize(self.rows, self.cols)
                except Exception as e:
                    print(f"[shell] setwinsize warning: {e}", flush=True)
                self._alive  = True
                self._use_pty = True
                self._q = asyncio.Queue()
                self._reader = _threading.Thread(target=self._reader_fn, daemon=True)
                self._reader.start()
                print(f"[shell] pywinpty session {self.session_id} started ({self.shell})", flush=True)
                return True
            except Exception as e:
                print(f"[shell] pywinpty unavailable, falling back to raw pipes: {e}", flush=True)

        # Fallback: raw pipes (no PSReadLine, but functional). Used on fleet
        # boxes without the pywinpty wheel and on non-Windows.
        cmd = ["cmd.exe"] if self.shell == "cmd" else ["powershell.exe", "-NoLogo", "-NoProfile"]
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._alive = True
            print(f"[shell] raw-pipe session {self.session_id} started ({self.shell})", flush=True)
            return True
        except Exception as e:
            print(f"[shell] start error: {e}", flush=True)
            return False

    async def send_input(self, text: str):
        if not self._alive:
            return
        if self._use_pty:
            # Raw write — pywinpty/ConPTY translates \x7f (Backspace) into a
            # proper VT erase, so no raw 0x7f reaches the terminal.
            await self._loop.run_in_executor(None, self._pty.write, text)
        elif self.proc and self.proc.stdin:
            self.proc.stdin.write(text.encode("utf-8", errors="replace"))
            await self.proc.stdin.drain()

    async def read_output(self) -> Optional[str]:
        if not self._alive:
            return None
        if self._use_pty:
            try:
                return await asyncio.wait_for(self._q.get(), timeout=0.1)
            except asyncio.TimeoutError:
                return None
        else:
            if not self.proc or not self.proc.stdout:
                return None
            try:
                chunk = await asyncio.wait_for(self.proc.stdout.read(4096), timeout=0.1)
                return chunk.decode("utf-8", errors="replace") if chunk else None
            except asyncio.TimeoutError:
                return None

    async def resize(self, cols: int, rows: int):
        self.cols = cols
        self.rows = rows
        if self._use_pty and self._pty:
            try:
                # pywinpty setwinsize signature is (rows, cols)
                await self._loop.run_in_executor(None, self._pty.setwinsize, rows, cols)
            except Exception as e:
                print(f"[shell] resize error: {e}", flush=True)

    def is_alive(self) -> bool:
        if not self._alive:
            return False
        if self._use_pty and self._pty:
            try:
                if not self._pty.isalive():
                    self._alive = False
                    return False
            except Exception:
                self._alive = False
                return False
        elif self.proc is not None:
            return self.proc.returncode is None
        return self._alive

    async def stop(self):
        self._alive = False
        if self._use_pty:
            if self._pty:
                try:
                    self._pty.terminate(force=True)
                except Exception:
                    pass
                self._pty = None
        elif self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None


async def shell_output_loop(session: ShellSession, ws, stop_event: asyncio.Event):
    while not stop_event.is_set() and session.is_alive():
        output = await session.read_output()
        if output:
            try:
                await ws.send(json.dumps({
                    "type": "shell_output",
                    "session_id": session.session_id,
                    "data": output,
                }))
            except Exception:
                break
        else:
            await asyncio.sleep(0.05)
    try:
        await ws.send(json.dumps({"type": "shell_exited", "session_id": session.session_id}))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

# Module-level state for the persistent Eagle Eyes window-query helper.
# Eagle Eyes is Cirque IT's SANCTIONED activity-monitoring feature. This helper is a
# named, declarable component: it is meant to be identifiable by endpoint security and
# allow-listed, NOT hidden. It runs in the interactive user's desktop session (the only
# context where the foreground window is visible to query) and reports the active
# app/window title back to the monitoring server while monitoring is enabled for the user.
_ee_helper_hproc: "ctypes.wintypes.HANDLE | None" = None   # process handle
_EE_OUT_DIR      = r"C:\ProgramData\CirqueRMM"
_EE_OUT_FILE     = r"C:\ProgramData\CirqueRMM\CirqueEagleWindow.json"
_EE_HELPER_CS    = r"C:\ProgramData\CirqueRMM\CirqueEagleHelper.cs"
_EE_HELPER_EXE   = r"C:\ProgramData\CirqueRMM\CirqueEagleHelper.exe"
# csc.exe ships with every .NET 4.x install (present on all modern Windows)
_EE_CSC          = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

# C# source for CirqueEagleHelper.exe -- compiled once via csc.exe on the Windows host.
# Runs as the interactive user (CreateProcessAsUserW), loops every 10 s.
# Uses GetForegroundWindow() -- the correct Win32 API for active window detection.
#
# Why a small compiled C# helper (rather than the SYSTEM service polling directly):
#   GetForegroundWindow() only returns a meaningful result from the interactive user's
#   desktop session, which the SYSTEM-context service cannot query itself. A tiny native
#   helper launched into that session is lighter and steadier for a 10 s poll loop than
#   re-spawning a script each tick. The helper is deliberately named CirqueEagleHelper.exe
#   and documented so Defender/EDR and any admin can recognise and allow-list it -- this
#   is a sanctioned monitoring tool and is not concealed from endpoint security.
#
# Diagnostic output:
#   CirqueEagleHelper_diag.txt -- written on start + each iteration for troubleshooting.
_EE_HELPER_CS_SRC = r"""
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Diagnostics;
using System.Threading;

class EagleEyes {
    const string OutFile  = @"C:\ProgramData\CirqueRMM\CirqueEagleWindow.json";
    const string DiagFile = @"C:\ProgramData\CirqueRMM\CirqueEagleHelper_diag.txt";
    const int    Interval = 10000; // ms

    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll")]
    static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);

    [DllImport("kernel32.dll")]
    static extern uint GetTickCount();

    struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }

    static string J(string s) {
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "").Replace("\n", " ");
    }

    static void Main() {
        File.WriteAllText(DiagFile, "start " + DateTime.Now.ToString("HH:mm:ss") + Environment.NewLine);
        int n = 0;
        while (true) {
            n++;
            Thread.Sleep(Interval);
            try {
                IntPtr hwnd = GetForegroundWindow();
                if (hwnd == IntPtr.Zero) {
                    File.AppendAllText(DiagFile, n + " hwnd=0" + Environment.NewLine);
                    continue;
                }
                var sb = new StringBuilder(512);
                GetWindowText(hwnd, sb, 512);
                string title = sb.ToString().Trim();
                if (title.Length == 0) {
                    File.AppendAllText(DiagFile, n + " title_empty" + Environment.NewLine);
                    continue;
                }
                uint pid = 0;
                GetWindowThreadProcessId(hwnd, out pid);
                string pname = "";
                try { pname = Process.GetProcessById((int)pid).ProcessName; } catch { }
                // Idle is computed HERE, inside the interactive user session: GetLastInputInfo
                // only sees the calling session's input, so the SYSTEM service in session 0
                // cannot measure the user's idle time (it reported bogus, ever-growing idle).
                uint idleSec = 0;
                try {
                    LASTINPUTINFO lii = new LASTINPUTINFO();
                    lii.cbSize = (uint)Marshal.SizeOf(lii);
                    if (GetLastInputInfo(ref lii)) { idleSec = (GetTickCount() - lii.dwTime) / 1000u; }
                } catch { }
                string json = "{\"p\":\"" + J(pname) + "\",\"t\":\"" + J(title) + "\",\"i\":" + idleSec + "}";
                File.WriteAllText(OutFile, json);
                File.AppendAllText(DiagFile, n + " ok p=" + pname +
                    " t=" + title.Substring(0, Math.Min(60, title.Length)) + Environment.NewLine);
            } catch (Exception ex) {
                File.AppendAllText(DiagFile, n + " err=" + ex.Message + Environment.NewLine);
            }
        }
    }
}
"""


_EE_DEBUG_LOG = r"C:\Windows\Temp\ee_debug.log"


def _ee_log(msg: str) -> None:
    """Append a timestamped debug line to the EE debug log (SYSTEM-writable)."""
    import datetime
    try:
        with open(_EE_DEBUG_LOG, "a", encoding="utf-8") as _lf:
            _lf.write(f"{datetime.datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass


def _ee_find_active_session() -> int:
    """Return Windows session-id of the first active interactive user, or -1."""
    import ctypes, ctypes.wintypes

    # WTSEnumerateSessions to find Active sessions (not just console session).
    # This handles RDP sessions as well as physical console sessions.
    WTS_CURRENT_SERVER_HANDLE = ctypes.c_void_p(0)

    class WTS_SESSION_INFO(ctypes.Structure):
        _fields_ = [
            ("SessionId",         ctypes.wintypes.DWORD),
            ("pWinStationName",   ctypes.c_wchar_p),
            ("State",             ctypes.c_int),
        ]

    wtsapi32 = ctypes.windll.wtsapi32
    wtsapi32.WTSEnumerateSessionsW.restype  = ctypes.wintypes.BOOL
    wtsapi32.WTSEnumerateSessionsW.argtypes = [
        ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(WTS_SESSION_INFO)), ctypes.POINTER(ctypes.wintypes.DWORD),
    ]

    pSessions = ctypes.POINTER(WTS_SESSION_INFO)()
    count     = ctypes.wintypes.DWORD(0)
    ok = wtsapi32.WTSEnumerateSessionsW(
        WTS_CURRENT_SERVER_HANDLE, 0, 1,
        ctypes.byref(pSessions), ctypes.byref(count),
    )
    if not ok:
        _ee_log(f"WTSEnumerateSessions failed err={ctypes.get_last_error()}")
        # Fall back to console session
        kernel32 = ctypes.windll.kernel32
        sid = kernel32.WTSGetActiveConsoleSessionId()
        _ee_log(f"console_session_id={sid}")
        return sid if (0 < sid < 0xFFFFFFFF) else -1

    WTSActive = 0
    found = -1
    for i in range(count.value):
        s = pSessions[i]
        _ee_log(f"session id={s.SessionId} state={s.State} name={s.pWinStationName}")
        if s.State == WTSActive and s.SessionId != 0 and found == -1:
            found = s.SessionId
    wtsapi32.WTSFreeMemory(pSessions)
    return found


def _ee_ensure_helper() -> bool:
    """Ensure the persistent Eagle Eyes helper is running in the user session.

    Returns True if the helper is (or just became) running, False on failure.
    Idempotent: safe to call every poll cycle.
    """
    global _ee_helper_hproc
    import ctypes
    import ctypes.wintypes

    kernel32 = ctypes.windll.kernel32
    wtsapi32 = ctypes.windll.wtsapi32
    advapi32 = ctypes.windll.advapi32

    # Check whether the existing helper process is still alive
    WAIT_OBJECT_0 = 0x00000000
    if _ee_helper_hproc is not None:
        result = kernel32.WaitForSingleObject(_ee_helper_hproc, 0)
        if result != WAIT_OBJECT_0:          # still running (WAIT_TIMEOUT)
            return True
        kernel32.CloseHandle(_ee_helper_hproc)
        _ee_helper_hproc = None
        _ee_log("helper process exited, will respawn")

    # Find active interactive session (handles both console and RDP)
    session_id = _ee_find_active_session()
    if session_id < 0:
        _ee_log("no active interactive session found")
        return False

    _ee_log(f"using session_id={session_id}")

    # Get the logon token of that session
    user_token = ctypes.wintypes.HANDLE()
    if not wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(user_token)):
        _ee_log(f"WTSQueryUserToken failed err={ctypes.get_last_error()}")
        return False

    _ee_log("WTSQueryUserToken OK")

    # Duplicate the token to a primary token (required for CreateProcessAsUser)
    TOKEN_ALL_ACCESS = 0xF01FF
    SecurityImpersonation = 2
    TokenPrimary = 1
    dup_token = ctypes.wintypes.HANDLE()
    ok_dup = advapi32.DuplicateTokenEx(
        user_token, TOKEN_ALL_ACCESS, None,
        SecurityImpersonation, TokenPrimary,
        ctypes.byref(dup_token),
    )
    kernel32.CloseHandle(user_token)   # no longer need original token
    if not ok_dup:
        _ee_log(f"DuplicateTokenEx failed err={ctypes.get_last_error()}")
        return False

    _ee_log("DuplicateTokenEx OK")

    # Ensure output directory exists and is writable by non-admin users
    try:
        os.makedirs(_EE_OUT_DIR, exist_ok=True)
        import subprocess as _sp
        _sp.run(
            ['icacls', _EE_OUT_DIR, '/grant', 'Users:(OI)(CI)W', '/T'],
            capture_output=True,
        )
    except Exception as _e:
        kernel32.CloseHandle(dup_token)
        _ee_log(f"makedirs/icacls failed: {_e}")
        return False

    # Write C# source and compile to exe (one-time; reused on subsequent calls).
    # A small compiled helper is simply the cleanest way to query the interactive
    # session's foreground window on a steady loop; the binary is named and documented
    # (CirqueEagleHelper.exe) so endpoint security and admins can identify/allow-list it.
    import subprocess as _sp
    try:
        with open(_EE_HELPER_CS, "w", encoding="utf-8") as _f:
            _f.write(_EE_HELPER_CS_SRC)
    except Exception as _e:
        kernel32.CloseHandle(dup_token)
        _ee_log(f"write CirqueEagleHelper.cs failed: {_e}")
        return False

    if not os.path.exists(_EE_HELPER_EXE):
        _ee_log("compiling CirqueEagleHelper.cs ...")
        r = _sp.run(
            [_EE_CSC, '/nologo', '/target:exe', f'/out:{_EE_HELPER_EXE}', _EE_HELPER_CS],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            _ee_log(f"csc compile failed rc={r.returncode} err={r.stderr.strip()[:200]}")
            kernel32.CloseHandle(dup_token)
            return False
        _ee_log("csc compile OK")

    # Build the user's environment block so the helper runs with the user's
    # environment variables (PATH, APPDATA, TEMP, etc.) rather than SYSTEM's.
    userenv32 = ctypes.windll.userenv
    env_block  = ctypes.c_void_p()
    ok_env = userenv32.CreateEnvironmentBlock(ctypes.byref(env_block), dup_token, False)
    if not ok_env:
        _ee_log(f"CreateEnvironmentBlock failed err={ctypes.get_last_error()} (will use default env)")
        env_block = None

    cmd = '"' + _EE_HELPER_EXE + '"'
    _ee_log(f"cmd={cmd}")

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb",              ctypes.wintypes.DWORD),
            ("lpReserved",      ctypes.wintypes.LPWSTR),
            ("lpDesktop",       ctypes.wintypes.LPWSTR),
            ("lpTitle",         ctypes.wintypes.LPWSTR),
            ("dwX",             ctypes.wintypes.DWORD),
            ("dwY",             ctypes.wintypes.DWORD),
            ("dwXSize",         ctypes.wintypes.DWORD),
            ("dwYSize",         ctypes.wintypes.DWORD),
            ("dwXCountChars",   ctypes.wintypes.DWORD),
            ("dwYCountChars",   ctypes.wintypes.DWORD),
            ("dwFillAttribute", ctypes.wintypes.DWORD),
            ("dwFlags",         ctypes.wintypes.DWORD),
            ("wShowWindow",     ctypes.wintypes.WORD),
            ("cbReserved2",     ctypes.wintypes.WORD),
            ("lpReserved2",     ctypes.c_char_p),
            ("hStdInput",       ctypes.wintypes.HANDLE),
            ("hStdOutput",      ctypes.wintypes.HANDLE),
            ("hStdError",       ctypes.wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess",    ctypes.wintypes.HANDLE),
            ("hThread",     ctypes.wintypes.HANDLE),
            ("dwProcessId", ctypes.wintypes.DWORD),
            ("dwThreadId",  ctypes.wintypes.DWORD),
        ]

    CREATE_NO_WINDOW           = 0x08000000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    STARTF_USESHOWWINDOW       = 0x00000001

    si = STARTUPINFO()
    si.cb        = ctypes.sizeof(si)
    si.lpDesktop = "winsta0\\default"
    si.dwFlags   = STARTF_USESHOWWINDOW
    si.wShowWindow = 0    # SW_HIDE
    pi = PROCESS_INFORMATION()

    ok = advapi32.CreateProcessAsUserW(
        dup_token, None, cmd,
        None, None, False,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        env_block, None,
        ctypes.byref(si), ctypes.byref(pi),
    )
    err = ctypes.get_last_error()
    kernel32.CloseHandle(dup_token)
    if env_block:
        userenv32.DestroyEnvironmentBlock(env_block)

    if not ok:
        _ee_log(f"CreateProcessAsUserW failed err={err}")
        return False

    _ee_log(f"helper spawned pid={pi.dwProcessId}")
    # Keep the process handle so we can check liveness later
    kernel32.CloseHandle(pi.hThread)
    _ee_helper_hproc = pi.hProcess
    return True


def _get_active_window_info() -> tuple:
    """Return (process_name, window_title, idle_seconds) for the foreground window.

    Reads the JSON maintained by the helper that runs inside the interactive user
    session. Idle is computed THERE — GetLastInputInfo only sees input from within
    the calling session, so the SYSTEM service in session 0 cannot measure the user's
    idle time (the old session-0 _get_idle_seconds reported bogus ever-growing idle,
    which made every event look idle). Returns ('', '', 0) on failure / non-Windows.
    """
    if sys.platform != "win32":
        return ("", "", 0)
    try:
        if not _ee_ensure_helper():
            return ("", "", 0)
        # Give the helper up to 4 s to produce a first result after fresh start
        import time as _time
        for _ in range(4):
            if os.path.exists(_EE_OUT_FILE):
                break
            _time.sleep(1)
        with open(_EE_OUT_FILE, encoding="utf-8-sig") as f:
            data = json.loads(f.read().strip())
        proc  = (data.get("p") or "").strip()
        title = (data.get("t") or "").strip()
        try:
            idle = max(0, int(data.get("i") or 0))
        except (TypeError, ValueError):
            idle = 0
        return (proc, title, idle)
    except Exception:
        return ("", "", 0)


def _get_idle_seconds() -> int:
    """Return seconds since last mouse/keyboard activity (Windows only).
    Uses GetLastInputInfo to query the system idle timer."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            elapsed_ms = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return max(0, elapsed_ms // 1000)
    except Exception:
        pass
    return 0


# ═══════════════════════════════════════════════════════ Windows Backup ════════

_BACKUP_DIR       = r"C:\CirqueRMM\backup"
_BACKUP_STATE     = r"C:\CirqueRMM\backup\state.json"
_BACKUP_RUNNING   = False   # single-instance guard


def _backup_api(tracker_url: str, method: str, path: str,
                agent_id: str, token: str, body: dict = None):
    """HTTP call to tracker backup API. Returns parsed JSON or raises."""
    url = f"{tracker_url}{path}?agent_id={agent_id}&token={token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": f"CirqueRMM/{AGENT_VERSION}"},
        method=method,
    )
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=30) as r:
        return json.loads(r.read())


def _backup_load_state() -> dict:
    try:
        with open(_BACKUP_STATE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"last_full": None, "last_incr": None}


def _backup_save_state(state: dict) -> None:
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    with open(_BACKUP_STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f)


def _backup_should_skip(fpath: str, ext_excludes: set, folder_excludes: set,
                        max_size_bytes: int, since_ts: float = None) -> bool:
    """Return True if this file should be excluded from the backup."""
    try:
        if os.path.getsize(fpath) > max_size_bytes:
            return True
        _, ext = os.path.splitext(fpath)
        if ext.lower().lstrip('.') in ext_excludes:
            return True
        # Check every path component against folder exclude list
        parts = set(os.path.normpath(fpath).split(os.sep))
        if parts & folder_excludes:
            return True
        if since_ts is not None and os.path.getmtime(fpath) <= since_ts:
            return True
    except Exception:
        return True   # skip unreadable files
    return False


def _backup_smb_connect(unc_path: str, username: str, password: str) -> None:
    """
    Authenticate to a non-domain SMB share from Windows SYSTEM context.

    Error 1244 (ERROR_NOT_AUTHENTICATED) occurs because SYSTEM's logon session
    has a cached null/anonymous SMB session to the NAS.  'net use /delete' and
    WNetCancelConnection2 only remove explicit connections — the implicit null
    session in SYSTEM's LSA session persists and blocks new credential attempts.

    Fix: LogonUserW(LOGON32_LOGON_NEW_CREDENTIALS) creates a NEW logon session
    whose network credentials are the supplied username/password (not SYSTEM).
    ImpersonateLoggedOnUser applies it to this thread so WNetAddConnection2W
    negotiates SMB using the NAS account instead of the cached SYSTEM token.
    The connection is registered globally in MPR and persists after RevertToSelf.
    """
    import subprocess, time as _time, ctypes, ctypes.wintypes as _wt

    parts = unc_path.replace('/', '\\').lstrip('\\').split('\\')
    share_path = f"\\\\{parts[0]}\\{parts[1]}" if len(parts) >= 2 else unc_path
    nas_host   = parts[0] if parts else 'NAS'
    # Strip any host prefix — LogonUserW wants the bare username; the domain/
    # machine is passed as a separate arg.  We use nas_host as the "domain" so
    # Windows knows this is a local account on the NAS, not a domain account.
    bare_user      = username.split('\\')[-1] if '\\' in username else username
    qualified_user = f"{nas_host}\\{bare_user}"

    # Clear all explicit net-use connections first (clears what IS visible)
    subprocess.run(['net', 'use', '*', '/delete', '/y'], capture_output=True, text=True)
    _time.sleep(0.5)

    class NETRESOURCEW(ctypes.Structure):
        _fields_ = [
            ('dwScope',       _wt.DWORD),
            ('dwType',        _wt.DWORD),
            ('dwDisplayType', _wt.DWORD),
            ('dwUsage',       _wt.DWORD),
            ('lpLocalName',   _wt.LPWSTR),
            ('lpRemoteName',  _wt.LPWSTR),
            ('lpComment',     _wt.LPWSTR),
            ('lpProvider',    _wt.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32',  use_last_error=True)
    mpr      = ctypes.WinDLL('mpr')

    # LOGON32_LOGON_NEW_CREDENTIALS (9): local identity stays SYSTEM but ALL
    # outbound network auth uses the supplied credentials — identical to
    # 'runas /netonly'.  This creates a fresh SMB session, not shared with
    # SYSTEM's cached null session.
    LOGON32_LOGON_NEW_CREDENTIALS = 9
    LOGON32_PROVIDER_DEFAULT      = 0

    token = _wt.HANDLE()
    logon_ok = advapi32.LogonUserW(
        bare_user, nas_host, password,
        LOGON32_LOGON_NEW_CREDENTIALS,
        LOGON32_PROVIDER_DEFAULT,
        ctypes.byref(token)
    )
    if logon_ok:
        advapi32.ImpersonateLoggedOnUser(token)
        print(f"[backup] Impersonating new logon session for {qualified_user}", flush=True)
    else:
        print(f"[backup] LogonUserW failed (err {ctypes.get_last_error()}), "
              f"attempting WNet without impersonation", flush=True)

    try:
        # Force-cancel any implicit IPC$ / share sessions under the new token
        mpr.WNetCancelConnection2W(f"\\\\{nas_host}\\IPC$", 0, True)
        mpr.WNetCancelConnection2W(share_path, 0, True)
        _time.sleep(0.3)

        nr = NETRESOURCEW()
        nr.dwType    = 1   # RESOURCETYPE_DISK
        nr.lpRemoteName = share_path
        wnet_err = mpr.WNetAddConnection2W(ctypes.byref(nr), password, qualified_user, 0)
    finally:
        if logon_ok:
            advapi32.RevertToSelf()
            kernel32.CloseHandle(token)

    if wnet_err != 0:
        raise RuntimeError(
            f"SMB connect failed for {share_path} as {qualified_user}: "
            f"WNetAddConnection2 error {wnet_err}"
        )
    print(f"[backup] Authenticated to {share_path} as {qualified_user}", flush=True)


def _backup_smb_disconnect(unc_path: str) -> None:
    """Cleanly disconnect the SMB session after backup completes."""
    import subprocess
    parts = unc_path.replace('/', '\\').lstrip('\\').split('\\')
    if len(parts) >= 2:
        share_path = f"\\\\{parts[0]}\\{parts[1]}"
    else:
        share_path = unc_path
    subprocess.run(['net', 'use', share_path, '/delete', '/yes'],
                   capture_output=True)


def _get_human_profile_paths() -> list:
    """Return all non-system user profile paths from the Windows registry ProfileList.

    Uses HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList which
    is always readable by SYSTEM and contains every profile with its exact path —
    independent of C:\\Users\\ filesystem permissions.
    """
    SYSTEM_PROFILE_PREFIXES = (
        'C:\\Windows', 'C:\\WINDOWS', 'C:\\windows',
        '%systemroot%', '%windir%',
    )
    profiles = []
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList',
        )
        i = 0
        while True:
            try:
                sid = winreg.EnumKey(key, i)
                i += 1
                try:
                    sub = winreg.OpenKey(key, sid)
                    profile_path, _ = winreg.QueryValueEx(sub, 'ProfileImagePath')
                    winreg.CloseKey(sub)
                    # expand any remaining env-var tokens
                    profile_path = os.path.expandvars(profile_path)
                    # skip service/system accounts
                    skip = False
                    for prefix in SYSTEM_PROFILE_PREFIXES:
                        if profile_path.upper().startswith(prefix.upper()):
                            skip = True
                            break
                    if not skip and os.path.isdir(profile_path):
                        profiles.append(profile_path)
                except OSError:
                    pass
            except OSError:
                break
        winreg.CloseKey(key)
    except Exception:
        pass
    return profiles


def _expand_include_paths(paths: list) -> list:
    """Expand include paths, replacing %USERPROFILE% with all real user profiles.

    When the agent runs as SYSTEM, %USERPROFILE% resolves to the SYSTEM profile
    (C:\\Windows\\system32\\config\\systemprofile), not the human users' profiles.
    This helper reads the registry ProfileList so it always works regardless of
    C:\\Users\\ filesystem permissions.
    """
    def _expand_one(path: str) -> list:
        upper = path.upper()
        if '%USERPROFILE%' not in upper:
            return [os.path.expandvars(path)]
        human_profiles = _get_human_profile_paths()
        if not human_profiles:
            # last resort: plain expandvars (will be SYSTEM profile, probably wrong)
            return [os.path.expandvars(path)]
        idx = upper.find('%USERPROFILE%')
        suffix = path[idx + len('%USERPROFILE%'):]
        return [p + suffix for p in human_profiles]

    result = []
    for p in paths:
        result.extend(_expand_one(p))
    return result


def _backup_smb_direct_upload(nas_host: str, share_name: str,
                              username: str, password: str,
                              hostname: str, snap_name: str,
                              include_paths: list, ext_excludes: set, folder_excludes: set,
                              max_size_bytes: int, since_ts,
                              progress_cb, job_id: str,
                              tracker_url: str, agent_id: str, token: str) -> dict:
    """
    Stream a compressed ZIP directly to the NAS using pure-Python SMB2 (smbprotocol).
    No local staging required — zipfile writes into the smbclient file handle.
    smbprotocol is auto-installed on first use.
    """
    import sys as _sys, subprocess as _sp, time as _time, zipfile as _zf, io as _io

    try:
        import smbclient
    except ImportError:
        print("[backup] smbprotocol not found — installing via pip...", flush=True)
        _sp.run([_sys.executable, '-m', 'pip', 'install', 'smbprotocol'],
                check=True, capture_output=True)
        import smbclient
        print("[backup] smbprotocol installed", flush=True)

    # Register a pure-Python SMB2 session — no Windows redirector involved
    smbclient.register_session(nas_host, username=username, password=password)
    print(f"[backup] smbprotocol session: {nas_host} as {username}", flush=True)

    share_root = f"\\\\{nas_host}\\{share_name}"
    host_dir   = f"{share_root}\\{hostname}"
    zip_name   = f"{snap_name}.zip"
    snap_dest  = f"{host_dir}\\{zip_name}"

    errors = []

    # Ensure \\nas\share\hostname\ exists
    try:
        smbclient.makedirs(host_dir, exist_ok=True)
    except Exception as e:
        errors.append(f"makedirs({host_dir}): {str(e)[:120]}")

    files_copied = files_skipped = files_failed = 0
    bytes_transferred = 0
    last_progress = _time.time()

    expanded = _expand_include_paths(include_paths)

    # Stream ZIP directly into the SMB file — no local temp file needed
    with smbclient.open_file(snap_dest, mode='wb') as smb_fh:
        with _zf.ZipFile(smb_fh, mode='w', compression=_zf.ZIP_DEFLATED, compresslevel=6) as zf:
            for src_root in expanded:
                if not os.path.isdir(src_root):
                    files_skipped += 1
                    continue
                for dirpath, dirnames, filenames in os.walk(src_root):
                    dirnames[:] = [d for d in dirnames if d not in folder_excludes]
                    for fname in filenames:
                        fpath = os.path.join(dirpath, fname)
                        if _backup_should_skip(fpath, ext_excludes, folder_excludes,
                                               max_size_bytes, since_ts):
                            files_skipped += 1
                            continue
                        _, tail = os.path.splitdrive(fpath)
                        arc_name = tail.lstrip(os.sep)  # path inside the zip
                        try:
                            fsize = os.path.getsize(fpath)
                            zf.write(fpath, arc_name)
                            files_copied += 1
                            bytes_transferred += fsize
                        except Exception as e:
                            files_failed += 1
                            errors.append(f"{arc_name[-80:]}: {str(e)[:120]}")
                            if len(errors) > 20:
                                errors = errors[-20:]

                        now = _time.time()
                        if now - last_progress >= 30:
                            last_progress = now
                            prog = {'job_id': job_id, 'files_copied': files_copied,
                                    'files_skipped': files_skipped, 'files_failed': files_failed,
                                    'bytes_transferred': bytes_transferred}
                            progress_cb(prog)
                            try:
                                _backup_api(tracker_url, 'PATCH',
                                            f'/api/rmm/backup-job/{job_id}', agent_id, token, prog)
                            except Exception:
                                pass

    smbclient.reset_connection_cache()
    return {
        'snap_dest': snap_dest,
        'files_copied': files_copied, 'files_skipped': files_skipped,
        'files_failed': files_failed, 'bytes_transferred': bytes_transferred,
        'errors': errors,
    }


def _backup_sftp_upload(sftp_host: str, sftp_port: int, username: str, password: str,
                        remote_base: str, hostname: str, snap_name: str,
                        include_paths: list, ext_excludes: set, folder_excludes: set,
                        max_size_bytes: int, since_ts, progress_cb, job_id: str,
                        tracker_url: str, agent_id: str, token: str) -> dict:
    """
    Upload backup files via SFTP using local NAS credentials (no domain auth).
    Returns stats dict matching the shutil-based approach.
    """
    import paramiko, time as _time

    transport = paramiko.Transport((sftp_host, sftp_port))
    transport.connect(username=username, password=password)
    sftp = paramiko.SFTPClient.from_transport(transport)

    zip_name  = f"{snap_name}.zip"
    host_dir  = f"{remote_base.rstrip('/')}/{hostname}"
    snap_dest = f"{host_dir}/{zip_name}"

    def _sftp_makedirs(path):
        parts = path.replace('\\', '/').split('/')
        current = ''
        for part in parts:
            if not part:
                continue
            current = f"{current}/{part}"
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)

    try:
        _sftp_makedirs(host_dir)
    except Exception as e:
        sftp.close(); transport.close()
        raise RuntimeError(f"Cannot create remote directory {host_dir}: {e}")

    files_copied = files_skipped = files_failed = 0
    bytes_transferred = 0
    errors = []
    last_progress = _time.time()

    import zipfile as _zf, io as _io

    # Stream ZIP directly into the SFTP file — no local staging
    with sftp.open(snap_dest, 'wb') as sftp_fh:
        sftp_fh.set_pipelined(True)
        with _zf.ZipFile(sftp_fh, mode='w', compression=_zf.ZIP_DEFLATED, compresslevel=6) as zf:
            for src_root in _expand_include_paths(include_paths):
                if not os.path.isdir(src_root):
                    files_skipped += 1
                    continue
                for dirpath, dirnames, filenames in os.walk(src_root):
                    dirnames[:] = [d for d in dirnames if d not in folder_excludes]
                    for fname in filenames:
                        fpath = os.path.join(dirpath, fname)
                        if _backup_should_skip(fpath, ext_excludes, folder_excludes,
                                               max_size_bytes, since_ts):
                            files_skipped += 1
                            continue
                        _, tail = os.path.splitdrive(fpath)
                        arc_name = tail.lstrip(os.sep).replace('\\', '/')
                        try:
                            fsize = os.path.getsize(fpath)
                            zf.write(fpath, arc_name)
                            files_copied += 1
                            bytes_transferred += fsize
                        except Exception as e:
                            files_failed += 1
                            errors.append(str(e)[:200])
                            if len(errors) > 50:
                                errors = errors[-50:]

                        now = _time.time()
                        if now - last_progress >= 30:
                            last_progress = now
                            prog = {'job_id': job_id, 'files_copied': files_copied,
                                    'files_skipped': files_skipped, 'files_failed': files_failed,
                                    'bytes_transferred': bytes_transferred}
                            progress_cb(prog)
                            try:
                                _backup_api(tracker_url, 'PATCH',
                                            f'/api/rmm/backup-job/{job_id}', agent_id, token, prog)
                            except Exception:
                                pass

    sftp.close()
    transport.close()
    return {
        'snap_dest': snap_dest,
        'files_copied': files_copied, 'files_skipped': files_skipped,
        'files_failed': files_failed, 'bytes_transferred': bytes_transferred,
        'errors': errors,
    }


def _backup_prune_retention(nas_path: str, hostname: str, retention_days: int,
                             nas_creds: dict = None) -> None:
    """Remove snapshot folders older than retention_days from the NAS.
    Handles both SMB (os.listdir) and SFTP (paramiko) based on nas_creds.
    """
    import time as _time
    cutoff = _time.time() - retention_days * 86400

    auth_method = (nas_creds or {}).get('auth_method', 'smb_local')

    if auth_method == 'sftp' and nas_creds:
        try:
            import paramiko
            host = nas_creds.get('sftp_host', '')
            port = int(nas_creds.get('sftp_port') or 22)
            remote_base = nas_creds.get('sftp_remote_path', '').rstrip('/')
            transport = paramiko.Transport((host, port))
            transport.connect(username=nas_creds['username'], password=nas_creds['password'])
            sftp = paramiko.SFTPClient.from_transport(transport)
            host_dir = f"{remote_base}/{hostname}"
            try:
                entries = sftp.listdir_attr(host_dir)
            except FileNotFoundError:
                sftp.close(); transport.close(); return
            for attr in entries:
                if attr.st_mtime and attr.st_mtime < cutoff:
                    snap = f"{host_dir}/{attr.filename}"
                    try:
                        # Recursively delete via exec channel
                        chan = transport.open_session()
                        chan.exec_command(f'rm -rf "{snap}"')
                        chan.close()
                        print(f"[backup] Pruned old SFTP snapshot: {snap}", flush=True)
                    except Exception:
                        pass
            sftp.close(); transport.close()
        except Exception as e:
            print(f"[backup] SFTP prune error: {e}", flush=True)
    else:
        # SMB / local path
        import shutil as _sh
        host_dir = os.path.join(nas_path, hostname)
        if not os.path.isdir(host_dir):
            return
        for entry in os.listdir(host_dir):
            snap = os.path.join(host_dir, entry)
            if os.path.isdir(snap):
                try:
                    if os.path.getmtime(snap) < cutoff:
                        _sh.rmtree(snap, ignore_errors=True)
                        print(f"[backup] Pruned old snapshot: {snap}", flush=True)
                except Exception:
                    pass


def _do_backup(tracker_url: str, agent_id: str, token: str,
               job_type: str, triggered_by: str, progress_cb) -> dict:
    """
    Blocking backup — runs in executor thread.
    Fetches policy from tracker, copies files to NAS using isolated local credentials
    (smb_local or sftp — never uses domain/Kerberos auth).
    Returns a stats dict.
    """
    import shutil, time as _time

    # ── Fetch policy ──────────────────────────────────────────────────────────
    try:
        resp = _backup_api(tracker_url, 'GET',
                           f'/api/rmm/backup-policy/{agent_id}', agent_id, token)
    except Exception as e:
        raise RuntimeError(f"Could not fetch backup policy: {e}")

    if not resp.get('ok') or not resp.get('enabled'):
        raise RuntimeError("No backup policy assigned or policy is disabled")

    policy = resp['policy']
    nas_path        = policy['nas_unc_path'].rstrip('/\\')
    nas_creds       = policy.get('nas_creds') or {}
    auth_method     = nas_creds.get('auth_method', 'smb_local')
    include_paths   = policy.get('include_paths') or []
    ext_excludes    = set(e.lower().lstrip('.') for e in (policy.get('exclude_extensions') or []))
    folder_excludes = set(policy.get('exclude_folders') or [])
    max_size_bytes  = (policy.get('max_file_size_mb') or 500) * 1024 * 1024
    full_interval   = policy.get('full_backup_interval_days') or 7
    retention_days  = policy.get('retention_days') or 30

    # ── Load state & decide job type ──────────────────────────────────────────
    state = _backup_load_state()
    def _ts(key):
        try:
            return datetime.fromisoformat(state[key]).timestamp() if state.get(key) else None
        except Exception:
            return None
    last_full_ts = _ts('last_full')
    last_incr_ts = _ts('last_incr')

    if job_type == 'auto':
        job_type = 'full' if (
            last_full_ts is None or
            (_time.time() - last_full_ts) > full_interval * 86400
        ) else 'incremental'

    since_ts = None
    if job_type == 'incremental':
        since_ts = last_incr_ts or last_full_ts

    hostname  = socket.gethostname()
    _snap_ts  = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')
    _snap_pfx = 'FULL' if job_type == 'full' else 'INC'
    snap_name = f"{_snap_pfx}-{_snap_ts}"

    # ── Parse NAS host / share from UNC path (needed for smb_direct) ─────────
    _nas_parts    = nas_path.lstrip('\\').split('\\')
    _nas_host     = _nas_parts[0] if _nas_parts else ''
    _nas_share    = _nas_parts[1] if len(_nas_parts) >= 2 else ''

    # ── Authenticate to NAS using isolated local credentials ─────────────────
    smb_connected = False
    if auth_method == 'smb_local':
        username = nas_creds.get('username') or ''
        password = nas_creds.get('password') or ''
        if username and password:
            _backup_smb_connect(nas_path, username, password)
            smb_connected = True
        elif username or password:
            raise RuntimeError("NAS credentials incomplete — both username and password required")
    # smb_direct and sftp establish their own connections inside the upload function

    # ── Check NAS reachable (SMB) — sftp/smb_direct test connectivity inside ─
    if auth_method not in ('sftp', 'smb_direct'):
        if not os.path.exists(nas_path):
            if smb_connected:
                _backup_smb_disconnect(nas_path)
            raise RuntimeError(f"NAS path not reachable: {nas_path}")

    # ── Display path for job record ───────────────────────────────────────────
    sftp_host = ''
    if auth_method == 'sftp':
        # Derive SFTP host from UNC path \\HOSTNAME\... if not stored separately
        parts = nas_path.lstrip('\\').split('\\')
        sftp_host = parts[0] if parts else nas_path
        sftp_remote = nas_creds.get('sftp_remote_path') or '/backups'
        snap_dest_display = f"sftp://{sftp_host}/{sftp_remote.lstrip('/')}/{hostname}/{snap_name}.zip"
    elif auth_method == 'smb_direct':
        snap_dest_display = f"\\\\{_nas_host}\\{_nas_share}\\{hostname}\\{snap_name}.zip"
    else:
        snap_dest_display = os.path.join(nas_path, hostname, snap_name)

    # ── Register job with tracker ─────────────────────────────────────────────
    start_resp = _backup_api(tracker_url, 'POST',
                             f'/api/rmm/backup-start/{agent_id}', agent_id, token, {
                                 'job_type': job_type,
                                 'snapshot_path': snap_dest_display,
                                 'triggered_by': triggered_by,
                             })
    if not start_resp.get('ok'):
        raise RuntimeError(f"Failed to register job: {start_resp.get('error')}")
    job_id = start_resp['job_id']

    # ── Copy files ────────────────────────────────────────────────────────────
    files_copied = files_skipped = files_failed = 0
    bytes_transferred = 0
    errors = []
    snap_dest = snap_dest_display

    try:
        if auth_method == 'sftp':
            # ── SFTP — paramiko, no domain auth at all ────────────────────────
            sftp_port = int(nas_creds.get('sftp_port') or 22)
            sftp_remote = nas_creds.get('sftp_remote_path') or '/backups'
            username = nas_creds.get('username') or ''
            password = nas_creds.get('password') or ''
            if not sftp_host or not username:
                raise RuntimeError("SFTP auth requires a NAS hostname and username")
            result = _backup_sftp_upload(
                sftp_host, sftp_port, username, password,
                sftp_remote, hostname, snap_name,
                include_paths, ext_excludes, folder_excludes,
                max_size_bytes, since_ts, progress_cb, job_id,
                tracker_url, agent_id, token,
            )
            snap_dest         = result['snap_dest']
            files_copied      = result['files_copied']
            files_skipped     = result['files_skipped']
            files_failed      = result['files_failed']
            bytes_transferred = result['bytes_transferred']
            errors            = result['errors']

        elif auth_method == 'smb_direct':
            # ── Pure-Python SMB2 — smbprotocol, bypasses Windows redirector ───
            username = nas_creds.get('username') or ''
            password = nas_creds.get('password') or ''
            if not _nas_host or not username:
                raise RuntimeError("smb_direct requires NAS host and username")
            result = _backup_smb_direct_upload(
                _nas_host, _nas_share,
                username, password,
                hostname, snap_name,
                include_paths, ext_excludes, folder_excludes,
                max_size_bytes, since_ts, progress_cb, job_id,
                tracker_url, agent_id, token,
            )
            snap_dest         = result['snap_dest']
            files_copied      = result['files_copied']
            files_skipped     = result['files_skipped']
            files_failed      = result['files_failed']
            bytes_transferred = result['bytes_transferred']
            errors            = result['errors']

        else:
            # ── SMB — shutil (net use session established above) ──────────────
            snap_dest = os.path.join(nas_path, hostname, snap_name)
            try:
                os.makedirs(snap_dest, exist_ok=True)
            except Exception as e:
                raise RuntimeError(f"Cannot create destination {snap_dest}: {e}")

            last_progress = _time.time()
            for src_root in include_paths:
                src_root = os.path.expandvars(src_root)
                if not os.path.isdir(src_root):
                    files_skipped += 1
                    continue

                for dirpath, dirnames, filenames in os.walk(src_root):
                    dirnames[:] = [d for d in dirnames if d not in folder_excludes]
                    for fname in filenames:
                        fpath = os.path.join(dirpath, fname)
                        if _backup_should_skip(fpath, ext_excludes, folder_excludes,
                                               max_size_bytes, since_ts):
                            files_skipped += 1
                            continue
                        _, tail = os.path.splitdrive(fpath)
                        rel    = tail.lstrip(os.sep)
                        dst    = os.path.join(snap_dest, rel)
                        try:
                            os.makedirs(os.path.dirname(dst), exist_ok=True)
                            shutil.copy2(fpath, dst)
                            files_copied   += 1
                            bytes_transferred += os.path.getsize(fpath)
                        except Exception as e:
                            files_failed += 1
                            errors.append(str(e)[:200])
                            if len(errors) > 50:
                                errors = errors[-50:]

                        now = _time.time()
                        if now - last_progress >= 30:
                            last_progress = now
                            prog = {'job_id': job_id, 'files_copied': files_copied,
                                    'files_skipped': files_skipped, 'files_failed': files_failed,
                                    'bytes_transferred': bytes_transferred}
                            progress_cb(prog)
                            try:
                                _backup_api(tracker_url, 'PATCH',
                                            f'/api/rmm/backup-job/{job_id}', agent_id, token, prog)
                            except Exception:
                                pass

    finally:
        # Always disconnect the SMB session regardless of success/failure
        if smb_connected:
            try:
                _backup_smb_disconnect(nas_path)
            except Exception:
                pass

    # ── Determine final status ────────────────────────────────────────────────
    if files_failed > 0 and files_copied == 0:
        status = 'failed'
    elif files_failed > 0:
        status = 'partial'
    else:
        status = 'success'

    # ── Report completion ─────────────────────────────────────────────────────
    final = {
        'status': status, 'files_copied': files_copied,
        'files_skipped': files_skipped, 'files_failed': files_failed,
        'bytes_transferred': bytes_transferred, 'snapshot_path': snap_dest,
        'errors': errors[:20] if errors else None,
    }
    try:
        _backup_api(tracker_url, 'POST',
                    f'/api/rmm/backup-complete/{job_id}', agent_id, token, final)
    except Exception as e:
        print(f"[backup] Failed to complete job {job_id}: {e}", flush=True)

    # ── Update local state ────────────────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    if job_type == 'full':
        state['last_full'] = now_iso
    state['last_incr'] = now_iso
    try:
        _backup_save_state(state)
    except Exception:
        pass

    # ── Retention pruning ─────────────────────────────────────────────────────
    try:
        _backup_prune_retention(nas_path, hostname, retention_days, nas_creds={
            **nas_creds, 'sftp_host': sftp_host
        })
    except Exception as e:
        print(f"[backup] Retention prune error: {e}", flush=True)

    print(f"[backup] {status}: {files_copied} copied, {files_failed} failed,"
          f" {bytes_transferred/1024/1024:.1f} MB → {snap_dest}", flush=True)
    return {**final, 'job_id': job_id, 'job_type': job_type}




async def _backup_task(ws, tracker_url: str, agent_id: str, token: str,
                       job_type: str, triggered_by: str) -> None:
    """Async wrapper: runs _do_backup in executor, sends progress/result via WebSocket."""
    global _BACKUP_RUNNING
    if _BACKUP_RUNNING:
        await ws.send(json.dumps({
            "type": "backup_result", "ok": False,
            "error": "A backup is already in progress on this agent",
        }))
        return

    _BACKUP_RUNNING = True
    loop = asyncio.get_event_loop()

    def progress_cb(data: dict):
        asyncio.run_coroutine_threadsafe(
            ws.send(json.dumps({"type": "backup_progress", **data})),
            loop
        )

    try:
        result = await loop.run_in_executor(
            None, _do_backup, tracker_url, agent_id, token,
            job_type, triggered_by, progress_cb
        )
        await ws.send(json.dumps({"type": "backup_result", "ok": True, **result}))
    except Exception as e:
        print(f"[backup] Error: {e}", flush=True)
        await ws.send(json.dumps({"type": "backup_result", "ok": False, "error": str(e)}))
    finally:
        _BACKUP_RUNNING = False


def _setup_agent_logging() -> None:
    """Tee stdout/stderr to a rotating C:\\CirqueRMM\\agent.log. The agent runs as a
    service (NSSM or native) whose stdout is discarded, so every print(..., flush=True)
    diagnostic was lost -- which made incidents undiagnosable without live probes.
    Mirror all output to a capped rotating file. Fully best-effort: any failure leaves
    stdout untouched."""
    try:
        import logging, logging.handlers
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent.log')
        h = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
        h.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        lg = logging.getLogger('cirquermm'); lg.setLevel(logging.INFO)
        lg.handlers = [h]; lg.propagate = False

        class _Tee:
            def __init__(self, orig): self._orig = orig
            def write(self, s):
                try:
                    if self._orig:
                        self._orig.write(s)
                except Exception:
                    pass
                try:
                    line = s.rstrip('\r\n')
                    if line:
                        lg.info(line)
                except Exception:
                    pass
            def flush(self):
                try:
                    if self._orig:
                        self._orig.flush()
                except Exception:
                    pass
        sys.stdout = _Tee(sys.stdout)
        sys.stderr = _Tee(sys.stderr)
        print(f"[agent] logging to {log_path} (rotating 10MB x5)", flush=True)
    except Exception:
        pass


async def main() -> None:
    global _disable_rustdesk, _disable_tray
    _setup_agent_logging()
    # Cloudflare fallback endpoints -- used when LAN (rmm.corp.cirque.com) is unreachable
    fallback_gateway = os.environ.get("RMM_GATEWAY_URL_PUBLIC", "wss://rmm.cirquetools.com").rstrip("/")
    fallback_tracker = os.environ.get("RMM_TRACKER_URL_PUBLIC", "https://tracker.cirquetools.com").rstrip("/")

    agent_id = os.environ.get("RMM_AGENT_ID") or socket.gethostname()
    token = get_env("RMM_AGENT_TOKEN")
    screenshot_enabled = os.environ.get("RMM_SCREENSHOT", "0") == "1"

    # Probe _LAN_GATEWAY_HOST (internal DNS only) to decide which endpoints to use.
    # Never probes Cloudflare hostnames -- their TCP port 443 is always reachable
    # even when the tunnel backend is down.
    tracker_url, gateway = _resolve_urls(fallback_tracker, fallback_gateway)

    # Self-update check on startup
    if check_for_update(tracker_url, agent_id, token):
        sys.exit(7)  # non-zero so NSSM always restarts after update

    # Provision the C:\ITTOOLS drop folder for the "Install software" feature
    # (idempotent; gated behind a sentinel so it doesn't thrash the ACL).
    ensure_ittools_dir()

    # Sync RustDesk peer ID on startup (fast, non-blocking)
    sync_rustdesk_id(tracker_url, agent_id, token)

    shells: Dict[int, ShellSession] = {}
    shell_tasks: Dict[int, asyncio.Task] = {}
    shell_stop_events: Dict[int, asyncio.Event] = {}

    ws_url = f"{gateway}/ws/agent/{agent_id}?token={token}"

    while True:
        try:
            async with websockets.connect(ws_url, max_size=20 * 1024 * 1024,
                                              ping_interval=None) as ws:
                print(f"[agent] Connected to gateway as {agent_id}", flush=True)

                loop = asyncio.get_event_loop()

                # Bounded collect: a single on-connect collection that hangs (e.g.
                # _collect_session_events on a box with a massive Security log)
                # previously blocked the WHOLE connect coroutine, so the periodic
                # tasks created below (telemetry, software inventory) were NEVER
                # started -- the box reported patches+pending then nothing, and
                # software inventory never sent (BRIAN-MSI). wait_for guarantees the
                # coroutine always reaches the task-creation lines.
                async def _collect(fn, *a, timeout=120):
                    return await asyncio.wait_for(
                        loop.run_in_executor(None, fn, *a), timeout=timeout)

                # Ensure RustDesk is installed -- runs in background after connect
                # so it never blocks the WebSocket from establishing.
                # Skipped for server-mode agents where disable_rustdesk flag is set.
                if not _disable_rustdesk:
                    # Run rustdesk-ensure THEN warp-ensure in a single background
                    # executor, in that order: WARP bake-in (off-site boxes only;
                    # on-LAN/site boxes skip) needs the clean RustDesk toml + key
                    # to already exist so its SYSTEM worker can preserve the key.
                    # Both are idempotent and deadlock-safe (WARP heavy steps run
                    # inside a one-shot SYSTEM task), and run off-thread so neither
                    # blocks the WebSocket from establishing.
                    # on_lan = this connection's OWN resolved verdict (LAN endpoint
                    # vs Cloudflare fallback). Fail-closed off-site signal for WARP.
                    _on_lan = (tracker_url == _LAN_TRACKER_URL)
                    def _rustdesk_then_warp():
                        try:
                            ensure_rustdesk(tracker_url, agent_id, token)
                        except Exception as _e:
                            print(f"[rustdesk] ensure error: {_e}", flush=True)
                        try:
                            ensure_warp(tracker_url, agent_id, token, _on_lan)
                        except Exception as _e:
                            print(f"[warp] ensure error: {_e}", flush=True)
                    loop.run_in_executor(None, _rustdesk_then_warp)

                # Collect extended info (hardware/OS/security) once on connect
                try:
                    extended = await _collect(_collect_extended, timeout=120)
                except Exception as e:
                    extended = {}
                    print(f"[agent] Extended collect failed/timed out: {e}", flush=True)

                # Initial telemetry on connect
                telemetry = collect_telemetry(agent_id)
                # Merge extended -- WMI values override the simpler ctypes ones
                for k, v in (extended or {}).items():
                    if v:
                        telemetry[k] = v
                await ws.send(json.dumps({**telemetry, "type": "agent_info"}))

                # Send patch report (can be slow -- run in executor)
                try:
                    patches = await _collect(_collect_patches, timeout=120)
                    await ws.send(json.dumps({"type": "patch_report", "patches": patches}))
                    print(f"[agent] Sent {len(patches)} patches", flush=True)
                except Exception as e:
                    print(f"[agent] Patch report failed: {e}", flush=True)

                # Send available/pending Windows Updates
                try:
                    pending = await _collect(_collect_pending_updates, timeout=120)
                    await ws.send(json.dumps({"type": "pending_updates", "updates": pending}))
                    print(f"[agent] Sent {len(pending)} pending update(s)", flush=True)
                except Exception as e:
                    print(f"[agent] Pending updates failed: {e}", flush=True)

                # Send session events (logon/logoff/lock/unlock/sleep/wake -- last 7 days)
                try:
                    sev = await _collect(_collect_session_events, timeout=90)
                    await ws.send(json.dumps({"type": "session_events", "events": sev}))
                    print(f"[agent] Sent {len(sev)} session event(s)", flush=True)
                except Exception as e:
                    print(f"[agent] Session events failed/timed out: {e}", flush=True)

                # Software inventory is NOT sent here in the sequential connect burst.
                # It rides its own task over the WS (software_inventory_loop below),
                # which -- thanks to the bounded _collect() above -- is now guaranteed
                # to actually get created even if a burst collection times out.

                # Periodic telemetry task
                async def telemetry_loop():
                    while True:
                        await asyncio.sleep(60)
                        try:
                            data = await loop.run_in_executor(None, collect_telemetry, agent_id)
                            await ws.send(json.dumps(data))
                        except Exception:
                            break

                telem_task = asyncio.create_task(telemetry_loop())

                # RustDesk watchdog -- reinstalls if user uninstalls it (check hourly)
                # Disabled entirely for server-mode agents.
                async def rustdesk_watchdog():
                    while True:
                        await asyncio.sleep(3600)  # check every hour
                        try:
                            if not _disable_rustdesk:
                                await loop.run_in_executor(
                                    None, ensure_rustdesk, tracker_url, agent_id, token
                                )
                        except Exception:
                            pass

                rustdesk_task = asyncio.create_task(rustdesk_watchdog())

                # Software inventory -- its OWN task, sent over the WebSocket (the
                # gateway stores it, NUL-sanitized). Runs immediately on connect then
                # every 24h. Previously this POSTed over a separate direct HTTPS call
                # (post_software_inventory) which failed silently on some boxes (TLS /
                # TeamViewer tv_x64.dll HTTP breakage / NUL inserts) -> 0 rows. Riding
                # the already-established WS channel makes delivery reliable. An empty
                # collection is skipped so a transient failure never wipes good data.
                async def software_inventory_loop():
                    while True:
                        try:
                            sw = await loop.run_in_executor(None, _collect_software)
                            if sw:
                                await ws.send(json.dumps(
                                    {"type": "software_inventory", "software": sw}))
                                print(f"[agent] Sent {len(sw)} software entries (WS)", flush=True)
                        except Exception as e:
                            print(f"[agent] software_inventory_loop error: {e}", flush=True)
                        await asyncio.sleep(86400)  # re-collect every 24h

                asyncio.create_task(software_inventory_loop())

                # Tray setup -- runs once per agent process lifetime, then refreshes every 24h
                # Disabled entirely for server-mode agents where disable_tray flag is set.
                global _tray_setup_done
                if not _tray_setup_done and not _disable_tray:
                    _tray_setup_done = True
                    async def tray_watchdog():
                        await asyncio.sleep(30)  # short delay after first connect
                        while True:
                            try:
                                await loop.run_in_executor(
                                    None, _setup_tray, tracker_url, agent_id, token
                                )
                            except Exception:
                                pass
                            await asyncio.sleep(86400)  # refresh every 24h

                    asyncio.create_task(tray_watchdog())

                # Periodic self-update check -- every 4 hours while running.
                # CRITICAL: use os._exit(7), NOT sys.exit(7). sys.exit raises
                # SystemExit, which inside a create_task'd coroutine is captured by
                # the task and does NOT terminate the process -- so the autonomous
                # self-update was dead: agents downloaded + wrote the new code to disk
                # but kept running the OLD in-memory code forever (fleet froze on old
                # versions). os._exit terminates immediately from any context, so the
                # service supervisor restarts into the new code. Guarded to spawn ONCE
                # per process (it's inside the WS reconnect loop -- without the guard
                # every reconnect leaked another 4h timer).
                global _periodic_update_started
                if not _periodic_update_started:
                    _periodic_update_started = True

                    async def periodic_update_check():
                        while True:
                            await asyncio.sleep(4 * 3600)
                            try:
                                updated = await loop.run_in_executor(
                                    None, check_for_update, tracker_url, agent_id, token
                                )
                                if updated:
                                    print("[update] periodic update applied -- restarting", flush=True)
                                    os._exit(7)
                            except Exception:
                                pass

                    asyncio.create_task(periodic_update_check())

                # Eagle Eyes monitoring task (started on demand via eagle_eyes_config)
                eagle_task: Optional[asyncio.Task] = None
                _eagle_cfg: dict = {"enabled": False, "screenshot_interval_min": 30, "screenshots_enabled": False}

                async def eagle_monitor_loop():
                    """Poll active window every 10s; screenshot every N minutes.

                    Emits:
                      eagle_event     - on window change or every HEARTBEAT_S (recorded to DB)
                      eagle_heartbeat - every LIVE_PING_S (live 'right now' panel, no DB write)
                    """
                    HEARTBEAT_S    = 60           # emit DB event every 1 minute on same window
                    LIVE_PING_S    = 30           # send live heartbeat every 30 seconds
                    IDLE_THRESH_S  = 300          # 5 min idle = consider user idle
                    last_process   = ""
                    last_title     = ""
                    last_change_at = datetime.now()
                    last_emit_at   = datetime.now()
                    last_ping_at   = datetime.min
                    last_shot_at   = datetime.min   # fire screenshot on first loop iteration
                    poll_s         = 10
                    first_poll     = True

                    while True:
                        await asyncio.sleep(poll_s)
                        now = datetime.now()

                        # Active window + idle state. idle_s comes from the user-session
                        # helper (session-0 GetLastInputInfo is blind to the interactive
                        # user, so the old _get_idle_seconds() reported bogus idle).
                        proc, title, idle_s = await loop.run_in_executor(None, _get_active_window_info)
                        is_idle     = idle_s >= IDLE_THRESH_S

                        if proc != last_process or title != last_title:
                            # Emit event for the window we're LEAVING (skip on very first poll)
                            if (last_process or last_title) and not first_poll:
                                dur_s = int((now - last_emit_at).total_seconds())
                                try:
                                    await ws.send(json.dumps({
                                        "type":       "eagle_event",
                                        "process":    last_process,
                                        "title":      last_title,
                                        "duration_s": dur_s,
                                        "idle_s":     idle_s,
                                        "captured_at": last_emit_at.strftime("%Y-%m-%dT%H:%M:%S"),
                                    }))
                                except Exception:
                                    return
                            last_process   = proc
                            last_title     = title
                            last_change_at = now
                            last_emit_at   = now

                        # Heartbeat: emit DB event on same window every HEARTBEAT_S
                        elif (last_process or last_title) and not first_poll:
                            elapsed = (now - last_emit_at).total_seconds()
                            if elapsed >= HEARTBEAT_S:
                                dur_s = int(elapsed)
                                try:
                                    await ws.send(json.dumps({
                                        "type":       "eagle_event",
                                        "process":    last_process,
                                        "title":      last_title,
                                        "duration_s": dur_s,
                                        "idle_s":     idle_s,
                                        "captured_at": last_emit_at.strftime("%Y-%m-%dT%H:%M:%S"),
                                    }))
                                except Exception:
                                    return
                                last_emit_at = now

                        # After first successful window detection, send an immediate event
                        if first_poll and (proc or title):
                            first_poll = False
                            try:
                                await ws.send(json.dumps({
                                    "type":       "eagle_event",
                                    "process":    proc,
                                    "title":      title,
                                    "duration_s": 0,
                                    "idle_s":     idle_s,
                                    "captured_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
                                }))
                            except Exception:
                                return
                        elif first_poll:
                            first_poll = False

                        # Live ping every LIVE_PING_S -- updates 'right now' panel without a DB write
                        if (now - last_ping_at).total_seconds() >= LIVE_PING_S:
                            last_ping_at = now
                            try:
                                await ws.send(json.dumps({
                                    "type":        "eagle_heartbeat",
                                    "process":     proc,
                                    "title":       title,
                                    "idle_s":      idle_s,
                                    "is_idle":     is_idle,
                                    "captured_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
                                }))
                            except Exception:
                                return

                        # Periodic screenshot -- honor the latest screenshots_enabled flag
                        # (re-read each cycle so toggling off in the UI takes effect
                        #  without an agent restart; the server pushes eagle_eyes_config
                        #  whenever the flag changes).
                        interval_s = _eagle_cfg.get("screenshot_interval_min", 30) * 60
                        if _eagle_cfg.get("screenshots_enabled", True) and (now - last_shot_at).total_seconds() >= interval_s:
                            last_shot_at = now
                            try:
                                result = await loop.run_in_executor(None, capture_screenshot)
                                if result and result.get("data"):
                                    await ws.send(json.dumps({
                                        "type":   "eagle_screenshot",
                                        "data":   result["data"],
                                        "width":  result.get("width", 0),
                                        "height": result.get("height", 0),
                                        "format": result.get("format", "jpeg"),
                                    }))
                            except Exception:
                                pass  # screenshot failure must NOT kill the monitor loop

                try:
                    async for raw in ws:
                        try:
                            payload = json.loads(raw)
                        except Exception:
                            continue

                        msg_type = payload.get("type")
                        session_id = payload.get("session_id")

                        if msg_type == "ping":
                            await ws.send(json.dumps({"type": "pong"}))
                            continue

                        # --- Agent config (server pushes flags on connect) ---
                        if msg_type == "agent_config":
                            _disable_rustdesk = bool(payload.get("disable_rustdesk", False))
                            _disable_tray     = bool(payload.get("disable_tray", False))
                            print(f"[agent] agent_config applied: disable_rustdesk={_disable_rustdesk} disable_tray={_disable_tray}", flush=True)
                            continue

                        # --- Screenshot ---
                        if msg_type == "screenshot_request":
                            if not screenshot_enabled:
                                await ws.send(json.dumps({
                                    "type": "screenshot_response",
                                    "session_id": session_id,
                                    "error": "Screenshots disabled on this agent. Set RMM_SCREENSHOT=1.",
                                }))
                                continue
                            loop = asyncio.get_event_loop()
                            result = await loop.run_in_executor(None, capture_screenshot)
                            await ws.send(json.dumps({
                                "type": "screenshot_response",
                                "session_id": session_id,
                                **(result or {"error": "Capture failed"}),
                            }))
                            continue

                        # --- Remote update trigger ---
                        if msg_type == "update_now":
                            print("[agent] Received update_now -- checking for new version...", flush=True)
                            if check_for_update(tracker_url, agent_id, token):
                                sys.exit(7)   # non-zero so NSSM always restarts; new version will run
                            await ws.send(json.dumps({"type": "update_result", "updated": False, "version": AGENT_VERSION}))
                            continue

                        # --- On-demand telemetry ---
                        if msg_type == "telemetry_request":
                            data = collect_telemetry(agent_id)
                            await ws.send(json.dumps(data))
                            continue

                        # --- Restart agent ---
                        if msg_type == "restart_agent":
                            print("[agent] Received restart_agent command -- restarting via NSSM", flush=True)
                            await ws.send(json.dumps({"type": "restart_agent_ack", "ok": True}))
                            await asyncio.sleep(0.5)
                            sys.exit(0)  # NSSM will restart the agent automatically

                        # --- Eagle Eyes monitoring toggle ---
                        if msg_type == "eagle_eyes_config":
                            _eagle_cfg["enabled"] = bool(payload.get("enabled", False))
                            _eagle_cfg["screenshot_interval_min"] = int(payload.get("screenshot_interval_min", 30))
                            _eagle_cfg["screenshots_enabled"] = bool(payload.get("screenshots_enabled", False))
                            if _eagle_cfg["enabled"]:
                                if eagle_task is None or eagle_task.done():
                                    eagle_task = asyncio.create_task(eagle_monitor_loop())
                                    print(f"[agent] Eagle Eyes started (screenshot every {_eagle_cfg['screenshot_interval_min']}min)", flush=True)
                            else:
                                if eagle_task and not eagle_task.done():
                                    eagle_task.cancel()
                                    eagle_task = None
                                    print("[agent] Eagle Eyes stopped.", flush=True)
                            continue

                        # --- Shell start ---
                        if msg_type == "shell_start":
                            sid = int(session_id)
                            if sid in shells:
                                await shells[sid].stop()
                                stop_ev = shell_stop_events.pop(sid, None)
                                if stop_ev:
                                    stop_ev.set()

                            shell_type = payload.get("shell", "powershell")
                            cols = int(payload.get("cols", 220))
                            rows = int(payload.get("rows", 50))
                            session = ShellSession(sid, shell=shell_type, cols=cols, rows=rows)
                            ok = await session.start()
                            if not ok:
                                await ws.send(json.dumps({"type": "error", "session_id": sid, "error": "Failed to start shell"}))
                                continue
                            shells[sid] = session
                            stop_ev = asyncio.Event()
                            shell_stop_events[sid] = stop_ev
                            task = asyncio.create_task(shell_output_loop(session, ws, stop_ev))
                            shell_tasks[sid] = task
                            await ws.send(json.dumps({"type": "shell_started", "session_id": sid, "shell": shell_type}))
                            continue

                        # --- Shell input ---
                        if msg_type == "shell_input":
                            sid = int(session_id)
                            if sid in shells and shells[sid].is_alive():
                                await shells[sid].send_input(payload.get("data", ""))
                            continue

                        # --- Shell resize ---
                        if msg_type == "shell_resize":
                            sid = int(session_id)
                            if sid in shells:
                                cols = int(payload.get("cols", 220))
                                rows = int(payload.get("rows", 50))
                                await shells[sid].resize(cols, rows)
                            continue

                        # --- Shell stop ---
                        if msg_type == "shell_stop":
                            sid = int(session_id)
                            stop_ev = shell_stop_events.pop(sid, None)
                            if stop_ev:
                                stop_ev.set()
                            t = shell_tasks.pop(sid, None)
                            if t:
                                t.cancel()
                            if sid in shells:
                                await shells[sid].stop()
                                shells.pop(sid, None)
                            await ws.send(json.dumps({"type": "shell_exited", "session_id": session_id}))
                            continue

                        # --- On-demand patch scan ---
                        if msg_type == "request_patch_scan":
                            print("[agent] request_patch_scan received -- scanning WUA", flush=True)
                            loop2 = asyncio.get_event_loop()
                            try:
                                pending = await loop2.run_in_executor(None, _collect_pending_updates)
                                await ws.send(json.dumps({"type": "pending_updates", "updates": pending}))
                                print(f"[agent] Sent {len(pending)} pending update(s) (on-demand)", flush=True)
                            except Exception as _e:
                                print(f"[agent] request_patch_scan failed: {_e}", flush=True)
                            continue

                        # --- Install approved patches ---
                        if msg_type == "install_patches":
                            job_id       = payload.get("job_id")
                            update_ids   = payload.get("update_ids") or []
                            kb_ids       = payload.get("kb_ids") or []
                            titles       = payload.get("titles") or []
                            allow_reboot = bool(payload.get("allow_reboot", False))
                            print(f"[agent] install_patches job={job_id} count={len(update_ids)} allow_reboot={allow_reboot}", flush=True)
                            loop2 = asyncio.get_event_loop()
                            result = await loop2.run_in_executor(None, _install_patches_wua, update_ids, kb_ids, titles)
                            await ws.send(json.dumps({
                                "type":   "patch_install_result",
                                "job_id": job_id,
                                "result": result,
                            }))
                            # Only reboot when the server explicitly allows it. Otherwise the
                            # reboot stays pending (reboot_required is reported) and the user
                            # reboots on their own schedule via the tray.
                            if result.get("reboot_required") and not result.get("error") and allow_reboot:
                                asyncio.create_task(_do_reboot_sequence())
                            continue

                        # --- Deploy patches by CVE ID (WUA searches locally) ---
                        if msg_type == "install_cve_patches":
                            job_id       = payload.get("job_id")
                            cve_ids      = payload.get("cve_ids") or []
                            product_name = payload.get("product_name") or ''
                            allow_reboot = bool(payload.get("allow_reboot", False))
                            print(f"[agent] install_cve_patches job={job_id} cves={cve_ids} product={product_name} allow_reboot={allow_reboot}", flush=True)
                            loop2 = asyncio.get_event_loop()
                            result = await loop2.run_in_executor(
                                None, _find_and_install_cve_patches, cve_ids, product_name
                            )
                            await ws.send(json.dumps({
                                "type":   "cve_patch_result",
                                "job_id": job_id,
                                "result": result,
                            }))
                            if result.get("reboot_required") and not result.get("error") and allow_reboot:
                                asyncio.create_task(_do_reboot_sequence())
                            continue

                        # --- Legacy exec ---
                        if msg_type == "exec":
                            cmd = payload.get("command", "")
                            allowed = {"whoami", "hostname", "ipconfig"}
                            if cmd not in allowed:
                                await ws.send(json.dumps({"type": "exec_result", "session_id": session_id, "exit_code": 126, "stdout": "", "stderr": f"Not allowed: {cmd}"}))
                                continue
                            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                            await ws.send(json.dumps({"type": "exec_result", "session_id": session_id, "exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}))
                            continue


                        # --- Run Script (PowerShell or CMD) ---
                        if msg_type == "run_script":
                            shell = payload.get("shell", "powershell").lower()
                            code  = payload.get("code", "")
                            tout  = min(int(payload.get("timeout", 60)), 300)
                            try:
                                loop = asyncio.get_event_loop()
                                if shell == "cmd":
                                    argv = ["cmd.exe", "/c", code]
                                else:
                                    argv = ["powershell.exe", "-NonInteractive", "-NoProfile",
                                            "-ExecutionPolicy", "Bypass", "-Command", code]
                                # Deadlock-safe: capture via temp files, not inheritable
                                # pipes, so a long-lived grandchild (cloudflared, service
                                # restart, detached helper) can never stall communicate()
                                # and wedge the WS command loop. (R: run_script deadlock)
                                rc, so, se = await loop.run_in_executor(
                                    None, _run_script_capture, argv, tout)
                                await ws.send(json.dumps({
                                    "type": "script_result", "session_id": session_id,
                                    "exit_code": rc,
                                    "stdout": so[-32000:],
                                    "stderr": se[-8000:],
                                }))
                            except subprocess.TimeoutExpired:
                                await ws.send(json.dumps({
                                    "type": "script_result", "session_id": session_id,
                                    "exit_code": -1, "stdout": "", "stderr": "Timed out",
                                }))
                            except Exception as e:
                                await ws.send(json.dumps({
                                    "type": "script_result", "session_id": session_id,
                                    "exit_code": -1, "stdout": "", "stderr": str(e),
                                }))
                            continue

                        # --- List Services ---
                        if msg_type == "list_services":
                            raw = _ps_json(
                                "Get-Service | Select-Object Name,DisplayName,"
                                "@{N='Status';E={$_.Status.ToString()}},"
                                "@{N='StartType';E={$_.StartType.ToString()}} | ConvertTo-Json -Compress",
                                timeout=20,
                            )
                            svcs = []
                            for s in _normalise_ps_list(raw):
                                svcs.append({
                                    "name":         s.get("Name", ""),
                                    "display_name": s.get("DisplayName", ""),
                                    "status":       s.get("Status", ""),
                                    "start_type":   s.get("StartType", ""),
                                })
                            await ws.send(json.dumps({
                                "type": "services_result", "session_id": session_id,
                                "services": svcs,
                            }))
                            continue

                        # --- Service Action (start/stop/restart) ---
                        if msg_type == "service_action":
                            svc_name   = payload.get("name", "")
                            svc_action = payload.get("action", "").lower()
                            if not svc_name or svc_action not in ("start", "stop", "restart"):
                                await ws.send(json.dumps({
                                    "type": "service_action_result", "session_id": session_id,
                                    "success": False, "error": "Invalid name or action",
                                }))
                                continue
                            ps_cmd = {
                                "start":   "Start-Service -Name '{}' -EA Stop".format(svc_name),
                                "stop":    "Stop-Service  -Name '{}' -Force -EA Stop".format(svc_name),
                                "restart": "Restart-Service -Name '{}' -Force -EA Stop".format(svc_name),
                            }[svc_action]
                            try:
                                r = subprocess.run(
                                    ["powershell", "-NonInteractive", "-NoProfile",
                                     "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                                    capture_output=True, text=True, timeout=30,
                                )
                                ok = r.returncode == 0
                                await ws.send(json.dumps({
                                    "type": "service_action_result", "session_id": session_id,
                                    "success": ok,
                                    "error": (r.stderr or r.stdout).strip() if not ok else "",
                                }))
                            except Exception as e:
                                await ws.send(json.dumps({
                                    "type": "service_action_result", "session_id": session_id,
                                    "success": False, "error": str(e),
                                }))
                            continue

                        # --- Event Viewer query ---
                        if msg_type == "get_event_log":
                            log_name = payload.get("log", "System")
                            max_ev   = min(int(payload.get("max", 100)), 500)
                            level    = payload.get("level")
                            source   = payload.get("source", "")
                            filt = "@{{LogName='{}'".format(log_name)
                            if level:
                                filt += ";Level={}".format(level)
                            if source:
                                filt += ";ProviderName='{}'".format(source)
                            filt += "}"
                            raw = _ps_json(
                                "Get-WinEvent -FilterHashtable {} -MaxEvents {} -EA SilentlyContinue".format(filt, max_ev)
                                + "|Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,Message"
                                + "|ForEach-Object{[pscustomobject]@{"
                                + "t=$_.TimeCreated.ToString('o');"
                                + "id=$_.Id;"
                                + "lvl=$_.LevelDisplayName;"
                                + "src=$_.ProviderName;"
                                + "msg=if($_.Message){($_.Message -split \"`n\")[0].Trim()}else{''}"
                                + "}}|ConvertTo-Json -Compress",
                                timeout=30,
                            )
                            evts = []
                            for ev in _normalise_ps_list(raw):
                                evts.append({
                                    "time":     ev.get("t", ""),
                                    "event_id": ev.get("id", ""),
                                    "level":    ev.get("lvl", ""),
                                    "source":   ev.get("src", ""),
                                    "message":  str(ev.get("msg", ""))[:300],
                                })
                            await ws.send(json.dumps({
                                "type": "event_log_result", "session_id": session_id,
                                "log": log_name, "events": evts,
                            }))
                            continue

                        # --- List Directory ---
                        if msg_type == "list_directory":
                            dir_path = payload.get("path", "")
                            try:
                                if not dir_path:
                                    # List available drives
                                    raw = _ps_json(
                                        "Get-PSDrive -PSProvider FileSystem"
                                        "| Select-Object @{N='Name';E={$_.Root}}"
                                        "| ConvertTo-Json -Compress",
                                        timeout=10,
                                    )
                                    entries = []
                                    for drv in _normalise_ps_list(raw):
                                        root = drv.get("Name", "")
                                        if root:
                                            entries.append({"name": root, "is_dir": True, "size": -1, "modified": ""})
                                    resp_path = ""
                                else:
                                    raw = _ps_json(
                                        f"Get-ChildItem -LiteralPath '{dir_path}' -Force -EA SilentlyContinue"
                                        f"|ForEach-Object{{[pscustomobject]@{{"
                                        f"n=$_.Name;"
                                        f"d=[int]$_.PSIsContainer;"
                                        f"s=if($_.PSIsContainer){{-1}}else{{[long]$_.Length}};"
                                        f"m=$_.LastWriteTime.ToString('o')"
                                        f"}}}}|ConvertTo-Json -Compress",
                                        timeout=15,
                                    )
                                    entries = []
                                    for item in _normalise_ps_list(raw):
                                        is_dir = bool(item.get("d", 0))
                                        raw_size = item.get("s", -1)
                                        size = -1 if is_dir else (int(raw_size) if isinstance(raw_size, (int, float, str)) else -1)
                                        entries.append({
                                            "name":     item.get("n", ""),
                                            "is_dir":   is_dir,
                                            "size":     size,
                                            "modified": item.get("m", ""),
                                        })
                                    # dirs first, then files, both ?-sorted
                                    entries.sort(key=lambda x: (0 if x["is_dir"] else 1, x["name"].lower()))
                                    resp_path = dir_path
                                await ws.send(json.dumps({
                                    "type": "list_dir_result", "session_id": session_id,
                                    "path": resp_path, "entries": entries,
                                }))
                            except Exception as exc:
                                await ws.send(json.dumps({
                                    "type": "list_dir_result", "session_id": session_id,
                                    "path": dir_path, "entries": [], "error": str(exc),
                                }))
                            continue

                        # --- File Download (agent to browser) ---
                        if msg_type == "file_download":
                            path = payload.get("path", "")
                            try:
                                with open(path, "rb") as fh:
                                    raw_bytes = fh.read()
                                import os as _os
                                await ws.send(json.dumps({
                                    "type": "file_download_data", "session_id": session_id,
                                    "filename": _os.path.basename(path),
                                    "size": len(raw_bytes),
                                    "data": base64.b64encode(raw_bytes).decode(),
                                }))
                            except Exception as e:
                                await ws.send(json.dumps({
                                    "type": "file_download_data", "session_id": session_id,
                                    "error": str(e),
                                }))
                            continue

                        # --- File Upload (browser to agent) ---
                        if msg_type == "file_upload":
                            path = payload.get("path", "")
                            data = payload.get("data", "")
                            try:
                                import os as _os
                                _os.makedirs(_os.path.dirname(_os.path.abspath(path)), exist_ok=True)
                                with open(path, "wb") as fh:
                                    fh.write(base64.b64decode(data))
                                await ws.send(json.dumps({
                                    "type": "file_upload_result", "session_id": session_id,
                                    "success": True, "path": path,
                                }))
                            except Exception as e:
                                await ws.send(json.dumps({
                                    "type": "file_upload_result", "session_id": session_id,
                                    "success": False, "error": str(e),
                                }))
                            continue

                        # --- Power / Shutdown actions ---
                        if msg_type == "power_action":
                            action = payload.get("action", "").lower()

                            def _run_in_user_session(cmd: str) -> str:
                                """Run cmd in the active interactive user session via WTS API (ctypes)."""
                                import ctypes, ctypes.wintypes as wt
                                wtsapi  = ctypes.WinDLL("wtsapi32", use_last_error=True)
                                kernel  = ctypes.WinDLL("kernel32",  use_last_error=True)
                                advapi  = ctypes.WinDLL("advapi32",  use_last_error=True)

                                session_id = kernel.WTSGetActiveConsoleSessionId()
                                if session_id == 0xFFFFFFFF:
                                    return "No active console session"

                                h_token = wt.HANDLE()
                                if not wtsapi.WTSQueryUserToken(session_id, ctypes.byref(h_token)):
                                    return f"WTSQueryUserToken failed: {ctypes.get_last_error()}"

                                class STARTUPINFOW(ctypes.Structure):
                                    _fields_ = [
                                        ("cb",              ctypes.c_ulong),
                                        ("lpReserved",      ctypes.c_wchar_p),
                                        ("lpDesktop",       ctypes.c_wchar_p),
                                        ("lpTitle",         ctypes.c_wchar_p),
                                        ("dwX",             ctypes.c_ulong),
                                        ("dwY",             ctypes.c_ulong),
                                        ("dwXSize",         ctypes.c_ulong),
                                        ("dwYSize",         ctypes.c_ulong),
                                        ("dwXCountChars",   ctypes.c_ulong),
                                        ("dwYCountChars",   ctypes.c_ulong),
                                        ("dwFillAttribute", ctypes.c_ulong),
                                        ("dwFlags",         ctypes.c_ulong),
                                        ("wShowWindow",     ctypes.c_ushort),
                                        ("cbReserved2",     ctypes.c_ushort),
                                        ("lpReserved2",     ctypes.c_void_p),
                                        ("hStdInput",       wt.HANDLE),
                                        ("hStdOutput",      wt.HANDLE),
                                        ("hStdError",       wt.HANDLE),
                                    ]
                                class PROCESS_INFORMATION(ctypes.Structure):
                                    _fields_ = [
                                        ("hProcess",    wt.HANDLE),
                                        ("hThread",     wt.HANDLE),
                                        ("dwProcessId", ctypes.c_ulong),
                                        ("dwThreadId",  ctypes.c_ulong),
                                    ]

                                si = STARTUPINFOW()
                                si.cb        = ctypes.sizeof(STARTUPINFOW)
                                si.lpDesktop = "winsta0\\default"
                                pi = PROCESS_INFORMATION()

                                CREATE_NO_WINDOW = 0x08000000
                                ok = advapi.CreateProcessAsUserW(
                                    h_token, None, cmd,
                                    None, None, False,
                                    CREATE_NO_WINDOW, None, None,
                                    ctypes.byref(si), ctypes.byref(pi),
                                )
                                kernel.CloseHandle(h_token)
                                if ok:
                                    kernel.CloseHandle(pi.hProcess)
                                    kernel.CloseHandle(pi.hThread)
                                    return ""
                                return f"CreateProcessAsUser failed: {ctypes.get_last_error()}"

                            def _get_active_session_id() -> str:
                                """Return the active session ID string for logoff."""
                                try:
                                    import ctypes
                                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                                    sid = kernel.WTSGetActiveConsoleSessionId()
                                    if sid != 0xFFFFFFFF:
                                        return str(sid)
                                except Exception:
                                    pass
                                return ""

                            _direct_cmds = {
                                "shutdown": ["shutdown", "/s", "/t", "30", "/c", "Remote shutdown via Cirque RMM"],
                                "restart":  ["shutdown", "/r", "/t", "30", "/c", "Remote restart via Cirque RMM"],
                                "cancel":   ["shutdown", "/a"],
                            }
                            _valid = {"shutdown", "restart", "cancel", "lock", "logoff"}
                            if action not in _valid:
                                await ws.send(json.dumps({
                                    "type": "power_ack", "session_id": session_id,
                                    "success": False, "error": "Unknown action",
                                }))
                                continue
                            try:
                                err = ""
                                if action in _direct_cmds:
                                    subprocess.Popen(_direct_cmds[action])
                                elif action == "lock":
                                    err = await loop.run_in_executor(
                                        None, _run_in_user_session,
                                        "rundll32.exe user32.dll,LockWorkStation"
                                    )
                                elif action == "logoff":
                                    sid_str = _get_active_session_id()
                                    if sid_str:
                                        subprocess.Popen(["logoff", sid_str])
                                    else:
                                        err = "Could not determine active session ID"
                                if err:
                                    await ws.send(json.dumps({
                                        "type": "power_ack", "session_id": session_id,
                                        "success": False, "error": err,
                                    }))
                                else:
                                    await ws.send(json.dumps({
                                        "type": "power_ack", "session_id": session_id,
                                        "success": True, "action": action,
                                    }))
                            except Exception as e:
                                await ws.send(json.dumps({
                                    "type": "power_ack", "session_id": session_id,
                                    "success": False, "error": str(e),
                                }))
                            continue

                        if msg_type in ("winget_search", "winget_install", "sw_uninstall"):
                            import shutil, glob as _glob

                            def _find_winget() -> str:
                                found = shutil.which("winget")
                                if found:
                                    return found
                                for pat in [
                                    r"C:\Program Files\WindowsApps\Microsoft.DesktopAppInstaller_*\winget.exe",
                                    r"C:\Program Files\WindowsApps\Microsoft.DesktopAppInstaller_*_x64__*\winget.exe",
                                ]:
                                    hits = _glob.glob(pat)
                                    if hits:
                                        return sorted(hits)[-1]
                                for profile in _glob.glob(os.path.join(os.environ.get("SystemDrive", "C:") + r"\Users", "*")):
                                    c = os.path.join(profile, r"AppData\Local\Microsoft\WindowsApps\winget.exe")
                                    if os.path.isfile(c):
                                        return c
                                return "winget"

                            def _get_user_localappdata() -> str:
                                """Get LOCALAPPDATA of the active console user via WTS so winget
                                   can find its source cache while running as SYSTEM."""
                                try:
                                    import ctypes, ctypes.wintypes as wt
                                    kernel  = ctypes.WinDLL("kernel32",  use_last_error=True)
                                    wtsapi  = ctypes.WinDLL("wtsapi32",  use_last_error=True)
                                    sid = kernel.WTSGetActiveConsoleSessionId()
                                    if sid == 0xFFFFFFFF:
                                        return ""
                                    # WTSQuerySessionInformation: WTSUserName=5, WTSDomainName=7
                                    pBuf   = ctypes.c_wchar_p()
                                    pBytes = ctypes.c_ulong()
                                    WTSUserName = 5
                                    ok = wtsapi.WTSQuerySessionInformationW(
                                        None, sid, WTSUserName,
                                        ctypes.byref(pBuf), ctypes.byref(pBytes))
                                    if not ok or not pBuf.value:
                                        return ""
                                    username = pBuf.value
                                    wtsapi.WTSFreeMemory(pBuf)
                                    drive = os.environ.get("SystemDrive", "C:")
                                    candidate = os.path.join(drive, "Users", username, "AppData", "Local")
                                    if os.path.isdir(candidate):
                                        return candidate
                                except Exception:
                                    pass
                                return ""

                            def _winget_env() -> dict:
                                """Build env for winget running as SYSTEM with user LOCALAPPDATA."""
                                env = os.environ.copy()
                                user_local = _get_user_localappdata()
                                if user_local:
                                    env["LOCALAPPDATA"] = user_local
                                return env

                            # --- placeholder so old references below compile ---
                            winget_exe = _find_winget()
                            cf = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                            env = _winget_env()

                            if msg_type == "winget_search":
                                term = payload.get("term", "").strip()
                                try:
                                    proc = await asyncio.create_subprocess_exec(
                                        winget_exe, "search", term,
                                        "--count", "20",
                                        "--accept-source-agreements",
                                        "--source", "winget",
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE,
                                        env=env,
                                        creationflags=cf,
                                    )
                                    try:
                                        raw_out, raw_err = await asyncio.wait_for(proc.communicate(), timeout=45)
                                    except asyncio.TimeoutError:
                                        proc.kill()
                                        await ws.send(json.dumps({
                                            "type": "winget_search_result", "session_id": session_id,
                                            "results": [], "term": term,
                                            "error": "winget timed out -- sources may not be cached yet. Run: winget source update",
                                        }))
                                        continue
                                    stdout_text = raw_out.decode("utf-8", errors="replace")
                                    stderr_text = raw_err.decode("utf-8", errors="replace")
                                    lines = stdout_text.splitlines()
                                    results = []
                                    header_idx = None
                                    for i, ln in enumerate(lines):
                                        if "Name" in ln and "Id" in ln and "Version" in ln:
                                            header_idx = i
                                            break
                                    if header_idx is not None:
                                        hdr = lines[header_idx]
                                        n_col   = hdr.index("Name")
                                        id_col  = hdr.index("Id")
                                        ver_col = hdr.index("Version") if "Version" in hdr else None
                                        src_col = hdr.index("Source")  if "Source"  in hdr else None
                                        for ln in lines[header_idx + 2:]:
                                            if not ln.strip():
                                                continue
                                            name    = ln[n_col:id_col].strip()
                                            id_end  = ver_col if ver_col else (src_col if src_col else len(ln))
                                            pkg_id  = ln[id_col:id_end].strip()
                                            ver_end = src_col if src_col else len(ln)
                                            version = ln[ver_col:ver_end].strip().split()[0] if ver_col and len(ln) > ver_col else ""
                                            if name and pkg_id:
                                                results.append({"name": name, "id": pkg_id, "version": version})
                                    await ws.send(json.dumps({
                                        "type": "winget_search_result", "session_id": session_id,
                                        "results": results[:20], "term": term,
                                        "debug_stdout": stdout_text[:2000],
                                        "debug_stderr": stderr_text[:500],
                                        "debug_exe": winget_exe,
                                        "debug_localappdata": env.get("LOCALAPPDATA", ""),
                                    }))
                                except Exception as e:
                                    await ws.send(json.dumps({
                                        "type": "winget_search_result", "session_id": session_id,
                                        "results": [], "error": str(e), "term": term,
                                    }))
                                continue

                            # winget_install / sw_uninstall
                            # Use PowerShell to redirect winget output to a temp file,
                            # then poll+stream it. PowerShell handles headless redirection
                            # better than Python subprocess with winget's console APIs.
                            if msg_type == "winget_install":
                                pkg_id = payload.get("id", "")
                                wg_action = f'install --id "{pkg_id}" --silent --accept-package-agreements --accept-source-agreements'
                                label = f"install {pkg_id}"
                            else:
                                name = payload.get("name", "")
                                wg_action = f'uninstall --name "{name}" --silent --accept-source-agreements --purge'
                                label = f"uninstall {name}"
                            print(f"[winget] {label} -- exe={winget_exe}", flush=True)
                            out_path = r"C:\Windows\Temp\rmm_winget_out.txt"
                            ps_cmd = (
                                f'& "{winget_exe}" {wg_action} 2>&1 '
                                f'| Tee-Object -FilePath "{out_path}"'
                            )

                            import re as _re
                            _ansi_re = _re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b[\[\]()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><~]')
                            # Block/box drawing chars winget uses for progress bars, plus replacement chars
                            _junk_chars = _re.compile(r'[\u2500-\u25FF\u2800-\u28FF\uFFFD\u0000-\u0008\u000B\u000C\u000E-\u001F]+')
                            # Spinner-only lines (after stripping)
                            _spinner_re = _re.compile(r'^[-\\|/\s]+$')

                            def _clean_winget(line: str) -> str:
                                """Strip ANSI codes, box-drawing chars, spinner frames from a winget output line."""
                                s = _ansi_re.sub('', line)
                                s = _junk_chars.sub('', s)
                                s = s.strip()
                                if not s or _spinner_re.match(s):
                                    return ''
                                # Drop lines that are only numbers/slashes/spaces (progress percentages)
                                if _re.match(r'^[\d\s\.%/KMGBkb,]+$', s):
                                    return ''
                                return s

                            try:
                                def _run_winget_ps():
                                    return subprocess.run(
                                        ["powershell.exe", "-NonInteractive", "-Command", ps_cmd],
                                        capture_output=True,
                                        env=env,
                                        timeout=300,
                                        creationflags=cf,
                                    )
                                # Remove stale output file
                                if os.path.exists(out_path):
                                    os.remove(out_path)
                                fut = loop.run_in_executor(None, _run_winget_ps)
                                sent_bytes = 0
                                tick = 0
                                while not fut.done():
                                    await asyncio.sleep(2)
                                    tick += 2
                                    # Stream new lines from output file
                                    if os.path.exists(out_path):
                                        try:
                                            with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                                                f.seek(sent_bytes)
                                                chunk = f.read()
                                            if chunk:
                                                for ln in chunk.splitlines():
                                                    clean = _clean_winget(ln)
                                                    if clean:
                                                        await ws.send(json.dumps({
                                                            "type": "install_chunk",
                                                            "session_id": session_id,
                                                            "text": clean,
                                                        }))
                                                sent_bytes += len(chunk.encode("utf-8"))
                                        except Exception:
                                            pass
                                    else:
                                        await ws.send(json.dumps({
                                            "type": "install_chunk",
                                            "session_id": session_id,
                                            "text": f"Working ({tick}s)...",
                                        }))
                                result = await fut
                                # Flush remaining lines
                                if os.path.exists(out_path):
                                    try:
                                        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                                            f.seek(sent_bytes)
                                            remainder = f.read()
                                        for ln in remainder.splitlines():
                                            clean = _clean_winget(ln)
                                            if clean:
                                                await ws.send(json.dumps({
                                                    "type": "install_chunk",
                                                    "session_id": session_id,
                                                    "text": clean,
                                                }))
                                        os.remove(out_path)
                                    except Exception:
                                        pass
                                # Also capture any stderr from PowerShell itself
                                ps_err = result.stderr.decode("utf-8", errors="replace").strip()
                                if ps_err:
                                    await ws.send(json.dumps({
                                        "type": "install_chunk", "session_id": session_id,
                                        "text": f"[ps stderr] {ps_err[:500]}",
                                    }))
                                print(f"[winget] {label} exit={result.returncode}", flush=True)
                                await ws.send(json.dumps({
                                    "type": "install_done", "session_id": session_id,
                                    "exit_code": result.returncode,
                                    "success": result.returncode == 0,
                                }))
                            except Exception as e:
                                print(f"[winget] {label} error: {e}", flush=True)
                                await ws.send(json.dumps({
                                    "type": "install_done", "session_id": session_id,
                                    "exit_code": -1, "success": False, "output": str(e),
                                }))
                            continue

                        if msg_type == "msi_install":
                            path      = payload.get("path", "")
                            extra_args = payload.get("args", "").strip()
                            try:
                                cf = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                                if path.lower().endswith(".msi"):
                                    log_path = r"C:\Windows\Temp\rmm_install.log"
                                    cmd = ["msiexec", "/i", path, "/quiet", "/norestart",
                                           "/l*v", log_path]
                                else:
                                    args_list = extra_args.split() if extra_args else ["/S"]
                                    cmd = [path] + args_list
                                proc = subprocess.run(
                                    cmd, capture_output=True, text=True,
                                    timeout=600, creationflags=cf,
                                )
                                output = (proc.stdout + proc.stderr).strip()
                                if path.lower().endswith(".msi"):
                                    try:
                                        if os.path.exists(log_path):
                                            with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                                                log_lines = lf.readlines()
                                            relevant = [l.rstrip() for l in log_lines
                                                        if any(k in l.lower() for k in
                                                               ("error", "return value", "installation", "success", "failed"))]
                                            if relevant:
                                                output += "\n--- Install log (filtered) ---\n" + "\n".join(relevant[-30:])
                                    except Exception:
                                        pass
                                await ws.send(json.dumps({
                                    "type": "install_done", "session_id": session_id,
                                    "exit_code": proc.returncode,
                                    "success": proc.returncode == 0,
                                    "output": output[-6000:],
                                }))
                            except Exception as e:
                                await ws.send(json.dumps({
                                    "type": "install_done", "session_id": session_id,
                                    "exit_code": -1, "success": False, "output": str(e),
                                }))
                            continue

                        if msg_type == "av_scan":
                            scan_type = payload.get("scan_type", "quick").lower()
                            ps_type      = "QuickScan" if scan_type == "quick" else "FullScan"
                            ps_in_prog   = "QuickScanInProgress" if scan_type == "quick" else "FullScanInProgress"
                            ps_end_field = "QuickScanEndTime"    if scan_type == "quick" else "FullScanEndTime"
                            cf = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

                            # Detect if a scan is already running before starting
                            try:
                                pre = await loop.run_in_executor(None, lambda: _ps_json(
                                    f"Get-MpComputerStatus | Select-Object {ps_in_prog} | ConvertTo-Json -Compress",
                                    timeout=10,
                                ))
                                if pre and pre.get(ps_in_prog):
                                    await ws.send(json.dumps({
                                        "type": "scan_done", "session_id": session_id,
                                        "success": False, "exit_code": -1,
                                        "output": "A scan is already in progress on this device.",
                                    }))
                                    continue
                            except Exception:
                                pass

                            scan_result: dict = {}
                            scan_finished = asyncio.Event()

                            def _run_av_scan():
                                try:
                                    proc = subprocess.run(
                                        ["powershell", "-NonInteractive", "-NoProfile",
                                         "-Command", f"Start-MpScan -ScanType {ps_type}"],
                                        capture_output=True, text=True,
                                        timeout=7200, creationflags=cf,
                                    )
                                    scan_result["exit_code"] = proc.returncode
                                    scan_result["output"]    = (proc.stdout + proc.stderr).strip()
                                except Exception as ex:
                                    scan_result["exit_code"] = -1
                                    scan_result["output"]    = str(ex)
                                finally:
                                    loop.call_soon_threadsafe(scan_finished.set)

                            loop.run_in_executor(None, _run_av_scan)
                            await ws.send(json.dumps({
                                "type": "scan_chunk", "session_id": session_id,
                                "text": f"{ps_type} initiated -- polling status every 5 s...\n",
                            }))

                            # PowerShell poll command -- format DateTime fields as strings
                            # so ConvertTo-Json produces readable values instead of /Date(ms)/
                            _mp_cmd = (
                                f"Get-MpComputerStatus | Select-Object {ps_in_prog},"
                                f"@{{N='{ps_end_field}';E={{if($_.{ps_end_field}){{$_.{ps_end_field}.ToString('yyyy-MM-dd HH:mm:ss')}}else{{''}}}}}},"
                                f"@{{N='SigDate';E={{if($_.AntivirusSignatureLastUpdated){{$_.AntivirusSignatureLastUpdated.ToString('yyyy-MM-dd')}}else{{''}}}}}} "
                                f"| ConvertTo-Json -Compress"
                            )

                            elapsed = 0
                            while not scan_finished.is_set():
                                await asyncio.sleep(5)
                                elapsed += 5
                                try:
                                    st = await loop.run_in_executor(
                                        None, lambda c=_mp_cmd: _ps_json(c, timeout=12)
                                    )
                                    if st:
                                        in_p  = st.get(ps_in_prog)
                                        end_t = st.get(ps_end_field) or ""
                                        sig   = st.get("SigDate") or ""
                                        in_p_str = "Yes" if in_p else "No"
                                        line  = f"[+{elapsed}s] Scanning: {in_p_str}"
                                        if end_t:
                                            line += f"  |  Last completed: {end_t}"
                                        if sig:
                                            line += f"  |  Definitions: {sig}"
                                        await ws.send(json.dumps({
                                            "type": "scan_chunk", "session_id": session_id,
                                            "text": line + "\n",
                                        }))
                                except Exception:
                                    pass

                            exit_code = scan_result.get("exit_code", -1)
                            output    = scan_result.get("output", "")
                            # "already in progress" can race past our pre-check -- treat as info
                            if exit_code != 0 and "already in progress" in output.lower():
                                await ws.send(json.dumps({
                                    "type": "scan_done", "session_id": session_id,
                                    "success": False, "exit_code": exit_code,
                                    "output": "A scan is already in progress on this device.",
                                }))
                            else:
                                # Query the actual scan end time from Defender
                                scan_end_time = ""
                                try:
                                    _end_cmd = (
                                        f"Get-MpComputerStatus | Select-Object "
                                        f"@{{N='EndTime';E={{if($_.{ps_end_field}){{$_.{ps_end_field}.ToString('yyyy-MM-dd HH:mm:ss')}}else{{''}}}}}} "
                                        f"| ConvertTo-Json -Compress"
                                    )
                                    end_st = await loop.run_in_executor(
                                        None, lambda c=_end_cmd: _ps_json(c, timeout=10)
                                    )
                                    if end_st:
                                        scan_end_time = end_st.get("EndTime") or ""
                                except Exception:
                                    pass
                                await ws.send(json.dumps({
                                    "type": "scan_done", "session_id": session_id,
                                    "success": exit_code == 0, "exit_code": exit_code,
                                    "output": output[-3000:],
                                    "scan_end_time": scan_end_time,
                                    "scan_type": scan_type,
                                }))
                            continue

                        # --- Windows Backup ---
                        if msg_type == "backup_run":
                            if platform.system() != "Windows":
                                await ws.send(json.dumps({
                                    "type": "backup_result", "ok": False,
                                    "error": "Windows backup only supported on Windows agents",
                                }))
                            else:
                                asyncio.create_task(_backup_task(
                                    ws, tracker_url, agent_id, token,
                                    payload.get("job_type", "incremental"),
                                    payload.get("triggered_by", "manual"),
                                ))
                            continue

                        await ws.send(json.dumps({"type": "error", "session_id": session_id, "error": f"Unknown: {msg_type}"}))

                finally:
                    telem_task.cancel()
                    rustdesk_task.cancel()
                    if eagle_task and not eagle_task.done():
                        eagle_task.cancel()

        except Exception as e:
            print(f"[agent] Disconnected: {e} -- retrying in 5s", flush=True)
            for sid, s in list(shells.items()):
                await s.stop()
            shells.clear()
            for ev in shell_stop_events.values():
                ev.set()
            shell_stop_events.clear()
            for t in shell_tasks.values():
                t.cancel()
            shell_tasks.clear()
            await asyncio.sleep(5)
            # SSL cert mismatch means the LAN host is TCP-reachable but the TLS cert
            # doesn't cover its hostname (machine is off-LAN with corp DNS still routing
            # the hostname to the internal server's IP). Force Cloudflare -- don't re-probe,
            # because the TCP probe would keep succeeding and we'd loop forever.
            err_str = str(e)
            if 'CERTIFICATE_VERIFY_FAILED' in err_str or 'Hostname mismatch' in err_str:
                print("[agent] SSL cert error on LAN endpoint -- forcing Cloudflare fallback", flush=True)
                tracker_url, gateway = fallback_tracker, fallback_gateway
            else:
                # Re-resolve endpoints every reconnect: if LAN came back up, prefer it;
                # if LAN went away, switch to Cloudflare automatically.
                tracker_url, gateway = _resolve_urls(fallback_tracker, fallback_gateway)
            ws_url = f"{gateway}/ws/agent/{agent_id}?token={token}"


if __name__ == "__main__":
    asyncio.run(main())
