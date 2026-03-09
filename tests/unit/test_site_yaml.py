"""Unit tests for the site_yaml inventory plugin.

Covers the pure-logic helper methods that can be exercised without a full
Ansible inventory run:
  - _subnet_reverse_zone
  - _resolve_iface_vlan
  - _resolve_iface_dhcp_network
  - _sanitise_networks_data  (subnet pre-computation and domain-name synthesis)
  - _synthesize_host_network_metadata  (dhcp_network and dns_name derivation)
"""
import pytest
from helpers import make_plugin, make_valid_keys


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh_parser():
    return {'errors': [], 'warnings': []}


def make_data(networks=None, hosts=None, default_vars=None):
    """Build a minimal site-data dict for feeding into plugin methods."""
    d = {}
    if networks is not None:
        d['networks'] = networks
    if hosts is not None:
        d['hosts'] = hosts
    if default_vars is not None:
        d['default_vars'] = default_vars
    else:
        d['default_vars'] = {}
    return d


# ---------------------------------------------------------------------------
# _subnet_reverse_zone
# ---------------------------------------------------------------------------

class TestSubnetReverseZone:
    def test_ipv4_slash24(self, plugin):
        assert plugin._subnet_reverse_zone('192.168.1.0/24') == '1.168.192.in-addr.arpa'

    def test_ipv4_slash24_hostbits(self, plugin):
        # strict=False: host bits are cleared, so 192.168.1.5/24 → 192.168.1.0/24
        assert plugin._subnet_reverse_zone('192.168.1.5/24') == '1.168.192.in-addr.arpa'

    def test_ipv4_slash16(self, plugin):
        assert plugin._subnet_reverse_zone('172.16.0.0/16') == '16.172.in-addr.arpa'

    def test_ipv4_slash8(self, plugin):
        assert plugin._subnet_reverse_zone('10.0.0.0/8') == '10.in-addr.arpa'

    def test_ipv4_slash25_classless(self, plugin):
        # Prefix > /24: classless delegation notation
        assert plugin._subnet_reverse_zone('192.168.1.128/25') == '128/25.1.168.192.in-addr.arpa'

    def test_ipv4_slash26_classless(self, plugin):
        assert plugin._subnet_reverse_zone('10.0.0.64/26') == '64/26.0.0.10.in-addr.arpa'

    def test_ipv6_slash32(self, plugin):
        assert plugin._subnet_reverse_zone('2001:db8::/32') == '8.b.d.0.1.0.0.2.ip6.arpa'

    def test_ipv6_slash48(self, plugin):
        assert plugin._subnet_reverse_zone('2001:db8:1::/48') == '1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa'

    def test_ipv6_slash64(self, plugin):
        result = plugin._subnet_reverse_zone('2001:db8::/64')
        assert result.endswith('.ip6.arpa')

    def test_invalid_subnet(self, plugin):
        assert plugin._subnet_reverse_zone('not-a-subnet') is None

    def test_plain_ipv4_no_prefix(self, plugin):
        # A bare host address is a valid /32; reverse zone is the full address
        result = plugin._subnet_reverse_zone('192.168.1.1')
        assert result is not None  # /32 falls into classless delegation path


# ---------------------------------------------------------------------------
# _resolve_iface_vlan
# ---------------------------------------------------------------------------

class TestResolveIfaceVlan:
    def test_direct_vlan_key(self, plugin):
        assert plugin._resolve_iface_vlan({}, 'h', 'server', 'if0', {'vlan': 'mgmt'}) == 'mgmt'

    def test_numeric_vlan_returned_as_string(self, plugin):
        assert plugin._resolve_iface_vlan({}, 'h', 'server', 'if0', {'vlan': 42}) == '42'

    def test_vlan_default_returns_none(self, plugin):
        # 'default' is special – treated as "no specific vlan"
        assert plugin._resolve_iface_vlan({}, 'h', 'server', 'if0', {'vlan': 'default'}) is None

    def test_no_vlan_key_returns_none(self, plugin):
        assert plugin._resolve_iface_vlan({}, 'h', 'server', 'if0', {}) is None

    def test_bridge_sibling_vlan(self, plugin):
        # if0 is a bridge; sibling if1 sets bridge=if0 and vlan=dmz
        hosts = {
            'h': {
                'networks': {
                    'if0': {'type': 'bridge'},
                    'if1': {'bridge': 'if0', 'vlan': 'dmz'},
                }
            }
        }
        assert plugin._resolve_iface_vlan(hosts, 'h', 'server', 'if0',
                                          {'type': 'bridge'}) == 'dmz'

    def test_lxc_inherits_parent_vlan(self, plugin):
        # LXC container references parent host via machine + link
        hosts = {
            'parent': {
                'networks': {
                    'eth0': {'vlan': 'trusted'},
                }
            },
            'container': {
                'machine': 'parent',
                'networks': {
                    'eth0': {'link': 'eth0'},
                }
            }
        }
        result = plugin._resolve_iface_vlan(hosts, 'container', 'lxc', 'eth0',
                                            {'link': 'eth0'})
        assert result == 'trusted'


