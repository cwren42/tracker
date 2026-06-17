"""On-prem Active Directory computer sync — AD is the source of truth for assets.

Pulls computer objects from AD (LDAPService.get_all_computers) and reconciles them
into the asset inventory:
  * match an existing asset by AD objectGUID, else by hostname (case-insensitive);
  * matched  -> enrich/correct identity from AD (name, ad_*, enabled, last logon, OU
                location). AD wins on identity; Intune/UniFi keep their richer telemetry.
  * unmatched -> create a new AD-sourced asset (category/device_type from the OS).

Reconcile-by-hostname is also what keeps AD / Intune / Tracker from drifting into
multiple rows for one machine (the CAPEX2/ITWORKBENCH class of bug).

Signature mirrors unifi_service.sync_unifi_assets so the scheduler + manual button
can call it the same way. Returns {synced, created, updated, errors, error}.
"""
import logging
import os
import re
from datetime import datetime, timedelta

# Don't materialize a NEW asset for an AD computer object that hasn't logged on in
# this many days — these are typically decommissioned/repurposed machines whose AD
# object lingers and would otherwise spawn a phantom device on the dashboard every
# sync (e.g. CHRISTIAN-MSI). Tunable via env. Existing assets are still updated.
AD_STALE_CREATE_DAYS = int(os.environ.get('AD_STALE_CREATE_DAYS', '90'))
import asset_field_ownership as afo  # field-ownership / lock-in model

logger = logging.getLogger(__name__)


def _classify(os_str):
    """(category, device_type) for a new AD-sourced asset from its operatingSystem."""
    o = (os_str or '').lower()
    if 'server' in o:
        return ('Server', 'Linux Server' if ('linux' in o or 'ubuntu' in o) else 'Windows Server')
    if 'windows' in o:
        return ('Computer', 'Windows PC')
    if 'mac' in o or 'os x' in o or 'darwin' in o:
        return ('Computer', 'Mac')
    if 'linux' in o or 'ubuntu' in o:
        return ('Server', 'Linux Server')
    return ('Computer', None)


def _fmt_os_version(comp):
    """Normalize AD OS to the 'Windows 10.0.<build>' shape the asset OS filters expect.
    AD reports e.g. '10.0 (22631)' or '10.0.22631' — pull the 5-digit build either way."""
    ver = (comp.get('os_version') or '').strip()
    osn = (comp.get('operating_system') or '').strip()
    m = re.search(r'10\.0\D*(\d{5})', ver)
    if m:
        return f"Windows 10.0.{m.group(1)}"
    if ver and ver[0].isdigit() and 'windows' in osn.lower():
        return f"Windows {ver}"
    return osn or (ver or None)


def _save_status(Setting, db, result, when):
    """Persist ad_asset_sync_last_* settings (parity with intune/unifi freshness line)."""
    msg = (f"synced={result['synced']} created={result['created']} "
           f"updated={result['updated']} errors={result['errors']}")
    status = 'error' if result.get('error') else 'success'
    pairs = {
        'ad_asset_sync_last_finished': when.isoformat(),
        'ad_asset_sync_last_status': status,
        'ad_asset_sync_last_message': result.get('error') or msg,
    }
    try:
        for k, v in pairs.items():
            row = Setting.query.filter_by(key=k).first()
            if row:
                row.value = v
            else:
                db.session.add(Setting(key=k, value=v))
        db.session.commit()
    except Exception:
        logger.exception('ad_asset_sync: failed to save status settings')
        db.session.rollback()


