#!/usr/bin/env bash
# Deploy the latest CI-built RMM agent installer onto this server.
#
# Closes the "last mile": agent-build.yml builds CirqueRMM.exe on a clean Windows
# runner (version-gated + checksummed) and publishes it as a GitHub Release asset on
# an `agent-v*` tag — but the server serves rmm_agent/CirqueRMM.exe from local disk
# (gitignored), so the CI build never lands here on its own. This fetches the latest
# `agent-v*` release's EXE, verifies its SHA-256 against the release checksum.txt, and
# atomically installs it — refusing to deploy a placeholder-token build.
#
# Usage:
#   scripts/fetch_agent_build.sh            # latest agent-v* release
#   scripts/fetch_agent_build.sh agent-v2.9.22   # a specific tag
#
# Auth: GITHUB_TOKEN env var, else the PAT in ~/.git-credentials.
set -euo pipefail

REPO="cwren42/tracker"
DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/rmm_agent/CirqueRMM.exe"
WANT_TAG="${1:-}"

# --- token ---
TOK="${GITHUB_TOKEN:-}"
if [ -z "$TOK" ] && [ -f "$HOME/.git-credentials" ]; then
  TOK="$(sed -nE 's#https://[^:]*:?([A-Za-z0-9_]+)@github.com.*#\1#p' "$HOME/.git-credentials" | head -1)"
fi
[ -n "$TOK" ] || { echo "ERROR: no GitHub token (set GITHUB_TOKEN or ~/.git-credentials)"; exit 1; }
api() { curl -fsSL -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" "$@"; }

# --- resolve the release (latest agent-v* tag, or the one requested) ---
echo "Looking up release ($([ -n "$WANT_TAG" ] && echo "$WANT_TAG" || echo "latest agent-v*")) ..."
if [ -n "$WANT_TAG" ]; then
  REL_JSON="$(api "https://api.github.com/repos/$REPO/releases/tags/$WANT_TAG")"
else
  REL_JSON="$(api "https://api.github.com/repos/$REPO/releases?per_page=30" \
    | python3 -c "import sys,json; rels=[r for r in json.load(sys.stdin) if (r.get('tag_name') or '').startswith('agent-v')]; print(json.dumps(rels[0]) if rels else '')")"
fi
if [ -z "$REL_JSON" ] || [ "$REL_JSON" = "null" ]; then
  echo "No agent-v* release found yet. Cut one with:  git tag agent-vX.Y.Z && git push origin agent-vX.Y.Z"
  echo "(The served EXE on disk is left unchanged.)"
  exit 2
fi

TAG="$(echo "$REL_JSON" | python3 -c "import sys,json;print(json.load(sys.stdin)['tag_name'])")"
exe_url="$(echo "$REL_JSON" | python3 -c "import sys,json;a=[x for x in json.load(sys.stdin)['assets'] if x['name']=='CirqueRMM.exe'];print(a[0]['url'] if a else '')")"
sum_url="$(echo "$REL_JSON" | python3 -c "import sys,json;a=[x for x in json.load(sys.stdin)['assets'] if x['name']=='checksum.txt'];print(a[0]['url'] if a else '')")"
[ -n "$exe_url" ] || { echo "ERROR: release $TAG has no CirqueRMM.exe asset"; exit 1; }

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
echo "Downloading $TAG ..."
curl -fsSL -H "Authorization: Bearer $TOK" -H "Accept: application/octet-stream" "$exe_url" -o "$tmp/CirqueRMM.exe"

# --- verify checksum (if the release published one) ---
if [ -n "$sum_url" ]; then
  curl -fsSL -H "Authorization: Bearer $TOK" -H "Accept: application/octet-stream" "$sum_url" -o "$tmp/checksum.txt"
  want="$(tr -d '[:space:]' < "$tmp/checksum.txt")"
  got="$(sha256sum "$tmp/CirqueRMM.exe" | awk '{print $1}')"
  [ "$want" = "$got" ] || { echo "ERROR: checksum mismatch (want $want, got $got) — refusing"; exit 1; }
  echo "checksum OK: $got"
else
  echo "WARNING: release has no checksum.txt — skipping hash verify"
fi

# --- placeholder-build guard: never deploy a non-enrolling installer ---
if grep -aq "PLACEHOLDER_SITE_TOKEN" "$tmp/CirqueRMM.exe"; then
  echo "ERROR: $TAG was built WITHOUT the RMM_SITE_TOKEN secret (placeholder token)."
  echo "       It can't self-enroll on double-click. Set the repo secret, rebuild, retry."
  echo "       (The served EXE on disk is left unchanged.)"
  exit 1
fi

# --- atomic install ---
mkdir -p "$(dirname "$DEST")"
cp "$tmp/CirqueRMM.exe" "$DEST.new"
mv -f "$DEST.new" "$DEST"
echo "Deployed $TAG -> $DEST ($(stat -c%s "$DEST") bytes)"
echo "Served immediately at /download/agent-installer (send_file from disk)."
