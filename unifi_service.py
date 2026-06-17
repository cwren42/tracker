"""
UniFi Network Controller sync service.

Connects to the local UniFi Network Application running on the UDM Pro
and syncs all managed devices (APs, switches, gateways, cameras, storage,
etc.) into the Asset table.

Auth flow: POST /api/auth/login  → cookie-based session (UDM OS ≥ 3.x)
Device endpoint: GET /proxy/network/api/s/{site}/stat/device
"""

import logging
from datetime import datetime

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# ── UniFi device-type mapping ────────────────────────────────────────────────
# UniFi "type" field values  →  (asset category,  device_type label)
UNIFI_TYPE_MAP = {
    'ugw':    ('Network Device', 'Gateway'),
    'udm':    ('Network Device', 'Gateway'),
    'uap':    ('Network Device', 'Access Point'),
    'usw':    ('Network Device', 'Switch'),
    'uxg':    ('Network Device', 'Gateway'),
    'uph':    ('Network Device', 'VoIP Phone'),
    'ubb':    ('Network Device', 'Building Bridge'),
    'uck':    ('Network Device', 'Cloud Key'),
    'usp':    ('Network Device', 'SmartPower'),
    # Protect / Storage
    'uvc':    ('Camera', 'UniFi Camera'),
    'unvr':   ('Storage Device', 'UniFi NVR'),
    'udr':    ('Storage Device', 'UniFi NVR'),
    'unas':   ('Network Device', 'UniFi NAS'),
    # Access
    'uas':    ('Network Device', 'Access Controller'),
    'uar':    ('Network Device', 'Door Reader'),
    'uah':    ('Network Device', 'Door Reader'),
    'uacc':   ('Network Device', 'Access Hub'),
}

# UniFi camera model prefixes → category override
CAMERA_PREFIXES = ('UVC', 'G3', 'G4', 'G5', 'AI', 'UP-')

# UniFi storage model prefixes
STORAGE_PREFIXES = ('UNVR', 'UDR', 'UNAS')

# UniFi Access device model prefixes
ACCESS_PREFIXES = ('UA-', 'UAS', 'UAH', 'UAR')


def _classify_device(dev: dict) -> tuple[str, str]:
    """Return (category, device_type) for a UniFi device dict."""
    model: str = (dev.get('model') or dev.get('type', '')).upper()
    utype: str = (dev.get('type') or '').lower()
    source: str = dev.get('_source', '')

    # Source hints from Protect / Access / UNAS take priority
    if source == 'unas':
        return ('Network Device', 'UniFi NAS')
    if source == 'protect':
        # The Protect bootstrap NVR is often the UDM/UDM Pro itself — classify as Gateway not Camera
        if utype in ('udm', 'udmpro', 'udm-pro', 'udmse') or model.startswith('UDM'):
            return UNIFI_TYPE_MAP.get('udm', ('Network Device', 'Gateway'))
        if utype in ('unvr', 'udr') or any(model.startswith(p) for p in STORAGE_PREFIXES):
            return ('Storage Device', 'UniFi NVR')
        return ('Camera', 'UniFi Camera')
    if source == 'access':
        if any(model.startswith(p) for p in ACCESS_PREFIXES):
            pass  # fall through to type map
        return UNIFI_TYPE_MAP.get(utype, ('Network Device', 'Access Controller'))

    for prefix in CAMERA_PREFIXES:
        if model.startswith(prefix):
            return ('Camera', 'UniFi Camera')

    for prefix in STORAGE_PREFIXES:
        if model.startswith(prefix):
            label = 'UniFi NAS' if prefix == 'UNAS' else 'UniFi NVR'
            return ('Storage Device', label)

    for prefix in ACCESS_PREFIXES:
        if model.startswith(prefix):
            return UNIFI_TYPE_MAP.get(utype, ('Network Device', 'Access Controller'))

    return UNIFI_TYPE_MAP.get(utype, ('Network Device', 'Network Device'))