# ---------------------------------------------------------------------------
# _resolve_iface_dhcp_network
# ---------------------------------------------------------------------------

class TestResolveIfaceDhcpNetwork:
    def _make_cidr_networks(self):
        return {
            'default': {
                'subnets': {
                    '192.168.1.0/24': {
                        'subnet_network': '192.168.1.0',
                        'subnet_netmask': '255.255.255.0',
                    }
                }
            },
            'mgmt': {
                'subnets': {
                    '10.1.0.0/24': {
                        'subnet_network': '10.1.0.0',
                        'subnet_netmask': '255.255.255.0',
                    }
                }
            },
        }

    def test_ip_in_cidr_subnet(self, plugin):
        nets = self._make_cidr_networks()
        assert plugin._resolve_iface_dhcp_network(nets, {'ipv4': '192.168.1.5/24'}) == 'default'

    def test_ip_in_second_network(self, plugin):
        nets = self._make_cidr_networks()
        assert plugin._resolve_iface_dhcp_network(nets, {'ipv4': '10.1.0.99'}) == 'mgmt'

    def test_ip_not_in_any_subnet(self, plugin):
        nets = self._make_cidr_networks()
        assert plugin._resolve_iface_dhcp_network(nets, {'ipv4': '172.16.0.1'}) is None

    def test_no_ipv4_returns_none(self, plugin):
        assert plugin._resolve_iface_dhcp_network({}, {}) is None

    def test_ipv6_only_returns_none(self, plugin):
        assert plugin._resolve_iface_dhcp_network({}, {'ipv6': '2001:db8::1/64'}) is None

    def test_explicit_netmask_subnet_uses_precomputed(self, plugin):
        # Subnet key is a bare IP (not CIDR); pre-computed subnet_network/netmask
        # must be used for correct matching (bare IP without mask is a /32).
        nets = {
            'second': {
                'subnets': {
                    '10.0.0.4': {
                        'netmask': '255.255.255.0',
                        'subnet_network': '10.0.0.0',
                        'subnet_netmask': '255.255.255.0',
                    }
                }
            }
        }
        assert plugin._resolve_iface_dhcp_network(nets, {'ipv4': '10.0.0.9/24'}) == 'second'

    def test_explicit_netmask_without_precomputed_misses(self, plugin):
        # Without pre-computed values a bare-IP key resolves as /32, so a
        # different host address in the same subnet is NOT matched.
        nets = {
            'second': {
                'subnets': {
                    '10.0.0.4': {'netmask': '255.255.255.0'}
                    # no subnet_network / subnet_netmask
                }
            }
        }
        assert plugin._resolve_iface_dhcp_network(nets, {'ipv4': '10.0.0.9'}) is None

    def test_non_dict_net_skipped(self, plugin):
        nets = {'broken': None}
        assert plugin._resolve_iface_dhcp_network(nets, {'ipv4': '1.2.3.4'}) is None


# ---------------------------------------------------------------------------
# _sanitise_networks_data  (subnet pre-computation + domain names)
# ---------------------------------------------------------------------------

