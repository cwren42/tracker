#!/usr/bin/env python3
"""
Cirque RMM Agent Launcher
=========================
Lightweight wrapper that validates and runs agent_client.py with automatic
self-healing: if the main agent has a syntax error it restores the .old
backup or downloads a fresh copy from the server before launching.

NSSM should be configured to run THIS script instead of agent_client.py
so that update-induced syntax errors are automatically recovered.

Failsafe features:
  - Backup before overwrite: saves .bak before every update
  - Crash loop detection: if 3+ crashes in 10 min -> force re-download
  - HTTP heartbeat thread: polls /api/rmm/agent/heartbeat every 5 min,
    executes server-issued control actions (force_update, restart, etc.)
    and shell commands, completely independent of the WebSocket
  - Force-update flag: if C:\\CirqueRMM\\force_update exists, always
    re-downloads regardless of version match, then deletes the flag
"""
import ast
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.request

_AGENT_DIR  = os.path.dirname(os.path.abspath(__file__))
_AGENT_PY   = os.path.join(_AGENT_DIR, "agent_client.py")
_AGENT_OLD  = _AGENT_PY + ".old"
_AGENT_BAK  = _AGENT_PY + ".bak"
_VER_FILE   = os.path.join(_AGENT_DIR, "version.txt")
_CRASH_LOG  = os.path.join(_AGENT_DIR, "logs", "agent.log")
_FORCE_FLAG = os.path.join(_AGENT_DIR, "force_update")
# Crash loop: threshold + window (seconds)
_CRASH_MAX  = 3
_CRASH_WIN  = 600  # 10 minutes
_crash_times: list = []
_crash_lock = threading.Lock()


def _local_version() -> str:
    try:
        return open(_VER_FILE).read().strip()
    except OSError:
        return "0.0.0"


def _parse_version(v: str):
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0, 0, 0)


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def _is_valid(path: str) -> bool:
    """Return True if the Python file at path has no syntax errors."""
    try:
        with open(path, "rb") as f:
            ast.parse(f.read())
        return True
    except (SyntaxError, OSError):
        return False


def _check_for_update() -> bool:
    """Check server version; download agent_client.py if server has a newer version.

    Returns True if an update was downloaded and applied, False otherwise.
    """
    tracker_url = os.environ.get("RMM_TRACKER_URL",
                                 "https://tracker.corp.cirque.com").rstrip("/")
    agent_id    = os.environ.get("RMM_AGENT_ID", "")
    token       = os.environ.get("RMM_AGENT_TOKEN", "")

    if not agent_id or not token:
        return False

    local_ver = _local_version()
    try:
        ver_url = f"{tracker_url}/rmm/agent/version?agent_id={agent_id}&token={token}"
        req = urllib.request.Request(ver_url, headers={"User-Agent": "CirqueLauncher/1.0"})
        with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=10) as r:
            data = json.loads(r.read())
        server_ver    = data.get("version", "0.0.0")
        server_cksum  = data.get("checksum", "")

        if _parse_version(server_ver) <= _parse_version(local_ver):
            print(f"[launcher] Agent up-to-date (local={local_ver} server={server_ver})", flush=True)
            return False

        print(f"[launcher] Update available: {local_ver} -> {server_ver}. Downloading...", flush=True)

        file_url = f"{tracker_url}/rmm/agent/file?agent_id={agent_id}&token={token}"
        req2 = urllib.request.Request(file_url, headers={"User-Agent": "CirqueLauncher/1.0"})
        with urllib.request.urlopen(req2, context=_ssl_ctx(), timeout=30) as r:
            new_code = r.read()

        if server_cksum and hashlib.sha256(new_code).hexdigest() != server_cksum:
            print("[launcher] Checksum mismatch on update -- skipping", flush=True)
            return False

        tmp = _AGENT_PY + ".update"
        with open(tmp, "wb") as f:
            f.write(new_code)

        if not _is_valid(tmp):
            print("[launcher] Downloaded update has syntax errors -- skipping", flush=True)
            os.remove(tmp)
            return False

        # Backup current, apply update, write new version
        if os.path.exists(_AGENT_PY):
            shutil.copy2(_AGENT_PY, _AGENT_OLD)
            shutil.copy2(_AGENT_PY, _AGENT_BAK)  # extra .bak for crash loop restore
        shutil.move(tmp, _AGENT_PY)
        with open(_VER_FILE, "w") as vf:
            vf.write(server_ver)
        print(f"[launcher] Updated to v{server_ver}", flush=True)
        return True

    except Exception as e:
        print(f"[launcher] Version check failed: {e}", flush=True)
        return False


