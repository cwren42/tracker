"""
rustdesk_service.py – RustDesk Pro Server API client
Docs: https://rustdesk.com/docs/en/self-host/rustdesk-server-pro/api/

Environment variables:
  RUSTDESK_API_URL   – base URL for the Pro API, e.g. http://rustdesk.corp.local:21114
  RUSTDESK_API_KEY   – admin API key shown in the Pro web console
"""

import os
import logging
import requests

log = logging.getLogger(__name__)

RUSTDESK_API_URL = os.environ.get('RUSTDESK_API_URL', '').rstrip('/')
RUSTDESK_API_KEY = os.environ.get('RUSTDESK_API_KEY', '')

# RustDesk web console URL (for the browser-based session)
# Typically served at the server root (no port needed if proxied, or port 21114)
RUSTDESK_WEB_URL = os.environ.get('RUSTDESK_WEB_URL', '').rstrip('/')


def _headers():
    return {
        'Authorization': f'Bearer {RUSTDESK_API_KEY}',
        'Content-Type': 'application/json',
    }


def is_configured():
    """Return True if the service has been configured via env vars."""
    return bool(RUSTDESK_API_URL and RUSTDESK_API_KEY)


def _get(path, params=None):
    """HTTP GET against the RustDesk Pro API."""
    url = f'{RUSTDESK_API_URL}{path}'
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=8, verify=False)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.warning('RustDesk API GET %s failed: %s', path, e)
        return None


def _post(path, payload=None):
    """HTTP POST against the RustDesk Pro API."""
    url = f'{RUSTDESK_API_URL}{path}'
    try:
        resp = requests.post(url, headers=_headers(), json=payload or {}, timeout=8, verify=False)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.warning('RustDesk API POST %s failed: %s', path, e)
        return None


# ── Peers ──────────────────────────────────────────────────────────────────────

def get_all_peers(limit=1000):
    """
    Return list of all peer dicts from the RustDesk server.
    Each peer has keys like: id, hostname, username, cpu, memory, os, version,
                             last_online, online, ip, ...
    """
    data = _get('/api/peers', params={'current': 1, 'status': 1, 'limit': limit})
    if data is None:
        return []
    # RustDesk Pro returns {"msg": "success", "data": [...]}
    if isinstance(data, dict):
        return data.get('data', []) or []
    if isinstance(data, list):
        return data
    return []


def get_peer(peer_id: str):
    """Return details for a single peer, or None if not found."""
    data = _get(f'/api/peer/{peer_id}')
    if isinstance(data, dict) and data.get('id'):
        return data
    return None


def peer_is_online(peer_id: str) -> bool:
    """Quick check: is the peer currently online?"""
    peer = get_peer(peer_id)
    if not peer:
        return False
    return bool(peer.get('online'))


def get_peer_status_batch(peer_ids: list) -> dict:
    """
    Return {peer_id: True/False} online status for a list of IDs.
    Uses individual calls (RustDesk Pro has no bulk status endpoint).
    """
    result = {}
    for pid in peer_ids:
        result[pid] = peer_is_online(pid)
    return result


# ── Sessions / OTP ─────────────────────────────────────────────────────────────

def generate_session_url(peer_id: str) -> dict:
    """
    Build session connection info for a peer.

    Returns:
        {
          'app_url':  'rustdesk://<peer_id>',          # native client
          'web_url':  'https://rust.corp.../web#<id>', # Pro web console (if configured)
          'peer_id':  '<peer_id>',
        }
    """
    app_url = f'rustdesk://{peer_id}'
    web_url = None
    if RUSTDESK_WEB_URL:
        web_url = f'{RUSTDESK_WEB_URL}/#id={peer_id}'

    return {
        'peer_id': peer_id,
        'app_url': app_url,
        'web_url': web_url,
    }


# ── Sync helpers ───────────────────────────────────────────────────────────────

def build_peer_lookup() -> dict:
    """
    Return a dict keyed by hostname (lowercase) → peer dict.
    Used to match RustDesk peers to assets by hostname.
    """
    peers = get_all_peers()
    lookup = {}
    for p in peers:
        hn = (p.get('hostname') or p.get('name') or '').strip().lower()
        if hn:
            lookup[hn] = p
    return lookup


def sync_assets_to_peers(assets) -> dict:
    """
    Given a list of Asset objects, attempt to match each to a RustDesk peer
    by hostname (asset.name) and return:
        {
          asset_id: {
            'peer_id': '<id>',
            'online':  True/False,
            'matched': True/False,
          }
        }
    """
    if not is_configured():
        return {}

    peer_lookup = build_peer_lookup()
    results = {}

    for asset in assets:
        name = (asset.name or '').strip().lower()
        peer = peer_lookup.get(name)

        if peer:
            results[asset.id] = {
                'peer_id': peer.get('id', ''),
                'online':  bool(peer.get('online')),
                'matched': True,
            }
        else:
            # If asset already has a rustdesk_id, check its status
            if asset.rustdesk_id:
                online = peer_is_online(asset.rustdesk_id)
                results[asset.id] = {
                    'peer_id': asset.rustdesk_id,
                    'online':  online,
                    'matched': False,  # couldn't re-match by name
                }

    return results