class TestSanitiseNetworksData:
    def _run(self, networks, default_vars=None, **plugin_opts):
        p = make_plugin(**plugin_opts)
        k = make_valid_keys()
        data = make_data(networks=networks,
                         default_vars=default_vars or {})
        parser = fresh_parser()
        _, result = p._sanitise_networks_data(data, k, parser)
        return result, parser

    def test_cidr_subnet_network_and_netmask(self):
        result, _ = self._run({'default': {'vlan_id': 1,
                                            'subnets': {'192.168.1.0/24': None}}})
        s = result['networks']['default']['subnets']['192.168.1.0/24']
        assert s['subnet_network'] == '192.168.1.0'
        assert s['subnet_netmask'] == '255.255.255.0'

    def test_host_ip_with_explicit_netmask(self):
        # Site YAML may write subnets as "2.2.3.4": netmask: 255.255.255.0
        result, _ = self._run({'second': {'vlan_id': 2,
                                           'subnets': {'2.2.3.4': {'netmask': '255.255.255.0'}}}})
        s = result['networks']['second']['subnets']['2.2.3.4']
        assert s['subnet_network'] == '2.2.3.0'
        assert s['subnet_netmask'] == '255.255.255.0'

    def test_none_subnet_value_becomes_dict(self):
        # Subnets written as "1.2.3.0/24:" (null value) must become a dict
        result, _ = self._run({'default': {'vlan_id': 1,
                                            'subnets': {'1.2.3.0/24': None}}})
        s = result['networks']['default']['subnets']['1.2.3.0/24']
        assert isinstance(s, dict)

    def test_domain_name_default_network(self):
        result, _ = self._run(
            {'default': {'vlan_id': 1, 'subnets': {'192.168.1.0/24': None}}},
            default_vars={'dns_domain': 'example.com'},
        )
        net = result['networks']['default']
        assert net['dhcp_domain_name'] == '"example.com"'
        assert net['dhcp_domain_search'] == '"example.com", "default.example.com"'

    def test_domain_name_non_default_network(self):
        result, _ = self._run(
            {'mgmt': {'vlan_id': 10, 'subnets': {'10.1.0.0/24': None}}},
            default_vars={'dns_domain': 'example.com'},
        )
        net = result['networks']['mgmt']
        assert net['dhcp_domain_name'] == '"mgmt.example.com"'
        assert net['dhcp_domain_search'] == '"mgmt.example.com", "example.com"'

    def test_no_domain_name_when_no_dns_domain(self):
        result, _ = self._run({'default': {'vlan_id': 1,
                                            'subnets': {'192.168.1.0/24': None}}})
        net = result['networks']['default']
        assert 'dhcp_domain_name' not in net
        assert 'dhcp_domain_search' not in net

    def test_reverse_zones_populated(self):
        result, _ = self._run(
            {'default': {'vlan_id': 1, 'subnets': {'192.168.1.0/24': None}}}
        )
        rzones = result['networks']['default'].get('reverse_zones', {})
        assert '192.168.1.0/24' in rzones
        assert rzones['192.168.1.0/24'] == '1.168.192.in-addr.arpa'

    def test_no_reverse_zone_for_host_only(self):
        # A bare host IP without mask resolves as /32 (classless delegation zone),
        # but reverse_zones should still be populated for it.
        result, _ = self._run(
            {'default': {'vlan_id': 1, 'subnets': {'192.168.1.1': None}}}
        )
        rzones = result['networks']['default'].get('reverse_zones', {})
        assert '192.168.1.1' in rzones

    def test_vlans_dict_populated(self):
        result, _ = self._run(
            {'default': {'vlan_id': 1, 'subnets': {}},
             'mgmt': {'vlan_id': 10, 'subnets': {}}}
        )
        # vlans are stored in data['vlans'] as well as set on inventory
        assert result.get('vlans', {}).get('default') == '1'
        assert result.get('vlans', {}).get('mgmt') == '10'

    def test_missing_vlan_id_uses_option(self):
        p = make_plugin(missing_vlan_id='UNKNOWN')
        k = make_valid_keys()
        data = make_data(networks={'nonet': {'subnets': {}}})
        _, result = p._sanitise_networks_data(data, k, fresh_parser())
        assert result['vlans']['nonet'] == 'UNKNOWN'

    def test_generate_dhcp_networks_calls_set_variable(self):
        p = make_plugin(generate_dhcp_networks=True)
        k = make_valid_keys()
        data = make_data(networks={'default': {'vlan_id': 1, 'subnets': {}}})
        p._sanitise_networks_data(data, k, fresh_parser())
        p.inventory.set_variable.assert_any_call('all', 'dhcp_networks',
                                                  data['networks'])

    def test_no_generate_dhcp_networks_skips_set_variable(self):
        p = make_plugin(generate_dhcp_networks=False)
        k = make_valid_keys()
        data = make_data(networks={'default': {'vlan_id': 1, 'subnets': {}}})
        p._sanitise_networks_data(data, k, fresh_parser())
        for call in p.inventory.set_variable.call_args_list:
            assert call.args[1] != 'dhcp_networks'


# ---------------------------------------------------------------------------
# _synthesize_host_network_metadata
# ---------------------------------------------------------------------------

