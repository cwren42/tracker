"""
Rogue-device network scan — the reconcile brain behind the /network page.

Polls the UniFi controller's active clients (stat/sta), classifies each MAC
against known assets + the acknowledged-device allowlist, upserts one row per
MAC into network_client, and fires a `network` / `rogue_device` alert for each
genuinely-new unknown device (Teams push + optional ticket via alert_service).

Classification precedence (highest wins):
  acknowledged  — a sticky Tracker-side OK (a human clicked "Acknowledge")
  known_asset   — MAC matches asset.hardware_mac_ethernet / _wifi (hex-normalized)
  acknowledged  — named or fixed-IP entry in UniFi rest/user (auto-allowlist)
  known_vendor  — OUI is a known appliance vendor (small, conservative list)
  unknown       — none of the above  → rogue candidate, alerts + one-click block

Alerting is baseline-safe: the very first scan (empty table) populates + classifies
but does NOT alert, so triaging the existing fleet doesn't spam 100 tickets. After
baseline, only a newly-inserted unknown MAC raises an alert (alerted_at dedups).
"""
import logging

from pg_db import pg_connect

logger = logging.getLogger(__name__)

# Conservative appliance-vendor hints. Keep this SMALL and specific — generic NIC
# vendors (Intel/Realtek/etc.) must NOT land here or real rogue PCs get suppressed.
# The acknowledge workflow is the real noise-reducer; this is just a nicety.
INFRA_VENDOR_HINTS = (
    'ubiquiti', 'owl labs',
)

# Which UniFi networks raise rogue ALERTS. Everything is still inventoried on the
# /network page regardless — this only controls what pages you. Guest is noise by
# design (it exists to hold unknown personal devices); Lab is a sandbox; k8s/CHR/
# generic are infra. Overridable at runtime via Setting 'network_alert_scope'
# (comma-separated network names). Matched case-insensitively on network_name.
DEFAULT_ALERT_NETWORKS = frozenset({
    'servers', 'workstations', 'workstations wifi', 'it', 'default',
})


def _load_alert_networks(con):
    """Return the set of lowercased network names that raise alerts."""
    row = con.execute(
        "SELECT value FROM setting WHERE key = 'network_alert_scope'").fetchone()
    if row and (row['value'] or '').strip():
        return frozenset(n.strip().lower() for n in row['value'].split(',') if n.strip())
    return DEFAULT_ALERT_NETWORKS


# ── Auto-block (rogue quarantine) ──────────────────────────────────────────────
# When armed via Setting `nac_auto_block_enabled`, a *new* unknown appearing on any
# NON-GUEST network is blocked at the controller (UniFi block-sta — an instant,
# reversible L2 kill) the moment it's detected, in addition to alerting. Guest is
# NEVER blocked (visitors live there by design). Optional `nac_auto_block_until`
# (ISO-8601) closes the window automatically so a temporary lockdown can't linger.
def _is_guest_network(network_name):
    return 'guest' in (network_name or '').lower()


