"""
Network / Rogue-Device page — the live LAN device inventory + one-click NAC.

Surfaces network_client (populated by network_scan) with unknown devices pinned
to the top, showing exactly where each is plugged in (switch+port / AP, VLAN) so
"shut it down" is physical, not guesswork. Actions:
  * block / unblock  — UniFi cmd/stamgr block-sta (instant L2 kill, reversible)
  * acknowledge      — sticky Tracker-side allowlist (stops it reading as rogue)
  * scan-now         — trigger an out-of-band reconcile

Admin-gated. All state writes go through the pg_db shim; UniFi enforcement calls
are approval-gated live-network writes routed through UnifiService.
"""
import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from pg_db import pg_connect
from utils import admin_required

logger = logging.getLogger(__name__)

bp = Blueprint('network', __name__)

# Order unknown → known_vendor → acknowledged → known_asset; online first.
_CLASS_ORDER = {
    'unknown': 0, 'known_vendor': 1, 'acknowledged': 2, 'known_asset': 3,
}


@bp.route('/network')
@login_required
@admin_required
def network_devices():
    con = pg_connect()
    try:
        rows = con.execute(
            """
            SELECT nc.*, a.name AS asset_name
            FROM network_client nc
            LEFT JOIN asset a ON a.id = nc.asset_id
            ORDER BY
                CASE WHEN nc.blocked THEN -1 ELSE 0 END,
                CASE nc.classification
                    WHEN 'unknown' THEN 0 WHEN 'known_vendor' THEN 1
                    WHEN 'acknowledged' THEN 2 ELSE 3 END,
                nc.online DESC,
                nc.last_seen DESC NULLS LAST
            """
        ).fetchall()

        from network_scan import _load_alert_networks
        alert_networks = _load_alert_networks(con)

        counts = {'unknown': 0, 'known_vendor': 0, 'acknowledged': 0,
                  'known_asset': 0, 'blocked': 0, 'online': 0, 'total': 0}
        for r in rows:
            counts['total'] += 1
            counts[r['classification']] = counts.get(r['classification'], 0) + 1
            if r['online']:
                counts['online'] += 1
            if r['blocked']:
                counts['blocked'] += 1
        return render_template('network_devices.html', rows=rows, counts=counts,
                               alert_networks=alert_networks)
    finally:
        con.close()


@bp.route('/network/map')
@login_required
@admin_required
def network_map():
    return render_template('network_map.html')


@bp.route('/network/map.json')
@login_required
@admin_required
def network_map_json():
    """Topology graph: infra devices (nodes) + uplink parents (edges), with
    per-device client + unknown counts from network_client."""
    from models import Setting
    from unifi_service import load_unifi_config, UnifiService

    cfg = load_unifi_config(Setting)
    if not cfg:
        return jsonify({'nodes': [], 'edges': [], 'error': 'UniFi not configured'}), 200
    svc = UnifiService(**cfg)
    try:
        svc.login()
        devices = svc.get_infra_topology()
    finally:
        try:
            svc.logout()
        except Exception:
            pass

    # Per-device client counts (match on uplink switch MAC or AP name).
    con = pg_connect()
    try:
        rows = con.execute(
            "SELECT LOWER(sw_mac) sw_mac, LOWER(ap_name) ap_name, classification, "
            "online FROM network_client").fetchall()
    finally:
        con.close()

    by_mac = {d['mac']: d for d in devices}
    name_to_mac = {d['name'].lower(): d['mac'] for d in devices}
    counts = {d['mac']: {'clients': 0, 'unknown': 0, 'online': 0} for d in devices}
    for r in rows:
        mac = r['sw_mac'] if r['sw_mac'] in by_mac else name_to_mac.get(r['ap_name'])
        if not mac or mac not in counts:
            continue
        counts[mac]['clients'] += 1
        if r['classification'] == 'unknown':
            counts[mac]['unknown'] += 1
        if r['online']:
            counts[mac]['online'] += 1

    nodes, edges = [], []
    for d in devices:
        c = counts[d['mac']]
        label = d['name']
        sub = f"{c['clients']} clients" + (f" · ⚠ {c['unknown']}" if c['unknown'] else '')
        nodes.append({
            'id': d['mac'], 'label': label, 'kind': d['kind'],
            'model': d['model'], 'clients': c['clients'], 'unknown': c['unknown'],
            'online': d['online'], 'sub': sub,
        })
        if d['uplink_mac'] and d['uplink_mac'] in by_mac:
            edges.append({'from': d['uplink_mac'], 'to': d['mac'],
                          'port': d['uplink_port']})

    stats = {
        'devices': len(devices),
        'switches': sum(1 for d in devices if d['kind'] == 'switch'),
        'aps': sum(1 for d in devices if d['kind'] == 'ap'),
        'gateways': sum(1 for d in devices if d['kind'] == 'gateway'),
        'total_unknown': sum(c['unknown'] for c in counts.values()),
    }
    return jsonify({'nodes': nodes, 'edges': edges, 'stats': stats})


