"""Proxmox VE / Backup Server API service.

Queries one or more PVE hosts (cluster + backup server) to collect:
  - Node status
  - ZFS pool health and usage
  - VM / LXC snapshot state (for backup freshness via cv4pve-autosnap)
  - Traditional vzdump backup tasks

Credentials are stored as Settings keys:
  proxmox_cluster_host          hostname or IP of any cluster node
  proxmox_cluster_port          default 8006
  proxmox_cluster_token_id      PVEAPIToken user, e.g. monitoring@pve!tracker
  proxmox_cluster_token_secret  UUID secret
  proxmox_cluster_verify_ssl    0 or 1 (default 0)
  proxmox_backup_host           (optional) standalone backup server hostname
  proxmox_backup_port           default 8006
  proxmox_backup_token_id
  proxmox_backup_token_secret
  proxmox_backup_verify_ssl     0 or 1 (default 0)
  proxmox_stale_hours           hours before a snapshot is deemed stale (default 26)
"""

import logging
import re
from datetime import datetime, timezone, timedelta

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Snapshot name prefixes created by cv4pve-autosnap / Sanoid
AUTO_SNAP_PREFIXES = ('auto_', 'autosnap_', 'auto-')


def _is_auto_snapshot(name: str) -> bool:
    return any(name.startswith(p) for p in AUTO_SNAP_PREFIXES)


def _parse_snap_time(name: str) -> datetime | None:
    """Try to parse a datetime from a cv4pve-autosnap/Sanoid snapshot name.

    Formats encountered:
      auto_daily-2025-10-28-0200         → cv4pve-autosnap VM disk
      autosnap_2025-10-29_00:00:03_daily → Sanoid pool
      auto-20251028-0200                 → older cv4pve
    """
    patterns = [
        # auto_daily-2025-10-28-0200
        r'(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})$',
        # autosnap_2025-10-29_00:00:03_daily
        r'(\d{4}-\d{2}-\d{2})_(\d{2}):(\d{2}):\d{2}',
        # auto-20251028-0200
        r'(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})$',
    ]
    for pat in patterns:
        m = re.search(pat, name)
        if m:
            g = m.groups()
            try:
                if len(g) == 3:
                    # YYYY-MM-DD, HH, MM
                    dt = datetime(int(g[0][:4]), int(g[0][5:7]), int(g[0][8:10]),
                                  int(g[1]), int(g[2]))
                elif len(g) == 5:
                    # YYYY, MM, DD, HH, MM
                    dt = datetime(int(g[0]), int(g[1]), int(g[2]),
                                  int(g[3]), int(g[4]))
                else:
                    continue
                return dt.replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                continue
    return None


