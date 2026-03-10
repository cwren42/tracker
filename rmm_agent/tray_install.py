#!/usr/bin/env python3
"""
Cirque Tray Installer
=====================
Run this from the RMM terminal (as SYSTEM) to:
  1. Find pythonw.exe
  2. Install pystray + pillow
  3. Test that imports work
  4. Kill any stale tray.py processes
  5. Launch tray.py directly in the logged-in user's desktop session
     using WTSQueryUserToken + CreateProcessAsUser (the correct Windows API
     for spawning GUI apps from a SYSTEM service — avoids scheduled-task
     session-mapping failures entirely)

Log:  C:\\CirqueRMM\\tray_install.log
Usage (from RMM shell / PowerShell as admin):
  python C:\\CirqueRMM\\tray_install.py
"""

import ctypes
import ctypes.wintypes
import glob
import os
import subprocess
import sys
import traceback
from datetime import datetime

LOG  = r'C:\CirqueRMM\tray_install.log'
TRAY = r'C:\CirqueRMM\tray.py'

# ── helpers ───────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line, flush=True)
    try:
        with open(LOG, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except Exception:
        pass


def _find_pythonw() -> list:
    seen, out = set(), []
    def _add(p):
        p = p.strip()
        if p and os.path.isfile(p) and p not in seen:
            seen.add(p); out.append(p)
    _add(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))
    for p in glob.glob(r'C:\Users\*\AppData\Local\Programs\Python\Python*\pythonw.exe'):
        _add(p)
    for p in glob.glob(r'C:\Program Files\Python*\pythonw.exe'):
        _add(p)
    for p in glob.glob(r'C:\Python*\pythonw.exe'):
        _add(p)
    try:
        r = subprocess.run(['where', 'pythonw.exe'], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            _add(line)
    except Exception:
        pass
    return out


def _install_deps(pythonw: str) -> bool:
    _log(f'pip install pystray pillow  ({pythonw})')
    r = subprocess.run(
        [pythonw, '-m', 'pip', 'install', '--quiet', 'pystray', 'pillow'],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode == 0:
        _log('  pip OK')
        return True
    _log(f'  pip FAILED rc={r.returncode}')
    if r.stdout.strip():
        _log(f'  stdout: {r.stdout.strip()[:400]}')
    if r.stderr.strip():
        _log(f'  stderr: {r.stderr.strip()[:400]}')
    return False


def _test_imports(pythonw: str) -> bool:
    r = subprocess.run(
        [pythonw, '-c',
         'import pystray; from PIL import Image; '
         'print(f"pystray={pystray.__version__}"); print("imports OK")'],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode == 0:
        _log(f'  import test: {r.stdout.strip()}')
        return True
    _log(f'  import test FAILED rc={r.returncode}')
    if r.stdout.strip():
        _log(f'  stdout: {r.stdout.strip()[:400]}')
    if r.stderr.strip():
        _log(f'  stderr: {r.stderr.strip()[:400]}')
    return False


def _kill_existing_tray() -> None:
    _log('Killing existing tray.py processes...')
    subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command',
         'Get-WmiObject Win32_Process '
         '| Where-Object { $_.Name -eq "pythonw.exe" -and $_.CommandLine -like "*tray.py*" } '
         '| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'],
        capture_output=True, timeout=15,
    )


# ── Windows API: CreateProcessAsUser via WTSQueryUserToken ────────────────────

CREATE_NO_WINDOW          = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
NORMAL_PRIORITY_CLASS     = 0x00000020


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ('cb',              ctypes.wintypes.DWORD),
        ('lpReserved',      ctypes.wintypes.LPWSTR),
        ('lpDesktop',       ctypes.wintypes.LPWSTR),
        ('lpTitle',         ctypes.wintypes.LPWSTR),
        ('dwX',             ctypes.wintypes.DWORD),
        ('dwY',             ctypes.wintypes.DWORD),
        ('dwXSize',         ctypes.wintypes.DWORD),
        ('dwYSize',         ctypes.wintypes.DWORD),
        ('dwXCountChars',   ctypes.wintypes.DWORD),
        ('dwYCountChars',   ctypes.wintypes.DWORD),
        ('dwFillAttribute', ctypes.wintypes.DWORD),
        ('dwFlags',         ctypes.wintypes.DWORD),
        ('wShowWindow',     ctypes.wintypes.WORD),
        ('cbReserved2',     ctypes.wintypes.WORD),
        ('lpReserved2',     ctypes.POINTER(ctypes.c_byte)),
        ('hStdInput',       ctypes.wintypes.HANDLE),
        ('hStdOutput',      ctypes.wintypes.HANDLE),
        ('hStdError',       ctypes.wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('hProcess',    ctypes.wintypes.HANDLE),
        ('hThread',     ctypes.wintypes.HANDLE),
        ('dwProcessId', ctypes.wintypes.DWORD),
        ('dwThreadId',  ctypes.wintypes.DWORD),
    ]


def _launch_as_user(pythonw: str) -> bool:
    """
    Use WTSQueryUserToken + CreateProcessAsUser to run tray.py inside the
    active interactive user's desktop session.  This is the correct way to
    start a GUI app from a Windows service / SYSTEM process.
    Returns True on success.
    """
    kernel  = ctypes.windll.kernel32
    wts     = ctypes.windll.wtsapi32
    adv     = ctypes.windll.advapi32
    userenv = ctypes.windll.userenv

    # Active console session (the physical desktop)
    session_id = kernel.WTSGetActiveConsoleSessionId()
    _log(f'WTSGetActiveConsoleSessionId → {session_id}')
    if session_id == 0xFFFFFFFF:
        _log('  No active console session (nobody logged in interactively)')
        return False

    # Get the user token for that session
    hToken = ctypes.wintypes.HANDLE()
    ok = wts.WTSQueryUserToken(ctypes.c_ulong(session_id), ctypes.byref(hToken))
    if not ok:
        err = kernel.GetLastError()
        _log(f'  WTSQueryUserToken FAILED — error {err} (0x{err:08X})')
        if err == 1314:
            _log('  ERROR 1314 = privilege not held (need SeTcbPrivilege).')
            _log('  This process is not running as SYSTEM with the right privileges.')
        return False
    _log(f'  WTSQueryUserToken OK — hToken=0x{hToken.value:X}')

    # Build environment block for the user
    lpEnv = ctypes.c_void_p(None)
    userenv.CreateEnvironmentBlock(ctypes.byref(lpEnv), hToken, False)

    cmd_str = f'"{pythonw}" "{TRAY}"'
    _log(f'  CreateProcessAsUser: {cmd_str}')

    si = _STARTUPINFOW()
    si.cb        = ctypes.sizeof(_STARTUPINFOW)
    si.lpDesktop = 'winsta0\\default'
    pi = _PROCESS_INFORMATION()

    flags = NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
    if lpEnv.value:
        flags |= CREATE_UNICODE_ENVIRONMENT

    ok = adv.CreateProcessAsUserW(
        hToken,
        None,                                        # lpApplicationName
        ctypes.create_unicode_buffer(cmd_str),       # lpCommandLine
        None, None,                                  # process/thread attrs
        False,                                       # bInheritHandles
        ctypes.c_uint(flags),
        lpEnv if lpEnv.value else None,
        r'C:\CirqueRMM',                             # working dir
        ctypes.byref(si),
        ctypes.byref(pi),
    )

    if lpEnv.value:
        try:
            userenv.DestroyEnvironmentBlock(lpEnv)
        except Exception:
            pass
    kernel.CloseHandle(hToken)

    if ok:
        _log(f'  CreateProcessAsUser OK — PID={pi.dwProcessId}')
        kernel.CloseHandle(pi.hProcess)
        kernel.CloseHandle(pi.hThread)
        return True

    err = kernel.GetLastError()
    _log(f'  CreateProcessAsUser FAILED — error {err} (0x{err:08X})')
    return False


def _launch_via_schtask(pythonw: str) -> bool:
    """Fallback: scheduled task targeting the actual logged-in username."""
    _log('Fallback: scheduled task launch...')
    py_esc = pythonw.replace("'", "''")
    ps = (
        "$ErrorActionPreference = 'SilentlyContinue';"
        "$u = (Get-WmiObject Win32_ComputerSystem).UserName;"
        "if (-not $u) { "
        "    $qw = & qwinsta 2>$null;"
        "    $line = ($qw | Select-String 'Active' | Select-Object -First 1) -replace '>','';"
        "    $u = (($line.Trim() -split '\\s+')[1]);"
        "}"
        "$u = $u -replace '.*\\\\','';"
        "if (-not $u) { Write-Host 'NO_USER'; exit 1; }"
        f"$a = New-ScheduledTaskAction -Execute '{py_esc}' "
        "       -Argument 'C:\\CirqueRMM\\tray.py' "
        "       -WorkingDirectory 'C:\\CirqueRMM';"
        "$p = New-ScheduledTaskPrincipal -UserId $u "
        "       -LogonType Interactive -RunLevel Limited;"
        "$s = New-ScheduledTaskSettingsSet "
        "       -ExecutionTimeLimit 0 "
        "       -DisallowStartIfOnBatteries $false "
        "       -StopIfGoingOnBatteries $false;"
        "Register-ScheduledTask -TaskName 'CirqueTrayLaunch' "
        "       -Action $a -Principal $p -Settings $s -Force | Out-Null;"
        "Start-ScheduledTask -TaskName 'CirqueTrayLaunch';"
        "Write-Host \"Launched as $u\";"
    )
    r = subprocess.run(
        ['powershell', '-NoProfile', '-Command', ps],
        capture_output=True, text=True, timeout=25,
    )
    out = (r.stdout or '').strip()
    err = (r.stderr or '').strip()
    _log(f'  stdout: {out or "(empty)"}')
    if err:
        _log(f'  stderr: {err[:300]}')
    return r.returncode == 0 and 'NO_USER' not in out


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    _log('=' * 60)
    _log(f'Cirque Tray Installer — Python {sys.version.split()[0]}')
    _log(f'Running as user : {os.environ.get("USERNAME", "?")}')
    _log(f'sys.executable  : {sys.executable}')
    _log(f'Tray script     : {TRAY}')

    # -- Check tray.log for previous failures --------------------------------
    tray_log = r'C:\CirqueRMM\tray.log'
    if os.path.isfile(tray_log):
        _log('--- Previous tray.log contents ---')
        try:
            with open(tray_log, 'r', encoding='utf-8', errors='replace') as fh:
                for line in fh.read().splitlines()[-30:]:
                    _log(f'  {line}')
        except Exception as e:
            _log(f'  (could not read tray.log: {e})')
        _log('--- End tray.log ---')
    else:
        _log('No tray.log found (tray has never started or log not written)')

    # -- Verify tray.py exists -----------------------------------------------
    if not os.path.isfile(TRAY):
        _log(f'FATAL: {TRAY} not found.')
        _log('The agent needs to run _setup_tray() at least once to download tray.py.')
        sys.exit(1)

    # -- Find pythonw.exe ----------------------------------------------------
    pythonws = _find_pythonw()
    _log(f'Found {len(pythonws)} pythonw.exe candidate(s):')
    for pw in pythonws:
        _log(f'  {pw}')
    if not pythonws:
        _log('FATAL: No pythonw.exe found. Install Python for Windows with the '
             '"Add to PATH" option checked.')
        sys.exit(1)

    # -- Install + test deps in each candidate; use first that works ---------
    good_python = None
    for pw in pythonws:
        _log(f'--- Testing: {pw} ---')
        _install_deps(pw)
        if _test_imports(pw):
            good_python = pw
            break
    if not good_python:
        _log('FATAL: No Python installation has working pystray+pillow imports.')
        _log('Try manually: pip install pystray pillow')
        sys.exit(1)

    _log(f'Using: {good_python}')

    # -- Kill existing tray --------------------------------------------------
    _kill_existing_tray()
    import time; time.sleep(1)

    # -- Check current session -----------------------------------------------
    session_id = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
    _log(f'Active console session: {session_id}')

    # -- Try CreateProcessAsUser first (correct approach from SYSTEM) --------
    _log('Trying CreateProcessAsUser (WTSQueryUserToken approach)...')
    if _launch_as_user(good_python):
        _log('SUCCESS — tray launched via CreateProcessAsUser')
        _log(f'Check {tray_log} in ~5 seconds to confirm tray started.')
    else:
        _log('CreateProcessAsUser failed — trying scheduled task fallback...')
        if _launch_via_schtask(good_python):
            _log('SUCCESS — tray launch via scheduled task')
        else:
            _log('FAILED — both launch methods failed')
            _log('Possible causes:')
            _log('  1. No interactive user is logged into the console session')
            _log('  2. pystray does not support this Windows version')
            _log(f'  3. Check {tray_log} for tray.py error details')
            sys.exit(1)

    _log(f'Log written to: {LOG}')
    _log('Done.')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        _log(f'FATAL EXCEPTION: {e}\n{traceback.format_exc()}')
        sys.exit(1)