def _online_state(dev: dict) -> str:
    """Map UniFi device state to 'Online'/'Offline'."""
    # Network app: state integer — 0=disconnected, 1=connected, 4=upgrading, 5=provisioning, 6=heartbeat missed
    state = dev.get('state')
    if isinstance(state, int):
        return 'Online' if state in (1, 4, 5, 6) else 'Offline'
    # Protect app: state string — 'CONNECTED', 'DISCONNECTED', etc.
    if isinstance(state, str):
        return 'Online' if state.upper() in ('CONNECTED', 'ONLINE') else 'Offline'
    # Protect also uses 'isConnected' boolean
    if dev.get('isConnected') is not None:
        return 'Online' if dev.get('isConnected') else 'Offline'
    # UniFi Access API uses 'connection_state'
    conn_state = dev.get('connection_state') or dev.get('connectionState') or ''
    if conn_state:
        return 'Online' if conn_state.upper() in ('CONNECTED', 'ONLINE') else 'Offline'
    # If the device appeared in the API at all it's adopted — assume Online
    return 'Online'


class UnifiService:
    """Encapsulates all UniFi API communication."""

    def __init__(self, host: str, username: str, password: str, site: str = 'default', verify_ssl: bool = False):
        # Normalize common typos (semicolons, missing scheme, trailing slash)
        host = host.strip().rstrip('/')
        if host and not host.startswith(('http://', 'https://')):
            host = 'https://' + host
        self.host = host
        self.username = username
        self.password = password
        self.site = site
        self.verify = verify_ssl
        self._session = requests.Session()
        self._session.verify = verify_ssl
        self._logged_in = False

    def login(self) -> None:
        """Authenticate and store session cookie."""
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })

        # Step 1: GET the login page to pick up any initial CSRF token / cookies
        try:
            pre = self._session.get(f'{self.host}/api/auth/login', timeout=8)
            csrf = (pre.headers.get('X-CSRF-Token') or
                    pre.headers.get('x-csrf-token') or
                    pre.cookies.get('csrf_token') or '')
            if csrf:
                self._session.headers['X-CSRF-Token'] = csrf
        except Exception:
            pass

        # Step 2: POST credentials — try UDM OS ≥ 3.x endpoint first
        for path in ('/api/auth/login', '/api/login'):
            try:
                resp = self._session.post(
                    f'{self.host}{path}',
                    json={'username': self.username, 'password': self.password},
                    timeout=10,
                )
            except Exception as e:
                raise RuntimeError(f'Connection failed to {self.host}: {e}') from e

            if resp.status_code == 200:
                csrf = (resp.headers.get('X-CSRF-Token') or
                        resp.headers.get('x-csrf-token'))
                if csrf:
                    self._session.headers['X-CSRF-Token'] = csrf
                self._logged_in = True
                logger.info('UniFi login successful to %s', self.host)
                return

            if resp.status_code == 404:
                continue  # try next path

            # Any other 4xx/5xx — surface a useful message
            try:
                body = resp.json()
            except Exception:
                body = {}
                detail = resp.text[:200]
            else:
                detail = body

            # Detect SSO/MFA requirement and give actionable advice
            code = body.get('code', '') if isinstance(body, dict) else ''
            if resp.status_code == 499 and 'MFA' in code:
                raise RuntimeError(
                    'UniFi SSO account detected — MFA is required for SSO accounts. '
                    'Create a local admin on the UDM Pro (Settings → Admins & Users → '
                    'Add Admin → Local Access Only) and use those credentials instead.'
                )

            raise RuntimeError(
                f'UniFi login returned HTTP {resp.status_code} at {path}: {detail}'
            )

        raise RuntimeError(f'UniFi: no valid auth endpoint found on {self.host}')

    def logout(self) -> None:
        try:
            self._session.post(f'{self.host}/api/auth/logout', timeout=5)
        except Exception:
            pass
        self._logged_in = False

    def get_sites(self) -> list[dict]:
        """Return list of available sites from the controller."""
        for path in ('/proxy/network/api/self/sites', '/api/self/sites'):
            try:
                resp = self._session.get(f'{self.host}{path}', timeout=10)
                if resp.status_code == 200:
                    return resp.json().get('data', [])
            except Exception:
                continue
        return []

    def get_devices(self) -> list[dict]:
        """Return list of all devices from the controller."""
        from urllib.parse import quote
        site_encoded = quote(self.site, safe='')
        # UDM Pro proxies the Network app here; older controllers use /api/s/...
        paths = (
            f'/proxy/network/api/s/{site_encoded}/stat/device',
            f'/api/s/{site_encoded}/stat/device',
        )
        for path in paths:
            url = f'{self.host}{path}'
            try:
                resp = self._session.get(url, timeout=15)
                logger.debug('UniFi GET %s → HTTP %s', url, resp.status_code)
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except Exception:
                        logger.warning('UniFi: non-JSON response from %s: %s', url, resp.text[:200])
                        continue
                    devices = body.get('data', [])
                    logger.info('UniFi: %d devices from %s (meta: %s)', len(devices), url, body.get('meta'))
                    if devices:
                        return devices
                    # 200 but empty data — may be wrong site name; log and continue
                    logger.warning('UniFi: 200 OK but empty data list from %s (body keys: %s)', url, list(body.keys()))
                elif resp.status_code in (301, 302, 303, 307, 308):
                    logger.warning('UniFi: redirect %s → %s', url, resp.headers.get('Location'))
                elif resp.status_code == 401:
                    logger.warning('UniFi: 401 Unauthorized at %s — session may not have carried over', url)
                else:
                    logger.warning('UniFi: HTTP %s from %s', resp.status_code, url)
            except Exception as exc:
                logger.warning('UniFi: request error for %s: %s', url, exc)
                continue
        return []

    def get_protect_cameras(self) -> list[dict]:
        """Fetch cameras and the NVR from UniFi Protect."""
        devices = []

        # 1. Fetch cameras
        for path in ('/proxy/protect/api/cameras',):
            try:
                resp = self._session.get(f'{self.host}{path}', timeout=15)
                if resp.status_code == 200:
                    body = resp.json()
                    raw = body if isinstance(body, list) else body.get('data', [])
                    for cam in raw:
                        cam.setdefault('type', 'uvc')
                        cam.setdefault('_source', 'protect')
                    devices.extend(raw)
                    logger.info('UniFi Protect cameras: %d from %s', len(raw), path)
                else:
                    logger.debug('UniFi Protect cameras %s → HTTP %s', path, resp.status_code)
            except Exception as exc:
                logger.debug('UniFi Protect cameras error: %s', exc)

        # 2. Fetch NVR from bootstrap
        try:
            resp = self._session.get(f'{self.host}/proxy/protect/api/bootstrap', timeout=15)
            if resp.status_code == 200:
                body = resp.json()
                nvr = body.get('nvr')
                if nvr and isinstance(nvr, dict) and nvr.get('mac'):
                    nvr_mac = nvr['mac'].lower()
                    nvr_model = (nvr.get('modelKey') or nvr.get('model') or '').upper()
                    # If the NVR is actually the UDM (runs Protect built-in), it's already
                    # in the network device list — skip adding it as a separate Protect entry
                    already_network = any(
                        (d.get('mac') or '').lower() == nvr_mac
                        for d in devices
                        if d.get('_source', 'network') == 'network'
                    )
                    if already_network or nvr_model.startswith('UDM'):
                        logger.info('UniFi Protect NVR (%s / %s) is UDM — skipping duplicate',
                                    nvr.get('name'), nvr_model)
                    else:
                        nvr.setdefault('type', 'unvr')
                        nvr.setdefault('_source', 'protect')
                        devices.append(nvr)
                        logger.info('UniFi Protect NVR: %s (model=%s)', nvr.get('name'), nvr.get('modelKey'))
                # Also pick up cameras from bootstrap if /cameras path failed
                if not any(d.get('_source') == 'protect' and d.get('type') == 'uvc' for d in devices):
                    for cam in body.get('cameras', []):
                        cam.setdefault('type', 'uvc')
                        cam.setdefault('_source', 'protect')
                        devices.append(cam)
            else:
                logger.debug('UniFi Protect bootstrap → HTTP %s', resp.status_code)
        except Exception as exc:
            logger.debug('UniFi Protect bootstrap error: %s', exc)

        return devices

    def get_unas_devices(self) -> list[dict]:
        """Fetch UniFi NAS (UNAS / UNAS Pro) devices.

        The UNAS is not adopted as a managed network device — it appears as a
        wired client in stat/sta.  We filter by hostname prefix 'UNAS', deduplicate
        by canonical hostname (strip trailing 'b'/'c' port suffixes), and prefer the
        interface that has an IP address.
        """
        from urllib.parse import quote
        site_encoded = quote(self.site, safe='')
        devices = []
        try:
            resp = self._session.get(
                f'{self.host}/proxy/network/api/s/{site_encoded}/stat/sta',
                timeout=15,
            )
            logger.debug('UniFi UNAS (sta) → HTTP %s', resp.status_code)
            if resp.status_code != 200:
                return []
            clients = resp.json().get('data', [])
        except Exception as exc:
            logger.debug('UniFi UNAS sta error: %s', exc)
            return []

        # Group UNAS interfaces by canonical name (strip trailing 'b', 'c', etc.)
        groups: dict[str, list[dict]] = {}
        for c in clients:
            hostname = (c.get('hostname') or c.get('name') or '').strip()
            if not hostname.upper().startswith('UNAS'):
                continue
            # Canonical key: strip single trailing letter suffix (port B/C)
            import re
            canonical = re.sub(r'[a-zA-Z]$', '', hostname).rstrip('-').strip()
            groups.setdefault(canonical, []).append(c)

        for canonical, interfaces in groups.items():
            # Prefer interface with an IP; fall back to first
            primary = next((i for i in interfaces if i.get('ip')), interfaces[0])
            # Use the shortest hostname as the display name (avoids 'b' suffixes)
            name = min((i.get('hostname') or canonical for i in interfaces), key=len)
            # Use first MAC (management interface)
            mac = primary.get('mac') or interfaces[0].get('mac', '')
            ip = primary.get('ip') or ''
            uptime = primary.get('uptime') or 0
            last_seen = primary.get('last_seen') or 0

            # Device is online if last_seen is within the last 10 minutes
            import time
            online = (time.time() - last_seen) < 600 if last_seen else bool(uptime)

            dev = {
                'name': name,
                'mac': mac,
                'ip': ip,
                'model': 'UNAS-Pro',
                'type': 'unas',
                '_source': 'unas',
                'uptime': uptime,
                # Synthesise a state integer so _online_state works correctly
                'state': 1 if online else 0,
                # Keep all MACs for reference
                '_all_macs': [i.get('mac') for i in interfaces if i.get('mac')],
            }
            devices.append(dev)
            logger.info('UniFi UNAS: found %s mac=%s ip=%s online=%s', name, mac, ip, online)

        return devices

    def get_access_devices(self) -> list[dict]:
        """Fetch door controllers and readers from UniFi Access."""
        # Try multiple known Access API paths
        for path in (
            '/proxy/access/api/v2/device',
            '/proxy/access/api/v2/devices',
            '/proxy/access/api/v2/door',
        ):
            try:
                resp = self._session.get(f'{self.host}{path}', timeout=15)
                logger.debug('UniFi Access %s → HTTP %s', path, resp.status_code)
                if resp.status_code == 200:
                    body = resp.json()
                    raw = body.get('data', body) if isinstance(body, dict) else body
                    if isinstance(raw, list):
                        for d in raw:
                            d.setdefault('type', 'uas')
                            d.setdefault('_source', 'access')
                        logger.info('UniFi Access: %d devices from %s', len(raw), path)
                        return raw
                elif resp.status_code in (404, 403):
                    continue
                else:
                    logger.warning('UniFi Access %s → HTTP %s', path, resp.status_code)
            except Exception as exc:
                logger.debug('UniFi Access %s error: %s', path, exc)
        logger.info('UniFi Access: no devices found (account may need Access Viewer role)')
        return []

    def test_connection(self) -> dict:
        """Login, fetch device count, logout. Returns status dict."""
        try:
            self.login()

            # Always discover available sites to help identify correct site ID
            sites = self.get_sites()
            site_info = ', '.join(
                f'"{s.get("name","?")}" ({s.get("desc","")})' for s in sites
            ) if sites else 'none found'
            logger.info('UniFi available sites: %s', site_info)

            devices = self.get_devices()
            protect = self.get_protect_cameras()
            access = self.get_access_devices()
            unas = self.get_unas_devices()
            total = len(devices) + len(protect) + len(access) + len(unas)

            if not devices:
                self.logout()
                return {
                    'success': False,
                    'message': (
                        f'Login succeeded but 0 network devices found for site ID "{self.site}". '
                        f'Available site IDs: {site_info}. '
                        f'Update the Site Name field with the ID (in quotes), not the display name.'
                    ),
                }
            self.logout()
            parts = [f'{len(devices)} network']
            if protect:
                parts.append(f'{len(protect)} camera/NVR')
            if access:
                parts.append(f'{len(access)} access control')
            if unas:
                parts.append(f'{len(unas)} NAS')
            return {
                'success': True,
                'device_count': total,
                'message': f'Connected — {total} devices ({", ".join(parts)}). Sites: {site_info}',
            }
        except Exception as exc:
            return {'success': False, 'message': str(exc)}


