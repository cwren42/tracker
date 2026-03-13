#!/usr/bin/env python3
"""
Cirque RMM Agent — Release Tool
================================
Bumps version, rebuilds the EXE installer, and updates checksum.txt.

Usage:
    python3 release.py patch          # 2.5.5 → 2.5.6
    python3 release.py minor          # 2.5.5 → 2.6.0
    python3 release.py major          # 2.5.5 → 3.0.0
    python3 release.py set 2.6.1      # Pin to exact version
    python3 release.py build          # Rebuild EXE without bumping version
    python3 release.py current        # Print current version and exit

Options:
    --no-build      Bump version only, skip EXE rebuild
    --site-token T  Override site token (default: read from DB)
    --tracker-url U Override tracker URL
    --gateway-url U Override gateway URL
"""

import argparse
import hashlib
import os
import subprocess
import sys

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
VER_FILE  = os.path.join(AGENT_DIR, "version.txt")
CKSUM_FILE = os.path.join(AGENT_DIR, "checksum.txt")
EXE_FILE  = os.path.join(AGENT_DIR, "CirqueRMM.exe")


def read_version() -> str:
    if os.path.exists(VER_FILE):
        return open(VER_FILE).read().strip()
    return "2.5.0"


def write_version(v: str):
    with open(VER_FILE, "w") as f:
        f.write(v)
    print(f"  version.txt → {v}")


def bump(current: str, part: str) -> str:
    parts = [int(x) for x in current.split(".")]
    while len(parts) < 3:
        parts.append(0)
    if part == "major":
        parts = [parts[0] + 1, 0, 0]
    elif part == "minor":
        parts = [parts[0], parts[1] + 1, 0]
    elif part == "patch":
        parts = [parts[0], parts[1], parts[2] + 1]
    return ".".join(str(p) for p in parts)


def update_checksum():
    if not os.path.exists(EXE_FILE):
        print("  WARNING: EXE not found, skipping checksum update")
        return
    sha = hashlib.sha256(open(EXE_FILE, "rb").read()).hexdigest()
    with open(CKSUM_FILE, "w") as f:
        f.write(sha)
    print(f"  checksum.txt → {sha[:16]}...")


def build_exe(site_token="", tracker_url="", gateway_url=""):
    cmd = [sys.executable, os.path.join(AGENT_DIR, "build_exe.py")]
    if site_token:
        cmd += ["--site-token", site_token]
    if tracker_url:
        cmd += ["--tracker-url", tracker_url]
    if gateway_url:
        cmd += ["--gateway-url", gateway_url]
    print(f"\n  Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=AGENT_DIR)
    if result.returncode != 0:
        print("\nERROR: build_exe.py failed.")
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description="Cirque RMM Agent release tool")
    parser.add_argument("command", nargs="?", default="patch",
                        choices=["patch", "minor", "major", "set", "build", "current"],
                        help="Version bump type or 'build' to rebuild without bumping")
    parser.add_argument("version_arg", nargs="?", default="",
                        help="Version string when using 'set' command")
    parser.add_argument("--no-build", action="store_true",
                        help="Bump version only, skip EXE rebuild")
    parser.add_argument("--site-token", default="")
    parser.add_argument("--tracker-url", default="")
    parser.add_argument("--gateway-url", default="")
    args = parser.parse_args()

    current = read_version()
    print(f"\n  Current version: {current}")

    if args.command == "current":
        return

    if args.command == "build":
        print(f"  Rebuilding EXE at v{current}...")
        build_exe(args.site_token, args.tracker_url, args.gateway_url)
        update_checksum()
        print(f"\n  Done — v{current}")
        return

    if args.command == "set":
        if not args.version_arg:
            print("ERROR: 'set' requires a version argument, e.g.: python3 release.py set 2.6.1")
            sys.exit(1)
        new_ver = args.version_arg.strip()
    else:
        new_ver = bump(current, args.command)

    print(f"  Bumping: {current} → {new_ver}")
    write_version(new_ver)

    if args.no_build:
        print("  --no-build: skipping EXE rebuild.")
        return

    build_exe(args.site_token, args.tracker_url, args.gateway_url)
    update_checksum()
    print(f"\n{'='*50}")
    print(f"  Released: v{new_ver}")
    print(f"  EXE:      {EXE_FILE}")
    print(f"  Checksum: {open(CKSUM_FILE).read().strip()[:16]}...")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