class TestSynthesizeHostNetworkMetadata:
    def _run(self, networks, hosts, default_vars=None):
        p = make_plugin()
        k = make_valid_keys()
        data = make_data(networks=networks, hosts=hosts,
                         default_vars=default_vars or {})
        _, result = p._synthesize_host_network_metadata(data, k, fresh_parser())
        return result

    def test_dhcp_network_set_from_ip(self):
        result = self._run(
            networks={
                'default': {
                    'subnets': {
                        '192.168.1.0/24': {
                            'subnet_network': '192.168.1.0',
                            'subnet_netmask': '255.255.255.0',
                        }
                    }
                }
            },
            hosts={
                'host1': {
                    'type': 'server',
                    'networks': {'if0': {'ipv4': '192.168.1.5/24'}},
                }
            },
        )
        assert result['hosts']['host1']['networks']['if0']['dhcp_network'] == 'default'

    def test_dhcp_network_not_set_when_no_match(self):
        result = self._run(
            networks={
                'default': {
                    'subnets': {
                        '192.168.1.0/24': {
                            'subnet_network': '192.168.1.0',
                            'subnet_netmask': '255.255.255.0',
                        }
                    }
                }
            },
            hosts={
                'host1': {
                    'type': 'server',
                    'networks': {'if0': {'ipv4': '10.0.0.1'}},
                }
            },
        )
        assert 'dhcp_network' not in result['hosts']['host1']['networks']['if0']

    def test_dns_name_no_vlan(self):
        result = self._run(
            networks={},
            hosts={'h': {'type': 'server',
                          'networks': {'if0': {'ipv4': '1.2.3.4'}}}},
            default_vars={'dns_domain': 'example.com'},
        )
        assert result['hosts']['h']['networks']['if0']['dns_name'] == 'h.example.com'

    def test_dns_name_with_vlan(self):
        result = self._run(
            networks={'second': {'subnets': {}}},
            hosts={'h': {'type': 'server',
                          'networks': {'if1': {'ipv4': '10.0.0.1',
                                               'vlan': 'second'}}}},
            default_vars={'dns_domain': 'example.com'},
        )
        assert result['hosts']['h']['networks']['if1']['dns_name'] == 'h.second.example.com'

    def test_dns_name_vlan_default_not_prefixed(self):
        # vlan=='default' → _resolve_iface_vlan returns None → no vlan label
        result = self._run(
            networks={},
            hosts={'h': {'type': 'server',
                          'networks': {'if0': {'ipv4': '1.2.3.4',
                                               'vlan': 'default'}}}},
            default_vars={'dns_domain': 'example.com'},
        )
        assert result['hosts']['h']['networks']['if0']['dns_name'] == 'h.example.com'

    def test_dns_name_explicit_dns_key(self):
        result = self._run(
            networks={},
            hosts={'h': {'type': 'server',
                          'networks': {'if0': {'ipv4': '1.2.3.4',
                                               'dns': 'gateway'}}}},
            default_vars={'dns_domain': 'example.com'},
        )
        assert result['hosts']['h']['networks']['if0']['dns_name'] == 'gateway.example.com'

    def test_dns_name_explicit_fqdn_dns_key(self):
        # Trailing dot → used verbatim (dot stripped from result)
        result = self._run(
            networks={},
            hosts={'h': {'type': 'server',
                          'networks': {'if0': {'ipv4': '1.2.3.4',
                                               'dns': 'ns1.other.org.'}}}},
            default_vars={'dns_domain': 'example.com'},
        )
        assert result['hosts']['h']['networks']['if0']['dns_name'] == 'ns1.other.org'

    def test_no_dns_domain_no_dns_name(self):
        result = self._run(
            networks={},
            hosts={'h': {'type': 'server',
                          'networks': {'if0': {'ipv4': '1.2.3.4'}}}},
        )
        assert 'dns_name' not in result['hosts']['h']['networks']['if0']

    def test_host_without_networks_skipped(self):
        result = self._run(
            networks={},
            hosts={'h': {'type': 'local'}},
            default_vars={'dns_domain': 'example.com'},
        )
        # No crash; host dict unchanged
        assert 'networks' not in result['hosts']['h']

    def test_resolved_vlan_stored(self):
        result = self._run(
            networks={'mgmt': {'subnets': {}}},
            hosts={'h': {'type': 'server',
                          'networks': {'if0': {'ipv4': '10.0.0.1',
                                               'vlan': 'mgmt'}}}},
            default_vars={'dns_domain': 'example.com'},
        )
        assert result['hosts']['h']['networks']['if0']['resolved_vlan'] == 'mgmt'
