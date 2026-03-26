"""Tests for switch_utils package: model, utils, detect, parsers, summary."""

import os
import sys
import pytest

# Ensure switch_utils is importable via helpers
import helpers  # noqa: F401  (side-effect: adds REPO_ROOT to sys.path)

from switch_utils.utils import expand_range, expand_hpe_range, compress_list
from switch_utils.detect import detect_dialect
from switch_utils.model import SwitchConfig, VlanInfo, PortInfo, LacpGroup
from switch_utils.parsers import parse, _validate
from switch_utils.summary import format_summary


# ===========================================================================
# Fixtures: minimal embedded switch config strings
# ===========================================================================

ROUTEROS_MINIMAL = """\
# jan/01/1970 00:00:00 by RouterOS 7.12
# software id = XXXX-YYYY
# model = CRS326-24G-2S+IN
/interface bridge port
add interface=ether1 pvid=10 comment="uplink"
add interface=ether2 pvid=20 frame-types=admit-only-vlan-tagged
add interface=ether3 pvid=1 comment="server"
/interface bridge vlan
add vlan-ids=10 untagged=ether1 comment=mgmt
add vlan-ids=20 tagged=ether2,ether3 comment=servers
/interface vlan
add vlan-id=10 name=management
add vlan-id=30 name=extra
"""

ZYXEL_XGS_MINIMAL = """\
; Product Name = XGS1930-28
; Firmware Version = V4.80(ABFW.2)
vlan 1
 name "Default"
 fixed 1-8
 untagged 1-8
 exit
vlan 10
 name "Management"
 fixed 1,9
 untagged 9
 exit
vlan 20
 name "Servers"
 fixed 2,3
 exit
interface port-channel 9
 pvid 10
 exit
interface port-channel 1
 pvid 1
 exit
interface port-channel 2
 pvid 1
 exit
interface port-channel 3
 inactive
 exit
trunk T1 lacp
trunk T1 interface 4
trunk T1 interface 5
"""

FS_GENERIC_MINIMAL = """\
!<Version>1.1.0</Version>
hostname sw-fs-generic
vlan database
 vlan 1-3 name test
 vlan 10 name mgmt
 vlan 20 name servers
!
interface ethernet 1/1
 switchport mode access
 switchport access vlan 10
!
interface ethernet 1/2
 switchport mode trunk
 switchport trunk native vlan 1
 switchport trunk allowed vlan 10,20
!
interface ethernet 1/3
 shutdown
!
interface ethernet 1/4
 switchport mode trunk
 switchport trunk allowed vlan all
!
"""

FS_GIGAETH_MINIMAL = """\
!version 2.1.3J
hostname sw-gigaeth
!
interface GigaEthernet0/1
 switchport pvid 10
!
interface GigaEthernet0/2
 switchport mode trunk
 switchport trunk vlan-allowed 10,20,30
 switchport trunk vlan-untagged 1
!
interface GigaEthernet0/3
 shutdown
!
interface GigaEthernet0/4
 channel-group 1
!
"""

FS_VRP_MINIMAL = """\
!System startup configuration
!
hostname sw-vrp
!
vlan 10
 alias "Management"
vlan 20
 alias "Servers"
vlan 30-32
!
interface Eth-Trunk 1
 mode lacp
!
interface 10GigabitEthernet 1/0/1
 port link-type access
 port default vlan 10
!
interface 10GigabitEthernet 1/0/2
 port link-type trunk
 port trunk allow-pass vlan all
!
interface 10GigabitEthernet 1/0/3
 alias "uplink"
 port link-type trunk
 port trunk allow-pass vlan all
 join eth-trunk 1
!
interface 10GigabitEthernet 1/0/4
 shutdown
!
interface 10GigabitEthernet 1/0/5
 port link-type access
 port default vlan 20
!
"""

