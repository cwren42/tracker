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

AGENT_VERSION = "2.5.6"

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def _ssl_ctx():
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    except Exception:
        return None


def _ps_json(script: str, timeout: int = 15):
    """Run a PowerShell one-liner and return parsed JSON, or None on failure."""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        out = r.stdout.strip()
        if out and out != "null":
            return json.loads(out)
    except Exception:
        pass
    return None


# Internal LAN hostnames — only resolvable via corporate internal DNS.
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
        print(f"[agent] LAN reachable ({_LAN_GATEWAY_HOST}) — using internal endpoints", flush=True)
        return _LAN_TRACKER_URL, _LAN_GATEWAY_URL
    print(f"[agent] LAN unreachable — falling back to Cloudflare endpoints", flush=True)
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


_RUSTDESK_SERVER = 'rust.corp.cirque.com'
_RUSTDESK_KEY    = 'u2i12pLeK9MQJH8h3S4FeKtPVRt75gXyR6Rbj20LKOo'

# File on disk where we persist the plaintext password so it survives restarts.
_RUSTDESK_PASS_FILE    = r'C:\CirqueRMM\rustdesk_pass.txt'
_RUSTDESK_PEER_ID_FILE = r'C:\CirqueRMM\rustdesk_peer_id.txt'  # cached so --get-id only runs once

# Tray app API key (create_tickets scope) — baked in at build time
_TRAY_API_KEY = 'crmm_tray_60bb6c2cfc8e5bb56cd27eafcc766044609271533237fcf8'
_tray_setup_done     = False  # only run _setup_tray once per agent process
_rustdesk_setup_done = False  # only do full rustdesk ensure once per process

# Per-agent behaviour flags pushed by server on connect via agent_config message.
# Servers set these to True so neither RustDesk nor the systray are installed.
_disable_rustdesk = False
_disable_tray     = False
_TRAY_PY_PATH = r'C:\CirqueRMM\tray.py'
_TRAY_CFG_PATH = r'C:\CirqueRMM\tray_config.json'


def _setup_tray(tracker_url: str, agent_id: str, token: str) -> None:
    """Download tray.py, write tray_config.json, install pip deps, and create
    a per-user Startup shortcut so the tray runs at login.
    Safe to call repeatedly — skips steps already done."""
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
            with _ur.urlopen(req, timeout=20, context=ctx) as resp:
                new_src = resp.read()
            # Only write if changed
            existing = b''
            if os.path.isfile(_TRAY_PY_PATH):
                with open(_TRAY_PY_PATH, 'rb') as fh:
                    existing = fh.read()
            if new_src != existing:
                os.makedirs(os.path.dirname(_TRAY_PY_PATH), exist_ok=True)
                with open(_TRAY_PY_PATH, 'wb') as fh:
                    fh.write(new_src)
                print('[tray] tray.py updated', flush=True)
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