class ProxmoxClient:
    """Thin wrapper around the PVE REST API using API token auth."""

    def __init__(self, host: str, port: int = 8006,
                 token_id: str = '', token_secret: str = '',
                 verify_ssl: bool = False, label: str = 'proxmox'):
        self.base_url = f"https://{host}:{port}/api2/json"
        self.headers = {'Authorization': f'PVEAPIToken={token_id}={token_secret}'}
        self.verify_ssl = verify_ssl
        self.label = label
        self._session = requests.Session()
        self._session.verify = verify_ssl

    def _get(self, path: str) -> list | dict:
        url = f"{self.base_url}{path}"
        r = self._session.get(url, headers=self.headers, timeout=15)
        r.raise_for_status()
        return r.json().get('data', [])

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------
    def get_nodes(self) -> list[dict]:
        """Return all nodes with status."""
        return self._get('/nodes') or []

    # ------------------------------------------------------------------
    # Storage (ZFS pools via storage list)
    # ------------------------------------------------------------------
    def get_storage_list(self) -> list[dict]:
        """Cluster-level storage configuration."""
        return self._get('/storage') or []

    def get_node_storage(self, node: str) -> list[dict]:
        """Per-node storage status (includes used/total for ZFS pools)."""
        return self._get(f'/nodes/{node}/storage') or []

    def get_node_zfs(self, node: str) -> list[dict]:
        """ZFS pool list from the hardware scan endpoint.
        Requires 'Sys.HWMap' or 'Datastore.Audit' on PVEAuditor — may 403."""
        try:
            return self._get(f'/nodes/{node}/disks/zfs') or []
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 404):
                logger.debug('%s: /disks/zfs not accessible on %s (%s)', self.label, node, e)
                return []
            raise

    # ------------------------------------------------------------------
    # VMs and containers
    # ------------------------------------------------------------------
    def get_qemu(self, node: str) -> list[dict]:
        vms = self._get(f'/nodes/{node}/qemu') or []
        for v in vms:
            v['_type'] = 'qemu'
            v['_node'] = node
        return vms

    def get_lxc(self, node: str) -> list[dict]:
        cts = self._get(f'/nodes/{node}/lxc') or []
        for c in cts:
            c['_type'] = 'lxc'
            c['_node'] = node
        return cts

    def get_all_vms(self, node: str) -> list[dict]:
        return self.get_qemu(node) + self.get_lxc(node)

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------
    def get_snapshots(self, node: str, vm_type: str, vmid: int) -> list[dict]:
        try:
            snaps = self._get(f'/nodes/{node}/{vm_type}/{vmid}/snapshot') or []
            return [s for s in snaps if s.get('name') != 'current']
        except requests.HTTPError:
            return []

    # ------------------------------------------------------------------
    # Tasks (vzdump backup logs)
    # ------------------------------------------------------------------
    def get_vzdump_tasks(self, node: str, limit: int = 100) -> list[dict]:
        try:
            return self._get(
                f'/nodes/{node}/tasks?typefilter=vzdump&limit={limit}'
            ) or []
        except requests.HTTPError:
            return []

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------
    def test_connection(self) -> dict:
        try:
            nodes = self.get_nodes()
            return {
                'success': True,
                'nodes': [n.get('node') for n in nodes],
                'node_count': len(nodes),
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}


# ---------------------------------------------------------------------------
# High-level sync
# ---------------------------------------------------------------------------

def _get_setting(Setting, key: str, default: str = '') -> str:
    row = Setting.query.filter_by(key=key).first()
    return (row.value or '').strip() if row else default


def _build_client(Setting, prefix: str, label: str) -> ProxmoxClient | None:
    host = _get_setting(Setting, f'proxmox_{prefix}_host')
    if not host:
        return None
    port = int(_get_setting(Setting, f'proxmox_{prefix}_port', '8006') or '8006')
    token_id = _get_setting(Setting, f'proxmox_{prefix}_token_id')
    token_secret = _get_setting(Setting, f'proxmox_{prefix}_token_secret')
    verify_ssl = _get_setting(Setting, f'proxmox_{prefix}_verify_ssl', '0') == '1'
    return ProxmoxClient(host, port, token_id, token_secret, verify_ssl, label)


def _upsert_zfs(db, ProxmoxZfsPool, server_label: str, node: str,
                pool_name: str, health: str, used_gb: float,
                total_gb: float, pct: float, frag: int):
    row = ProxmoxZfsPool.query.filter_by(
        server=server_label, node=node, pool_name=pool_name
    ).first()
    if row is None:
        row = ProxmoxZfsPool(server=server_label, node=node, pool_name=pool_name)
        db.session.add(row)
    row.health = health
    row.used_gb = used_gb
    row.total_gb = total_gb
    row.percent_used = pct
    row.fragmentation = frag
    row.last_synced = datetime.utcnow()


def _upsert_backup(db, ProxmoxBackupJob, node: str, vmid: int,
                   vm_name: str, vm_type: str, vm_status: str,
                   last_snapshot: str | None, last_snapshot_time: datetime | None,
                   snapshot_count: int, backup_status: str):
    row = ProxmoxBackupJob.query.filter_by(node=node, vmid=vmid).first()
    if row is None:
        row = ProxmoxBackupJob(node=node, vmid=vmid)
        db.session.add(row)
    row.vm_name = vm_name
    row.vm_type = vm_type
    row.vm_status = vm_status
    row.last_snapshot = last_snapshot
    row.last_snapshot_time = last_snapshot_time
    row.snapshot_count = snapshot_count
    row.backup_status = backup_status
    row.last_synced = datetime.utcnow()