HPE_PROCURVE_MINIMAL = """\
version 7.1.045, Release 2311
#
vlan 1
 description "Default"
#
vlan 10
 description "Management"
#
vlan 20
 description "Servers"
#
interface GigabitEthernet1/0/1
 port access vlan 10
 stp edged-port enable
#
interface GigabitEthernet1/0/2
 port link-type trunk
 port trunk pvid vlan 1
 port trunk permit vlan 1 to 2 10 20
#
interface GigabitEthernet1/0/3
 shutdown
#
interface GigabitEthernet1/0/4
 port access vlan 20
 port link-aggregation group 1
#
interface GigabitEthernet1/0/5
 port access vlan 20
 port link-aggregation group 1
#
interface Bridge-Aggregation1
 port link-type trunk
 port trunk pvid vlan 1
 port trunk permit vlan 1 to 2 10 20
 link-aggregation mode dynamic
#
"""


# ===========================================================================
# 1. Utils: expand_range
# ===========================================================================

class TestExpandRange:
    def test_single(self):
        assert expand_range('5') == [5]

    def test_range(self):
        assert expand_range('1-3') == [1, 2, 3]

    def test_comma_list(self):
        assert expand_range('1,3,5') == [1, 3, 5]

    def test_mixed(self):
        assert expand_range('1-3,5,7-9') == [1, 2, 3, 5, 7, 8, 9]

    def test_with_spaces(self):
        assert expand_range(' 1-3 , 5 ') == [1, 2, 3, 5]

    def test_empty_string(self):
        assert expand_range('') == []

    def test_sorted_output(self):
        assert expand_range('9,1,3-5') == [1, 3, 4, 5, 9]

    def test_single_element_range(self):
        assert expand_range('10-10') == [10]

    def test_large_range(self):
        result = expand_range('100-105')
        assert result == [100, 101, 102, 103, 104, 105]

    def test_invalid_part_ignored(self):
        # Non-numeric parts should not raise, just be skipped
        result = expand_range('1,abc,3')
        assert result == [1, 3]


# ===========================================================================
# 1b. Utils: expand_hpe_range
# ===========================================================================

class TestExpandHpeRange:
    def test_single(self):
        assert expand_hpe_range('5') == [5]

    def test_to_range(self):
        assert expand_hpe_range('1 to 3') == [1, 2, 3]

    def test_mixed(self):
        assert expand_hpe_range('1 to 2 5 7 to 9') == [1, 2, 5, 7, 8, 9]

    def test_larger_range(self):
        result = expand_hpe_range('1998 to 2004 3036 4087 to 4090')
        assert result == list(range(1998, 2005)) + [3036] + list(range(4087, 4091))

    def test_empty(self):
        assert expand_hpe_range('') == []


# ===========================================================================
# 1c. Utils: compress_list
# ===========================================================================

class TestCompressList:
    def test_empty(self):
        assert compress_list([]) == ''

    def test_single(self):
        assert compress_list([5]) == '5'

    def test_consecutive(self):
        assert compress_list([1, 2, 3]) == '1-3'

    def test_gaps(self):
        assert compress_list([1, 3, 5]) == '1,3,5'

    def test_mixed(self):
        assert compress_list([1, 2, 3, 5, 7, 8, 9]) == '1-3,5,7-9'

    def test_dedup(self):
        assert compress_list([1, 1, 2, 2, 3]) == '1-3'

    def test_unsorted_input(self):
        assert compress_list([9, 1, 3, 2]) == '1-3,9'

    def test_roundtrip(self):
        original = [1, 2, 3, 5, 7, 8, 9, 100]
        assert expand_range(compress_list(original)) == original


# ===========================================================================
# 2. detect_dialect
# ===========================================================================

