"""Rogue-device scan — DB-free unit tests.

Exercises the pure classification logic and asserts the wiring (routes, service
methods) exists, mirroring the repo's source-level guard pattern. No DB / no
live UniFi required, so this runs in CI.
"""
import network_scan


def test_mac_norm_strips_delimiters_and_lowercases():
    assert network_scan._mac_norm('AA:BB:CC:11:22:33') == 'aabbcc112233'
    assert network_scan._mac_norm('aa-bb-cc-11-22-33') == 'aabbcc112233'
    assert network_scan._mac_norm('') == ''
    assert network_scan._mac_norm(None) == ''


def _classify(mac, vendor='', asset=None, allow=None, acked=None, host='', known_hosts=None):
    return network_scan._classify(
        mac, host, vendor, asset or {}, allow or set(), acked or set(), known_hosts or {})


def test_hostname_match_recognizes_known_box_with_drifted_mac():
    # unknown MAC (new dock/NIC) but hostname matches a known asset -> known_asset
    cls, aid = _classify('aabbccddeeff', host='ADMIN-CHARITY', known_hosts={'admin-charity': 141})
    assert cls == 'known_asset' and aid == 141
    # domain suffix is stripped before matching
    cls2, aid2 = _classify('aabbccddeef0', host='ADMIN-CHARITY.corp.cirque.com',
                           known_hosts={'admin-charity': 141})
    assert cls2 == 'known_asset' and aid2 == 141
    # unknown MAC + hostname NOT in known set -> still unknown
    cls3, _ = _classify('aabbccddeef1', host='SOME-RANDOM-LAPTOP', known_hosts={'admin-charity': 141})
    assert cls3 == 'unknown'


def test_acknowledged_wins_over_everything():
    # Sticky acknowledgement beats an asset match.
    cls, aid = _classify('m1', asset={'m1': 5}, acked={'m1'})
    assert cls == 'acknowledged'
    assert aid == 5  # asset_id still carried through when known


def test_known_asset_match():
    cls, aid = _classify('m1', asset={'m1': 42})
    assert cls == 'known_asset'
    assert aid == 42


def test_unifi_allowlist_marks_acknowledged():
    cls, aid = _classify('m1', allow={'m1'})
    assert cls == 'acknowledged'
    assert aid is None


def test_infra_vendor_hint():
    cls, _ = _classify('m1', vendor='Ubiquiti Inc')
    assert cls == 'known_vendor'


def test_generic_nic_vendor_is_not_suppressed():
    # A real rogue PC (Intel/Realtek NIC) must stay 'unknown', never known_vendor.
    for v in ('Intel Corporate', 'Realtek', 'Micro-Star INTL', ''):
        cls, _ = _classify('m1', vendor=v)
        assert cls == 'unknown', v


def test_unknown_when_nothing_matches():
    cls, aid = _classify('mystery', vendor='Some Random Co')
    assert cls == 'unknown'
    assert aid is None


def test_bogus_macs_are_filtered():
    b = network_scan._is_bogus_mac
    # non-host / artifact MACs → filtered
    assert b('0000ffffffff')      # switch-learned phantom (device half all-F)
    assert b('ffffffffffff')      # broadcast
    assert b('01005e000001')      # IPv4 multicast
    assert b('333300000001')      # IPv6 multicast
    assert b('0180c2000000')      # STP/LLDP reserved
    assert b('000000000000')      # all zero
    assert b('00:00')             # malformed length (norm-stripped elsewhere; len!=12)
    # real unicast host MACs → NOT filtered
    assert not b('bc2411da667d')  # a real Proxmox VM MAC
    assert not b('a89c6c80ab51')  # a real switch/host MAC


def test_unifi_service_has_discovery_and_enforcement():
    import unifi_service
    for m in ('get_active_clients', 'get_known_users',
              'block_client', 'unblock_client', '_stamgr'):
        assert hasattr(unifi_service.UnifiService, m), m


def test_network_routes_registered_and_admin_gated():
    import os
    os.environ.setdefault('SECRET_KEY', 'test')
    os.environ.setdefault('LINUX_AGENT_API_KEY', 'test')
    os.environ.setdefault('DATABASE_URL', 'postgresql://t:t@localhost/unused')
    from app import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    for r in ('/network', '/network/<int:client_id>/block',
              '/network/<int:client_id>/unblock',
              '/network/<int:client_id>/acknowledge', '/network/scan',
              '/network/map', '/network/map.json'):
        assert r in rules, r
