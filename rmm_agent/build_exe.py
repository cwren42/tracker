#!/usr/bin/env python3
"""
Build CirqueRMM.exe — NSIS-based Windows installer.

NSIS runs PowerShell directly (no msiexec Custom Action engine), so the
"parameter is incorrect" issue that plagues msibuild-generated MSIs does
not exist here.

Usage:
    python3 build_exe.py [--site-token TOKEN] [--tracker-url URL] [--gateway-url URL]

If --site-token is omitted the script fetches it from the tracker DB.
"""

import os
import sys
import subprocess
import tempfile
import argparse

AGENT_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTPUT_EXE   = os.path.join(AGENT_DIR, "CirqueRMM.exe")
PRODUCT_NAME = "Cirque RMM Agent"
_VER_FILE    = os.path.join(AGENT_DIR, "version.txt")
VERSION      = open(_VER_FILE).read().strip() if os.path.exists(_VER_FILE) else "2.5.0"
PUBLISHER    = "Cirque IT"
INSTALL_DIR  = r"C:\CirqueRMM"  # No spaces — avoids quoting issues with NSSM AppParameters

BUNDLE_FILES = [
    "agent_client.py",
    "agent_launcher.py",
    "tray.py",
    "requirements.txt",
    "version.txt",
    "install_agent.ps1",
]
OPTIONAL_FILES = [
    "cirque_icon_ico.b64",
    "cirque_icon_png.b64",
    "cirque_logo.png",
    "nssm.exe",  # bundled so no download needed at install time
]


def get_site_token_from_db() -> str:
    """Fetch the site enrollment token from the tracker DB."""
    import psycopg2
    conn = psycopg2.connect(
        host="localhost", dbname="tracker",
        user="tracker_user", password="tracker_secure_2026"
    )
    cur = conn.cursor()
    cur.execute("SELECT value FROM setting WHERE key = 'rmm_site_enrollment_token'")
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    print("\nERROR: No site enrollment token found in the database.")
    print("Visit Settings → RMM in the Tracker web UI to generate one first.")
    print("Or pass it directly: python3 build_exe.py --site-token YOUR_TOKEN")
    sys.exit(1)