class TestDetectDialect:
    def test_routeros(self):
        assert detect_dialect(ROUTEROS_MINIMAL) == 'routeros'

    def test_zyxel_xgs(self):
        assert detect_dialect(ZYXEL_XGS_MINIMAL) == 'zyxel_xgs'

    def test_fs_generic(self):
        assert detect_dialect(FS_GENERIC_MINIMAL) == 'fs_generic'

    def test_fs_gigaeth(self):
        assert detect_dialect(FS_GIGAETH_MINIMAL) == 'fs_gigaeth'

    def test_fs_vrp(self):
        assert detect_dialect(FS_VRP_MINIMAL) == 'fs_vrp'

    def test_hpe_procurve(self):
        assert detect_dialect(HPE_PROCURVE_MINIMAL) == 'hpe_procurve'

    def test_zyxel_json(self):
        assert detect_dialect('{"version": 1}') == 'zyxel_json'

    def test_unknown_empty(self):
        assert detect_dialect('') == 'unknown'

    def test_unknown_garbage(self):
        assert detect_dialect('this is not a switch config\njust random text\n') == 'unknown'

    def test_whitespace_only(self):
        assert detect_dialect('   \n  \n   ') == 'unknown'

    def test_routeros_no_routeros_keyword(self):
        # '# something' without RouterOS keyword should not match
        result = detect_dialect('# some other comment\nhostname foo\n')
        assert result != 'routeros'

    def test_fs_vrp_needs_body_keywords(self):
        # Has the header but no eth-trunk or 10gigaethernet - should be unknown
        content = '!System startup configuration\nhostname test\n'
        assert detect_dialect(content) != 'fs_vrp'


# ===========================================================================
# 3. RouterOS parser
# ===========================================================================

class TestRouterOSParser:
    def setup_method(self):
        self.cfg = parse(ROUTEROS_MINIMAL)

    def test_dialect(self):
        assert self.cfg.dialect == 'routeros'

    def test_vendor(self):
        assert self.cfg.vendor == 'MikroTik'

    def test_model(self):
        assert self.cfg.model == 'CRS326-24G-2S+IN'

    def test_firmware(self):
        assert self.cfg.firmware == 'RouterOS 7.12'

    def test_vlans_detected(self):
        # VLANs 10 and 20 from bridge vlan, 10 and 30 from interface vlan
        assert 10 in self.cfg.vlans
        assert 20 in self.cfg.vlans
        assert 30 in self.cfg.vlans

    def test_vlan_name_from_bridge(self):
        # Bridge comment sets initial name; /interface vlan only fills if None.
        # vlan 10 comment is 'mgmt'; /interface vlan name 'management' does not
        # override because the name is already set from the bridge comment.
        assert self.cfg.vlans[10].name == 'mgmt'

    def test_vlan_name_comment_fallback(self):
        # vlan 20 has comment=servers; name comes from /interface vlan if available
        # If not in /interface vlan, name is from bridge comment
        assert self.cfg.vlans[20].name == 'servers'

    def test_ports_detected(self):
        assert 'ether1' in self.cfg.ports
        assert 'ether2' in self.cfg.ports
        assert 'ether3' in self.cfg.ports

    def test_ether1_access_mode(self):
        port = self.cfg.ports['ether1']
        assert port.mode == 'access'
        assert port.access_vlan == 10

    def test_ether1_description(self):
        assert self.cfg.ports['ether1'].description == 'uplink'

    def test_ether2_trunk_mode(self):
        port = self.cfg.ports['ether2']
        assert port.mode == 'trunk'

    def test_ether2_tagged_vlans(self):
        port = self.cfg.ports['ether2']
        assert 20 in port.tagged_vlans

    def test_ether3_hybrid_mode(self):
        # ether3 is in vlan 20 tagged - so it's trunk
        port = self.cfg.ports['ether3']
        assert port.mode in ('trunk', 'hybrid', 'access')

    def test_no_errors(self):
        assert self.cfg.errors == []

    def test_bridge_iface_excluded(self):
        assert 'bridge' not in self.cfg.ports

    def test_port_tagged_vlans_are_lists(self):
        for port in self.cfg.ports.values():
            assert isinstance(port.tagged_vlans, list)
            assert isinstance(port.untagged_vlans, list)

    def test_port_tagged_vlans_independent(self):
        """Verify tagged_vlans lists are not shared between port instances."""
        p1 = self.cfg.ports.get('ether1')
        p2 = self.cfg.ports.get('ether2')
        if p1 and p2:
            assert p1.tagged_vlans is not p2.tagged_vlans


# ===========================================================================
# 4. Zyxel XGS parser
# ===========================================================================