def _load_auto_block(con):
    """Return {'enabled': bool}. Reads Setting nac_auto_block_enabled; if
    nac_auto_block_until is set and already past, the window is treated as closed."""
    def _get(k):
        r = con.execute("SELECT value FROM setting WHERE key = ?", (k,)).fetchone()
        return (r['value'] if r and r['value'] is not None else '')
    enabled = _get('nac_auto_block_enabled').strip().lower() in ('1', 'true', 'yes', 'on')
    if enabled:
        until = _get('nac_auto_block_until').strip()
        if until:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(until.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > dt:
                    logger.info('network_scan: auto-block window closed (until=%s); not blocking', until)
                    enabled = False
            except Exception as e:
                logger.warning('network_scan: bad nac_auto_block_until %r: %s', until, e)
    return {'enabled': enabled}


def _auto_block_clients(con, to_block):
    """Block each (mac_norm, client) at the UniFi controller and stamp
    network_client.blocked. Opens its own UniFi session (the scan's is already
    logged out by here). Returns the set of mac_norms actually blocked. Never
    raises — a controller hiccup must not abort the scan."""
    from models import Setting
    from unifi_service import load_unifi_config, UnifiService
    cfg = load_unifi_config(Setting)
    if not cfg:
        return set()
    svc = UnifiService(**cfg)
    try:
        svc.login()
    except Exception as e:
        logger.warning('network_scan: auto-block UniFi login failed: %s', e)
        return set()
    blocked = set()
    try:
        for mn, cl in to_block:
            net = cl['network_name'] or 'unknown network'
            try:
                svc.block_client(cl['mac'])
                con.execute("SAVEPOINT ab_row")
                con.execute(
                    "UPDATE network_client SET blocked = TRUE, blocked_at = NOW(), "
                    "block_note = ? WHERE mac_norm = ?",
                    (f"auto-blocked (visitor lockdown): rogue on {net}", mn))
                con.execute("RELEASE SAVEPOINT ab_row")
                con.commit()
                blocked.add(mn)
                logger.warning(
                    'network_scan: AUTO-BLOCKED %s host=%s net=%s where=%s/%s',
                    cl['mac'], cl['hostname'], net, cl['ap_name'], cl['sw_port'])
            except Exception as e:
                try:
                    con.rollback()
                except Exception:
                    pass
                logger.warning('network_scan: auto-block FAILED for %s: %s', cl['mac'], e)
    finally:
        try:
            svc.logout()
        except Exception:
            pass
    return blocked


def _mac_norm(mac: str) -> str:
    return ''.join(c for c in (mac or '').lower() if c in '0123456789abcdef')


def _is_bogus_mac(mn: str) -> bool:
    """True for non-host MACs that must never classify/alert — L2 artifacts, not
    plugged-in devices. Filters: malformed length; multicast/broadcast (the I/G
    bit of the first octet, which also catches ff:ff:ff:ff:ff:ff, 01:00:5e:*,
    33:33:*, 01:80:c2:* STP); all-zero; and a device-half of all-F (e.g. the
    switch-learned phantom 00:00:ff:ff:ff:ff)."""
    if not mn or len(mn) != 12:
        return True
    try:
        if int(mn[:2], 16) & 1:          # I/G bit set → multicast/broadcast
            return True
    except ValueError:
        return True
    if mn == '000000000000':
        return True
    if mn[6:] == 'ffffff':               # device portion all-F → bogus
        return True
    return False


def _epoch_to_ts(epoch):
    """UniFi ships unix epochs; return the int for to_timestamp() or None."""
    if not epoch:
        return None
    try:
        return int(epoch)
    except (TypeError, ValueError):
        return None


def _host_key(hostname):
    """Normalize a hostname for matching: lowercase, drop domain suffix."""
    return (hostname or '').strip().lower().split('.')[0]


def _classify(mac_norm, hostname, oui_vendor, asset_by_mac, unifi_allow, acked, known_hosts):
    """Return (classification, asset_id).

    known_hosts maps a normalized hostname -> asset_id for every known asset name
    and every live RMM agent. Matching on hostname (not just MAC) means a known
    box is still recognized when its MAC drifts — a new dock/USB-NIC, a re-image,
    or a swapped adapter (e.g. ADMIN-CHARITY on a BizLink dock)."""
    if mac_norm in acked:
        return 'acknowledged', asset_by_mac.get(mac_norm)
    if mac_norm in asset_by_mac:
        return 'known_asset', asset_by_mac[mac_norm]
    hk = _host_key(hostname)
    if hk and hk in known_hosts:
        return 'known_asset', known_hosts[hk]
    if mac_norm in unifi_allow:
        return 'acknowledged', None
    vlow = (oui_vendor or '').lower()
    if vlow and any(h in vlow for h in INFRA_VENDOR_HINTS):
        return 'known_vendor', None
    return 'unknown', None


def run_scan(flask_app=None):
    """Poll UniFi, reconcile, upsert, and alert on new unknowns.

    Returns a summary dict. Designed to be called from the scheduler (already
    inside an app context there) or standalone.
    """
    from models import Setting
    from unifi_service import load_unifi_config, UnifiService

    cfg = load_unifi_config(Setting)
    if not cfg:
        logger.info('network_scan: UniFi not configured; skipping')
        return {'skipped': 'unifi-not-configured'}

    svc = UnifiService(**cfg)
    svc.login()
    try:
        clients = svc.get_active_clients()
        users = svc.get_known_users()
    finally:
        try:
            svc.logout()
        except Exception:
            pass

    # UniFi-side allowlist: any rest/user MAC with a friendly name or fixed IP.
    unifi_allow = set()
    for u in users:
        if (u.get('name') or '').strip() or u.get('use_fixedip'):
            mn = _mac_norm(u.get('mac'))
            if mn:
                unifi_allow.add(mn)

    con = pg_connect()
    try:
        # Known-asset MAC → asset_id map (both NIC columns, hex-normalized).
        asset_by_mac = {}
        rows = con.execute(
            "SELECT id, hardware_mac_ethernet, hardware_mac_wifi FROM asset "
            "WHERE hardware_mac_ethernet IS NOT NULL OR hardware_mac_wifi IS NOT NULL"
        ).fetchall()
        for r in rows:
            for col in ('hardware_mac_ethernet', 'hardware_mac_wifi'):
                mn = _mac_norm(r[col])
                if mn:
                    asset_by_mac.setdefault(mn, r['id'])

        # Known-hostname -> asset_id map: asset names + live RMM agent hostnames.
        # Lets us recognize a known box by hostname when its MAC has drifted.
        known_hosts = {}
        for r in con.execute("SELECT id, name FROM asset WHERE name IS NOT NULL AND name <> ''").fetchall():
            hk = _host_key(r['name'])
            if hk:
                known_hosts.setdefault(hk, r['id'])
        for r in con.execute("SELECT agent_id, asset_id FROM rmm_agent WHERE enabled = TRUE AND asset_id IS NOT NULL").fetchall():
            hk = _host_key(r['agent_id'])
            if hk:
                known_hosts.setdefault(hk, r['asset_id'])

        # Sticky Tracker-side allowlist we must preserve across scans. (blocked
        # state is orthogonal — a separate column, never re-derived — so it does
        # not feed classification.)
        acked = {row['mac_norm'] for row in con.execute(
            "SELECT mac_norm FROM network_client WHERE acknowledged = TRUE").fetchall()}

        alert_networks = _load_alert_networks(con)
        auto_block = _load_auto_block(con)

        # Self-baseline on arm transition (OFF->ON): stamp the current unknown
        # backlog so only NEW arrivals block. Arming widens scope to all-non-guest
        # and would otherwise mass-block devices that were merely out of the prior
        # alert scope. Makes the /settings toggle safe however it's flipped.
        _la = con.execute("SELECT value FROM setting WHERE key = 'nac_auto_block_last_armed'").fetchone()
        _was_armed = bool(_la and str(_la['value'] or '').strip().lower() in ('1', 'true', 'yes', 'on'))
        if auto_block['enabled'] and not _was_armed:
            con.execute("UPDATE network_client SET alerted_at = NOW() "
                        "WHERE classification = 'unknown' AND alerted_at IS NULL")
            con.execute("INSERT INTO setting(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
                        ('nac_auto_block_last_armed', 'true'))
            con.commit()
            logger.info('network_scan: auto-block ARMED via toggle/setting — baselined existing unknown backlog')
        elif (not auto_block['enabled']) and _was_armed:
            con.execute("INSERT INTO setting(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
                        ('nac_auto_block_last_armed', 'false'))
            con.commit()

        pre_count = con.execute(
            "SELECT COUNT(*) AS n FROM network_client").fetchone()['n']
        baseline = pre_count == 0

        new_unknowns = []
        seen = 0
        for cl in clients:
            mn = _mac_norm(cl['mac'])
            if not mn or _is_bogus_mac(mn):
                continue  # skip broadcast/multicast/all-F L2 artifacts (not hosts)
            classification, asset_id = _classify(
                mn, cl['hostname'], cl['oui_vendor'], asset_by_mac, unifi_allow, acked, known_hosts)

            # Per-row savepoint: a single malformed UniFi record can't abort the
            # whole scan (an aborted txn would fail every later upsert). Sticky
            # columns (acknowledged/blocked/*_note/alerted_at/first_seen/
            # first_detected_at) are intentionally NOT overwritten on conflict.
            con.execute("SAVEPOINT nc_row")
            try:
                res = con.execute(
                    """
                    INSERT INTO network_client
                        (mac, mac_norm, ip, hostname, oui_vendor, is_wired, vlan,
                         network_name, sw_mac, sw_port, ap_name, classification,
                         asset_id, online, first_seen, last_seen,
                         first_detected_at, updated_at)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE,
                         to_timestamp(?), to_timestamp(?), NOW(), NOW())
                    ON CONFLICT (mac_norm) DO UPDATE SET
                        mac = EXCLUDED.mac,
                        ip = EXCLUDED.ip,
                        hostname = EXCLUDED.hostname,
                        oui_vendor = EXCLUDED.oui_vendor,
                        is_wired = EXCLUDED.is_wired,
                        vlan = EXCLUDED.vlan,
                        network_name = EXCLUDED.network_name,
                        sw_mac = EXCLUDED.sw_mac,
                        sw_port = EXCLUDED.sw_port,
                        ap_name = EXCLUDED.ap_name,
                        classification = EXCLUDED.classification,
                        asset_id = EXCLUDED.asset_id,
                        online = TRUE,
                        last_seen = EXCLUDED.last_seen,
                        updated_at = NOW()
                    RETURNING alerted_at
                    """,
                    (cl['mac'], mn, cl['ip'] or None, cl['hostname'] or None,
                     cl['oui_vendor'] or None, cl['is_wired'],
                     cl['vlan'], cl['network_name'] or None,
                     cl['sw_mac'] or None, cl['sw_port'], cl['ap_name'] or None,
                     classification, asset_id,
                     _epoch_to_ts(cl['first_seen']),
                     _epoch_to_ts(cl['last_seen'])),
                ).fetchone()
                con.execute("RELEASE SAVEPOINT nc_row")
            except Exception as e:
                con.execute("ROLLBACK TO SAVEPOINT nc_row")
                logger.warning('network_scan: upsert failed for %s: %s', mn, e)
                continue

            seen += 1
            # Alert on an unknown that has never been alerted (alerted_at IS NULL)
            # AND is on an in-scope network. NOT gated on insert-vs-update: a device
            # that failed to alert before, or was just un-acknowledged (which nulls
            # alerted_at), re-qualifies. The baseline branch below stamps alerted_at
            # on the initial fleet so the first populate doesn't flood — only
            # genuinely-new MACs on monitored networks alert after. Out-of-scope
            # networks (Guest/Lab/infra) are inventoried on the page but never page.
            # When auto-block is armed, alert scope widens to EVERY non-guest
            # network (anything we might block must also be surfaced); otherwise
            # the configured alert_networks set governs. Guest is never in scope.
            in_scope = (cl['network_name'] or '').strip().lower() in alert_networks
            if auto_block['enabled'] and not _is_guest_network(cl['network_name']):
                in_scope = True
            if (classification == 'unknown' and res
                    and res['alerted_at'] is None and in_scope):
                new_unknowns.append((mn, cl))

        # Mark everything not seen this pass as offline (best-effort).
        con.execute("UPDATE network_client SET online = FALSE "
                    "WHERE mac_norm NOT IN (SELECT mac_norm FROM network_client "
                    "WHERE updated_at > NOW() - INTERVAL '2 minutes')")
        con.commit()

        # Auto-block new unknowns on non-guest networks (if armed), BEFORE alerting
        # so the alert can announce the block. Enforcement is independent of the
        # alert cooldown — every rogue is blocked even if its email is throttled.
        blocked_macs = set()
        if auto_block['enabled'] and not baseline and new_unknowns:
            to_block = [(mn, cl) for mn, cl in new_unknowns
                        if not _is_guest_network(cl['network_name'])]
            if to_block:
                blocked_macs = _auto_block_clients(con, to_block)

        alerted = 0
        if baseline:
            # Suppress the initial fleet: stamp alerted_at so the pre-existing
            # unknown backlog is surfaced on the page but never Teams-spammed.
            con.execute("UPDATE network_client SET alerted_at = NOW() "
                        "WHERE classification = 'unknown' AND alerted_at IS NULL")
            con.commit()
        elif new_unknowns:
            alerted = _alert_new_unknowns(con, new_unknowns, blocked_macs)

        summary = {
            'clients_seen': seen,
            'known_asset_macs': len(asset_by_mac),
            'unifi_allowlist': len(unifi_allow),
            'new_unknowns': len(new_unknowns),
            'auto_blocked': len(blocked_macs),
            'alerted': alerted,
            'baseline': baseline,
        }
        logger.info('network_scan: %s', summary)
        return summary
    finally:
        con.close()


def _alert_new_unknowns(con, new_unknowns, blocked_macs=None):
    """Fire a rogue_device alert for each new unknown; stamp alerted_at. MACs in
    blocked_macs were just auto-blocked, so their alert announces the block."""
    blocked_macs = blocked_macs or set()
    try:
        from alert_service import _fire_alert
    except Exception as e:
        logger.warning('network_scan: alert_service unavailable: %s', e)
        return 0

    rule = con.execute(
        "SELECT * FROM alert_rule WHERE category = 'network' "
        "AND alert_type = 'rogue_device'").fetchone()
    if not rule or not rule['enabled']:
        logger.info('network_scan: rogue_device rule missing/disabled; not alerting')
        return 0

    fired = 0
    for mn, cl in new_unknowns:
        vendor = cl['oui_vendor'] or 'unknown vendor'
        vlan = cl['vlan'] if cl['vlan'] is not None else 'untagged'
        net = cl['network_name'] or 'unknown network'
        # For wired clients `ap_name` is the uplink SWITCH's friendly name, so the
        # switch-name + port pinpoints the physical wall jack. Lead with WIRED —
        # a device can't land on wired without someone physically plugging in.
        if cl['is_wired']:
            jack = cl['ap_name'] or 'switch'
            port = cl['sw_port'] if cl['sw_port'] is not None else '?'
            where = f"{jack} port {port}"
            lead = f"🔌 WIRED unknown device plugged into {where}"
        else:
            where = f"Wi-Fi via {cl['ap_name'] or 'AP'}"
            lead = f"📶 Unknown Wi-Fi device on {cl['ap_name'] or 'the wireless'}"
        was_blocked = mn in blocked_macs
        if was_blocked:
            lead = "🚫 AUTO-BLOCKED — " + lead
        msg = (f"{lead}: {cl['hostname'] or cl['mac']} ({vendor}) — "
               f"{cl['ip'] or 'no IP'} on VLAN {vlan} ({net}).")
        action_html = (
            '<p><strong>Action:</strong> 🚫 Auto-blocked at the controller '
            '(visitor lockdown). <a href="/network">Review / unblock →</a></p>'
            if was_blocked else
            '<p><a href="/network">Review &amp; block on the Network page →</a></p>'
        )
        extra = (
            f'<p><strong>Connection:</strong> {"WIRED — " + where if cl["is_wired"] else where}<br>'
            f'<strong>MAC:</strong> {cl["mac"]}<br>'
            f'<strong>Vendor:</strong> {vendor}<br>'
            f'<strong>VLAN:</strong> {vlan} ({net})<br>'
            f'<strong>IP:</strong> {cl["ip"] or "—"}</p>'
            f'{action_html}'
        )
        try:
            # Per-device keying: agent_id=MAC + cooldown_key=MAC so distinct
            # rogues each alert instead of collapsing onto the shared
            # (asset_id=0) cooldown bucket. asset_id stays NULL — support_ticket
            # has an FK to asset, so a synthetic id there would abort the insert.
            _fire_alert(con, rule, msg, agent_id=mn, asset_id=None,
                        hostname=(cl['hostname'] or cl['mac']),
                        extra_html=extra, dedup_token=mn, cooldown_key=mn)
            con.execute("UPDATE network_client SET alerted_at = NOW() "
                        "WHERE mac_norm = ?", (mn,))
            con.commit()
            fired += 1
        except Exception as e:
            # Roll back the aborted txn so the connection stays usable for the
            # rest of the batch (pg_db requires this); alerted_at stays NULL so
            # this device re-qualifies on the next scan (no permanent silence).
            try:
                con.rollback()
            except Exception:
                pass
            logger.warning('network_scan: alert fire failed for %s: %s', mn, e)
    return fired


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    from app import app
    with app.app_context():
        print(run_scan(app))
