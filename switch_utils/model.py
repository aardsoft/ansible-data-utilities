from dataclasses import dataclass, field


@dataclass
class VlanInfo:
    vlan_id: int
    name = None
    description = None

    def __init__(self, vlan_id, name=None, description=None):
        self.vlan_id = vlan_id
        self.name = name
        self.description = description


@dataclass
class PortInfo:
    name: str

    def __init__(self, name, description=None):
        self.name = name
        self.description = description
        self.mode = 'unknown'       # access, trunk, hybrid, uplink, shutdown, unknown
        self.access_vlan = None     # int, for access mode
        self.native_vlan = None     # int, pvid for trunk/hybrid
        self.tagged_vlans = []      # list of int
        self.untagged_vlans = []    # list of int (for hybrid)
        self.trunk_all_vlans = False
        self.lacp_group = None      # group id string
        self.shutdown = False
        self.stp_edge = False
        self.warnings = []


@dataclass
class LacpGroup:
    group_id: str

    def __init__(self, group_id, members=None, mode=None):
        self.group_id = group_id
        self.members = members or []
        self.mode = mode


@dataclass
class SwitchConfig:
    dialect: str

    def __init__(self, dialect, vendor=None):
        self.dialect = dialect
        self.vendor = vendor
        self.model = None
        self.firmware = None
        self.hostname = None
        self.vlans = {}         # int -> VlanInfo
        self.ports = {}         # str -> PortInfo
        self.lacp_groups = {}   # str -> LacpGroup
        self.errors = []
        self.warnings = []
        self.unhandled = []