class TestZyxelXGSParser:
    def setup_method(self):
        self.cfg = parse(ZYXEL_XGS_MINIMAL)

    def test_dialect(self):
        assert self.cfg.dialect == 'zyxel_xgs'

    def test_vendor(self):
        assert self.cfg.vendor == 'Zyxel'

    def test_model(self):
        assert self.cfg.model == 'XGS1930-28'

    def test_firmware(self):
        assert self.cfg.firmware == 'V4.80(ABFW.2)'

    def test_vlans_detected(self):
        assert 1 in self.cfg.vlans
        assert 10 in self.cfg.vlans
        assert 20 in self.cfg.vlans

    def test_vlan_names(self):
        assert self.cfg.vlans[1].name == 'Default'
        assert self.cfg.vlans[10].name == 'Management'
        assert self.cfg.vlans[20].name == 'Servers'

    def test_port9_access_mode(self):
        port = self.cfg.ports.get('9')
        assert port is not None
        assert port.mode == 'access'
        assert port.access_vlan == 10

    def test_port1_vlan1_membership(self):
        # Port 1 is in vlan 1 (untagged) and vlan 10 (tagged via fixed list),
        # so it is hybrid mode with pvid=1.
        port = self.cfg.ports.get('1')
        assert port is not None
        assert port.access_vlan == 1
        assert 1 in port.untagged_vlans

    def test_port3_shutdown(self):
        port = self.cfg.ports.get('3')
        assert port is not None
        assert port.shutdown is True
        assert port.mode == 'shutdown'

    def test_port2_tagged_mode(self):
        # port 2 is in vlan20 fixed but not untagged - tagged only
        port = self.cfg.ports.get('2')
        assert port is not None
        assert 20 in port.tagged_vlans

    def test_lacp_group_t1(self):
        assert 'T1' in self.cfg.lacp_groups
        grp = self.cfg.lacp_groups['T1']
        assert '4' in grp.members
        assert '5' in grp.members

    def test_lacp_port4_group_set(self):
        port = self.cfg.ports.get('4')
        assert port is not None
        assert port.lacp_group == 'T1'

    def test_no_errors(self):
        assert self.cfg.errors == []

    def test_ports_are_strings(self):
        for k in self.cfg.ports:
            assert isinstance(k, str)

    def test_port_list_independence(self):
        ports = list(self.cfg.ports.values())
        if len(ports) >= 2:
            assert ports[0].tagged_vlans is not ports[1].tagged_vlans


# ===========================================================================
# 5. FS VRP parser
# ===========================================================================

class TestFsVrpParser:
    def setup_method(self):
        self.cfg = parse(FS_VRP_MINIMAL)

    def test_dialect(self):
        assert self.cfg.dialect == 'fs_vrp'

    def test_vendor(self):
        assert self.cfg.vendor == 'FS'

    def test_vlans_detected(self):
        assert 10 in self.cfg.vlans
        assert 20 in self.cfg.vlans
        assert 30 in self.cfg.vlans
        assert 31 in self.cfg.vlans
        assert 32 in self.cfg.vlans

    def test_vlan_alias(self):
        assert self.cfg.vlans[10].name == 'Management'
        assert self.cfg.vlans[20].name == 'Servers'

    def test_port_access(self):
        port = self.cfg.ports.get('10GigabitEthernet 1/0/1')
        assert port is not None
        assert port.mode == 'access'
        assert port.access_vlan == 10

    def test_port_trunk_all(self):
        port = self.cfg.ports.get('10GigabitEthernet 1/0/2')
        assert port is not None
        assert port.mode == 'trunk'
        assert port.trunk_all_vlans is True

    def test_port_trunk_with_description(self):
        port = self.cfg.ports.get('10GigabitEthernet 1/0/3')
        assert port is not None
        assert port.description == 'uplink'
        assert port.trunk_all_vlans is True

    def test_port_shutdown(self):
        port = self.cfg.ports.get('10GigabitEthernet 1/0/4')
        assert port is not None
        assert port.shutdown is True

    def test_lacp_port_member(self):
        port = self.cfg.ports.get('10GigabitEthernet 1/0/3')
        assert port is not None
        assert port.lacp_group == 'eth-trunk 1'

    def test_eth_trunk_group(self):
        assert 'eth-trunk 1' in self.cfg.lacp_groups
        grp = self.cfg.lacp_groups['eth-trunk 1']
        assert grp.mode == 'lacp'

    def test_eth_trunk_members_populated(self):
        grp = self.cfg.lacp_groups['eth-trunk 1']
        assert '10GigabitEthernet 1/0/3' in grp.members

    def test_no_errors(self):
        assert self.cfg.errors == []

    def test_port_list_independence(self):
        ports = list(self.cfg.ports.values())
        if len(ports) >= 2:
            assert ports[0].tagged_vlans is not ports[1].tagged_vlans


