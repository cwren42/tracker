"""Guards for the asset field-ownership / lock-in model (asset_field_ownership.py)."""
from types import SimpleNamespace

import asset_field_ownership as afo


def _asset(**kw):
    base = dict(name='OLD', asset_tag='20-0000001', employee_id=None,
                online_state='Offline', model=None, serial_number=None,
                ip_address=None, locked_fields=None, auto_discovered=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_operator_can_always_write():
    a = _asset()
    assert afo.can_write(a, afo.OPERATOR, 'asset_tag')
    assert afo.can_write(a, afo.OPERATOR, 'name')


def test_asset_tag_never_sync_writable():
    a = _asset()
    for src in (afo.INTUNE, afo.AD, afo.UNIFI, afo.RMM):
        assert not afo.can_write(a, src, 'asset_tag')


def test_locked_field_blocks_sync_but_not_operator():
    a = _asset()
    afo.lock_fields(a, 'name', 'employee_id')
    assert not afo.can_write(a, afo.AD, 'name')          # AD owns name but it's locked
    assert not afo.can_write(a, afo.INTUNE, 'employee_id')
    assert afo.can_write(a, afo.OPERATOR, 'name')         # operator overrides lock


def test_name_ad_owns_intune_fills_only_when_empty():
    a = _asset(name='REAL-NAME')
    assert afo.can_write(a, afo.AD, 'name')                # AD is the owner -> may overwrite
    assert not afo.can_write(a, afo.INTUNE, 'name')        # Intune is fill-only, name non-empty
    a2 = _asset(name='')
    assert afo.can_write(a2, afo.INTUNE, 'name')           # Intune may seed an empty name


def test_intune_cannot_write_online_state():
    a = _asset()
    assert not afo.can_write(a, afo.INTUNE, 'online_state')
    assert afo.can_write(a, afo.UNIFI, 'online_state')     # live signal may
    assert afo.can_write(a, afo.RMM, 'online_state')


def test_employee_fill_only_for_intune():
    a = _asset(employee_id=5)
    assert not afo.can_write(a, afo.INTUNE, 'employee_id')  # already assigned -> don't reassign
    a2 = _asset(employee_id=None)
    assert afo.can_write(a2, afo.INTUNE, 'employee_id')     # empty -> may seed


def test_apply_sync_update_returns_changed_and_respects_rules():
    a = _asset(name='REAL', model=None, employee_id=7)
    changed = afo.apply_sync_update(a, afo.INTUNE, {
        'name': 'INTUNE-WANTS-THIS',   # blocked: fill-only, name non-empty
        'model': 'Latitude 7440',      # allowed: fill-only, model empty
        'employee_id': 99,             # blocked: fill-only, already assigned
        'online_state': 'Online',      # blocked: intune can't own online_state
    })
    assert changed == ['model']
    assert a.model == 'Latitude 7440'
    assert a.name == 'REAL'
    assert a.employee_id == 7
    assert a.online_state == 'Offline'


def test_lock_unlock_roundtrip():
    a = _asset()
    afo.lock_fields(a, 'name')
    assert 'name' in afo._locked(a)
    afo.unlock_fields(a, 'name')
    assert 'name' not in afo._locked(a)