def load_unifi_config(Setting) -> dict | None:
    """Load UniFi credentials from the Setting table. Returns None if not configured."""
    def get(key):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s and s.value else ''

    from secret_store import decrypt_secret
    host = get('unifi_host')
    username = get('unifi_username')
    password = decrypt_secret(get('unifi_password'))
    site = get('unifi_site') or 'default'

    if not (host and username and password):
        return None
    return {'host': host, 'username': username, 'password': password, 'site': site}


def sync_unifi_assets(app_instance, db, Asset, Setting, AssetHistory, MonitoringAlert=None) -> dict:
    """
    Full sync: fetch all UniFi devices and upsert them as Asset records.

    Returns a summary dict: {synced, created, updated, errors, skipped}.
    """
    summary = {'synced': 0, 'created': 0, 'updated': 0, 'errors': 0, 'skipped': 0}

    with app_instance.app_context():
        try:
            config = load_unifi_config(Setting)
            if not config:
                logger.warning('UniFi sync skipped — credentials not configured')
                summary['skipped'] = 1
                _set_setting(db, Setting, 'unifi_last_sync_status', 'skipped')
                _set_setting(db, Setting, 'unifi_last_sync_message', 'Credentials not configured')
                _set_setting(db, Setting, 'unifi_last_sync_time', datetime.utcnow().isoformat())
                db.session.commit()
                return summary

            svc = UnifiService(**config)
            svc.login()
            devices = svc.get_devices()
            protect_devices = svc.get_protect_cameras()
            access_devices = svc.get_access_devices()
            unas_devices = svc.get_unas_devices()
            svc.logout()

            all_devices = devices + protect_devices + access_devices + unas_devices
            logger.info(
                'UniFi sync: fetched %d network + %d protect + %d access + %d UNAS = %d total',
                len(devices), len(protect_devices), len(access_devices), len(unas_devices), len(all_devices),
            )
            devices = all_devices

            now = datetime.utcnow()

            for dev in devices:
                try:
                    source: str = dev.get('_source', 'network')
                    mac: str = (dev.get('mac') or '').lower().strip()
                    name: str = (dev.get('name') or dev.get('model') or mac or 'Unknown UniFi Device').strip()
                    # Protect uses 'modelKey', network uses 'model'
                    model: str = dev.get('model') or dev.get('modelKey') or ''
                    # Protect uses 'host', network uses 'ip'
                    ip: str = dev.get('ip') or dev.get('host') or ''
                    # Protect uses 'firmwareVersion', network uses 'version'
                    firmware: str = dev.get('version') or dev.get('firmwareVersion') or ''
                    # Protect uses 'uptime', network uses 'uptime'
                    uptime_secs: int = dev.get('uptime') or 0
                    category, device_type = _classify_device(dev)
                    online_state = _online_state(dev)
                    # Protect uses 'id', network uses 'device_id' or '_id'
                    real_id: str = (dev.get('device_id') or dev.get('_id') or dev.get('id') or '').strip()
                    mac_norm: str = ''.join(c for c in mac if c in '0123456789abcdef')
                    unifi_id: str = real_id or mac  # identifier stored on the asset

                    if not mac and not unifi_id:
                        logger.warning('UniFi sync: skipping device with no MAC or ID: %s', name)
                        summary['skipped'] += 1
                        continue

                    # Find existing asset (priority order — stops duplicate creation):
                    #  1) a real UniFi device id (UNAS / older gear may not expose one)
                    #  2) normalized MAC — handles ':'-vs-'' formatting mismatches
                    #  3) name for network gear, which rotates its MAC and can change id
                    def _strip_mac(col):
                        return db.func.replace(db.func.replace(
                            db.func.lower(db.func.coalesce(col, '')), ':', ''), '-', '')
                    asset = None
                    if real_id:
                        asset = Asset.query.filter_by(unifi_device_id=real_id).first()
                    if not asset and mac_norm:
                        asset = Asset.query.filter(
                            (_strip_mac(Asset.hardware_mac_ethernet) == mac_norm) |
                            (_strip_mac(Asset.hardware_mac_wifi) == mac_norm)
                        ).first()
                    if not asset and name and category == 'Network Device':
                        asset = Asset.query.filter(
                            Asset.name == name, Asset.category == 'Network Device'
                        ).first()

                    if asset:
                        # Update existing
                        changes = []
                        prev_state = asset.online_state
                        if asset.online_state != online_state:
                            changes.append(f'online_state: {asset.online_state} → {online_state}')
                            asset.online_state = online_state
                            # Fire or resolve alert on state transition
                            if MonitoringAlert:
                                if online_state == 'Offline' and prev_state == 'Online':
                                    # Check for existing active alert first
                                    existing = MonitoringAlert.query.filter_by(
                                        asset_id=asset.id, status='active'
                                    ).filter(MonitoringAlert.message.like('%went offline%')).first()
                                    if not existing:
                                        alert = MonitoringAlert(
                                            asset_id=asset.id,
                                            severity='warning',
                                            status='active',
                                            message=f'{asset.name} went offline',
                                            details=f'UniFi device {device_type} lost connectivity',
                                            triggered_at=now,
                                            first_failed_at=now,
                                            last_failed_at=now,
                                        )
                                        db.session.add(alert)
                                        logger.warning('UniFi alert: %s went offline', asset.name)
                                elif online_state == 'Online' and prev_state == 'Offline':
                                    # Auto-resolve any open offline alerts
                                    open_alerts = MonitoringAlert.query.filter_by(
                                        asset_id=asset.id, status='active'
                                    ).filter(MonitoringAlert.message.like('%went offline%')).all()
                                    for a in open_alerts:
                                        a.status = 'resolved'
                                        a.resolved_at = now
                                    if open_alerts:
                                        logger.info('UniFi alert resolved: %s back online', asset.name)
                        # IP + firmware update SILENTLY (no history row, no updated_at
                        # bump). Gateways report both WAN and LAN addresses and the API
                        # returns a different one each poll, so logging IP changes spammed
                        # ~570 flip rows/day on the UDM (#336) -- the bulk of the entire
                        # asset-history table. Only real online/offline transitions
                        # (appended to `changes` above) are worth recording.
                        if asset.ip_address != ip and ip:
                            asset.ip_address = ip
                        if asset.os_version != firmware and firmware:
                            asset.os_version = firmware
                        if not asset.unifi_device_id:
                            asset.unifi_device_id = unifi_id
                        asset.unifi_last_seen = now
                        asset.unifi_uptime_secs = uptime_secs
                        # Only stamp updated_at + log history on a MEANINGFUL change
                        # (online/offline transition), not on every heartbeat poll.
                        if changes:
                            asset.updated_at = now
                            history = AssetHistory(
                                asset_id=asset.id,
                                action='unifi_sync',
                                description='; '.join(changes),
                                timestamp=now,
                            )
                            db.session.add(history)
                        summary['updated'] += 1
                    else:
                        # Create new asset
                        # Generate a unique asset tag
                        tag_base = f'UNF-{mac.replace(":", "").upper()[-8:]}'
                        asset_tag = tag_base
                        counter = 1
                        while Asset.query.filter_by(asset_tag=asset_tag).first():
                            asset_tag = f'{tag_base}-{counter}'
                            counter += 1

                        asset = Asset(
                            asset_tag=asset_tag,
                            name=name,
                            category=category,
                            device_type=device_type,
                            manufacturer='Ubiquiti',
                            model=model,
                            ip_address=ip,
                            os_version=firmware,
                            hardware_mac_ethernet=mac,
                            online_state=online_state,
                            status='In Use',
                            unifi_device_id=unifi_id,
                            unifi_last_seen=now,
                            unifi_uptime_secs=uptime_secs,
                            created_at=now,
                            updated_at=now,
                        )
                        db.session.add(asset)
                        db.session.flush()  # get asset.id

                        history = AssetHistory(
                            asset_id=asset.id,
                            action='created',
                            description=f'Auto-created by UniFi sync ({device_type})',
                            timestamp=now,
                        )
                        db.session.add(history)
                        summary['created'] += 1

                    summary['synced'] += 1

                except Exception as dev_err:
                    logger.warning('UniFi sync: error on device %s: %s', dev.get('name', '?'), dev_err)
                    summary['errors'] += 1

            db.session.commit()

            msg = (f"synced={summary['synced']} created={summary['created']} "
                   f"updated={summary['updated']} errors={summary['errors']}")
            _set_setting(db, Setting, 'unifi_last_sync_status', 'success')
            _set_setting(db, Setting, 'unifi_last_sync_message', msg)
            _set_setting(db, Setting, 'unifi_last_sync_time', now.isoformat())
            db.session.commit()
            logger.info('UniFi sync complete: %s', msg)

        except Exception as exc:
            try:
                db.session.rollback()
                _set_setting(db, Setting, 'unifi_last_sync_status', 'error')
                _set_setting(db, Setting, 'unifi_last_sync_message', str(exc))
                _set_setting(db, Setting, 'unifi_last_sync_time', datetime.utcnow().isoformat())
                db.session.commit()
            except Exception:
                pass
            logger.exception('UniFi sync crashed')
            summary['errors'] += 1

    return summary


def _set_setting(db, Setting, key: str, value: str) -> None:
    s = Setting.query.filter_by(key=key).first()
    if not s:
        s = Setting(key=key)
        db.session.add(s)
    s.value = value
    s.updated_at = datetime.utcnow()