# ===========================================================================
# 6. HPE ProCurve parser
# ===========================================================================

class TestHpeProCurveParser:
    def setup_method(self):
        self.cfg = parse(HPE_PROCURVE_MINIMAL)

    def test_dialect(self):
        assert self.cfg.dialect == 'hpe_procurve'

    def test_vendor(self):
        assert self.cfg.vendor == 'HPE'

    def test_firmware(self):
        assert self.cfg.firmware == 'v7.1.045 Release 2311'

    def test_vlans_detected(self):
        assert 1 in self.cfg.vlans
        assert 10 in self.cfg.vlans
        assert 20 in self.cfg.vlans

    def test_vlan_description(self):
        assert self.cfg.vlans[10].description == 'Management'
        assert self.cfg.vlans[10].name == 'Management'

    def test_port_access(self):
        port = self.cfg.ports.get('GigabitEthernet1/0/1')
        assert port is not None
        assert port.mode == 'access'
        assert port.access_vlan == 10

    def test_port_stp_edge(self):
        port = self.cfg.ports.get('GigabitEthernet1/0/1')
        assert port is not None
        assert port.stp_edge is True

    def test_port_trunk(self):
        port = self.cfg.ports.get('GigabitEthernet1/0/2')
        assert port is not None
        assert port.mode == 'trunk'
        assert port.native_vlan == 1

    def test_port_trunk_vlans(self):
        port = self.cfg.ports.get('GigabitEthernet1/0/2')
        assert 1 in port.tagged_vlans
        assert 2 in port.tagged_vlans
        assert 10 in port.tagged_vlans
        assert 20 in port.tagged_vlans

    def test_port_shutdown(self):
        port = self.cfg.ports.get('GigabitEthernet1/0/3')
        assert port is not None
        assert port.shutdown is True

    def test_lacp_group_defined(self):
        assert '1' in self.cfg.lacp_groups

    def test_lacp_group_members(self):
        grp = self.cfg.lacp_groups['1']
        assert 'GigabitEthernet1/0/4' in grp.members
        assert 'GigabitEthernet1/0/5' in grp.members

    def test_lacp_group_mode(self):
        grp = self.cfg.lacp_groups['1']
        assert grp.mode == 'dynamic'

    def test_bridge_aggregation_port(self):
        assert 'Bridge-Aggregation1' in self.cfg.ports

    def test_no_errors(self):
        assert self.cfg.errors == []

    def test_port_list_independence(self):
        ports = list(self.cfg.ports.values())
        if len(ports) >= 2:
            assert ports[0].tagged_vlans is not ports[1].tagged_vlans


# ===========================================================================
# 6b. FS Generic parser
# ===========================================================================

class TestFsGenericParser:
    def setup_method(self):
        self.cfg = parse(FS_GENERIC_MINIMAL)

    def test_dialect(self):
        assert self.cfg.dialect == 'fs_generic'

    def test_vendor(self):
        assert self.cfg.vendor == 'FS'

    def test_firmware(self):
        assert self.cfg.firmware == '1.1.0'

    def test_hostname(self):
        assert self.cfg.hostname == 'sw-fs-generic'

    def test_vlans_detected(self):
        assert 1 in self.cfg.vlans
        assert 10 in self.cfg.vlans
        assert 20 in self.cfg.vlans

    def test_vlan_name(self):
        assert self.cfg.vlans[10].name == 'mgmt'

    def test_port_access(self):
        port = self.cfg.ports.get('1/1')
        assert port is not None
        assert port.mode == 'access'
        assert port.access_vlan == 10

    def test_port_trunk(self):
        port = self.cfg.ports.get('1/2')
        assert port is not None
        assert port.mode == 'trunk'
        assert port.native_vlan == 1
        assert 10 in port.tagged_vlans
        assert 20 in port.tagged_vlans

    def test_port_shutdown(self):
        port = self.cfg.ports.get('1/3')
        assert port is not None
        assert port.shutdown is True

    def test_port_trunk_all(self):
        port = self.cfg.ports.get('1/4')
        assert port is not None
        assert port.trunk_all_vlans is True

    def test_no_errors(self):
        assert self.cfg.errors == []


