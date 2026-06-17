"""Asset field-ownership / precedence model ("lock-in", Phase 2).

Four sync sources (Intune, AD, UniFi, RMM) plus the operator all write to the same
Asset rows. Without a defined authority they fight — e.g. the Intune sync reverts an
operator rename, or a thin auto-record's fields churn daily. This module centralizes
the rules so every sync ENRICHES rather than CLOBBERS:

  * Each field has an owning source (or "operator"). A sync may write a field it owns.
  * Some fields are FILL-ONLY for syncs: written only when currently empty (the
    operator/procurement owns them; a sync may seed a blank but never overwrite).
  * Any field the operator has explicitly set is recorded in asset.locked_fields and
    is NEVER written by a sync, regardless of ownership.
  * online_state is owned by LIVE signals only (RMM gateway / UniFi) — Intune must
    never write it (compliance != connectivity). See [[online-state-vs-compliance]].

Usage from a sync:
    from asset_field_ownership import apply_sync_update, mark_auto_discovered
    changed = apply_sync_update(asset, 'intune', {'name': dn, 'model': m, ...})
    # `changed` is the list of fields actually written (use it to decide history/updated_at)

On operator edit (UI):
    from asset_field_ownership import lock_fields
    lock_fields(asset, 'name', 'asset_tag', 'employee_id')
"""
import json

# Sources
OPERATOR = 'operator'
INTUNE = 'intune'
AD = 'ad'
UNIFI = 'unifi'
RMM = 'rmm'

# field -> ownership spec:
#   owners:    sources allowed to write it (besides operator, who can always set it)
#   fill_only: if True, a sync may write only when the current value is empty/None
# Fields not listed are NOT writable by any sync through this helper (operator-only),
# which deliberately includes asset_tag.
_OWNERSHIP = {
    # Identity — AD is source of truth for name; Intune may only seed it when empty.
    'name':              {'owners': {AD},               'fill_only': False, 'fill_for': {INTUNE, UNIFI, RMM}},
    # Procurement/hardware facts — operator owns; syncs may seed only when empty.
    'serial_number':     {'owners': {INTUNE, AD, UNIFI, RMM}, 'fill_only': True},
    'manufacturer':      {'owners': {INTUNE, AD, UNIFI},      'fill_only': True},
    'model':             {'owners': {INTUNE, AD, UNIFI},      'fill_only': True},
    'device_type':       {'owners': {INTUNE, AD, UNIFI, RMM}, 'fill_only': True},
    'os_version':        {'owners': {INTUNE, AD, UNIFI, RMM}, 'fill_only': False},
    'category':          {'owners': {INTUNE, AD, UNIFI},      'fill_only': True},
    'location':          {'owners': {AD},                'fill_only': True},
    # Assignment — operator/onboarding owns; Intune may set only when currently empty.
    'employee_id':       {'owners': {INTUNE},            'fill_only': True},
    # Live connectivity — RMM gateway / UniFi only. Intune must NEVER write this.
    'online_state':      {'owners': {RMM, UNIFI},        'fill_only': False},
    'ip_address':        {'owners': {UNIFI, RMM},        'fill_only': False},
    # Sync-owned identifiers — each owns its own namespace.
    'intune_device_id':  {'owners': {INTUNE}, 'fill_only': False},
    'azure_ad_device_id':{'owners': {INTUNE}, 'fill_only': False},
    'ad_device_guid':    {'owners': {AD},     'fill_only': False},
    'ad_dn':             {'owners': {AD},     'fill_only': False},
    'unifi_device_id':   {'owners': {UNIFI},  'fill_only': False},
}


def _locked(asset):
    raw = getattr(asset, 'locked_fields', None)
    if not raw:
        return set()
    try:
        v = json.loads(raw)
        return set(v) if isinstance(v, list) else set()
    except Exception:
        return set()


def can_write(asset, source, field):
    """True if `source` may write `field` on `asset` right now (ownership + not locked
    + fill-only respected)."""
    if source == OPERATOR:
        return True
    if field in _locked(asset):
        return False
    spec = _OWNERSHIP.get(field)
    if not spec:
        return False  # not a sync-writable field (e.g. asset_tag) — operator only
    allowed = source in spec['owners'] or source in spec.get('fill_for', set())
    if not allowed:
        return False
    # fill-only (globally, or because this source is only a "fill_for" seeder):
    fill_only = spec.get('fill_only') or (source in spec.get('fill_for', set()) and source not in spec['owners'])
    if fill_only:
        cur = getattr(asset, field, None)
        if cur not in (None, '', 0):
            return False
    return True


def apply_sync_update(asset, source, updates):
    """Apply `updates` (dict field->value) to `asset` honoring ownership + locks.
    Returns the list of fields actually changed (skips no-op writes). Sync-owned
    *_last_seen / telemetry fields can be written directly by the caller; this helper
    is for the contested identity/assignment/state fields."""
    changed = []
    for field, value in updates.items():
        if value is None or value == '':
            continue
        if not can_write(asset, source, field):
            continue
        if getattr(asset, field, None) == value:
            continue
        setattr(asset, field, value)
        changed.append(field)
    return changed


def lock_fields(asset, *fields):
    """Record that the operator has explicitly set these fields, so syncs won't
    clobber them. Idempotent."""
    cur = _locked(asset)
    cur.update(f for f in fields if f)
    asset.locked_fields = json.dumps(sorted(cur))


def unlock_fields(asset, *fields):
    cur = _locked(asset)
    for f in fields:
        cur.discard(f)
    asset.locked_fields = json.dumps(sorted(cur)) if cur else None


def mark_auto_discovered(asset):
    """Flag a row as created by a sync (vs operator/procurement)."""
    asset.auto_discovered = True