def sync_ad_computers(app, db, Asset, Setting, AssetHistory=None):
    result = {'synced': 0, 'created': 0, 'updated': 0, 'errors': 0, 'error': None}
    now = datetime.utcnow()

    from ldap_service import LDAPService, load_ad_config
    cfg = load_ad_config(Setting)
    if not cfg.enabled:
        result['error'] = 'Active Directory integration is not enabled (Settings → Directory).'
        return result

    svc = LDAPService(cfg)
    try:
        computers = svc.get_all_computers()
    except Exception as e:
        logger.exception('ad_asset_sync: AD query failed')
        result['error'] = f'AD query failed: {e}'
        _save_status(Setting, db, result, now)
        return result
    finally:
        try:
            svc.disconnect()
        except Exception:
            pass

    if not computers:
        result['error'] = 'No computers returned from AD — check the computer OU / bind creds.'
        _save_status(Setting, db, result, now)
        return result

    # Index existing assets: by AD GUID first (stable), then by uppercased name.
    by_guid = {a.ad_device_guid: a for a in Asset.query.filter(Asset.ad_device_guid.isnot(None)).all()}
    by_name = {}
    for a in Asset.query.filter(Asset.name.isnot(None)).all():
        by_name.setdefault((a.name or '').strip().upper(), a)

    for c in computers:
        host = c.get('short_hostname') or c.get('hostname')
        if not host:
            continue
        try:
            asset = by_guid.get(c['ad_guid'])
            matched_by_guid = asset is not None
            if asset is None:
                asset = by_name.get(host.strip().upper())
            created = asset is None
            if created:
                # Skip creating a phantom for a long-dormant AD object (decommissioned/
                # repurposed machine whose object lingers). A still-live machine logs on
                # and gets created when it next does; existing assets are unaffected
                # (this guards CREATE only). last_logon may be tz-aware or naive.
                _ll = c.get('last_logon')
                if _ll is not None:
                    try:
                        _lln = _ll.replace(tzinfo=None) if getattr(_ll, 'tzinfo', None) else _ll
                        if _lln < (now - timedelta(days=AD_STALE_CREATE_DAYS)):
                            result['skipped_stale'] = result.get('skipped_stale', 0) + 1
                            continue
                    except Exception:
                        pass
                cat, dt = _classify(c.get('operating_system'))
                asset = Asset(name=host, category=cat, status='In Use', created_at=now,
                              auto_discovered=True)  # created by a sync, not procurement
                asset.device_type = dt
                asset.os_version = _fmt_os_version(c)
                asset.last_seen = c.get('last_logon')
                db.session.add(asset)
            elif (asset.status or '').strip().lower() == 'retired':
                # Retired assets are deliberately archived. Don't rewrite their AD
                # identity / updated_at on every daily run -- that churned retired
                # records (e.g. #125 SHAMPTON-THINK) for no reason. Keep them indexed
                # for dedup, but make no writes.
                by_guid[c['ad_guid']] = asset
                by_name[host.strip().upper()] = asset
                continue
            elif matched_by_guid and (asset.name or '').strip().upper() != host.strip().upper():
                # A known machine (stable GUID) was genuinely renamed in AD -> adopt it,
                # UNLESS the operator has locked the name (ownership model). AD is the
                # name authority, so it may overwrite a sync-set name, but not an
                # operator override. Name-matched (non-GUID) assets are skipped above to
                # avoid churning case-only diffs.
                afo.apply_sync_update(asset, afo.AD, {'name': host})

            # AD = source of truth for identity
            asset.ad_device_guid = c['ad_guid']
            asset.ad_dn = (c.get('distinguished_name') or '')[:400] or None
            asset.ad_enabled = c.get('ad_enabled')
            asset.ad_last_logon = c.get('last_logon')
            if c.get('ou_location') and not asset.location:
                asset.location = c['ou_location']
            asset.updated_at = now

            db.session.commit()
            by_guid[c['ad_guid']] = asset
            by_name[host.strip().upper()] = asset
            result['synced'] += 1
            result['created' if created else 'updated'] += 1
            if created and AssetHistory is not None:
                try:
                    db.session.add(AssetHistory(asset_id=asset.id, action='Created',
                                                description=f'Created from AD sync ({host})'))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        except Exception as e:
            db.session.rollback()
            result['errors'] += 1
            logger.warning('ad_asset_sync: failed for %s: %s', host, e)

    _save_status(Setting, db, result, now)
    logger.info('AD computer sync complete: %s', result)
    return result