def _try_download_fresh() -> bool:
    """Download a fresh agent_client.py from the tracker server.

    Returns True if a valid copy was saved, False on any failure.
    """
    tracker_url = os.environ.get("RMM_TRACKER_URL",
                                 "https://tracker.corp.cirque.com").rstrip("/")
    fallback_url = os.environ.get("RMM_TRACKER_URL_PUBLIC",
                                  "https://tracker.cirquetools.com").rstrip("/")
    agent_id    = os.environ.get("RMM_AGENT_ID", "")
    token       = os.environ.get("RMM_AGENT_TOKEN", "")

    if not agent_id or not token:
        print("[launcher] RMM_AGENT_ID/TOKEN not set -- cannot download fresh copy", flush=True)
        return False

    for tracker in (tracker_url, fallback_url):
        try:
            # Fetch version manifest to get expected checksum
            ver_url = f"{tracker}/rmm/agent/version?agent_id={agent_id}&token={token}"
            req = urllib.request.Request(ver_url, headers={"User-Agent": "CirqueLauncher/1.0"})
            with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=15) as r:
                data = json.loads(r.read())
            server_cksum = data.get("checksum", "")

            # Download the file
            file_url = f"{tracker}/rmm/agent/file?agent_id={agent_id}&token={token}"
            req2 = urllib.request.Request(file_url, headers={"User-Agent": "CirqueLauncher/1.0"})
            with urllib.request.urlopen(req2, context=_ssl_ctx(), timeout=30) as r:
                new_code = r.read()

            # Verify checksum
            if server_cksum and hashlib.sha256(new_code).hexdigest() != server_cksum:
                print("[launcher] Checksum mismatch on downloaded agent -- aborting", flush=True)
                continue

            # Write and validate
            tmp = _AGENT_PY + ".dl"
            with open(tmp, "wb") as f:
                f.write(new_code)

            if not _is_valid(tmp):
                print("[launcher] Downloaded agent has syntax errors -- keeping current", flush=True)
                os.remove(tmp)
                continue

            # Atomically replace
            if os.path.exists(_AGENT_PY):
                shutil.copy2(_AGENT_PY, _AGENT_OLD)
                shutil.copy2(_AGENT_PY, _AGENT_BAK)
            shutil.move(tmp, _AGENT_PY)
            print(f"[launcher] Fresh agent downloaded from {tracker}", flush=True)
            return True

        except Exception as e:
            print(f"[launcher] Download from {tracker} failed: {e}", flush=True)
            continue

    return False


def _restore_old() -> bool:
    """Restore agent_client.py.old as agent_client.py if it's valid."""
    if not os.path.exists(_AGENT_OLD):
        print("[launcher] No .old backup found", flush=True)
        return False
    if not _is_valid(_AGENT_OLD):
        print("[launcher] .old backup has syntax errors too -- cannot restore", flush=True)
        return False
    shutil.copy2(_AGENT_OLD, _AGENT_PY)
    print("[launcher] Restored agent from .old backup", flush=True)
    return True


def _restore_bak() -> bool:
    """Restore agent_client.py.bak (pre-last-update snapshot) if it's valid."""
    if not os.path.exists(_AGENT_BAK):
        return False
    if not _is_valid(_AGENT_BAK):
        print("[launcher] .bak has syntax errors -- cannot restore", flush=True)
        return False
    shutil.copy2(_AGENT_BAK, _AGENT_PY)
    print("[launcher] Restored agent from .bak snapshot", flush=True)
    return True


def _record_crash() -> bool:
    """Record a crash timestamp. Returns True if we're in a crash loop."""
    now = time.monotonic()
    with _crash_lock:
        _crash_times.append(now)
        # Prune entries outside the window
        cutoff = now - _CRASH_WIN
        while _crash_times and _crash_times[0] < cutoff:
            _crash_times.pop(0)
        in_loop = len(_crash_times) >= _CRASH_MAX
    if in_loop:
        print(f"[launcher] Crash loop detected ({_CRASH_MAX} crashes in {_CRASH_WIN}s)", flush=True)
    return in_loop


