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

from secret_store import decrypt_secret

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


class PbsClient:
    """Thin wrapper around the Proxmox Backup Server REST API.

    PBS speaks a DIFFERENT API than PVE: port 8007, and token auth uses the
    ``PBSAPIToken=<id>:<secret>`` header (note the ``:`` separator, vs PVE's
    ``=``). Backups are organised as one *group* per guest
    (``backup-type`` vm|ct + ``backup-id`` = vmid), each holding N snapshots.
    """

    def __init__(self, host: str, port: int = 8007,
                 token_id: str = '', token_secret: str = '',
                 verify_ssl: bool = False, datastore: str = 'main',
                 label: str = 'PBS'):
        self.base_url = f"https://{host}:{port}/api2/json"
        self.headers = {'Authorization': f'PBSAPIToken={token_id}:{token_secret}'}
        self.datastore = datastore
        self.verify_ssl = verify_ssl
        self.label = label
        self._session = requests.Session()
        self._session.verify = verify_ssl

    def _get(self, path: str) -> list | dict:
        url = f"{self.base_url}{path}"
        r = self._session.get(url, headers=self.headers, timeout=30)
        r.raise_for_status()
        return r.json().get('data', [])

    def get_groups(self) -> list[dict]:
        """One entry per guest: backup-type, backup-id, backup-count, last-backup."""
        return self._get(f'/admin/datastore/{self.datastore}/groups') or []

    def get_snapshots(self) -> list[dict]:
        """Every snapshot in the datastore (carries the per-backup ``comment``)."""
        return self._get(f'/admin/datastore/{self.datastore}/snapshots') or []

    def get_datastore_status(self) -> dict:
        """Datastore capacity: {total, used, avail} bytes (+ gc info if present)."""
        try:
            d = self._get(f'/admin/datastore/{self.datastore}/status')
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def test_connection(self) -> dict:
        try:
            groups = self.get_groups()
            return {
                'success': True,
                'datastore': self.datastore,
                'group_count': len(groups),
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
    token_id = _get_setting(Setting, f'proxmox_{prefix}_token_id')
    token_secret = _get_setting(Setting, f'proxmox_{prefix}_token_secret')
    # Without a PVEAPIToken this client can't authenticate — skip it. This also
    # keeps the PVE 'backup' path from firing against a host that is actually a
    # PBS server (port 8007, PBSAPIToken) configured via proxmox_backup_host +
    # the proxmox_pbs_* keys, which has no proxmox_backup_token_id.
    if not token_id:
        return None
    port = int(_get_setting(Setting, f'proxmox_{prefix}_port', '8006') or '8006')
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
# PBS (Proxmox Backup Server) sync
# ---------------------------------------------------------------------------

def _build_pbs_client(Setting) -> PbsClient | None:
    """Build the PBS client from Settings, or None if not configured.

    Reuses ``proxmox_backup_host`` for the host and adds PBS-specific keys:
    ``proxmox_pbs_token_id``, ``proxmox_pbs_token_secret`` (Fernet-encrypted at
    rest), ``proxmox_pbs_port`` (default 8007), ``proxmox_pbs_datastore``
    (default 'main'), ``proxmox_pbs_verify_ssl`` (default 0).
    """
    host = _get_setting(Setting, 'proxmox_backup_host')
    token_id = _get_setting(Setting, 'proxmox_pbs_token_id')
    token_secret = decrypt_secret(_get_setting(Setting, 'proxmox_pbs_token_secret'))
    if not host or not token_id or not token_secret:
        return None
    port = int(_get_setting(Setting, 'proxmox_pbs_port', '8007') or '8007')
    datastore = _get_setting(Setting, 'proxmox_pbs_datastore', 'main') or 'main'
    verify_ssl = _get_setting(Setting, 'proxmox_pbs_verify_ssl', '0') == '1'
    return PbsClient(host, port, token_id, token_secret, verify_ssl, datastore, 'PBS')


def sync_pbs(app_ctx, db, ProxmoxBackupJob, Setting, MonitoringAlert=None):
    """Sync PBS backup freshness into ``proxmox_backup_job`` (node='pbs').

    Reads the datastore's backup groups (per-guest latest backup + snapshot
    count) plus the snapshot list (for the guest name from each snapshot's
    ``comment``), and upserts one row per guest. ``backup_status`` is 'ok' when
    the newest backup is within ``proxmox_stale_hours`` (default 26h), 'stale'
    when older, and 'missing' when the guest has no snapshots. Fires a *warning*
    (never a critical) per stale/missing guest so intentionally-unbacked guests
    can't page.
    """
    summary = {
        'guests_synced': 0,
        'stale': 0,
        'missing': 0,
        'alerts_fired': 0,
        'errors': [],
    }

    client = _build_pbs_client(Setting)
    if client is None:
        logger.info('PBS not configured — skipping')
        return summary

    stale_hours = int(_get_setting(Setting, 'proxmox_stale_hours', '26') or '26')
    stale_threshold = timedelta(hours=stale_hours)
    now_utc = datetime.now(timezone.utc)

    try:
        groups = client.get_groups()
    except Exception as e:
        summary['errors'].append(f"PBS: cannot list groups: {e}")
        logger.error('PBS: cannot list groups: %s', e)
        return summary

    # Best-effort {(type, id): guest-name} from the latest snapshot's comment.
    name_map = {}
    try:
        latest = {}
        for s in client.get_snapshots():
            key = (s.get('backup-type'), str(s.get('backup-id')))
            bt = s.get('backup-time') or 0
            if key not in latest or bt > latest[key][0]:
                latest[key] = (bt, (s.get('comment') or '').strip())
        name_map = {k: v[1] for k, v in latest.items()}
    except Exception as e:
        logger.warning('PBS: snapshot list for names failed: %s', e)

    for g in groups:
        vm_type = g.get('backup-type', '')          # 'vm' | 'ct'
        vmid_raw = str(g.get('backup-id', ''))
        if vm_type not in ('vm', 'ct') or not vmid_raw:
            continue
        try:
            vmid = int(vmid_raw)
        except ValueError:
            continue

        count = int(g.get('backup-count', 0) or 0)
        last_ts = int(g.get('last-backup', 0) or 0)
        last_snap_time = (datetime.fromtimestamp(last_ts, tz=timezone.utc)
                          if last_ts > 0 else None)

        comment = name_map.get((vm_type, vmid_raw), '')
        vm_name = comment if comment else f"vm-{vmid}"

        if count <= 0 or last_snap_time is None:
            bstatus = 'missing'
        elif (now_utc - last_snap_time) > stale_threshold:
            bstatus = 'stale'
        else:
            bstatus = 'ok'

        last_snap_name = (last_snap_time.strftime('%Y-%m-%dT%H:%M:%SZ')
                          if last_snap_time else None)

        _upsert_backup(db, ProxmoxBackupJob, 'pbs', vmid, vm_name, vm_type,
                       '', last_snap_name, last_snap_time, count, bstatus)
        summary['guests_synced'] += 1
        if bstatus == 'stale':
            summary['stale'] += 1
        elif bstatus == 'missing':
            summary['missing'] += 1

        if MonitoringAlert:
            stale_key = f"PBS: {vm_name} ({vm_type} {vmid}) backup {bstatus}"
            if bstatus in ('stale', 'missing'):
                age_str = (f"{int((now_utc - last_snap_time).total_seconds() / 3600)}h ago"
                           if last_snap_time else "never")
                _fire_or_resolve_alert(
                    db, MonitoringAlert, 'warning', stale_key,
                    f"Last PBS backup: {last_snap_name or 'none'} ({age_str}). "
                    f"Threshold: {stale_hours}h", resolve=False)
                summary['alerts_fired'] += 1
            else:
                _fire_or_resolve_alert(db, MonitoringAlert, 'warning',
                                       stale_key, '', resolve=True)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        summary['errors'].append(f"PBS: DB commit error: {e}")
        logger.error('PBS: DB commit error: %s', e)

    return summary


# ---------------------------------------------------------------------------
# Connection test helper
# ---------------------------------------------------------------------------

def test_proxmox_connection(Setting, prefix: str = 'cluster') -> dict:
    """Quick connection test; returns dict with success/error/nodes.

    prefix='pbs' tests the Proxmox Backup Server (port 8007) instead of a PVE host.
    """
    if prefix == 'pbs':
        client = _build_pbs_client(Setting)
        if client is None:
            return {'success': False, 'error': 'PBS not configured (host/token missing)'}
        return client.test_connection()
    host = _get_setting(Setting, f'proxmox_{prefix}_host')
    if not host:
        return {'success': False, 'error': f'proxmox_{prefix}_host not set'}
    try:
        client = _build_client(Setting, prefix, prefix)
        if client is None:
            return {'success': False, 'error': f'proxmox_{prefix}_token_id not set'}
        return client.test_connection()
    except Exception as e:
        return {'success': False, 'error': str(e)}


def _fmt_age(hours):
    if hours is None:
        return 'never'
    if hours < 1:
        return f'{int(hours * 60)}m ago'
    if hours < 48:
        return f'{hours:.0f}h ago'
    return f'{hours / 24:.1f}d ago'


def send_pbs_daily_report(app_ctx, db, ProxmoxBackupJob, Setting, recipients=None) -> dict:
    """Build and email a daily PBS backup summary — guests protected, capacity,
    and any stale/missing — intended to run each morning after the overnight run.
    Reads the already-synced ``proxmox_backup_job`` rows (node='pbs'); the caller
    should refresh them via ``sync_pbs`` first so the report reflects last night."""
    from utils import send_email

    summary = {'total': 0, 'ok': 0, 'issues': 0, 'sent': False, 'recipients': []}
    now = datetime.utcnow()

    if not recipients:
        raw = _get_setting(Setting, 'pbs_report_recipients', '') or ''
        recipients = [e.strip() for e in raw.replace(';', ',').split(',') if e.strip()] or ['cwren@cirque.com']
    summary['recipients'] = recipients

    rows = db.session.execute(db.text("""
        SELECT vmid, vm_name, vm_type, backup_status, last_snapshot_time, snapshot_count
        FROM proxmox_backup_job
        WHERE node = 'pbs'
        ORDER BY lower(coalesce(vm_name, '')), vmid
    """)).fetchall()

    def age_h(ts):
        return (now - ts).total_seconds() / 3600.0 if ts else None

    ok      = [r for r in rows if (r.backup_status or '') == 'ok']
    stale   = [r for r in rows if (r.backup_status or '') == 'stale']
    missing = [r for r in rows if (r.backup_status or '') == 'missing']
    issues  = stale + missing
    total   = len(rows)
    total_snaps = sum((r.snapshot_count or 0) for r in rows)
    summary.update(total=total, ok=len(ok), issues=len(issues))

    # Datastore capacity (live from PBS)
    cap_html = ''
    try:
        client = _build_pbs_client(Setting)
        st = client.get_datastore_status() if client else {}
        tot, used, avail = st.get('total') or 0, st.get('used') or 0, st.get('avail') or 0
        if tot:
            tb = lambda b: f"{b / (1024 ** 4):.2f} TB"
            pct = round(used / tot * 100, 1)
            bar_col = '#dc3545' if pct >= 90 else ('#f0ad4e' if pct >= 75 else '#4caf50')
            cap_html = (
                f'<div style="margin:6px 0 2px;font-weight:600;">Datastore capacity</div>'
                f'<div style="background:#e9ecef;border-radius:6px;height:16px;width:100%;overflow:hidden;">'
                f'<div style="background:{bar_col};height:16px;width:{min(pct,100)}%;"></div></div>'
                f'<div style="color:#555;font-size:13px;margin-top:3px;">'
                f'{tb(used)} used of {tb(tot)} ({pct}%) &middot; {tb(avail)} free</div>'
            )
    except Exception as e:
        logger.warning('PBS report: datastore status failed: %s', e)

    # Guest table
    def badge(status):
        c = {'ok': '#4caf50', 'stale': '#f0ad4e', 'missing': '#dc3545'}.get(status, '#888')
        return (f'<span style="background:{c};color:#fff;padding:1px 8px;border-radius:10px;'
                f'font-size:12px;">{status or "?"}</span>')

    def guest_rows(rs):
        out = []
        for r in rs:
            nm = (r.vm_name or f'vmid {r.vmid}')
            a = age_h(r.last_snapshot_time)
            out.append(
                f'<tr>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{nm}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;text-transform:uppercase;color:#666;font-size:12px;">{r.vm_type or ""}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{_fmt_age(a)}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center;">{r.snapshot_count or 0}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center;">{badge(r.backup_status)}</td>'
                f'</tr>'
            )
        return ''.join(out)

    all_ok = not issues
    hdr_col = '#4caf50' if all_ok else '#f0ad4e'
    hdr_txt = ('✅ All guests protected' if all_ok
               else f'⚠️ {len(issues)} guest(s) need attention')
    subject = (f"✅ PBS Backup Report — {now.strftime('%b %d')} — all {total} guests protected"
               if all_ok else
               f"⚠️ PBS Backup Report — {now.strftime('%b %d')} — {len(issues)} issue(s), {len(ok)}/{total} protected")

    issues_block = ''
    if issues:
        issues_block = (
            '<div style="background:#fff8 e1;border-left:4px solid #f0ad4e;padding:10px 14px;margin:14px 0;border-radius:4px;">'
            '<b>Needs attention:</b><table style="width:100%;border-collapse:collapse;margin-top:6px;">'
            '<tr style="text-align:left;color:#666;font-size:12px;"><th style="padding:4px 10px;">Guest</th>'
            '<th style="padding:4px 10px;">Type</th><th style="padding:4px 10px;">Last backup</th>'
            '<th style="padding:4px 10px;text-align:center;">Snaps</th><th style="padding:4px 10px;text-align:center;">Status</th></tr>'
            + guest_rows(issues) + '</table></div>'
        ).replace('#fff8 e1', '#fff8e1')

    html = f"""<html><body style="font-family:Arial,Helvetica,sans-serif;color:#222;max-width:760px;margin:0 auto;">
      <div style="border-top:5px solid {hdr_col};padding:16px 20px;background:#fafafa;">
        <h2 style="margin:0 0 4px;">Proxmox Backup Server — Nightly Report</h2>
        <div style="color:#666;">{now.strftime('%A, %B %d, %Y')} &middot; overnight run (03:00)</div>
        <div style="font-size:18px;font-weight:600;color:{hdr_col};margin-top:10px;">{hdr_txt}</div>
      </div>
      <div style="padding:8px 20px;">
        <table style="width:100%;margin:14px 0;text-align:center;border-collapse:collapse;">
          <tr>
            <td style="padding:10px;"><div style="font-size:26px;font-weight:700;">{total}</div><div style="color:#666;font-size:12px;">GUESTS</div></td>
            <td style="padding:10px;"><div style="font-size:26px;font-weight:700;color:#4caf50;">{len(ok)}</div><div style="color:#666;font-size:12px;">PROTECTED</div></td>
            <td style="padding:10px;"><div style="font-size:26px;font-weight:700;color:{'#dc3545' if issues else '#4caf50'};">{len(issues)}</div><div style="color:#666;font-size:12px;">ISSUES</div></td>
            <td style="padding:10px;"><div style="font-size:26px;font-weight:700;">{total_snaps}</div><div style="color:#666;font-size:12px;">SNAPSHOTS</div></td>
          </tr>
        </table>
        {cap_html}
        {issues_block}
        <div style="margin:16px 0 6px;font-weight:600;">All guests</div>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
          <tr style="text-align:left;color:#666;font-size:12px;border-bottom:2px solid #ddd;">
            <th style="padding:6px 10px;">Guest</th><th style="padding:6px 10px;">Type</th>
            <th style="padding:6px 10px;">Last backup</th><th style="padding:6px 10px;text-align:center;">Snaps</th>
            <th style="padding:6px 10px;text-align:center;">Status</th></tr>
          {guest_rows(rows)}
        </table>
        <p style="color:#999;font-size:12px;margin-top:22px;">Automated report from the Asset Tracker &middot; PBS datastore <b>main</b> &middot; source of truth: proxmox_backup_job (synced from PBS).</p>
      </div></body></html>"""

    text = (f"PBS Backup Report — {now.strftime('%Y-%m-%d')}\n{hdr_txt}\n\n"
            f"Guests: {total} | Protected: {len(ok)} | Issues: {len(issues)} | Snapshots: {total_snaps}\n\n"
            + '\n'.join(f"  {(r.vm_name or r.vmid)}: {r.backup_status} "
                        f"(last {_fmt_age(age_h(r.last_snapshot_time))}, {r.snapshot_count or 0} snaps)"
                        for r in rows))

    try:
        summary['sent'] = bool(send_email(subject, recipients, text, html))
    except Exception as e:
        logger.error('PBS report: send failed: %s', e)
        summary['sent'] = False
    return summary