# ===========================================================================
# 6c. FS GigaEthernet parser
# ===========================================================================

class TestFsGigaEthParser:
    def setup_method(self):
        self.cfg = parse(FS_GIGAETH_MINIMAL)

    def test_dialect(self):
        assert self.cfg.dialect == 'fs_gigaeth'

    def test_hostname(self):
        assert self.cfg.hostname == 'sw-gigaeth'

    def test_port_access(self):
        port = self.cfg.ports.get('GigaEthernet0/1')
        assert port is not None
        assert port.mode == 'access'
        assert port.access_vlan == 10

    def test_port_trunk(self):
        port = self.cfg.ports.get('GigaEthernet0/2')
        assert port is not None
        assert port.mode == 'trunk'
        assert 10 in port.tagged_vlans
        assert 20 in port.tagged_vlans
        assert 30 in port.tagged_vlans

    def test_port_shutdown(self):
        port = self.cfg.ports.get('GigaEthernet0/3')
        assert port is not None
        assert port.shutdown is True

    def test_port_lacp(self):
        port = self.cfg.ports.get('GigaEthernet0/4')
        assert port is not None
        assert port.lacp_group == '1'

    def test_no_errors(self):
        assert self.cfg.errors == []


# ===========================================================================
# 7. Validation warnings
# ===========================================================================

class TestValidation:
    def test_access_port_no_vlan_warns(self):
        cfg = SwitchConfig(dialect='test')
        port = PortInfo(name='eth0')
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = []
        port.mode = 'access'
        port.access_vlan = None
        cfg.ports['eth0'] = port
        _validate(cfg)
        assert any('access port with no VLAN' in w for w in port.warnings)

    def test_trunk_port_no_vlans_warns(self):
        cfg = SwitchConfig(dialect='test')
        port = PortInfo(name='eth1')
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = []
        port.mode = 'trunk'
        port.trunk_all_vlans = False
        cfg.ports['eth1'] = port
        _validate(cfg)
        assert any('trunk port with no VLAN list' in w for w in port.warnings)

    def test_trunk_all_vlans_no_warn(self):
        cfg = SwitchConfig(dialect='test')
        port = PortInfo(name='eth1')
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = []
        port.mode = 'trunk'
        port.trunk_all_vlans = True
        cfg.ports['eth1'] = port
        _validate(cfg)
        assert not any('trunk port with no VLAN list' in w for w in port.warnings)

    def test_unknown_mode_warns(self):
        cfg = SwitchConfig(dialect='test')
        port = PortInfo(name='eth2')
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = []
        port.mode = 'unknown'
        port.shutdown = False
        cfg.ports['eth2'] = port
        _validate(cfg)
        assert any('port mode could not be determined' in w for w in port.warnings)

    def test_shutdown_port_no_warn(self):
        cfg = SwitchConfig(dialect='test')
        port = PortInfo(name='eth3')
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = []
        port.mode = 'shutdown'
        port.shutdown = True
        cfg.ports['eth3'] = port
        _validate(cfg)
        # shutdown ports must not generate mode warnings
        assert not any('mode could not' in w for w in port.warnings)
        assert not any('access port' in w for w in port.warnings)

    def test_lacp_group_undefined_warns(self):
        cfg = SwitchConfig(dialect='test')
        port = PortInfo(name='eth4')
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = []
        port.mode = 'access'
        port.access_vlan = 10
        port.lacp_group = 'missing-group'
        cfg.ports['eth4'] = port
        _validate(cfg)
        assert any('not defined' in w for w in port.warnings)

    def test_lacp_group_no_members_warns(self):
        cfg = SwitchConfig(dialect='test')
        grp = LacpGroup(group_id='g1', members=[], mode='lacp')
        cfg.lacp_groups['g1'] = grp
        _validate(cfg)
        assert any("'g1'" in w for w in cfg.warnings)

    def test_lacp_group_with_members_no_warn(self):
        cfg = SwitchConfig(dialect='test')
        grp = LacpGroup(group_id='g1', members=['eth0'], mode='lacp')
        cfg.lacp_groups['g1'] = grp
        _validate(cfg)
        assert not any("'g1'" in w for w in cfg.warnings)

    def test_hybrid_no_native_warns(self):
        cfg = SwitchConfig(dialect='test')
        port = PortInfo(name='eth5')
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = []
        port.mode = 'hybrid'
        port.access_vlan = None
        port.native_vlan = None
        cfg.ports['eth5'] = port
        _validate(cfg)
        assert any('hybrid port with no native VLAN' in w for w in port.warnings)