def _heartbeat_loop():
    """Background thread: poll /api/rmm/agent/heartbeat every 5 min.

    Completely independent of the WebSocket -- works even when the gateway
    or main agent loop is broken.  Executes control actions returned by
    the server and runs queued shell commands.
    """
    tracker_url  = os.environ.get("RMM_TRACKER_URL", "https://tracker.corp.cirque.com").rstrip("/")
    fallback_url = os.environ.get("RMM_TRACKER_URL_PUBLIC", "https://tracker.cirquetools.com").rstrip("/")
    agent_id     = os.environ.get("RMM_AGENT_ID", "")
    token        = os.environ.get("RMM_AGENT_TOKEN", "")

    if not agent_id or not token:
        return

    INTERVAL = 300  # 5 minutes

    def _post_result(base_url, cmd_id, result, exit_code):
        try:
            body = json.dumps({'id': cmd_id, 'result': result, 'exit_code': exit_code}).encode()
            req = urllib.request.Request(
                f"{base_url}/api/rmm/agent/command_result?agent_id={agent_id}&token={token}",
                data=body, headers={"Content-Type": "application/json",
                                    "User-Agent": "CirqueLauncher/1.0"},
                method="POST"
            )
            urllib.request.urlopen(req, context=_ssl_ctx(), timeout=10)
        except Exception:
            pass

    while True:
        time.sleep(INTERVAL)
        for base in (tracker_url, fallback_url):
            try:
                url = f"{base}/api/rmm/agent/heartbeat?agent_id={agent_id}&token={token}"
                req = urllib.request.Request(url, headers={"User-Agent": "CirqueLauncher/1.0"})
                with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=20) as r:
                    data = json.loads(r.read())

                action   = data.get("action", "none")
                commands = data.get("pending_commands", [])

                if action == "force_update":
                    print("[launcher] Server requested force_update -- restarting to update", flush=True)
                    # Write force flag and restart this process so the update runs through main()
                    with open(_FORCE_FLAG, "w") as ff:
                        ff.write("server_requested")
                    sys.exit(0)  # NSSM will restart the launcher, which will pick up the flag

                elif action == "restart":
                    print("[launcher] Server requested restart -- exiting for NSSM restart", flush=True)
                    sys.exit(0)

                elif action == "reinstall":
                    print("[launcher] Server requested reinstall -- exiting for NSSM restart (full reinstall needed)", flush=True)
                    sys.exit(0)

                for cmd in commands:
                    cmd_id       = cmd.get("id")
                    cmd_str      = cmd.get("command", "")
                    cmd_type     = cmd.get("command_type", "shell")
                    if not cmd_str:
                        continue
                    try:
                        if cmd_type in ("shell", "powershell"):
                            proc = subprocess.run(
                                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd_str],
                                capture_output=True, text=True, timeout=120
                            )
                            result    = (proc.stdout + proc.stderr)[:3000]
                            exit_code = proc.returncode
                        else:
                            result, exit_code = f"Unknown command_type: {cmd_type}", 1
                    except Exception as ex:
                        result, exit_code = str(ex), 1
                    _post_result(base, cmd_id, result, exit_code)

                break  # success on this URL, no need to try fallback
            except Exception as e:
                print(f"[launcher] Heartbeat to {base} failed: {e}", flush=True)
                continue


def main():
    print(f"[launcher] Starting -- agent: {_AGENT_PY}", flush=True)

    # --- Check for force_update flag (set by server via heartbeat or manual file) ---
    if os.path.exists(_FORCE_FLAG):
        print("[launcher] force_update flag found -- forcing fresh download", flush=True)
        try:
            os.remove(_FORCE_FLAG)
        except OSError:
            pass
        _try_download_fresh()

    # --- Proactive update check: download newer agent_client.py if available ---
    _check_for_update()

    # --- Validate current agent_client.py ---
    if not _is_valid(_AGENT_PY):
        print("[launcher] agent_client.py has errors -- attempting recovery", flush=True)

        # 1. Try .old backup
        if not _restore_old():
            # 2. Try .bak snapshot
            if not _restore_bak():
                # 3. Try to download fresh from server
                if not _try_download_fresh():
                    print("[launcher] Recovery failed -- sleeping 60 s before exit", flush=True)
                    time.sleep(60)
                    sys.exit(1)

    # --- Start heartbeat background thread ---
    hb = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    hb.start()

    # --- Launch the agent ---
    print("[launcher] agent_client.py OK -- launching", flush=True)
    # Use runpy to run agent_client.py in-process so NSSM keeps tracking this PID.
    import runpy
    sys.argv = [_AGENT_PY]
    try:
        runpy.run_path(_AGENT_PY, run_name="__main__")
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
    except BaseException as _e:
        if type(_e).__name__ in ("CancelledError", "TaskGroupError"):
            sys.exit(0)
        import traceback as _tb
        try:
            os.makedirs(os.path.join(_AGENT_DIR, 'logs'), exist_ok=True)
            with open(_CRASH_LOG, 'a', encoding='utf-8') as _lf:
                _lf.write(f'\n=== LAUNCHER CRASH {__import__("datetime").datetime.now().isoformat()} ===\n')
                _tb.print_exc(file=_lf)
        except Exception:
            pass
        _tb.print_exc()

        # --- Crash loop detection ---
        if _record_crash():
            print("[launcher] Crash loop: forcing fresh download then exiting", flush=True)
            # Attempt to restore .bak first (quickest path)
            if not _restore_bak():
                _try_download_fresh()
            time.sleep(30)  # brief pause before NSSM restart

        sys.exit(1)


if __name__ == "__main__":
    main()
