#!/usr/bin/env python3
"""
Build CirqueRMM.msi using gcab (cabinet builder) + msibuild (MSI database builder).
These are native Linux tools from the msitools package.

Usage:
    python3 build_msi.py
    => produces CirqueRMM.msi in the same directory

The MSI installs agent files to C:\\Program Files\\CirqueRMM\\ then runs
install_agent.ps1 -SkipDownload as a deferred elevated custom action.

Deploy:
    msiexec /i CirqueRMM.msi ENROLLMENT_TOKEN=agent_xxxx
"""
import os
import subprocess
import sys
import tempfile
import uuid

# -- Configuration ----------------------------------------------------------

AGENT_DIR       = os.path.dirname(os.path.abspath(__file__))
OUTPUT_MSI      = os.path.join(AGENT_DIR, "CirqueRMM.msi")
PRODUCT_VERSION = "2.3.3"
PRODUCT_CODE    = "{3C7A8142-5F26-4E0D-B1D9-FAB2C6E8D291}"
UPGRADE_CODE    = "{A9F3B812-7D54-4C2A-8E1F-DC0943B65718}"

BUNDLE_FILES = [
    "agent_client.py",
    "agent_launcher.py",
    "tray.py",
    "requirements.txt",
    "version.txt",
    "install_agent.ps1",
]
for _f in ("cirque_icon_ico.b64", "cirque_icon_png.b64", "cirque_logo.png"):
    if os.path.exists(os.path.join(AGENT_DIR, _f)):
        BUNDLE_FILES.append(_f)


# -- Helpers ----------------------------------------------------------------

def q(s):
    return s.replace("'", "''")


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        label = " ".join(str(x) for x in cmd[:4])
        print(f"\nPass FAILED ({r.returncode}): {label}")
        if r.stdout:
            print("STDOUT:", r.stdout[:3000])
        if r.stderr:
            print("STDERR:", r.stderr[:3000])
        sys.exit(1)
    return r.stdout


def ins(table, data):
    """
    Build INSERT statement omitting columns whose value is None.
    libmsi does NOT support NULL literals in INSERT - omit nullable cols instead.
    """
    cols, vals = [], []
    for k, v in data.items():
        if v is None:
            continue
        cols.append(k)
        vals.append(f"'{q(v)}'" if isinstance(v, str) else str(v))
    return f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(vals)})"


# -- Builder ----------------------------------------------------------------