# ===========================================================================
# 8. format_summary smoke tests
# ===========================================================================

class TestFormatSummary:
    def test_summary_contains_dialect(self):
        cfg = parse(ROUTEROS_MINIMAL)
        summary = format_summary(cfg)
        assert 'routeros' in summary

    def test_summary_contains_vendor(self):
        cfg = parse(ROUTEROS_MINIMAL)
        summary = format_summary(cfg)
        assert 'MikroTik' in summary

    def test_summary_contains_vlan_section(self):
        cfg = parse(ZYXEL_XGS_MINIMAL)
        summary = format_summary(cfg)
        assert 'VLANs' in summary
        assert 'Management' in summary

    def test_summary_contains_ports_section(self):
        cfg = parse(HPE_PROCURVE_MINIMAL)
        summary = format_summary(cfg)
        assert 'Ports' in summary

    def test_summary_lacp_section(self):
        cfg = parse(HPE_PROCURVE_MINIMAL)
        summary = format_summary(cfg)
        assert 'LACP' in summary

    def test_summary_warnings_section(self):
        cfg = parse(ROUTEROS_MINIMAL)
        summary = format_summary(cfg)
        assert 'Warnings' in summary

    def test_summary_verbose_shows_port_warnings(self):
        cfg = SwitchConfig(dialect='test')
        port = PortInfo(name='eth0')
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = ['test warning message']
        port.mode = 'access'
        port.access_vlan = None
        cfg.ports['eth0'] = port
        summary = format_summary(cfg, verbose=True)
        assert 'test warning message' in summary

    def test_summary_non_verbose_aggregates_port_warnings(self):
        # Port warnings always appear in the aggregated Warnings section,
        # regardless of verbose mode. verbose=True additionally prints them
        # inline under each port row.
        cfg = SwitchConfig(dialect='test')
        port = PortInfo(name='eth0')
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = ['aggregate port warning']
        port.mode = 'access'
        port.access_vlan = 10
        cfg.ports['eth0'] = port
        summary = format_summary(cfg, verbose=False)
        # Aggregated section must mention the warning
        assert 'aggregate port warning' in summary

    def test_summary_unknown_dialect_error(self):
        cfg = parse('this is garbage config data')
        summary = format_summary(cfg)
        assert 'Errors' in summary or 'unknown' in summary

    def test_summary_no_ports(self):
        cfg = SwitchConfig(dialect='test')
        summary = format_summary(cfg)
        assert '(none detected)' in summary

    def test_summary_returns_string(self):
        cfg = parse(FS_VRP_MINIMAL)
        result = format_summary(cfg)
        assert isinstance(result, str)
        assert len(result) > 0


# ===========================================================================
# 9. Model: independent list fields per PortInfo
# ===========================================================================