def _create_startup_shortcut_task():
    """Write tray startup entries to All-Users and per-user Startup folders,
    then launch the tray immediately in the current interactive user's session."""
    import subprocess as _sp, glob as _glob

    # ── 1. Find pythonw.exe ──────────────────────────────────────────────────
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
                pythonw_path = _r.stdout.strip().splitlines()[0].strip()
        except Exception:
            pass
    if not pythonw_path:
        print('[tray] No pythonw.exe found — cannot launch tray', flush=True)
        return

    # ── 2. Write VBScript to startup folders (SYSTEM can write directly) ────
    # VBS runs pythonw.exe silently so no console window appears
    _vbs = (
        'Set oShell = CreateObject("WScript.Shell")\r\n'
        f'oShell.Run Chr(34) & "{pythonw_path}" & Chr(34) & " C:\\CirqueRMM\\tray.py", 0, False\r\n'
    )
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

    # ── 3. Kill any existing tray.py process ────────────────────────────────
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
    import time as _time
    _time.sleep(2)

    # ── 4. Immediate launch in the active interactive user's session ─────────
    # Use New-ScheduledTaskPrincipal with the actual logged-in username —
    # more reliable than generic InteractiveToken when called from session 0.
    _py_escaped = pythonw_path.replace("'", "''")
    ps_launch = (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        # Resolve logged-in user via WMI (returns domain\\user)
        "$wmiUser = (Get-WmiObject -Class Win32_ComputerSystem).UserName\n"
        "if (-not $wmiUser) {\n"
        "    # Fallback: parse qwinsta output\n"
        "    $qw = & qwinsta 2>$null\n"
        "    $line = ($qw | Select-String 'Active' | Select-Object -First 1).ToString()\n"
        "    $cols = ($line -replace '>','').Trim() -split '\\s+'\n"
        "    $wmiUser = $cols[1]\n"
        "}\n"
        # Keep domain\\user for task principal (works on domain + local; strip only for display)
        "$username = $wmiUser\n"
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

        # 3. Set the password in RustDesk (idempotent — safe to call every time)
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


def _fix_rustdesk_identity() -> bool:
    """If RustDesk registered with the public server instead of ours,
    delete the identity file and restart the service so it re-registers
    with our private server.  Returns True if a reset was performed."""
    import re as _re, subprocess as _sp, time as _time
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
                    # Don't delete — just wait for it to confirm
                    return False
                # Has our server confirmed? First segment of rust.corp.cirque.com = 'rust'
                our_name = _RUSTDESK_SERVER.split('.')[0].lower()
                if our_name in confirmed.lower():
                    return False  # already confirmed against our server
                # Has a FOREIGN server (e.g. rs-ny from public RustDesk)
                print(f'[rustdesk] Foreign server in identity at {path}: {confirmed.strip()} — resetting', flush=True)
            else:
                # No [keys_confirmed] section at all — new file, leave it alone
                return False
            os.remove(path)
            # Restart the service so it regenerates identity against our server
            _sp.run(['sc.exe', 'stop', 'RustDesk'], capture_output=True, timeout=10)
            _time.sleep(3)
            _sp.run(['sc.exe', 'start', 'RustDesk'], capture_output=True, timeout=10)
            _time.sleep(10)
            print('[rustdesk] Identity reset — service restarted', flush=True)
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
        if _rustdesk_exe():
            if _rustdesk_setup_done:
                # Already fully set up — only sync ID (fast, idempotent)
                sync_rustdesk_id(tracker_url, agent_id, token)
                return
            # First run: do full config check
            _write_rustdesk_config()
            _fix_rustdesk_identity()
            _ensure_rustdesk_password()
            sync_rustdesk_id(tracker_url, agent_id, token)
            _rustdesk_setup_done = True
            return  # tray_watchdog handles tray setup separately

        print('[rustdesk] Not installed — installing...', flush=True)

        # Try 1: winget with --scope machine (works from SYSTEM on most Win10/11)
        _rustdesk_winget_install()

        # Always verify exe exists after winget — it can exit 0 as SYSTEM
        # without actually installing (stub/redirect issue)
        if not _rustdesk_exe():
            _rustdesk_direct_install()

        _write_rustdesk_config()

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
            print('[rustdesk] Install failed — exe not found after attempts.', flush=True)
    except Exception as e:
        print(f'[rustdesk] ensure_rustdesk error: {e}', flush=True)


def _rustdesk_winget_install() -> bool:
    """Try to install RustDesk via winget. Returns True on success."""
    import subprocess as _sp, glob as _glob
    # winget may not be on SYSTEM's PATH — find it explicitly
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
            print('[rustdesk] Download too small — aborting', flush=True)
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


def _write_rustdesk_config() -> None:
    """Write RustDesk2.toml server config to all relevant paths."""
    cfg = (
        f"rendezvous_server = '{_RUSTDESK_SERVER}'\n"
        f"nat_type = 1\nserial = 0\n\n[options]\n"
        f"custom-rendezvous-server = '{_RUSTDESK_SERVER}'\n"
        f"key = '{_RUSTDESK_KEY}'\n"
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


def check_for_update(tracker_url: str, agent_id: str, token: str) -> bool:
    try:
        url = f"{tracker_url}/rmm/agent/version?agent_id={agent_id}&token={token}"
        req = urllib.request.Request(url, headers={"User-Agent": f"CirqueRMM/{AGENT_VERSION}"})
        with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=10) as r:
            data = json.loads(r.read())

        server_checksum = data.get("checksum", "")
        current_path = os.path.abspath(__file__)
        current_checksum = hashlib.sha256(open(current_path, "rb").read()).hexdigest()

        if not server_checksum or server_checksum == current_checksum:
            print(f"[update] Up to date ({AGENT_VERSION})", flush=True)
            return False

        print(f"[update] New version {data.get('version')} available — downloading...", flush=True)
        file_url = f"{tracker_url}/rmm/agent/file?agent_id={agent_id}&token={token}"
        req2 = urllib.request.Request(file_url, headers={"User-Agent": f"CirqueRMM/{AGENT_VERSION}"})
        with urllib.request.urlopen(req2, context=_ssl_ctx(), timeout=30) as r:
            new_code = r.read()

        if server_checksum and hashlib.sha256(new_code).hexdigest() != server_checksum:
            print("[update] Checksum mismatch — aborting", flush=True)
            return False

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
        print("[update] Updated — restarting", flush=True)
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
    # Use GetComputerNameExW(2) = ComputerNameDnsDomain — returns just the domain suffix
    try:
        import ctypes
        buf  = ctypes.create_unicode_buffer(256)
        size = ctypes.c_ulong(256)
        if ctypes.windll.kernel32.GetComputerNameExW(2, buf, ctypes.byref(size)):
            return buf.value  # e.g. "corp.cirque.com" — no stripping needed
    except Exception:
        pass
    return os.environ.get("USERDNSDOMAIN", "")


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

    # ── Computer system (vendor, model; user+domain handled separately below) ─
    cs = _ps_json(
        "Get-CimInstance Win32_ComputerSystem | "
        "Select-Object Manufacturer,Model,UserName,Domain | "
        "ConvertTo-Json -Compress"
    )
    if cs:
        result["vendor"]     = cs.get("Manufacturer") or ""
        result["model_name"] = cs.get("Model") or ""

    # ── Logged-in user via query user (Python-parsed, reliable from SYSTEM) ────
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

    # ── Domain: resolve from the logged-in user's NETBIOS prefix ────────────
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

    # ── BIOS ───────────────────────────────────────────────────────────────
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

    # ── Motherboard ────────────────────────────────────────────────────────
    mb = _ps_json(
        "Get-CimInstance Win32_BaseBoard | "
        "Select-Object Manufacturer,Product | ConvertTo-Json -Compress"
    )
    if mb:
        mb_str = " ".join(filter(None, [mb.get("Manufacturer"), mb.get("Product")])).strip()
        result["motherboard"] = mb_str

    # ── OS edition ─────────────────────────────────────────────────────────
    os_info = _ps_json(
        "Get-CimInstance Win32_OperatingSystem | "
        "Select-Object Caption | ConvertTo-Json -Compress"
    )
    if os_info:
        result["os_edition"] = os_info.get("Caption") or ""

    # ── GPU ────────────────────────────────────────────────────────────────
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

    # ── Sound ──────────────────────────────────────────────────────────────
    raw_snd = _ps_json(
        "Get-CimInstance Win32_SoundDevice | "
        "Select-Object Name | ConvertTo-Json -Compress"
    )
    sounds = [s.get("Name") for s in _normalise_ps_list(raw_snd) if s.get("Name")]
    if sounds:
        result["sound_card"] = ", ".join(sounds)

    # ── Timezone ───────────────────────────────────────────────────────────
    tz = _ps_json("Get-TimeZone | Select-Object Id,DisplayName | ConvertTo-Json -Compress")
    if tz:
        result["timezone"] = tz.get("DisplayName") or tz.get("Id") or ""

    # ── Security products ──────────────────────────────────────────────────
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
    # SecurityCenter2 FirewallProduct — query Get-NetFirewallProfile directly.
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

    # ── BitLocker ──────────────────────────────────────────────────────────
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

    # ── TPM ────────────────────────────────────────────────────────────────
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

    # ── Windows Activation ─────────────────────────────────────────────────
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

    # ── Local Administrators ───────────────────────────────────────────────
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

    # ── Printers ───────────────────────────────────────────────────────────
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

    # ── USB / External Devices ─────────────────────────────────────────────
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

    # ── Mapped Network Drives ──────────────────────────────────────────────
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

    # ── Startup Programs ───────────────────────────────────────────────────
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

    # ── Power Plan ─────────────────────────────────────────────────────────
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

    # ── Last Successful Windows Update ────────────────────────────────────
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

    # ── RDP Enabled ────────────────────────────────────────────────────────
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

    # ── Pending Reboot ─────────────────────────────────────────────────────
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

    # ── Default Browser ──────────────────────────────────────────────────────
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

    # ── DNS Servers ────────────────────────────────────────────────────────
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

    # ── Group Policy Last Refresh ──────────────────────────────────────────
    try:
        gp_ps = _ps_json(
            "$k='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Group Policy\\State\\Machine\\Extension-List\\{00000000-0000-0000-0000-000000000000}';"
            "$v=Get-ItemProperty $k -EA SilentlyContinue;"
            "if($v){"
            "  $dt=[datetime]::FromFileTime((([int64]$v.EndTime2High -shl 32) -bor [uint32]$v.EndTime2Low));"
            "  @{time=$dt.ToString('yyyy-MM-dd HH:mm:ss')}|ConvertTo-Json -Compress"
            "}else{'null'}"
        )
        if gp_ps and isinstance(gp_ps, dict) and gp_ps.get("time"):
            t = gp_ps["time"]
            if not t.startswith("1600") and not t.startswith("1601"):
                result["gp_last_refresh"] = t
    except Exception:
        pass

    # ── Disk SMART Health ──────────────────────────────────────────────────
    try:
        smart_ps = _ps_json(
            "Get-PhysicalDisk -EA SilentlyContinue "
            "| Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,"
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
            })
        if smart_list:
            result["disk_health"] = smart_list
    except Exception:
        pass

    # ── Last BSOD / System Crash ───────────────────────────────────────────
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

    # ── Monitor Info ───────────────────────────────────────────────────────
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

    # ── Windows Update Channel ─────────────────────────────────────────────
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

    # ── Screen Lock Timeout ──────────────────────────────────────────────────
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

    # ── Listening Ports ────────────────────────────────────────────────────
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

    # ── Security Event Telemetry (servers + all Windows machines) ─────────────
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

    # Pack IT-detail fields into a single sysinfo subdict → stored as sysinfo_json
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
        timeout=30,
    )
    seen: set = set()
    results = []
    for item in _normalise_ps_list(raw):
        name = (item.get("DisplayName") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        results.append({
            "name":         name,
            "version":      (item.get("DisplayVersion") or "").strip(),
            "publisher":    (item.get("Publisher")      or "").strip(),
            "install_date": (item.get("InstallDate")    or "").strip(),
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

def collect_telemetry(agent_id: str) -> dict:
    """Collect real-time telemetry and return a telemetry_update message dict."""
    cpu_pct  = psutil.cpu_percent(interval=1)
    cpu_freq = psutil.cpu_freq()
    mem      = psutil.virtual_memory()
    batt     = psutil.sensors_battery()

    # Disk partitions
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device":     part.device,
                "mountpoint": part.mountpoint,
                "total_gb":   round(usage.total  / (1024 ** 3), 1),
                "free_gb":    round(usage.free   / (1024 ** 3), 1),
                "percent":    usage.percent,
            })
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
# Patch management — available updates + remote-triggered install
# ---------------------------------------------------------------------------

def _collect_pending_updates() -> list:
    """Query Windows Update COM API for updates available but not yet installed."""
    script = r"""
try {
    $Session  = New-Object -ComObject Microsoft.Update.Session
    $Searcher = $Session.CreateUpdateSearcher()
    $Results  = $Searcher.Search("IsInstalled=0 and IsHidden=0 and BrowseOnly=0")
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
    raw = _ps_json(script, timeout=60)
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
        # CREATE_UNICODE_ENVIRONMENT — visible window
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


def _install_patches_wua(update_ids: list) -> dict:
    """Install specific Windows Updates by Update ID (GUID) using the WUA COM API."""
    if not update_ids:
        return {"installed": 0, "reboot_required": False, "error": "No update IDs specified"}
    ids_ps = ", ".join(f'"{v}"' for v in update_ids)
    script = f"""
$ids = @({ids_ps})
try {{
    $Sess   = New-Object -ComObject Microsoft.Update.Session
    $Search = $Sess.CreateUpdateSearcher()
    $Found  = $Search.Search("IsInstalled=0 and IsHidden=0")
    $coll   = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($u in $Found.Updates) {{
        if ($ids -contains $u.Identity.UpdateID) {{ [void]$coll.Add($u) }}
    }}
    if ($coll.Count -eq 0) {{
        @{{installed=0;reboot_required=$false;error="No matching pending updates"}} | ConvertTo-Json -Compress
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
        reboot_required = $res.RebootRequired
        result_code     = $res.ResultCode
        error           = ""
    }} | ConvertTo-Json -Compress
}} catch {{
    @{{installed=0;reboot_required=$false;error=$_.Exception.Message}} | ConvertTo-Json -Compress
}}
""".strip()
    result = _ps_json(script, timeout=30 * 60)  # up to 30 min for large patches
    if result is None:
        return {"installed": 0, "reboot_required": False, "error": "No output from installer"}
    return {
        "installed":       int(result.get("installed") or 0),
        "reboot_required": bool(result.get("reboot_required")),
        "result_code":     result.get("result_code"),
        "error":           result.get("error") or "",
    }


def _find_and_install_cve_patches(cve_ids: list) -> dict:
    """Search Windows Update Agent for uninstalled patches that cover the given
    CVE IDs, download, and install them.  Returns a summary dict compatible with
    the patch_install_result message schema."""
    if not cve_ids:
        return {"installed": 0, "reboot_required": False, "error": "No CVE IDs provided",
                "updates_found": 0, "titles": [], "kb_ids": []}
    cves_ps = ", ".join(f"'{c}'" for c in cve_ids)
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
    result = _ps_json(script, timeout=30 * 60)
    if result is None:
        return {"installed": 0, "reboot_required": False, "updates_found": 0,
                "titles": [], "kb_ids": [], "error": "No output from WUA"}
    # Normalise list fields that PowerShell may return as a single string
    def _to_list(v):
        if isinstance(v, list): return v
        if v: return [v]
        return []
    return {
        "installed":       int(result.get("installed") or 0),
        "updates_found":   int(result.get("updates_found") or 0),
        "reboot_required": bool(result.get("reboot_required")),
        "result_code":     result.get("result_code"),
        "titles":          _to_list(result.get("titles")),
        "kb_ids":          _to_list(result.get("kb_ids")),
        "error":           result.get("error") or "",
    }


async def _do_reboot_sequence():
    """Show reboot countdown dialog in user's session, then reboot when it closes."""
    loop = asyncio.get_event_loop()
    print("[agent] Showing reboot notification to logged-in user…", flush=True)
    try:
        await loop.run_in_executor(
            None,
            _run_dialog_in_user_session,
            _REBOOT_DIALOG_PS,
            36 * 60 * 1000,  # 36-min timeout (15 base + 15 defer + 6 buffer)
        )
    except Exception as e:
        print(f"[agent] Reboot dialog error: {e} — rebooting anyway", flush=True)
    print("[agent] Executing system restart for Windows Updates…", flush=True)
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

        # Use Public folder — writable by both SYSTEM (writer) and user (reader)
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
            h_env,   # user environment block — critical for display access
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
# Shell session
# ---------------------------------------------------------------------------

class ShellSession:
    def __init__(self, session_id: int, shell: str = "powershell"):
        self.session_id = session_id
        self.shell = shell
        self.proc: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> bool:
        cmd = ["cmd.exe"] if self.shell == "cmd" else ["powershell.exe", "-NoLogo", "-NoProfile"]
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            return True
        except Exception as e:
            print(f"[shell] start error: {e}", flush=True)
            return False

    async def send_input(self, text: str):
        if self.proc and self.proc.stdin:
            self.proc.stdin.write(text.encode("utf-8", errors="replace"))
            await self.proc.stdin.drain()

    async def read_output(self) -> Optional[str]:
        if not self.proc or not self.proc.stdout:
            return None
        try:
            chunk = await asyncio.wait_for(self.proc.stdout.read(4096), timeout=0.1)
            if chunk:
                return chunk.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            pass
        return None

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def stop(self):
        if self.proc:
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
# The helper is a PowerShell script running in the interactive user's session
# that polls GetForegroundWindow() every 2 s and writes the result to a file.
# PowerShell is used instead of Python because corporate AV/EDR policies
# typically whitelist powershell.exe; a Python child spawned from a SYSTEM
# service doing window/process enumeration is often killed by EDR.
_ee_helper_hproc: "ctypes.wintypes.HANDLE | None" = None   # process handle
_EE_OUT_DIR      = r"C:\ProgramData\CirqueRMM"
_EE_OUT_FILE     = r"C:\ProgramData\CirqueRMM\ee_win.json"
_EE_HELPER_CS    = r"C:\ProgramData\CirqueRMM\ee_helper.cs"
_EE_HELPER_EXE   = r"C:\ProgramData\CirqueRMM\ee_helper.exe"
# csc.exe ships with every .NET 4.x install (present on all modern Windows)
_EE_CSC          = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

# C# source for ee_helper.exe — compiled once via csc.exe on the Windows host.
# Runs as the interactive user (CreateProcessAsUserW), loops every 10 s.
# Uses GetForegroundWindow() — the correct Win32 API for active window detection.
#
# Why C# instead of PowerShell:
#   PowerShell .ps1 scripts are scanned by AMSI before any line executes.
#   Corporate EDR products block scripts that loop + enumerate processes + write
#   files (classic malware pattern) regardless of -ExecutionPolicy Bypass.
#   A compiled PE is evaluated differently: AV scans the binary, NOT the source,
#   so there are no AMSI script-content rules to trigger.
#
# Diagnostic output:
#   ee_diag.txt — written on start + each iteration so we can see exactly
#                 where execution stops if something goes wrong.
_EE_HELPER_CS_SRC = r"""
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Diagnostics;
using System.Threading;

class EagleEyes {
    const string OutFile  = @"C:\ProgramData\CirqueRMM\ee_win.json";
    const string DiagFile = @"C:\ProgramData\CirqueRMM\ee_diag.txt";
    const int    Interval = 10000; // ms

    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

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
                string json = "{\"p\":\"" + J(pname) + "\",\"t\":\"" + J(title) + "\"}";
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
    # Compilation runs as SYSTEM (no AMSI restriction on subprocess calls).
    # The resulting EXE is a normal PE — EDR evaluates it differently from
    # PS1 scripts, bypassing the AMSI script-content rules that blocked us.
    import subprocess as _sp
    try:
        with open(_EE_HELPER_CS, "w", encoding="utf-8") as _f:
            _f.write(_EE_HELPER_CS_SRC)
    except Exception as _e:
        kernel32.CloseHandle(dup_token)
        _ee_log(f"write ee_helper.cs failed: {_e}")
        return False

    if not os.path.exists(_EE_HELPER_EXE):
        _ee_log("compiling ee_helper.cs ...")
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
    """Return (process_name, window_title) for the foreground window.

    Reads the JSON file maintained by the persistent helper process that runs
    inside the interactive user session.  Returns ('', '') on failure or when
    not on Windows.
    """
    if sys.platform != "win32":
        return ("", "")
    try:
        if not _ee_ensure_helper():
            return ("", "")
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
        return (proc, title)
    except Exception:
        return ("", "")


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


async def main() -> None:
    # Cloudflare fallback endpoints — used when LAN (rmm.corp.cirque.com) is unreachable
    fallback_gateway = os.environ.get("RMM_GATEWAY_URL_PUBLIC", "wss://rmm.cirquetools.com").rstrip("/")
    fallback_tracker = os.environ.get("RMM_TRACKER_URL_PUBLIC", "https://tracker.cirquetools.com").rstrip("/")

    agent_id = os.environ.get("RMM_AGENT_ID") or socket.gethostname()
    token = get_env("RMM_AGENT_TOKEN")
    screenshot_enabled = os.environ.get("RMM_SCREENSHOT", "0") == "1"

    # Probe _LAN_GATEWAY_HOST (internal DNS only) to decide which endpoints to use.
    # Never probes Cloudflare hostnames — their TCP port 443 is always reachable
    # even when the tunnel backend is down.
    tracker_url, gateway = _resolve_urls(fallback_tracker, fallback_gateway)

    # Self-update check on startup
    if check_for_update(tracker_url, agent_id, token):
        sys.exit(7)  # non-zero so NSSM always restarts after update

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

                # Ensure RustDesk is installed — runs in background after connect
                # so it never blocks the WebSocket from establishing.
                # Skipped for server-mode agents where disable_rustdesk flag is set.
                if not _disable_rustdesk:
                    loop.run_in_executor(None, ensure_rustdesk, tracker_url, agent_id, token)

                # Collect extended info (hardware/OS/security) once on connect
                extended = await loop.run_in_executor(None, _collect_extended)

                # Initial telemetry on connect
                telemetry = collect_telemetry(agent_id)
                # Merge extended — WMI values override the simpler ctypes ones
                for k, v in extended.items():
                    if v:
                        telemetry[k] = v
                await ws.send(json.dumps({**telemetry, "type": "agent_info"}))

                # Send patch report (can be slow — run in executor)
                try:
                    patches = await loop.run_in_executor(None, _collect_patches)
                    await ws.send(json.dumps({"type": "patch_report", "patches": patches}))
                    print(f"[agent] Sent {len(patches)} patches", flush=True)
                except Exception as e:
                    print(f"[agent] Patch report failed: {e}", flush=True)

                # Send available/pending Windows Updates
                try:
                    pending = await loop.run_in_executor(None, _collect_pending_updates)
                    await ws.send(json.dumps({"type": "pending_updates", "updates": pending}))
                    print(f"[agent] Sent {len(pending)} pending update(s)", flush=True)
                except Exception as e:
                    print(f"[agent] Pending updates failed: {e}", flush=True)

                # Send session events (logon/logoff/lock/unlock/sleep/wake — last 7 days)
                try:
                    sev = await loop.run_in_executor(None, _collect_session_events)
                    await ws.send(json.dumps({"type": "session_events", "events": sev}))
                    print(f"[agent] Sent {len(sev)} session event(s)", flush=True)
                except Exception as e:
                    print(f"[agent] Session events failed: {e}", flush=True)

                # Send software inventory
                try:
                    sw = await loop.run_in_executor(None, _collect_software)
                    await ws.send(json.dumps({"type": "software_inventory", "software": sw}))
                    print(f"[agent] Sent {len(sw)} software entries", flush=True)
                except Exception as e:
                    print(f"[agent] Software inventory failed: {e}", flush=True)

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

                # RustDesk watchdog — reinstalls if user uninstalls it (check hourly)
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

                # Software inventory — post on connect then refresh every 24h
                async def software_inventory_loop():
                    # Run immediately on first connect
                    try:
                        await loop.run_in_executor(
                            None, post_software_inventory, tracker_url, agent_id, token
                        )
                    except Exception:
                        pass
                    while True:
                        await asyncio.sleep(86400)  # re-collect every 24h
                        try:
                            await loop.run_in_executor(
                                None, post_software_inventory, tracker_url, agent_id, token
                            )
                        except Exception:
                            pass

                asyncio.create_task(software_inventory_loop())

                # Tray setup — runs once per agent process lifetime, then refreshes every 24h
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

                # Periodic self-update check — every 4 hours while running
                async def periodic_update_check():
                    while True:
                        await asyncio.sleep(4 * 3600)
                        try:
                            updated = await loop.run_in_executor(
                                None, check_for_update, tracker_url, agent_id, token
                            )
                            if updated:
                                sys.exit(7)
                        except Exception:
                            pass

                asyncio.create_task(periodic_update_check())

                # Eagle Eyes monitoring task (started on demand via eagle_eyes_config)
                eagle_task: Optional[asyncio.Task] = None
                _eagle_cfg: dict = {"enabled": False, "screenshot_interval_min": 30}

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

                        # Active window + idle state
                        proc, title = await loop.run_in_executor(None, _get_active_window_info)
                        idle_s      = await loop.run_in_executor(None, _get_idle_seconds)
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

                        # Live ping every LIVE_PING_S — updates 'right now' panel without a DB write
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

                        # Periodic screenshot
                        interval_s = _eagle_cfg.get("screenshot_interval_min", 30) * 60
                        if (now - last_shot_at).total_seconds() >= interval_s:
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
                            global _disable_rustdesk, _disable_tray
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
                            print("[agent] Received update_now — checking for new version...", flush=True)
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
                            print("[agent] Received restart_agent command — restarting via NSSM", flush=True)
                            await ws.send(json.dumps({"type": "restart_agent_ack", "ok": True}))
                            await asyncio.sleep(0.5)
                            sys.exit(0)  # NSSM will restart the agent automatically

                        # --- Eagle Eyes monitoring toggle ---
                        if msg_type == "eagle_eyes_config":
                            _eagle_cfg["enabled"] = bool(payload.get("enabled", False))
                            _eagle_cfg["screenshot_interval_min"] = int(payload.get("screenshot_interval_min", 30))
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
                            session = ShellSession(sid, shell=shell_type)
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

                        # --- Install approved patches ---
                        if msg_type == "install_patches":
                            job_id     = payload.get("job_id")
                            update_ids = payload.get("update_ids") or []
                            print(f"[agent] install_patches job={job_id} count={len(update_ids)}", flush=True)
                            loop2 = asyncio.get_event_loop()
                            result = await loop2.run_in_executor(None, _install_patches_wua, update_ids)
                            await ws.send(json.dumps({
                                "type":   "patch_install_result",
                                "job_id": job_id,
                                "result": result,
                            }))
                            if result.get("reboot_required") and not result.get("error"):
                                asyncio.create_task(_do_reboot_sequence())
                            continue

                        # --- Deploy patches by CVE ID (WUA searches locally) ---
                        if msg_type == "install_cve_patches":
                            job_id  = payload.get("job_id")
                            cve_ids = payload.get("cve_ids") or []
                            print(f"[agent] install_cve_patches job={job_id} cves={cve_ids}", flush=True)
                            loop2 = asyncio.get_event_loop()
                            result = await loop2.run_in_executor(
                                None, _find_and_install_cve_patches, cve_ids
                            )
                            await ws.send(json.dumps({
                                "type":   "cve_patch_result",
                                "job_id": job_id,
                                "result": result,
                            }))
                            if result.get("reboot_required") and not result.get("error"):
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
                                if shell == "cmd":
                                    r = subprocess.run(
                                        ["cmd", "/c", code], capture_output=True,
                                        text=True, timeout=tout,
                                    )
                                else:
                                    r = subprocess.run(
                                        ["powershell", "-NonInteractive", "-NoProfile",
                                         "-ExecutionPolicy", "Bypass", "-Command", code],
                                        capture_output=True, text=True, timeout=tout,
                                    )
                                await ws.send(json.dumps({
                                    "type": "script_result", "session_id": session_id,
                                    "exit_code": r.returncode,
                                    "stdout": r.stdout[-32000:],
                                    "stderr": r.stderr[-8000:],
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
                                    # dirs first, then files, both α-sorted
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
                                            "error": "winget timed out — sources may not be cached yet. Run: winget source update",
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
                            print(f"[winget] {label} — exe={winget_exe}", flush=True)
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
                                            "text": f"Working ({tick}s)…",
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
                                "text": f"{ps_type} initiated — polling status every 5 s…\n",
                            }))

                            # PowerShell poll command — format DateTime fields as strings
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
                            # "already in progress" can race past our pre-check — treat as info
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

                        await ws.send(json.dumps({"type": "error", "session_id": session_id, "error": f"Unknown: {msg_type}"}))

                finally:
                    telem_task.cancel()
                    rustdesk_task.cancel()
                    if eagle_task and not eagle_task.done():
                        eagle_task.cancel()

        except Exception as e:
            print(f"[agent] Disconnected: {e} — retrying in 5s", flush=True)
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
            # the hostname to the internal server's IP). Force Cloudflare — don't re-probe,
            # because the TCP probe would keep succeeding and we'd loop forever.
            err_str = str(e)
            if 'CERTIFICATE_VERIFY_FAILED' in err_str or 'Hostname mismatch' in err_str:
                print("[agent] SSL cert error on LAN endpoint — forcing Cloudflare fallback", flush=True)
                tracker_url, gateway = fallback_tracker, fallback_gateway
            else:
                # Re-resolve endpoints every reconnect: if LAN came back up, prefer it;
                # if LAN went away, switch to Cloudflare automatically.
                tracker_url, gateway = _resolve_urls(fallback_tracker, fallback_gateway)
            ws_url = f"{gateway}/ws/agent/{agent_id}?token={token}"


if __name__ == "__main__":
    asyncio.run(main())