def build():
    print("=" * 60)
    print("  Cirque RMM Agent - MSI Builder")
    print("=" * 60)

    file_entries = []
    for seq, fname in enumerate(BUNDLE_FILES, start=1):
        src = os.path.join(AGENT_DIR, fname)
        if not os.path.exists(src):
            print(f"  WARNING: {fname} not found, skipping")
            continue
        fkey = "F_" + "".join(c if c.isalnum() else "_" for c in fname).upper()
        file_entries.append({"key": fkey, "name": fname, "src": src,
                             "size": os.path.getsize(src), "seq": seq})
        print(f"  [{seq}] {fname}  ({os.path.getsize(src):,} bytes)")

    if not file_entries:
        print("ERROR: No files to bundle"); sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        # Build cabinet
        cab_path = os.path.join(tmp, "files.cab")
        for fe in file_entries:
            subprocess.run(["cp", fe["src"], os.path.join(tmp, fe["name"])], check=True)
        flist = " ".join(f'"{fe["name"]}"' for fe in file_entries)
        print(f"\nBuilding cabinet ({len(file_entries)} files)...")
        subprocess.run(f'cd "{tmp}" && gcab -cn "{cab_path}" {flist}', shell=True, check=True)
        print(f"  Cabinet: {os.path.getsize(cab_path):,} bytes")

        sql = []

        # Property -----------------------------------------------------------
        # IMPORTANT: libmsi rejects empty string '' in NOT NULL columns.
        # Use placeholder defaults for optional tokens - overridden at msiexec time.
        sql.append("CREATE TABLE Property (Property CHAR(72) NOT NULL, Value CHAR(0) NOT NULL PRIMARY KEY Property)")
        for k, v in [
            ("ProductName",              "Cirque RMM Agent"),
            ("ProductCode",              PRODUCT_CODE),
            ("ProductVersion",           PRODUCT_VERSION),
            ("Manufacturer",             "Cirque IT"),
            ("ProductLanguage",          "1033"),
            ("UpgradeCode",              UPGRADE_CODE),
            ("INSTALLLEVEL",             "3"),
            ("MSIRESTARTMANAGERCONTROL", "Disable"),
            ("ENROLLMENT_TOKEN",         "(not set)"),
            ("TRACKER_URL",              "https://tracker.corp.cirque.com"),
            ("GATEWAY_URL",              "wss://rmm.corp.cirque.com"),
            ("AGENT_ID",                 "(not set)"),
            ("PSEXEPATH",                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
            ("SecureCustomProperties",   "ENROLLMENT_TOKEN;TRACKER_URL;GATEWAY_URL;AGENT_ID"),
        ]:
            sql.append(ins("Property", {"Property": k, "Value": v}))

        # Directory ----------------------------------------------------------
        # Root dir: omit Directory_Parent (nullable) - do NOT use NULL literal
        sql.append("CREATE TABLE Directory (Directory CHAR(72) NOT NULL, Directory_Parent CHAR(72), DefaultDir CHAR(255) NOT NULL LOCALIZABLE PRIMARY KEY Directory)")
        sql.append(ins("Directory", {"Directory": "TARGETDIR",            "DefaultDir": "SourceDir"}))
        sql.append(ins("Directory", {"Directory": "ProgramFiles64Folder", "Directory_Parent": "TARGETDIR",            "DefaultDir": "PFiles64"}))
        sql.append(ins("Directory", {"Directory": "INSTALLDIR",           "Directory_Parent": "ProgramFiles64Folder", "DefaultDir": "CirqueRMM"}))

        # Feature ------------------------------------------------------------
        # Omit Feature_Parent for top-level feature (nullable)
        sql.append("CREATE TABLE Feature (Feature CHAR(38) NOT NULL, Feature_Parent CHAR(38), Title CHAR(64) LOCALIZABLE, Description CHAR(255) LOCALIZABLE, Display INTEGER, Level INTEGER NOT NULL, Directory_ CHAR(72), Attributes INTEGER NOT NULL PRIMARY KEY Feature)")
        sql.append(ins("Feature", {"Feature": "Complete", "Title": "Cirque RMM Agent",
                                   "Description": "Cirque IT remote management agent",
                                   "Display": 1, "Level": 3, "Directory_": "INSTALLDIR", "Attributes": 8}))

        # Component + FeatureComponents + File --------------------------------
        sql.append("CREATE TABLE Component (Component CHAR(72) NOT NULL, ComponentId CHAR(38), Directory_ CHAR(72) NOT NULL, Attributes INTEGER NOT NULL, Condition CHAR(255), KeyPath CHAR(72) PRIMARY KEY Component)")
        sql.append("CREATE TABLE FeatureComponents (Feature_ CHAR(38) NOT NULL, Component_ CHAR(72) NOT NULL PRIMARY KEY Feature_,Component_)")
        sql.append("CREATE TABLE File (File CHAR(72) NOT NULL, Component_ CHAR(72) NOT NULL, FileName CHAR(255) NOT NULL LOCALIZABLE, FileSize LONG NOT NULL, Version CHAR(72), Language CHAR(20), Attributes INTEGER, Sequence INTEGER NOT NULL PRIMARY KEY File)")
        ns = uuid.UUID(UPGRADE_CODE.strip("{}"))
        for fe in file_entries:
            cguid = "{" + str(uuid.uuid5(ns, fe["name"])).upper() + "}"
            # Omit Condition (nullable) in Component
            sql.append(ins("Component", {"Component": fe["key"], "ComponentId": cguid,
                                         "Directory_": "INSTALLDIR", "Attributes": 0,
                                         "KeyPath": fe["key"]}))
            sql.append(ins("FeatureComponents", {"Feature_": "Complete", "Component_": fe["key"]}))
            # Omit Version, Language (nullable) in File; Attributes 512 = compressed
            sql.append(ins("File", {"File": fe["key"], "Component_": fe["key"],
                                    "FileName": fe["name"], "FileSize": fe["size"],
                                    "Attributes": 512, "Sequence": fe["seq"]}))

        # Media --------------------------------------------------------------
        # Omit VolumeLabel, Source (nullable); DiskPrompt must be non-empty string
        last_seq = max(fe["seq"] for fe in file_entries)
        sql.append("CREATE TABLE Media (DiskId INTEGER NOT NULL, LastSequence INTEGER NOT NULL, DiskPrompt CHAR(64) LOCALIZABLE, Cabinet CHAR(255), VolumeLabel CHAR(32), Source CHAR(72) PRIMARY KEY DiskId)")
        sql.append(ins("Media", {"DiskId": 1, "LastSequence": last_seq,
                                  "DiskPrompt": "Disk 1", "Cabinet": "#files.cab"}))

        # CustomAction -------------------------------------------------------
        # Type 4658 = 50 (exe from property) + 512 (deferred) + 4096 (system ctx)
        # Do NOT add ExtendedType column - libmsi INSERT with NULL for it fails.
        ca_target = (
            "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden"
            " -File \"[INSTALLDIR]install_agent.ps1\""
            " -Token \"[ENROLLMENT_TOKEN]\""
            " -TrackerUrl \"[TRACKER_URL]\""
            " -GatewayUrl \"[GATEWAY_URL]\""
            " -AgentId \"[AGENT_ID]\""
            " -SkipDownload"
        )
        sql.append("CREATE TABLE CustomAction (Action CHAR(72) NOT NULL, Type INTEGER NOT NULL, Source CHAR(72), Target CHAR(255) PRIMARY KEY Action)")
        sql.append(ins("CustomAction", {"Action": "RunAgentSetup", "Type": 4658,
                                        "Source": "PSEXEPATH", "Target": ca_target}))

        # InstallExecuteSequence ---------------------------------------------
        # Omit Condition when absent - do NOT use NULL literal
        sql.append("CREATE TABLE InstallExecuteSequence (Action CHAR(72) NOT NULL, Condition CHAR(255), Sequence INTEGER PRIMARY KEY Action)")
        for action, cond, seqn in [
            ("ValidateProductID",  None,          700),
            ("CostInitialize",     None,          800),
            ("FileCost",           None,          900),
            ("CostFinalize",       None,         1000),
            ("InstallValidate",    None,         1400),
            ("InstallInitialize",  None,         1500),
            ("ProcessComponents",  None,         1600),
            ("UnpublishFeatures",  None,         1800),
            ("RemoveFiles",        None,         3500),
            ("InstallFiles",       None,         4000),
            ("RunAgentSetup",      "NOT REMOVE", 5000),
            ("RegisterUser",       None,         6000),
            ("RegisterProduct",    None,         6100),
            ("PublishComponents",  None,         6200),
            ("PublishFeatures",    None,         6300),
            ("PublishProduct",     None,         6400),
            ("InstallFinalize",    None,         6600),
        ]:
            row = {"Action": action, "Sequence": seqn}
            if cond:
                row["Condition"] = cond
            sql.append(ins("InstallExecuteSequence", row))

        # AdvtExecuteSequence ------------------------------------------------
        sql.append("CREATE TABLE AdvtExecuteSequence (Action CHAR(72) NOT NULL, Condition CHAR(255), Sequence INTEGER PRIMARY KEY Action)")
        for a, s in [("CostInitialize", 800), ("CostFinalize", 1000),
                     ("PublishFeatures", 6300), ("PublishProduct", 6400)]:
            sql.append(ins("AdvtExecuteSequence", {"Action": a, "Sequence": s}))

        # AdminExecuteSequence -----------------------------------------------
        sql.append("CREATE TABLE AdminExecuteSequence (Action CHAR(72) NOT NULL, Condition CHAR(255), Sequence INTEGER PRIMARY KEY Action)")
        for a, s in [("CostInitialize", 800), ("CostFinalize", 1000),
                     ("InstallFiles", 4000), ("InstallFinalize", 6600)]:
            sql.append(ins("AdminExecuteSequence", {"Action": a, "Sequence": s}))

        # -- Run msibuild (3 passes) ----------------------------------------
        # Pass 1 and pass 2 CANNOT be combined: msibuild rejects -s with -q.
        msi_tmp = os.path.join(tmp, "CirqueRMM.msi")

        print(f"\nPass 1: summary info...")
        run(["msibuild", msi_tmp,
             "-s", "Cirque RMM Agent", "Cirque IT", "Intel;1033",
             PRODUCT_CODE.strip("{}")])

        print(f"Pass 2: {len(sql)} SQL statements...")
        cmd2 = ["msibuild", msi_tmp]
        for stmt in sql:
            cmd2 += ["-q", stmt]
        run(cmd2)

        print("Pass 3: embedding cabinet...")
        run(["msibuild", msi_tmp, "-a", "files.cab", cab_path])

        subprocess.run(["cp", msi_tmp, OUTPUT_MSI], check=True)

    size = os.path.getsize(OUTPUT_MSI)
    print(f"\n{'='*60}")
    print(f"  SUCCESS: {OUTPUT_MSI}")
    print(f"  Size:    {size:,} bytes ({size//1024} KB)")
    print(f"{'='*60}")
    print("\nInstall:")
    print("  msiexec /i CirqueRMM.msi ENROLLMENT_TOKEN=agent_xxxx\n")


if __name__ == "__main__":
    build()