def _fire_or_resolve_alert(db, MonitoringAlert, severity: str, key_msg: str,
                            details: str, resolve: bool = False):
    """Raise or resolve a Proxmox health alert.

    NOTE: MonitoringAlert requires a valid asset FK.  Proxmox alerts don't map
    to an asset row, so we skip firing them here.  Issues are surfaced via the
    Backups page badge counts and log warnings instead.
    """
    if resolve:
        return
    logger.warning('Proxmox alert: [%s] %s — %s', severity, key_msg, details)


def sync_proxmox(app_ctx, db, ProxmoxBackupJob, ProxmoxZfsPool, Setting,
                 MonitoringAlert=None):
    """Full sync of Proxmox cluster + backup server.

    Returns a summary dict with counts and errors.
    """
    summary = {
        'nodes_synced': 0,
        'pools_synced': 0,
        'vms_synced': 0,
        'alerts_fired': 0,
        'errors': [],
    }

    stale_hours = int(_get_setting(Setting, 'proxmox_stale_hours', '26') or '26')
    stale_threshold = timedelta(hours=stale_hours)
    now_utc = datetime.now(timezone.utc)

    def _sync_server(client: ProxmoxClient, is_backup_server: bool = False):
        label = client.label
        try:
            nodes = client.get_nodes()
        except Exception as e:
            summary['errors'].append(f"{label}: cannot get nodes: {e}")
            logger.error('%s: cannot get nodes: %s', label, e)
            return

        for node_info in nodes:
            node = node_info.get('node', '')
            if not node:
                continue
            summary['nodes_synced'] += 1

            # --- ZFS pools via storage list (more permissions-friendly) ---
            try:
                storage_rows = client.get_node_storage(node)
                for s in storage_rows:
                    if s.get('type') != 'zfspool':
                        continue
                    pool_name = s.get('storage', '')
                    total_b = s.get('total', 0) or 0
                    used_b = s.get('used', 0) or 0
                    total_gb = round(total_b / (1024 ** 3), 2) if total_b else 0.0
                    used_gb = round(used_b / (1024 ** 3), 2) if used_b else 0.0
                    pct = round(used_gb / total_gb * 100, 1) if total_gb else 0.0
                    health = s.get('status', 'UNKNOWN').upper()
                    if health == 'AVAILABLE':
                        health = 'ONLINE'
                    _upsert_zfs(db, ProxmoxZfsPool, label, node,
                                pool_name, health, used_gb, total_gb, pct, 0)
                    summary['pools_synced'] += 1

                    if MonitoringAlert:
                        # Degraded pool alert
                        pool_key = f"Proxmox {label}/{node}: ZFS pool '{pool_name}' is {health}"
                        if health not in ('ONLINE', 'AVAILABLE'):
                            _fire_or_resolve_alert(
                                db, MonitoringAlert, 'critical', pool_key,
                                f"Pool {pool_name} on {node} ({label}) health: {health}",
                                resolve=False)
                            summary['alerts_fired'] += 1
                        else:
                            _fire_or_resolve_alert(db, MonitoringAlert, 'critical',
                                                   pool_key, '', resolve=True)

                        # Capacity alert (>80%)
                        cap_key = f"Proxmox {label}/{node}: ZFS pool '{pool_name}' >80% full"
                        if pct >= 80:
                            _fire_or_resolve_alert(
                                db, MonitoringAlert, 'warning', cap_key,
                                f"Pool {pool_name} on {node} ({label}) is {pct}% used ({used_gb}GB/{total_gb}GB)",
                                resolve=False)
                            summary['alerts_fired'] += 1
                        else:
                            _fire_or_resolve_alert(db, MonitoringAlert, 'warning',
                                                   cap_key, '', resolve=True)
            except Exception as e:
                summary['errors'].append(f"{label}/{node}: storage error: {e}")
                logger.warning('%s/%s: storage error: %s', label, node, e)

            # --- VMs / LXC (skip for dedicated backup server) ---
            if is_backup_server:
                continue

            try:
                vms = client.get_all_vms(node)
            except Exception as e:
                summary['errors'].append(f"{label}/{node}: vm list error: {e}")
                logger.warning('%s/%s: vm list error: %s', label, node, e)
                continue

            for vm in vms:
                vmid = vm.get('vmid')
                vm_name = vm.get('name', f"vm-{vmid}")
                vm_type = vm.get('_type', 'qemu')
                vm_status = vm.get('status', 'unknown')

                try:
                    snaps = client.get_snapshots(node, vm_type, vmid)
                except Exception as e:
                    logger.debug('%s: snapshot fetch error vm %s: %s', label, vmid, e)
                    snaps = []

                auto_snaps = [s for s in snaps if _is_auto_snapshot(s.get('name', ''))]
                last_snap_name = None
                last_snap_time = None
                if auto_snaps:
                    # Find newest by parsed time; fall back to snaptime field
                    def snap_sort_key(s):
                        t = _parse_snap_time(s.get('name', ''))
                        if t:
                            return t
                        st = s.get('snaptime')
                        if st:
                            return datetime.fromtimestamp(st, tz=timezone.utc)
                        return datetime.min.replace(tzinfo=timezone.utc)

                    newest = max(auto_snaps, key=snap_sort_key)
                    last_snap_name = newest.get('name')
                    t = _parse_snap_time(last_snap_name or '')
                    if t:
                        last_snap_time = t
                    elif newest.get('snaptime'):
                        last_snap_time = datetime.fromtimestamp(
                            newest['snaptime'], tz=timezone.utc)

                if last_snap_time:
                    age = now_utc - last_snap_time
                    if age > stale_threshold:
                        bstatus = 'stale'
                    else:
                        bstatus = 'ok'
                elif auto_snaps:
                    bstatus = 'ok'  # has snaps but couldn't parse time
                else:
                    bstatus = 'missing'

                _upsert_backup(db, ProxmoxBackupJob, node, vmid, vm_name,
                               vm_type, vm_status,
                               last_snap_name, last_snap_time,
                               len(auto_snaps), bstatus)
                summary['vms_synced'] += 1

                if MonitoringAlert:
                    stale_key = f"Proxmox: {vm_name} (VM {vmid} on {node}) backup stale"
                    if bstatus in ('stale', 'missing'):
                        age_str = (f"{int(age.total_seconds() / 3600)}h ago"
                                   if last_snap_time else "never")
                        _fire_or_resolve_alert(
                            db, MonitoringAlert, 'warning', stale_key,
                            f"Last auto-snapshot: {last_snap_name or 'none'} ({age_str}). "
                            f"Threshold: {stale_hours}h",
                            resolve=False)
                        summary['alerts_fired'] += 1
                    else:
                        _fire_or_resolve_alert(db, MonitoringAlert, 'warning',
                                               stale_key, '', resolve=True)

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            summary['errors'].append(f"{label}: DB commit error: {e}")
            logger.error('%s: DB commit error: %s', label, e)

    # --- Cluster ---
    cluster_client = _build_client(Setting, 'cluster', 'Endor Cluster')
    if cluster_client:
        _sync_server(cluster_client, is_backup_server=False)
    else:
        logger.info('Proxmox cluster not configured — skipping')

    # --- Backup server ---
    backup_client = _build_client(Setting, 'backup', 'Bespin Backup')
    if backup_client:
        _sync_server(backup_client, is_backup_server=True)
    else:
        logger.info('Proxmox backup server not configured — skipping')

    return summary


# ---------------------------------------------------------------------------
# Connection test helper
# ---------------------------------------------------------------------------

def test_proxmox_connection(Setting, prefix: str = 'cluster') -> dict:
    """Quick connection test; returns dict with success/error/nodes."""
    host = _get_setting(Setting, f'proxmox_{prefix}_host')
    if not host:
        return {'success': False, 'error': f'proxmox_{prefix}_host not set'}
    try:
        client = _build_client(Setting, prefix, prefix)
        return client.test_connection()
    except Exception as e:
        return {'success': False, 'error': str(e)}