def _get_client(con, client_id):
    return con.execute(
        "SELECT * FROM network_client WHERE id = ?", (client_id,)).fetchone()


@bp.route('/network/<int:client_id>/acknowledge', methods=['POST'])
@login_required
@admin_required
def acknowledge(client_id):
    data = request.get_json(silent=True) or {}
    note = (data.get('note') or request.form.get('note') or '')
    con = pg_connect()
    try:
        cl = _get_client(con, client_id)
        if not cl:
            return jsonify({'success': False, 'message': 'not found'}), 404
        con.execute(
            "UPDATE network_client SET acknowledged = TRUE, acknowledged_by = ?, "
            "acknowledged_at = NOW(), ack_note = ?, classification = 'acknowledged' "
            "WHERE id = ?", (current_user.id, note[:500], client_id))
        con.commit()
        logger.info('network: %s acknowledged client %s (%s)',
                    current_user.id, client_id, cl['mac'])
        return jsonify({'success': True})
    finally:
        con.close()


@bp.route('/network/<int:client_id>/unacknowledge', methods=['POST'])
@login_required
@admin_required
def unacknowledge(client_id):
    con = pg_connect()
    try:
        cl = _get_client(con, client_id)
        if not cl:
            return jsonify({'success': False, 'message': 'not found'}), 404
        # Drop back to unknown; the next scan re-classifies (may become known_asset).
        con.execute(
            "UPDATE network_client SET acknowledged = FALSE, acknowledged_by = NULL, "
            "acknowledged_at = NULL, classification = 'unknown', alerted_at = NULL "
            "WHERE id = ?", (client_id,))
        con.commit()
        return jsonify({'success': True})
    finally:
        con.close()


@bp.route('/network/<int:client_id>/block', methods=['POST'])
@login_required
@admin_required
def block(client_id):
    con = pg_connect()
    try:
        cl = _get_client(con, client_id)
        if not cl:
            return jsonify({'success': False, 'message': 'not found'}), 404
        result = _unifi_enforce('block', cl['mac'])
        if not result.get('success'):
            return jsonify({'success': False,
                            'message': f"UniFi block failed: {result.get('message')}"}), 502
        con.execute(
            "UPDATE network_client SET blocked = TRUE, blocked_by = ?, "
            "blocked_at = NOW() WHERE id = ?", (current_user.id, client_id))
        con.commit()
        logger.warning('network: %s BLOCKED client %s (%s)',
                       current_user.id, client_id, cl['mac'])
        return jsonify({'success': True})
    finally:
        con.close()


@bp.route('/network/<int:client_id>/unblock', methods=['POST'])
@login_required
@admin_required
def unblock(client_id):
    con = pg_connect()
    try:
        cl = _get_client(con, client_id)
        if not cl:
            return jsonify({'success': False, 'message': 'not found'}), 404
        result = _unifi_enforce('unblock', cl['mac'])
        if not result.get('success'):
            return jsonify({'success': False,
                            'message': f"UniFi unblock failed: {result.get('message')}"}), 502
        con.execute(
            "UPDATE network_client SET blocked = FALSE, blocked_by = NULL, "
            "blocked_at = NULL WHERE id = ?", (client_id,))
        con.commit()
        logger.warning('network: %s UNBLOCKED client %s (%s)',
                       current_user.id, client_id, cl['mac'])
        return jsonify({'success': True})
    finally:
        con.close()


@bp.route('/network/scan', methods=['POST'])
@login_required
@admin_required
def scan_now():
    try:
        import network_scan
        summary = network_scan.run_scan()
        return jsonify({'success': True, 'summary': summary})
    except Exception as e:
        logger.exception('network: manual scan failed')
        return jsonify({'success': False, 'message': str(e)}), 500


def _unifi_enforce(action, mac):
    """Route a block/unblock through UnifiService (a live-network write)."""
    from models import Setting
    from unifi_service import load_unifi_config, UnifiService
    cfg = load_unifi_config(Setting)
    if not cfg:
        return {'success': False, 'message': 'UniFi not configured'}
    svc = UnifiService(**cfg)
    try:
        svc.login()
        return (svc.block_client(mac) if action == 'block'
                else svc.unblock_client(mac))
    finally:
        try:
            svc.logout()
        except Exception:
            pass