def build():
    parser = argparse.ArgumentParser(description="Build CirqueRMM.exe with NSIS")
    parser.add_argument("--site-token", default="",
                        help="Site-wide enrollment token (fetched from DB if omitted)")
    parser.add_argument("--tracker-url", default="https://tracker.corp.cirque.com")
    parser.add_argument("--gateway-url", default="wss://rmm.corp.cirque.com")
    args = parser.parse_args()

    site_token  = args.site_token.strip() or get_site_token_from_db()
    tracker_url = args.tracker_url.strip()
    gateway_url = args.gateway_url.strip()

    print("=" * 60)
    print("  Cirque RMM Agent - EXE Installer Builder (NSIS)")
    print("=" * 60)
    print(f"  Tracker URL : {tracker_url}")
    print(f"  Gateway URL : {gateway_url}")
    print(f"  Site token  : {site_token[:12]}...{site_token[-6:]}  ({len(site_token)} chars)")

    with tempfile.TemporaryDirectory() as tmp:
        # ── 1. Patch install_agent.ps1 with baked-in defaults ─────────────
        ps1_src = os.path.join(AGENT_DIR, "install_agent.ps1")
        # Read with utf-8-sig to strip any existing BOM from the source file
        ps1_content = open(ps1_src, encoding="utf-8-sig").read()
        ps1_configured = ps1_content
        ps1_configured = ps1_configured.replace(
            '[string]$SiteToken = "",',
            f'[string]$SiteToken = "{site_token}",',
            1)
        ps1_configured = ps1_configured.replace(
            '[string]$TrackerUrl  = "https://tracker.corp.cirque.com",',
            f'[string]$TrackerUrl  = "{tracker_url}",',
            1)
        ps1_configured = ps1_configured.replace(
            '[string]$GatewayUrl  = "wss://rmm.corp.cirque.com",',
            f'[string]$GatewayUrl  = "{gateway_url}",',
            1)

        # Ensure pure ASCII — strip any non-ASCII chars that sneak in via edits.
        # Pure ASCII needs no BOM and is parsed correctly by every PowerShell version.
        import re
        ps1_configured = ps1_configured.replace('\u2192', '->').replace('\u2014', '--').replace('\u2013', '-')
        ps1_configured = re.sub(r'[^\x00-\x7f]', '-', ps1_configured)

        configured_ps1 = os.path.join(tmp, "install_agent.ps1")
        # Write as UTF-8-sig (BOM + ASCII = safest for PowerShell 5.1 detection)
        with open(configured_ps1, "w", encoding="utf-8-sig") as f:
            f.write(ps1_configured)
        print(f"  Configured PS1: {len(ps1_configured)} chars, pure ASCII + BOM")

        # ── 2. Collect file paths ──────────────────────────────────────────
        file_paths = []  # list of (abs_path, filename)
        for fname in BUNDLE_FILES:
            if fname == "install_agent.ps1":
                file_paths.append((configured_ps1, "install_agent.ps1"))
                continue
            src = os.path.join(AGENT_DIR, fname)
            if not os.path.exists(src):
                print(f"  WARNING: {fname} not found, skipping")
                continue
            file_paths.append((src, fname))
        for fname in OPTIONAL_FILES:
            src = os.path.join(AGENT_DIR, fname)
            if os.path.exists(src):
                file_paths.append((src, fname))

        for path, name in file_paths:
            print(f"  + {name}  ({os.path.getsize(path):,} bytes)")

        # ── 3. Build NSIS uninstall file-delete commands ───────────────────
        delete_cmds = "\n".join(f'  Delete "$INSTDIR\\{name}"' for _, name in file_paths)
        delete_cmds += "\n  Delete \"$INSTDIR\\Uninstall.exe\""

        # ── 4. Write NSIS script ───────────────────────────────────────────
        # NSIS runs ExecWait which directly interacts with the Windows process API —
        # no msiexec Custom Action engine, no type flags, no property expansion.
        # PowerShell path is $SYSDIR\WindowsPowerShell\v1.0\powershell.exe which
        # NSIS resolves correctly on both 32-bit and 64-bit Windows.
        nsi_path = os.path.join(tmp, "installer.nsi")

        # Build the File lines for the NSIS Section
        file_lines = []
        for path, name in file_paths:
            # Copy each source file into tmp so NSIS can find them by relative name
            dest = os.path.join(tmp, name)
            if not os.path.exists(dest):
                subprocess.run(["cp", path, dest], check=True)
            file_lines.append(f'  File "{name}"')
        file_cmds = "\n".join(file_lines)

        nsi_content = f"""; Cirque RMM Agent NSIS Installer
; Generated by build_exe.py — do not edit directly.

Unicode True
SetCompressor /SOLID lzma
!include "LogicLib.nsh"

!define PRODUCT_NAME    "{PRODUCT_NAME}"
!define PRODUCT_VERSION "{VERSION}"
!define PUBLISHER       "{PUBLISHER}"
!define INSTALL_DIR     "{INSTALL_DIR}"
!define REGKEY          "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\CirqueRMM"

Name "${{PRODUCT_NAME}} ${{PRODUCT_VERSION}}"
OutFile "{OUTPUT_EXE}"
InstallDir "${{INSTALL_DIR}}"
RequestExecutionLevel admin
ShowInstDetails show

; ── Pages ────────────────────────────────────────────────────────────────────
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

; ── Install section ───────────────────────────────────────────────────────────
Section "Install" SEC_MAIN
  ; Stop existing service + tray before overwriting files (handles reinstall)
  ${{If}} ${{FileExists}} "$INSTDIR\\agent_client.py"
    DetailPrint "Stopping existing CirqueRMM installation..."
    ExecWait 'sc.exe stop CirqueRMM'
    ExecWait 'taskkill /f /im pythonw.exe'
    Sleep 2000
  ${{EndIf}}

  ; Create log dir now so failures are always captured even if PS1 crashes early
  CreateDirectory "$INSTDIR\\logs"

  SetOutPath "$INSTDIR"
  SetOverwrite on

{file_cmds}

  ; Write uninstaller
  WriteUninstaller "$INSTDIR\\Uninstall.exe"

  ; Registry entries for Add/Remove Programs
  WriteRegStr   HKLM "${{REGKEY}}" "DisplayName"      "${{PRODUCT_NAME}}"
  WriteRegStr   HKLM "${{REGKEY}}" "DisplayVersion"   "${{PRODUCT_VERSION}}"
  WriteRegStr   HKLM "${{REGKEY}}" "Publisher"        "${{PUBLISHER}}"
  WriteRegStr   HKLM "${{REGKEY}}" "InstallLocation"  "$INSTDIR"
  WriteRegStr   HKLM "${{REGKEY}}" "UninstallString"  '"$INSTDIR\\Uninstall.exe"'
  WriteRegDWORD HKLM "${{REGKEY}}" "NoModify"         1
  WriteRegDWORD HKLM "${{REGKEY}}" "NoRepair"         1

  ; ── Launch service setup asynchronously (PDQ Deploy compatible) ─────────
  ; Exec (no Wait) detaches PowerShell so the EXE exits immediately after
  ; file extraction. The PS1 runs hidden, installs Python/pip/NSSM/service
  ; in background, and completes within 2-3 minutes.
  ; PDQ Deploy sees exit code 0 instantly. Verify via PDQ "Run Script" step:
  ;   (Get-Service CirqueRMM -ErrorAction SilentlyContinue).Status -eq 'Running'
  DetailPrint "Files installed. Launching service setup in background..."
  Exec '$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\\install_agent.ps1" -SkipDownload'
  IfSilent skip_msg
  MessageBox MB_ICONINFORMATION "Cirque RMM Agent {VERSION} files installed.$\\n$\\nService setup runs in the background (~2 min).$\\nLog: $INSTDIR\\logs\\setup.log"
  skip_msg:
SectionEnd

; ── Uninstall section ─────────────────────────────────────────────────────────
Section "Uninstall"
  ; Kill tray application
  ExecWait 'taskkill /f /im pythonw.exe'
  Sleep 1000

  ; Remove tray startup shortcuts (all users and current user)
  SetShellVarContext all
  Delete "$SMSTARTUP\\CirqueRMM Tray.lnk"
  SetShellVarContext current
  Delete "$SMSTARTUP\\CirqueRMM Tray.lnk"

  ; Stop and remove service
  ExecWait 'sc.exe stop CirqueRMM'
  Sleep 1000
  ExecWait 'sc.exe delete CirqueRMM'

  ; Remove files
{delete_cmds}
  RMDir /r "$INSTDIR\\logs"
  RMDir "$INSTDIR"

  ; Remove App Path and registry keys
  DeleteRegKey HKLM "${{REGKEY}}"
SectionEnd
"""
        with open(nsi_path, "w", encoding="utf-8") as f:
            f.write(nsi_content)
        print(f"\n  NSIS script written ({len(nsi_content)} chars)")

        # ── 5. Run makensis ────────────────────────────────────────────────
        print("  Building installer with makensis...")
        result = subprocess.run(
            ["makensis", "-V2", nsi_path],
            capture_output=True, text=True, cwd=tmp
        )
        if result.returncode != 0:
            print("MAKENSIS FAILED:")
            print(result.stdout[-3000:])
            print(result.stderr[-3000:])
            sys.exit(1)
        # makensis output goes to stdout with -V2
        if result.stdout:
            print(result.stdout)

    if not os.path.exists(OUTPUT_EXE):
        print("ERROR: Expected output not found:", OUTPUT_EXE)
        sys.exit(1)

    size = os.path.getsize(OUTPUT_EXE)
    print("=" * 60)
    print(f"  SUCCESS: {OUTPUT_EXE}")
    print(f"  Size:    {size:,} bytes ({size // 1024} KB)")
    print("=" * 60)
    print(f"\nInstaller: double-click CirqueRMM.exe on Windows to install.")
    print(f"  Site token baked in: {site_token[:12]}...")


if __name__ == "__main__":
    build()
