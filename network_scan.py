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


# ── Option 2 (fast-follow, NOT yet active): auto-quarantine on sensitive VLANs ──
# Chris approved building this after option 1 ships. When enabled, a *new* unknown
# appearing on a sensitive VLAN gets auto-blocked on sight (pending review) instead
# of only alerting. Everything below is inert until AUTO_QUARANTINE_ENABLED is set.
AUTO_QUARANTINE_ENABLED = False   # flip on (or read from Setting) when option 2 lands
SENSITIVE_VLANS = (10, 50)         # Servers (v10), IT (v50)


def _maybe_auto_quarantine(con, mac_norm, cl):
    """SCAFFOLD ONLY — not wired into run_scan yet. When option 2 is built, call
    this for each new unknown: if AUTO_QUARANTINE_ENABLED and the device is on a
    SENSITIVE_VLAN, block it immediately via UnifiService.block_client() and stamp
    network_client.blocked, then still alert (as a block notification). Left inert
    so nothing auto-blocks until this is deliberately turned on and reviewed."""
    if not AUTO_QUARANTINE_ENABLED:
        return False
    if cl.get('vlan') not in SENSITIVE_VLANS:
        return False
    # TODO(option-2): UnifiService.block_client(cl['mac']); UPDATE ... blocked=TRUE;
    #                 fire alert as an auto-block notice; audit-log the action.
    return False


def _mac_norm(mac: str) -> str:
    return ''.join(c for c in (mac or '').lower() if c in '0123456789abcdef')


def _epoch_to_ts(epoch):
    """UniFi ships unix epochs; return the int for to_timestamp() or None."""
    if not epoch:
        return None
    try:
        return int(epoch)
    except (TypeError, ValueError):
        return None


def _classify(mac_norm, oui_vendor, asset_by_mac, unifi_allow, acked):
    """Return (classification, asset_id)."""
    if mac_norm in acked:
        return 'acknowledged', asset_by_mac.get(mac_norm)
    if mac_norm in asset_by_mac:
        return 'known_asset', asset_by_mac[mac_norm]
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

        # Sticky Tracker-side allowlist we must preserve across scans. (blocked
        # state is orthogonal — a separate column, never re-derived — so it does
        # not feed classification.)
        acked = {row['mac_norm'] for row in con.execute(
            "SELECT mac_norm FROM network_client WHERE acknowledged = TRUE").fetchall()}

        pre_count = con.execute(
            "SELECT COUNT(*) AS n FROM network_client").fetchone()['n']
        baseline = pre_count == 0

        new_unknowns = []
        seen = 0
        for cl in clients:
            mn = _mac_norm(cl['mac'])
            if not mn:
                continue
            classification, asset_id = _classify(
                mn, cl['oui_vendor'], asset_by_mac, unifi_allow, acked)

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
            # Alert on an unknown that has never been alerted (alerted_at IS NULL).
            # NOT gated on insert-vs-update: a device that failed to alert before,
            # or was just un-acknowledged (which nulls alerted_at), re-qualifies.
            # The baseline branch below stamps alerted_at on the initial fleet so
            # the first populate doesn't flood — only genuinely-new MACs alert after.
            if classification == 'unknown' and res and res['alerted_at'] is None:
                new_unknowns.append((mn, cl))

        # Mark everything not seen this pass as offline (best-effort).
        con.execute("UPDATE network_client SET online = FALSE "
                    "WHERE mac_norm NOT IN (SELECT mac_norm FROM network_client "
                    "WHERE updated_at > NOW() - INTERVAL '2 minutes')")
        con.commit()

        alerted = 0
        if baseline:
            # Suppress the initial fleet: stamp alerted_at so the pre-existing
            # unknown backlog is surfaced on the page but never Teams-spammed.
            con.execute("UPDATE network_client SET alerted_at = NOW() "
                        "WHERE classification = 'unknown' AND alerted_at IS NULL")
            con.commit()
        elif new_unknowns:
            alerted = _alert_new_unknowns(con, new_unknowns)

        summary = {
            'clients_seen': seen,
            'known_asset_macs': len(asset_by_mac),
            'unifi_allowlist': len(unifi_allow),
            'new_unknowns': len(new_unknowns),
            'alerted': alerted,
            'baseline': baseline,
        }
        logger.info('network_scan: %s', summary)
        return summary
    finally:
        con.close()


def _alert_new_unknowns(con, new_unknowns):
    """Fire a rogue_device alert for each new unknown; stamp alerted_at."""
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
        msg = (f"{lead}: {cl['hostname'] or cl['mac']} ({vendor}) — "
               f"{cl['ip'] or 'no IP'} on VLAN {vlan} ({net}).")
        extra = (
            f'<p><strong>Connection:</strong> {"WIRED — " + where if cl["is_wired"] else where}<br>'
            f'<strong>MAC:</strong> {cl["mac"]}<br>'
            f'<strong>Vendor:</strong> {vendor}<br>'
            f'<strong>VLAN:</strong> {vlan} ({net})<br>'
            f'<strong>IP:</strong> {cl["ip"] or "—"}</p>'
            f'<p><a href="/network">Review & block on the Network page →</a></p>'
        )
        try:
            _fire_alert(con, rule, msg, hostname=(cl['hostname'] or cl['mac']),
                        extra_html=extra, dedup_token=mn)
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