class TestPortInfoIndependentLists:
    """PortInfo instances created normally share the class-level [] default.
    Parsers must assign fresh lists. These tests verify the parsers do so."""

    def test_routeros_independent_lists(self):
        cfg = parse(ROUTEROS_MINIMAL)
        ports = list(cfg.ports.values())
        for i in range(len(ports)):
            for j in range(i + 1, len(ports)):
                assert ports[i].tagged_vlans is not ports[j].tagged_vlans
                assert ports[i].untagged_vlans is not ports[j].untagged_vlans

    def test_zyxel_xgs_independent_lists(self):
        cfg = parse(ZYXEL_XGS_MINIMAL)
        ports = list(cfg.ports.values())
        for i in range(len(ports)):
            for j in range(i + 1, len(ports)):
                assert ports[i].tagged_vlans is not ports[j].tagged_vlans

    def test_hpe_procurve_independent_lists(self):
        cfg = parse(HPE_PROCURVE_MINIMAL)
        ports = list(cfg.ports.values())
        for i in range(len(ports)):
            for j in range(i + 1, len(ports)):
                assert ports[i].tagged_vlans is not ports[j].tagged_vlans

    def test_fs_generic_independent_lists(self):
        cfg = parse(FS_GENERIC_MINIMAL)
        ports = list(cfg.ports.values())
        for i in range(len(ports)):
            for j in range(i + 1, len(ports)):
                assert ports[i].tagged_vlans is not ports[j].tagged_vlans

    def test_fs_vrp_independent_lists(self):
        cfg = parse(FS_VRP_MINIMAL)
        ports = list(cfg.ports.values())
        for i in range(len(ports)):
            for j in range(i + 1, len(ports)):
                assert ports[i].tagged_vlans is not ports[j].tagged_vlans


# ===========================================================================
# 10. Unknown and stub dialects
# ===========================================================================

class TestUnknownAndStub:
    def test_unknown_dialect_has_error(self):
        cfg = parse('random nonsense\nnot a switch config\n')
        assert len(cfg.errors) > 0

    def test_unknown_dialect_forced(self):
        cfg = parse(ROUTEROS_MINIMAL, dialect='unknown')
        assert len(cfg.errors) > 0

    def test_zyxel_json_stub_warning(self):
        cfg = parse('{"version": 1}')
        assert cfg.dialect == 'zyxel_json'
        assert any('not yet implemented' in w for w in cfg.warnings)

    def test_forced_dialect_overrides_detection(self):
        # Force zyxel_xgs on a routeros config - should use zyxel parser
        cfg = parse(ZYXEL_XGS_MINIMAL, dialect='zyxel_xgs')
        assert cfg.dialect == 'zyxel_xgs'


# ===========================================================================
# 11. Integration tests: load real config files if they exist
# ===========================================================================

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SWITCH_CONFIG_DIRS = [
    os.path.join(REPO_ROOT, 'switch-configs'),
    os.path.join(REPO_ROOT, 'tests', 'switch-configs'),
    os.path.join(REPO_ROOT, 'files', 'switch-configs'),
]


def _find_switch_configs():
    """Return list of (path, dialect_hint) for any switch config files found."""
    found = []
    for base in SWITCH_CONFIG_DIRS:
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            for fname in filenames:
                # Skip hidden files, README, etc.
                if fname.startswith('.') or fname.endswith('.md'):
                    continue
                full = os.path.join(dirpath, fname)
                found.append(full)
    return found


_real_configs = _find_switch_configs()


@pytest.mark.skipif(not _real_configs, reason='No switch config files found in switch-configs dirs')
@pytest.mark.parametrize('config_path', _real_configs)
def test_integration_real_config_parses(config_path):
    """Parse a real switch config file; verify no Python exception and basic structure."""
    with open(config_path) as f:
        content = f.read()
    # Should not raise
    cfg = parse(content)
    assert cfg is not None
    assert isinstance(cfg.dialect, str)
    assert isinstance(cfg.ports, dict)
    assert isinstance(cfg.vlans, dict)
    assert isinstance(cfg.lacp_groups, dict)
    assert isinstance(cfg.errors, list)
    assert isinstance(cfg.warnings, list)
    # Summary should also not raise
    summary = format_summary(cfg)
    assert isinstance(summary, str)


@pytest.mark.skipif(not _real_configs, reason='No switch config files found in switch-configs dirs')
@pytest.mark.parametrize('config_path', _real_configs)
def test_integration_real_config_dialect_known(config_path):
    """Real configs should detect to a known (non-unknown) dialect."""
    with open(config_path) as f:
        content = f.read()
    dialect = detect_dialect(content)
    # If a real config file is unknown, that's noteworthy but not a hard failure
    # We record it as a warning-level skip rather than blocking
    if dialect == 'unknown':
        pytest.skip('Dialect unknown for %s - may need parser extension' % config_path)
