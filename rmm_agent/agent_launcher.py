#!/usr/bin/env python3
"""
Cirque RMM Agent Launcher
=========================
Lightweight wrapper that validates and runs agent_client.py with automatic
self-healing: if the main agent has a syntax error it restores the .old
backup or downloads a fresh copy from the server before launching.

NSSM should be configured to run THIS script instead of agent_client.py
so that update-induced syntax errors are automatically recovered.
"""
import ast
import hashlib
import json
import os
import shutil
import ssl
import sys
import time
import urllib.request

_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_PY  = os.path.join(_AGENT_DIR, "agent_client.py")
_AGENT_OLD = _AGENT_PY + ".old"


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


def _try_download_fresh() -> bool:
    """Download a fresh agent_client.py from the tracker server.

    Returns True if a valid copy was saved, False on any failure.
    """
    tracker_url = os.environ.get("RMM_TRACKER_URL",
                                 "https://tracker.corp.cirque.com").rstrip("/")
    agent_id    = os.environ.get("RMM_AGENT_ID", "")
    token       = os.environ.get("RMM_AGENT_TOKEN", "")

    if not agent_id or not token:
        print("[launcher] RMM_AGENT_ID/TOKEN not set — cannot download fresh copy", flush=True)
        return False

    try:
        # Fetch version manifest to get expected checksum
        ver_url = f"{tracker_url}/rmm/agent/version?agent_id={agent_id}&token={token}"
        req = urllib.request.Request(ver_url, headers={"User-Agent": "CirqueLauncher/1.0"})
        with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=15) as r:
            data = json.loads(r.read())
        server_cksum = data.get("checksum", "")

        # Download the file
        file_url = f"{tracker_url}/rmm/agent/file?agent_id={agent_id}&token={token}"
        req2 = urllib.request.Request(file_url, headers={"User-Agent": "CirqueLauncher/1.0"})
        with urllib.request.urlopen(req2, context=_ssl_ctx(), timeout=30) as r:
            new_code = r.read()

        # Verify checksum
        if server_cksum and hashlib.sha256(new_code).hexdigest() != server_cksum:
            print("[launcher] Checksum mismatch on downloaded agent — aborting", flush=True)
            return False

        # Write and validate
        tmp = _AGENT_PY + ".dl"
        with open(tmp, "wb") as f:
            f.write(new_code)

        if not _is_valid(tmp):
            print("[launcher] Downloaded agent has syntax errors — keeping current", flush=True)
            os.remove(tmp)
            return False

        # Atomically replace
        if os.path.exists(_AGENT_PY):
            shutil.copy2(_AGENT_PY, _AGENT_OLD)
        shutil.move(tmp, _AGENT_PY)
        print("[launcher] Fresh agent downloaded and installed", flush=True)
        return True

    except Exception as e:
        print(f"[launcher] Download failed: {e}", flush=True)
        return False


def _restore_old() -> bool:
    """Restore agent_client.py.old as agent_client.py if it's valid."""
    if not os.path.exists(_AGENT_OLD):
        print("[launcher] No .old backup found", flush=True)
        return False
    if not _is_valid(_AGENT_OLD):
        print("[launcher] .old backup has syntax errors too — cannot restore", flush=True)
        return False
    shutil.copy2(_AGENT_OLD, _AGENT_PY)
    print("[launcher] Restored agent from .old backup", flush=True)
    return True


def main():
    print(f"[launcher] Starting — agent: {_AGENT_PY}", flush=True)

    # --- Validate current agent_client.py ---
    if not _is_valid(_AGENT_PY):
        print("[launcher] agent_client.py has errors — attempting recovery", flush=True)

        # 1. Try to restore from .old backup
        if not _restore_old():
            # 2. Try to download fresh from server
            if not _try_download_fresh():
                # 3. Can't recover — wait to avoid hammering NSSM restart loop
                print("[launcher] Recovery failed — sleeping 60 s before exit", flush=True)
                time.sleep(60)
                sys.exit(1)

    # --- Launch the agent ---
    print("[launcher] agent_client.py OK — launching", flush=True)
    # Use runpy to run agent_client.py in-process so NSSM keeps tracking this PID.
    # os.execv on Windows spawns a new process then exits, causing NSSM to restart
    # the launcher in a loop and the spawned child inherits unquoted argv that splits
    # on spaces in "C:\Program Files\..." paths.
    import runpy
    sys.argv = [_AGENT_PY]
    try:
        runpy.run_path(_AGENT_PY, run_name="__main__")
    except (KeyboardInterrupt, SystemExit):
        # Normal service stop — exit cleanly without traceback
        sys.exit(0)
    except BaseException as _e:
        # CancelledError and other asyncio exceptions — suppress traceback, let NSSM restart
        if type(_e).__name__ in ("CancelledError", "TaskGroupError"):
            sys.exit(0)
        # Log the crash to disk so we can diagnose it, then exit so NSSM can restart
        import traceback as _tb
        _crash_log = os.path.join(_AGENT_DIR, 'logs', 'agent.log')
        try:
            os.makedirs(os.path.join(_AGENT_DIR, 'logs'), exist_ok=True)
            with open(_crash_log, 'a', encoding='utf-8') as _lf:
                _lf.write(f'\n=== LAUNCHER CRASH {__import__("datetime").datetime.now().isoformat()} ===\n')
                _tb.print_exc(file=_lf)
        except Exception:
            pass
        _tb.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
