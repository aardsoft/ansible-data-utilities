"""Switch config parsers for all supported dialects."""

import re
from collections import defaultdict
from .model import SwitchConfig, VlanInfo, PortInfo, LacpGroup
from .utils import expand_range, expand_hpe_range, expand_dell_port_list, compress_list
from .detect import detect_dialect


def parse(content, dialect=None):
    """Parse switch config content into SwitchConfig.

    dialect: force a specific dialect, or None to auto-detect.
    """
    if dialect is None:
        dialect = detect_dialect(content)

    parsers = {
        'routeros':     _parse_routeros,
        'zyxel_xgs':    _parse_zyxel_xgs,
        'fs_generic':   _parse_fs_generic,
        'fs_gigaeth':   _parse_fs_gigaeth,
        'fs_vrp':       _parse_fs_vrp,
        'fs_eth0':      _parse_fs_eth0,
        'fs_s3400':     _parse_fs_s3400,
        'dell_os9':     _parse_dell_os9,
        'hpe_procurve': _parse_hpe_procurve,
        'cisco_ios':    _parse_cisco_ios,
        'zyxel_json':   _parse_stub,
    }

    fn = parsers.get(dialect, _parse_unknown)
    cfg = fn(content)
    cfg.dialect = dialect
    _validate(cfg)
    return cfg


# ---------------------------------------------------------------------------
# RouterOS
# ---------------------------------------------------------------------------

def _ros_join_continuations(content):
    """Join lines ending with backslash."""
    joined = []
    pending = ''
    for line in content.split('\n'):
        if line.rstrip().endswith('\\'):
            pending += line.rstrip()[:-1] + ' '
        else:
            joined.append(pending + line)
            pending = ''
    if pending:
        joined.append(pending)
    return joined


def _ros_parse_args(line):
    """Parse RouterOS 'key=value ...' pairs from an add/set line."""
    result = {}
    # Match key=value where value is quoted or unquoted
    for m in re.finditer(r'([\w-]+)=("(?:[^"\\]|\\.)*"|[^\s]+)', line):
        key = m.group(1)
        val = m.group(2)
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        result[key] = val
    return result


def _parse_routeros(content):
    cfg = SwitchConfig(dialect='routeros', vendor='MikroTik')

    lines = _ros_join_continuations(content)

    # Extract header metadata
    for line in lines[:6]:
        m = re.match(r'# .* by RouterOS ([\d.]+)', line)
        if m:
            cfg.firmware = 'RouterOS ' + m.group(1)
        m = re.match(r'# model = (.+)', line)
        if m:
            cfg.model = m.group(1).strip()

    section = ''
    # Accumulated per-bridge-port and per-bridge-vlan data
    bridge_ports = {}   # iface -> {pvid, comment, frame_types}
    bridge_vlans = {}   # vlan_id -> {tagged: [], untagged: [], comment}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('/'):
            section = stripped
            continue
        if stripped.startswith('#'):
            continue
        if not (stripped.startswith('add ') or stripped.startswith('set ')):
            continue

        args = _ros_parse_args(stripped)

        if section == '/interface bridge port':
            iface = args.get('interface', '')
            if iface:
                bridge_ports[iface] = {
                    'pvid': int(args.get('pvid', 1)),
                    'comment': args.get('comment', ''),
                    'frame_types': args.get('frame-types', ''),
                }

        elif section == '/interface bridge vlan':
            try:
                vlan_id = int(args.get('vlan-ids', '0'))
            except ValueError:
                continue
            if not vlan_id:
                continue

            tagged_str = args.get('tagged', '')
            untagged_str = args.get('untagged', '')
            comment = args.get('comment', '')

            tagged = [v.strip() for v in tagged_str.split(',') if v.strip()]
            untagged = [v.strip() for v in untagged_str.split(',') if v.strip()]

            bridge_vlans[vlan_id] = {
                'tagged': tagged,
                'untagged': untagged,
                'comment': comment,
            }

            if vlan_id not in cfg.vlans:
                cfg.vlans[vlan_id] = VlanInfo(vlan_id=vlan_id, name=comment or None)

        elif section == '/interface vlan':
            vlan_id_str = args.get('vlan-id', '')
            name = args.get('name', '')
            try:
                vid = int(vlan_id_str)
                if vid not in cfg.vlans:
                    cfg.vlans[vid] = VlanInfo(vlan_id=vid, name=name or None)
                elif not cfg.vlans[vid].name and name:
                    cfg.vlans[vid].name = name
            except ValueError:
                pass

    # Build PortInfo
    for iface, bp in bridge_ports.items():
        if iface == 'bridge':
            continue

        port = PortInfo(name=iface, description=bp['comment'] or None)
        pvid = bp['pvid']
        port.native_vlan = pvid

        tagged_vlans = []
        untagged_vlans = []
        for vlan_id, bv in bridge_vlans.items():
            if iface in bv['tagged']:
                tagged_vlans.append(vlan_id)
            if iface in bv['untagged']:
                untagged_vlans.append(vlan_id)

        port.tagged_vlans = sorted(tagged_vlans)
        port.untagged_vlans = sorted(untagged_vlans)

        ft = bp.get('frame_types', '')
        if ft == 'admit-only-vlan-tagged':
            port.mode = 'trunk'
        elif untagged_vlans and not tagged_vlans:
            port.mode = 'access'
            port.access_vlan = untagged_vlans[0]
        elif untagged_vlans and tagged_vlans:
            port.mode = 'hybrid'
            port.access_vlan = pvid
        elif tagged_vlans:
            port.mode = 'trunk'
        else:
            port.mode = 'unknown'
            port.warnings.append('no VLAN membership in bridge vlan table')

        cfg.ports[iface] = port

    return cfg


# ---------------------------------------------------------------------------
# Zyxel XGS  (exit-terminated blocks, VLAN-centric membership)
# ---------------------------------------------------------------------------

def _parse_zyxel_xgs(content):
    cfg = SwitchConfig(dialect='zyxel_xgs', vendor='Zyxel')

    lines = content.split('\n')

    # Header
    for line in lines[:5]:
        m = re.match(r';\s*Product Name\s*=\s*(.+)', line)
        if m:
            cfg.model = m.group(1).strip()
        m = re.match(r';\s*Firmware Version\s*=\s*(.+)', line)
        if m:
            cfg.firmware = m.group(1).strip()

    # Per-port membership derived from VLAN blocks
    port_tagged = defaultdict(set)    # port_num(int) -> {vlan_id}
    port_untagged = defaultdict(set)  # port_num(int) -> {vlan_id}
    port_info = {}                    # port_num(int) -> dict
    lacp_members = defaultdict(list)  # trunk_name -> [port_num]
    lacp_modes = {}                   # trunk_name -> mode str

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        # VLAN block
        m = re.match(r'^vlan (\d+)\s*$', stripped)
        if m:
            vlan_id = int(m.group(1))
            vname = None
            fixed = set()
            untagged_ports = set()
            i += 1
            while i < len(lines):
                vl = lines[i].strip()
                if vl == 'exit':
                    break
                m2 = re.match(r'name\s+"?([^"]+)"?\s*$', vl)
                if m2:
                    vname = m2.group(1).strip()
                m2 = re.match(r'fixed\s+(.+)', vl)
                if m2:
                    fixed = set(expand_range(m2.group(1)))
                m2 = re.match(r'untagged\s+(.+)', vl)
                if m2:
                    untagged_ports = set(expand_range(m2.group(1)))
                i += 1

            cfg.vlans[vlan_id] = VlanInfo(vlan_id=vlan_id, name=vname)
            for p in fixed:
                if p in untagged_ports:
                    port_untagged[p].add(vlan_id)
                else:
                    port_tagged[p].add(vlan_id)
            i += 1
            continue

        # Interface port-channel block
        m = re.match(r'^interface port-channel\s+(\d+)\s*$', stripped)
        if m:
            pnum = int(m.group(1))
            info = {'pvid': 1, 'name': None, 'inactive': False,
                    'vlan_trunking': False, 'gvrp': False}
            i += 1
            while i < len(lines):
                pl = lines[i].strip()
                if pl == 'exit':
                    break
                m2 = re.match(r'name\s+"?([^"]+)"?', pl)
                if m2:
                    info['name'] = m2.group(1).strip().strip('"')
                m2 = re.match(r'pvid\s+(\d+)', pl)
                if m2:
                    info['pvid'] = int(m2.group(1))
                if pl == 'inactive':
                    info['inactive'] = True
                if pl == 'vlan-trunking':
                    info['vlan_trunking'] = True
                if pl == 'gvrp':
                    info['gvrp'] = True
                i += 1
            port_info[pnum] = info
            i += 1
            continue

        # Trunk lacp mode
        m = re.match(r'^trunk\s+(\S+)\s+lacp', stripped)
        if m:
            lacp_modes[m.group(1)] = 'lacp'
            i += 1
            continue

        # Trunk interface membership
        m = re.match(r'^trunk\s+(\S+)\s+interface\s+(\d+)', stripped)
        if m:
            lacp_members[m.group(1)].append(int(m.group(2)))
            i += 1
            continue

        i += 1

    # Build PortInfo
    all_ports = (set(port_info.keys()) | set(port_tagged.keys()) |
                 set(port_untagged.keys()))
    for pnum in sorted(all_ports):
        info = port_info.get(pnum, {'pvid': 1, 'name': None, 'inactive': False,
                                    'vlan_trunking': False, 'gvrp': False})
        port = PortInfo(name=str(pnum), description=info.get('name'))
        port.tagged_vlans = []
        port.untagged_vlans = []
        port.warnings = []
        port.native_vlan = info.get('pvid', 1)

        if info.get('inactive'):
            port.shutdown = True
            port.mode = 'shutdown'
        elif info.get('vlan_trunking') or info.get('gvrp'):
            port.mode = 'trunk'
            port.tagged_vlans = sorted(port_tagged.get(pnum, set()))
        else:
            tagged = sorted(port_tagged.get(pnum, set()))
            untagged = sorted(port_untagged.get(pnum, set()))
            port.tagged_vlans = tagged
            port.untagged_vlans = untagged
            if untagged and not tagged:
                port.mode = 'access'
                port.access_vlan = untagged[0]
            elif untagged and tagged:
                port.mode = 'hybrid'
                port.access_vlan = info.get('pvid')
            elif tagged:
                port.mode = 'tagged_only'
            else:
                port.mode = 'unknown'

        cfg.ports[str(pnum)] = port

    # LACP groups
    for tname, members in lacp_members.items():
        grp = LacpGroup(group_id=tname, members=[str(p) for p in sorted(members)],
                        mode=lacp_modes.get(tname, 'lacp'))
        cfg.lacp_groups[tname] = grp
        for p in members:
            key = str(p)
            if key in cfg.ports:
                cfg.ports[key].lacp_group = tname

    return cfg


# ---------------------------------------------------------------------------
# FS Generic  (ethernet 1/N, vlan database, IOS-like but distinct dialect)
# ---------------------------------------------------------------------------

def _parse_fs_generic(content):
    cfg = SwitchConfig(dialect='fs_generic', vendor='FS')

    m = re.search(r'!<Version>([\d.]+)</Version>', content)
    if m:
        cfg.firmware = m.group(1)

    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        # Hostname
        m = re.match(r'^hostname\s+(\S+)', stripped)
        if m:
            cfg.hostname = m.group(1)

        # VLAN database
        if stripped == 'vlan database':
            i += 1
            while i < len(lines):
                orig = lines[i]
                vl = orig.strip()
                # Exit vlan database block when we hit a non-indented non-empty line
                # that is not a comment. Must check original line indentation.
                if orig and not orig[0].isspace() and vl and not vl.startswith('!'):
                    break
                if vl == '!':
                    break
                m2 = re.match(r'(?:VLAN|vlan)\s+([\d,\-]+)(?:\s+name\s+(\S+))?', vl)
                if m2:
                    for vid in expand_range(m2.group(1)):
                        if vid not in cfg.vlans:
                            cfg.vlans[vid] = VlanInfo(vlan_id=vid, name=m2.group(2))
                i += 1
            continue

        # Interface block
        m = re.match(r'^interface ethernet\s+(\S+)', stripped)
        if m:
            iface = m.group(1)
            port = PortInfo(name=iface)
            port.tagged_vlans = []
            port.untagged_vlans = []
            port.warnings = []
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls and pls != '!':
                    break
                if pls == '!':
                    break
                _parse_fs_port_line(pls, port, cfg)
                i += 1
            cfg.ports[iface] = port
            continue

        i += 1

    return cfg


def _parse_fs_port_line(line, port, cfg):
    """Parse a single port config line (FS dialects)."""
    if line == 'shutdown':
        port.shutdown = True
        port.mode = 'shutdown'
    elif re.match(r'description\s+"?(.+)"?', line):
        m = re.match(r'description\s+"?(.+?)"?\s*$', line)
        if m:
            port.description = m.group(1)
    elif line == 'switchport mode access':
        port.mode = 'access'
    elif line == 'switchport mode trunk':
        port.mode = 'trunk'
    elif re.match(r'switchport access vlan\s+(\d+)', line):
        m = re.match(r'switchport access vlan\s+(\d+)', line)
        port.access_vlan = int(m.group(1))
        port.native_vlan = int(m.group(1))
        if port.mode == 'unknown':
            port.mode = 'access'
    elif re.match(r'switchport trunk native vlan\s+(\d+)', line):
        m = re.match(r'switchport trunk native vlan\s+(\d+)', line)
        port.native_vlan = int(m.group(1))
    elif re.match(r'switchport trunk allowed vlan all', line):
        port.trunk_all_vlans = True
        if port.mode == 'unknown':
            port.mode = 'trunk'
    elif re.match(r'switchport trunk allowed vlan add\s+(.+)', line):
        m = re.match(r'switchport trunk allowed vlan add\s+(.+)', line)
        port.tagged_vlans = sorted(set(port.tagged_vlans) | set(expand_range(m.group(1))))
        if port.mode == 'unknown':
            port.mode = 'trunk'
    elif re.match(r'switchport trunk allowed vlan remove\s+(.+)', line):
        m = re.match(r'switchport trunk allowed vlan remove\s+(.+)', line)
        port.tagged_vlans = sorted(set(port.tagged_vlans) - set(expand_range(m.group(1))))
    elif re.match(r'switchport trunk allowed vlan\s+(.+)', line):
        m = re.match(r'switchport trunk allowed vlan\s+(.+)', line)
        port.tagged_vlans = sorted(expand_range(m.group(1)))
        if port.mode == 'unknown':
            port.mode = 'trunk'
    elif re.match(r'switchport hybrid allowed vlan add\s+([\d,\-]+)\s+tagged', line):
        m = re.match(r'switchport hybrid allowed vlan add\s+([\d,\-]+)\s+tagged', line)
        port.tagged_vlans = sorted(set(port.tagged_vlans) | set(expand_range(m.group(1))))
        if port.mode == 'unknown':
            port.mode = 'hybrid'
    elif re.match(r'switchport pvid\s+(\d+)', line):
        m = re.match(r'switchport pvid\s+(\d+)', line)
        port.native_vlan = int(m.group(1))
        if port.mode == 'unknown':
            port.mode = 'access'
            port.access_vlan = int(m.group(1))
    elif re.match(r'channel-group\s+(\d+)\s+mode\s+(\S+)', line):
        m = re.match(r'channel-group\s+(\d+)\s+mode\s+(\S+)', line)
        port.lacp_group = m.group(1)
    elif re.match(r'switchport acceptable-frame-types tagged', line):
        if port.mode == 'unknown':
            port.mode = 'trunk'


# ---------------------------------------------------------------------------
# FS GigaEthernet  (GigaEthernet0/N, switchport pvid, trunk vlan-allowed)
# ---------------------------------------------------------------------------

def _parse_fs_gigaeth(content):
    cfg = SwitchConfig(dialect='fs_gigaeth', vendor='FS')

    m = re.search(r'!version\s+([\d.]+J[^\s]*)', content)
    if m:
        cfg.firmware = m.group(1)

    lines = content.split('\n')
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        m = re.match(r'^hostname\s+(\S+)', stripped)
        if m:
            cfg.hostname = m.group(1)

        m = re.match(r'^interface\s+(GigaEthernet\S+|Port-aggregator\S*)', stripped,
                     re.IGNORECASE)
        if m:
            iface = m.group(1)
            port = PortInfo(name=iface)
            port.tagged_vlans = []
            port.untagged_vlans = []
            port.warnings = []
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls and pls != '!':
                    break
                if pls == '!':
                    break
                _parse_fs_gigaeth_port_line(pls, port, cfg)
                i += 1

            if iface.lower().startswith('port-aggregator'):
                # LACP group aggregator interface
                gid = re.sub(r'^port-aggregator', '', iface, flags=re.IGNORECASE).strip()
                if gid not in cfg.lacp_groups:
                    cfg.lacp_groups[gid] = LacpGroup(group_id=gid)
                if port.native_vlan is not None:
                    cfg.lacp_groups[gid].mode = 'lacp'
            else:
                cfg.ports[iface] = port
            continue

        i += 1

    # Register VLANs seen in port assignments
    for port in cfg.ports.values():
        if port.access_vlan and port.access_vlan not in cfg.vlans:
            cfg.vlans[port.access_vlan] = VlanInfo(vlan_id=port.access_vlan)
        for v in port.tagged_vlans:
            if v not in cfg.vlans:
                cfg.vlans[v] = VlanInfo(vlan_id=v)

    return cfg


def _parse_fs_gigaeth_port_line(line, port, cfg):
    if line == 'shutdown':
        port.shutdown = True
        port.mode = 'shutdown'
    elif re.match(r'description\s+"?(.+?)"?\s*$', line):
        m = re.match(r'description\s+"?(.+?)"?\s*$', line)
        if m:
            port.description = m.group(1)
    elif re.match(r'switchport pvid\s+(\d+)', line):
        m = re.match(r'switchport pvid\s+(\d+)', line)
        port.native_vlan = int(m.group(1))
        port.access_vlan = int(m.group(1))
        if port.mode == 'unknown':
            port.mode = 'access'
    elif re.match(r'switchport mode trunk', line):
        port.mode = 'trunk'
    elif re.match(r'switchport trunk vlan-allowed\s+(.+)', line):
        m = re.match(r'switchport trunk vlan-allowed\s+(.+)', line)
        port.tagged_vlans = sorted(expand_range(m.group(1)))
    elif re.match(r'switchport trunk vlan-untagged\s+(\d+)', line):
        m = re.match(r'switchport trunk vlan-untagged\s+(\d+)', line)
        vid = int(m.group(1))
        port.native_vlan = vid
        if vid not in port.untagged_vlans:
            port.untagged_vlans.append(vid)
    elif re.match(r'channel-group\s+(\d+)', line):
        m = re.match(r'channel-group\s+(\d+)', line)
        port.lacp_group = m.group(1)


# ---------------------------------------------------------------------------
# FS VRP-like  (N5850, 10gigaethernet 1/0/N, eth-trunk)
# ---------------------------------------------------------------------------

def _parse_fs_vrp(content):
    cfg = SwitchConfig(dialect='fs_vrp', vendor='FS')

    m = re.search(r'hostname\s+(\S+)', content)
    if m:
        cfg.model = m.group(1)

    lines = content.split('\n')
    i = 0
    trunk_members = defaultdict(list)  # trunk_id -> [iface]

    while i < len(lines):
        stripped = lines[i].strip()

        # VLAN definition
        m = re.match(r'^vlan\s+([\d,\-]+)\s*$', stripped)
        if m:
            for vid in expand_range(m.group(1)):
                if vid not in cfg.vlans:
                    cfg.vlans[vid] = VlanInfo(vlan_id=vid)
            # Check next line for alias
            if i + 1 < len(lines):
                al = lines[i + 1].strip()
                m2 = re.match(r'alias\s+"?([^"]+)"?', al)
                if m2 and len(expand_range(m.group(1))) == 1:
                    vid = expand_range(m.group(1))[0]
                    cfg.vlans[vid].name = m2.group(1).strip()
            i += 1
            continue

        # eth-trunk interface
        m = re.match(r'^interface eth-trunk\s+(\d+)\s*$', stripped, re.IGNORECASE)
        if m:
            tid = m.group(1)
            grp = LacpGroup(group_id='eth-trunk ' + tid)
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls and pls != '!':
                    break
                if pls == '!':
                    break
                m2 = re.match(r'mode\s+(\S+)', pls)
                if m2:
                    grp.mode = m2.group(1)
                i += 1
            cfg.lacp_groups['eth-trunk ' + tid] = grp
            continue

        # Physical/10GE interface
        m = re.match(r'^interface\s+(10giga(?:bit)?ethernet\s+\S+|giga(?:bit)?ethernet\s+\S+)',
                     stripped, re.IGNORECASE)
        if m:
            raw_iface = m.group(1)
            # Normalise whitespace in name
            iface = ' '.join(raw_iface.split())
            port = PortInfo(name=iface)
            port.tagged_vlans = []
            port.untagged_vlans = []
            port.warnings = []
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls and pls != '!':
                    break
                if pls == '!':
                    break
                m2 = re.match(r'alias\s+"?([^"]+)"?', pls)
                if m2:
                    port.description = m2.group(1).strip().strip('"')
                elif pls == 'port link-type access':
                    port.mode = 'access'
                elif pls == 'port link-type trunk':
                    port.mode = 'trunk'
                elif re.match(r'port default vlan\s+(\d+)', pls):
                    m2 = re.match(r'port default vlan\s+(\d+)', pls)
                    port.access_vlan = int(m2.group(1))
                    port.native_vlan = int(m2.group(1))
                elif re.match(r'port hybrid pvid\s+(\d+)', pls):
                    m2 = re.match(r'port hybrid pvid\s+(\d+)', pls)
                    port.native_vlan = int(m2.group(1))
                    port.access_vlan = int(m2.group(1))
                    if port.mode == 'unknown':
                        port.mode = 'hybrid'
                elif re.match(r'port hybrid vlan\s+([\d,\-]+)\s+tagged', pls):
                    m2 = re.match(r'port hybrid vlan\s+([\d,\-]+)\s+tagged', pls)
                    port.tagged_vlans = sorted(set(port.tagged_vlans) |
                                               set(expand_range(m2.group(1))))
                    if port.mode == 'unknown':
                        port.mode = 'hybrid'
                elif re.match(r'port hybrid vlan\s+([\d,\-]+)\s+untagged', pls):
                    m2 = re.match(r'port hybrid vlan\s+([\d,\-]+)\s+untagged', pls)
                    port.untagged_vlans = sorted(set(port.untagged_vlans) |
                                                 set(expand_range(m2.group(1))))
                elif re.match(r'port trunk allow-pass vlan all', pls):
                    port.trunk_all_vlans = True
                elif re.match(r'join eth-trunk\s+(\d+)', pls):
                    m2 = re.match(r'join eth-trunk\s+(\d+)', pls)
                    tid = m2.group(1)
                    port.lacp_group = 'eth-trunk ' + tid
                    trunk_members['eth-trunk ' + tid].append(iface)
                elif pls == 'shutdown':
                    port.shutdown = True
                    port.mode = 'shutdown'
                i += 1
            cfg.ports[iface] = port
            continue

        i += 1

    # Populate LACP group members
    for gid, members in trunk_members.items():
        if gid in cfg.lacp_groups:
            cfg.lacp_groups[gid].members = members

    return cfg


# ---------------------------------------------------------------------------
# HPE ProCurve
# ---------------------------------------------------------------------------

def _parse_hpe_procurve(content):
    cfg = SwitchConfig(dialect='hpe_procurve', vendor='HPE')

    # Firmware from header comment
    for line in content.split('\n')[:10]:
        m = re.match(r'\s*version\s+([\d.]+),\s*Release\s+(\d+)', line)
        if m:
            cfg.firmware = 'v%s Release %s' % (m.group(1), m.group(2))

    lines = content.split('\n')
    i = 0
    agg_port_map = defaultdict(list)  # agg_id -> [port_names]

    while i < len(lines):
        stripped = lines[i].strip()

        # VLAN block
        m = re.match(r'^vlan\s+(\d+)\s*$', stripped)
        if m:
            vid = int(m.group(1))
            if vid not in cfg.vlans:
                cfg.vlans[vid] = VlanInfo(vlan_id=vid)
            i += 1
            while i < len(lines):
                vl = lines[i].strip()
                if not lines[i].startswith(' ') and vl and vl != '#':
                    break
                if vl == '#':
                    break
                m2 = re.match(r'description\s+(.+)', vl)
                if m2:
                    desc = m2.group(1).strip().strip('"')
                    cfg.vlans[vid].description = desc
                    if not cfg.vlans[vid].name:
                        cfg.vlans[vid].name = desc
                i += 1
            continue

        # Bridge-Aggregation interface
        m = re.match(r'^interface\s+Bridge-Aggregation(\d+)', stripped)
        if m:
            gid = m.group(1)
            grp = LacpGroup(group_id=gid)
            port = PortInfo(name='Bridge-Aggregation' + gid)
            port.tagged_vlans = []
            port.untagged_vlans = []
            port.warnings = []
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls and pls != '#':
                    break
                if pls == '#':
                    break
                if pls == 'port link-type trunk':
                    port.mode = 'trunk'
                elif re.match(r'port trunk permit vlan\s+(.+)', pls):
                    m2 = re.match(r'port trunk permit vlan\s+(.+)', pls)
                    port.tagged_vlans = expand_hpe_range(m2.group(1))
                elif re.match(r'port trunk pvid vlan\s+(\d+)', pls):
                    m2 = re.match(r'port trunk pvid vlan\s+(\d+)', pls)
                    port.native_vlan = int(m2.group(1))
                elif re.match(r'link-aggregation mode\s+(\S+)', pls):
                    m2 = re.match(r'link-aggregation mode\s+(\S+)', pls)
                    grp.mode = m2.group(1)
                i += 1
            cfg.lacp_groups[gid] = grp
            cfg.ports['Bridge-Aggregation' + gid] = port
            continue

        # GigabitEthernet interface
        m = re.match(r'^interface\s+(GigabitEthernet\S+)', stripped)
        if m:
            iface = m.group(1)
            port = PortInfo(name=iface)
            port.tagged_vlans = []
            port.untagged_vlans = []
            port.warnings = []
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls and pls != '#':
                    break
                if pls == '#':
                    break
                if pls == 'shutdown':
                    port.shutdown = True
                    port.mode = 'shutdown'
                elif re.match(r'port access vlan\s+(\d+)', pls):
                    m2 = re.match(r'port access vlan\s+(\d+)', pls)
                    port.mode = 'access'
                    port.access_vlan = int(m2.group(1))
                    port.native_vlan = int(m2.group(1))
                elif pls == 'port link-type trunk':
                    port.mode = 'trunk'
                elif re.match(r'port trunk permit vlan\s+(.+)', pls):
                    m2 = re.match(r'port trunk permit vlan\s+(.+)', pls)
                    port.tagged_vlans = expand_hpe_range(m2.group(1))
                elif re.match(r'port trunk pvid vlan\s+(\d+)', pls):
                    m2 = re.match(r'port trunk pvid vlan\s+(\d+)', pls)
                    port.native_vlan = int(m2.group(1))
                elif pls == 'stp edged-port enable':
                    port.stp_edge = True
                elif re.match(r'port link-aggregation group\s+(\d+)', pls):
                    m2 = re.match(r'port link-aggregation group\s+(\d+)', pls)
                    gid = m2.group(1)
                    port.lacp_group = gid
                    agg_port_map[gid].append(iface)
                i += 1
            cfg.ports[iface] = port
            continue

        i += 1

    # Populate LACP group members
    for gid, members in agg_port_map.items():
        if gid in cfg.lacp_groups:
            cfg.lacp_groups[gid].members = members

    return cfg


# ---------------------------------------------------------------------------
# FS eth-0  (interface eth-0-N, named VLANs in vlan database, additive VLAN syntax)
# ---------------------------------------------------------------------------

def _parse_fs_eth0(content):
    cfg = SwitchConfig(dialect='fs_eth0', vendor='FS')

    lines = content.split('\n')
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if not stripped or stripped == '!':
            i += 1
            continue

        # VLAN database (names included: 'vlan N name X')
        if stripped == 'vlan database':
            i += 1
            while i < len(lines):
                vl = lines[i]
                vls = vl.strip()
                if not vl.startswith(' ') and vls and vls != '!':
                    break
                if vls == '!':
                    break
                # vlan N name X
                m = re.match(r'vlan\s+(\d+)\s+name\s+(\S+)', vls)
                if m:
                    vid = int(m.group(1))
                    if vid not in cfg.vlans:
                        cfg.vlans[vid] = VlanInfo(vlan_id=vid)
                    cfg.vlans[vid].name = m.group(2)
                    i += 1
                    continue
                # vlan N1,N2-N3  (range, no name)
                m = re.match(r'vlan\s+([\d,\-]+)\s*$', vls)
                if m:
                    for vid in expand_range(m.group(1)):
                        if vid not in cfg.vlans:
                            cfg.vlans[vid] = VlanInfo(vlan_id=vid)
                i += 1
            continue

        # Top-level LACP declaration: port-channel N lacp-mode dynamic
        m = re.match(r'^port-channel\s+(\d+)\s+lacp-mode\s+(\S+)', stripped)
        if m:
            gid = m.group(1)
            if gid not in cfg.lacp_groups:
                cfg.lacp_groups[gid] = LacpGroup(group_id=gid, mode=m.group(2))
            i += 1
            continue

        # Interface block
        m = re.match(r'^interface\s+eth-0-(\d+)$', stripped, re.IGNORECASE)
        if m:
            iface = 'eth-0-%s' % m.group(1)
            port = PortInfo(name=iface)
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls and pls != '!':
                    break
                if pls == '!':
                    break
                _parse_fs_port_line(pls, port, cfg)
                i += 1
            cfg.ports[iface] = port
            continue

        i += 1

    # Populate LACP group members
    for port in cfg.ports.values():
        if port.lacp_group and port.lacp_group in cfg.lacp_groups:
            grp = cfg.lacp_groups[port.lacp_group]
            if port.name not in grp.members:
                grp.members.append(port.name)

    return cfg


# ---------------------------------------------------------------------------
# Dell EMC OS9  (VLAN-centric: interface Vlan N defines tagged/untagged ports)
# ---------------------------------------------------------------------------

def _parse_dell_os9(content):
    cfg = SwitchConfig(dialect='dell_os9', vendor='Dell EMC')

    m = re.search(r'! Version\s+([\d.()\-]+)', content)
    if m:
        cfg.firmware = m.group(1)
    m = re.search(r'^hostname\s+(\S+)', content, re.MULTILINE)
    if m:
        cfg.hostname = m.group(1)
    # Model from stack-unit provision (take first match)
    m = re.search(r'stack-unit \d+ provision\s+(\S+)', content)
    if m:
        cfg.model = m.group(1)
    m = re.search(r'default vlan-id\s+(\d+)', content)
    default_vlan = int(m.group(1)) if m else 1

    lines = content.split('\n')

    # VLAN-centric membership tables
    port_tagged = defaultdict(set)    # port_name -> {vlan_id}
    port_untagged = defaultdict(set)  # port_name -> {vlan_id}
    lacp_groups = {}                  # gid -> LacpGroup
    phys_info = {}                    # port_name -> {shutdown, portmode, lacp_group}

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Physical interface: GigabitEthernet or TenGigabitEthernet
        m = re.match(r'^interface\s+(TenGigabitEthernet|GigabitEthernet)\s+(\S+)',
                     stripped, re.IGNORECASE)
        if m:
            iface = '%s %s' % (m.group(1), m.group(2))
            info = {'shutdown': False, 'portmode': None, 'lacp_group': None}
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                # Dell OS9: '!' is a visual separator, not a block terminator
                if pls == '!':
                    i += 1
                    continue
                if not pl.startswith(' ') and pls:
                    break
                if pls == 'shutdown':
                    info['shutdown'] = True
                m2 = re.match(r'portmode\s+(\S+)', pls)
                if m2:
                    info['portmode'] = m2.group(1)
                # LACP: 'port-channel N mode active' (under port-channel-protocol LACP)
                m2 = re.match(r'port-channel\s+(\d+)\s+mode\s+(\S+)', pls)
                if m2:
                    gid = m2.group(1)
                    info['lacp_group'] = gid
                    if gid not in lacp_groups:
                        lacp_groups[gid] = LacpGroup(group_id=gid, mode=m2.group(2))
                    if iface not in lacp_groups[gid].members:
                        lacp_groups[gid].members.append(iface)
                i += 1
            phys_info[iface] = info
            continue

        # Port-channel interface
        m = re.match(r'^interface\s+Port-channel\s+(\d+)', stripped, re.IGNORECASE)
        if m:
            gid = m.group(1)
            if gid not in lacp_groups:
                lacp_groups[gid] = LacpGroup(group_id=gid)
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if pls == '!':
                    i += 1
                    continue
                if not pl.startswith(' ') and pls:
                    break
                m2 = re.match(r'portmode\s+(\S+)', pls)
                if m2:
                    lacp_groups[gid].mode = m2.group(1)
                i += 1
            continue

        # Vlan interface (defines which ports are members)
        m = re.match(r'^interface\s+Vlan\s+(\d+)', stripped, re.IGNORECASE)
        if m:
            vid = int(m.group(1))
            if vid not in cfg.vlans:
                cfg.vlans[vid] = VlanInfo(vlan_id=vid)
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if pls == '!':
                    i += 1
                    continue
                if not pl.startswith(' ') and pls:
                    break
                m2 = re.match(r'(name|description)\s+(.+)', pls)
                if m2:
                    label = m2.group(2).strip()
                    if not cfg.vlans[vid].name:
                        cfg.vlans[vid].name = label
                    if not cfg.vlans[vid].description:
                        cfg.vlans[vid].description = label
                m2 = re.match(r'tagged\s+(.+)', pls)
                if m2:
                    for pname in expand_dell_port_list(m2.group(1)):
                        port_tagged[pname].add(vid)
                m2 = re.match(r'untagged\s+(.+)', pls)
                if m2:
                    for pname in expand_dell_port_list(m2.group(1)):
                        port_untagged[pname].add(vid)
                i += 1
            continue

        i += 1

    # Build PortInfo for physical ports
    all_phys = set(phys_info.keys()) | set(port_tagged.keys()) | set(port_untagged.keys())
    for pname in sorted(all_phys):
        # Skip Port-channel names here; handled below
        if pname.lower().startswith('port-channel'):
            continue
        info = phys_info.get(pname, {})
        port = PortInfo(name=pname)
        if info.get('shutdown'):
            port.shutdown = True
            port.mode = 'shutdown'
        elif info.get('lacp_group'):
            port.lacp_group = info['lacp_group']
            port.mode = 'trunk'
        else:
            tagged = sorted(port_tagged.get(pname, set()))
            untagged = sorted(port_untagged.get(pname, set()))
            port.tagged_vlans = tagged
            port.untagged_vlans = untagged
            if untagged and not tagged:
                port.mode = 'access'
                port.access_vlan = untagged[0]
                port.native_vlan = untagged[0]
            elif tagged and untagged:
                port.mode = 'hybrid'
                port.native_vlan = untagged[0]
                port.access_vlan = untagged[0]
            elif tagged:
                port.mode = 'trunk'
                port.native_vlan = default_vlan
            else:
                port.mode = 'unknown'
        cfg.ports[pname] = port

    # Build PortInfo for Port-channel aggregations
    for gid, grp in lacp_groups.items():
        pname = 'Port-channel %s' % gid
        port = PortInfo(name=pname)
        tagged = sorted(port_tagged.get(pname, set()))
        untagged = sorted(port_untagged.get(pname, set()))
        port.tagged_vlans = tagged
        port.untagged_vlans = untagged
        if tagged and untagged:
            port.mode = 'hybrid'
            port.native_vlan = untagged[0]
        elif tagged:
            port.mode = 'trunk'
            port.native_vlan = default_vlan
        elif untagged:
            port.mode = 'access'
            port.access_vlan = untagged[0]
            port.native_vlan = untagged[0]
        else:
            port.mode = 'unknown'
        cfg.ports[pname] = port
        cfg.lacp_groups[gid] = grp

    return cfg


# ---------------------------------------------------------------------------
# FS S3400
# ---------------------------------------------------------------------------

def _parse_fs_s3400(content):
    """Parse FS S3400 config.

    Similar to IOS in structure but with distinct differences:
      - GigaEthernet0/N and TGigaEthernet0/N interface naming (no 'bit')
      - Port-aggregator<N> for LAG interfaces
      - aggregator-group N mode lacp inside physical interfaces
      - switchport trunk vlan-allowed <ranges> (not 'allowed vlan')
      - switchport trunk vlan-untagged <N|none> (not 'native vlan')
      - switchport pvid N for access/native VLAN
      - VLANs declared at the bottom as a flat 'vlan <ranges>' line
    """
    cfg = SwitchConfig(dialect='fs_s3400', vendor='FS')

    m = re.search(r'^hostname\s+(\S+)', content, re.MULTILINE)
    if m:
        cfg.hostname = m.group(1)
    m = re.search(r'^!version\s+(\S+)', content, re.MULTILINE)
    if m:
        cfg.firmware = m.group(1)

    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # VLAN declaration: 'vlan <ranges>' — may be split across lines by terminal wrap.
        # The switch outputs a single logical line; handle continuation by joining the
        # next line if it starts with a digit (wrapped range continuation).
        m = re.match(r'^vlan\s+([\d,\-]+)\s*$', stripped)
        if m:
            vlan_str = m.group(1)
            # Consume wrapped continuation lines (start with digit, no indent keyword)
            while i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if re.match(r'^[\d,\-]+$', nxt):
                    vlan_str += nxt
                    i += 1
                else:
                    break
            for vid in expand_range(vlan_str):
                if vid not in cfg.vlans:
                    cfg.vlans[vid] = VlanInfo(vlan_id=vid)
            i += 1
            continue

        # SVI — skip (interface VLAN<N>, uppercase or mixed case)
        if re.match(r'^interface\s+VLAN\d', stripped, re.IGNORECASE):
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls:
                    break
                i += 1
            continue

        # Port-aggregator interface — record as LAG group
        m = re.match(r'^interface\s+Port-aggregator(\d+)', stripped, re.IGNORECASE)
        if m:
            gid = m.group(1)
            if gid not in cfg.lacp_groups:
                cfg.lacp_groups[gid] = LacpGroup(group_id=gid)
            lag = cfg.lacp_groups[gid]
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls:
                    break
                m2 = re.match(r'switchport mode\s+(\S+)', pls)
                if m2:
                    lag.mode = m2.group(1)
                i += 1
            continue

        # Physical interfaces: GigaEthernet0/N (1G) and TGigaEthernet0/N (10G)
        m = re.match(r'^interface\s+((?:T)?GigaEthernet\S+)', stripped, re.IGNORECASE)
        if m:
            iface = m.group(1)
            port = PortInfo(name=iface)
            port.tagged_vlans = []
            port.untagged_vlans = []
            port.warnings = []
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls:
                    break
                _parse_fs_s3400_port_line(pls, port, cfg)
                i += 1
            cfg.ports[iface] = port
            if port.lacp_group is not None:
                gid = port.lacp_group
                if gid not in cfg.lacp_groups:
                    cfg.lacp_groups[gid] = LacpGroup(group_id=gid)
                if iface not in cfg.lacp_groups[gid].members:
                    cfg.lacp_groups[gid].members.append(iface)
            continue

        i += 1

    return cfg


def _parse_fs_s3400_port_line(line, port, cfg):
    """Parse a single port config line for the FS S3400 dialect."""
    if line == 'shutdown':
        port.shutdown = True
        port.mode = 'shutdown'
    elif re.match(r'description\s+"?(.+)"?', line):
        m = re.match(r'description\s+"?(.+?)"?\s*$', line)
        if m:
            port.description = m.group(1)
    elif line == 'switchport mode access':
        port.mode = 'access'
    elif line == 'switchport mode trunk':
        port.mode = 'trunk'
    elif re.match(r'switchport pvid\s+(\d+)', line):
        m = re.match(r'switchport pvid\s+(\d+)', line)
        port.native_vlan = int(m.group(1))
        port.access_vlan = int(m.group(1))
        if port.mode == 'unknown':
            port.mode = 'access'
    elif re.match(r'switchport trunk vlan-allowed\s+(.+)', line):
        m = re.match(r'switchport trunk vlan-allowed\s+(.+)', line)
        port.tagged_vlans = sorted(expand_range(m.group(1)))
        if port.mode == 'unknown':
            port.mode = 'trunk'
    elif re.match(r'switchport trunk vlan-untagged\s+(\d+)', line):
        m = re.match(r'switchport trunk vlan-untagged\s+(\d+)', line)
        port.native_vlan = int(m.group(1))
    elif line == 'switchport trunk vlan-untagged none':
        port.native_vlan = None
    elif re.match(r'aggregator-group\s+(\d+)\s+mode\s+(\S+)', line):
        m = re.match(r'aggregator-group\s+(\d+)\s+mode\s+(\S+)', line)
        port.lacp_group = m.group(1)


# ---------------------------------------------------------------------------
# Cisco IOS / IOS-XE
# ---------------------------------------------------------------------------

def _parse_cisco_ios(content):
    """Parse Cisco IOS / IOS-XE config.

    VLAN membership is carried in the interface blocks via switchport commands,
    not in a separate vlan database block.  SVIs (interface Vlan<N>) and the
    out-of-band management port (any interface with 'vrf forwarding') are skipped.
    """
    cfg = SwitchConfig(dialect='cisco_ios', vendor='Cisco')

    m = re.search(r'^hostname\s+(\S+)', content, re.MULTILINE)
    if m:
        cfg.hostname = m.group(1)
    m = re.search(r'^version\s+([\d.]+)', content, re.MULTILINE)
    if m:
        cfg.firmware = m.group(1)
    m = re.search(r'switch\s+\d+\s+provision\s+(\S+)', content)
    if m:
        cfg.model = m.group(1)

    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Top-level VLAN declaration: 'vlan <id>' (single or range, comma-separated)
        m = re.match(r'^vlan\s+([\d,\-]+)\s*$', stripped)
        if m:
            vids = expand_range(m.group(1))
            i += 1
            # Peek into the sub-block for optional 'name' line
            name = None
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls:
                    break
                m2 = re.match(r'name\s+(\S+)', pls)
                if m2:
                    name = m2.group(1)
                i += 1
            for vid in vids:
                if vid not in cfg.vlans:
                    cfg.vlans[vid] = VlanInfo(vlan_id=vid, name=name)
            continue

        # SVI — skip as a switch port (interface Vlan<N>)
        if re.match(r'^interface\s+[Vv]lan', stripped):
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls:
                    break
                i += 1
            continue

        # Port-channel interface — record as LAG group
        m = re.match(r'^interface\s+Port-channel(\d+)', stripped, re.IGNORECASE)
        if m:
            gid = m.group(1)
            if gid not in cfg.lacp_groups:
                cfg.lacp_groups[gid] = LacpGroup(group_id=gid)
            lag = cfg.lacp_groups[gid]
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls:
                    break
                m2 = re.match(r'switchport mode\s+(\S+)', pls)
                if m2:
                    lag.mode = m2.group(1)
                i += 1
            continue

        # Physical interface: GigabitEthernet, TenGigabitEthernet, FortyGigabitEthernet,
        # HundredGigE, TwentyFiveGigE, etc.  'interface' at column 0.
        m = re.match(r'^interface\s+((?:Ten|Forty|Hundred|TwentyFive|Twenty-Five)?'
                     r'(?:Gig(?:abit)?Ethernet|GigE)\S+)', stripped, re.IGNORECASE)
        if m:
            iface = m.group(1)
            port = PortInfo(name=iface)
            port.tagged_vlans = []
            port.untagged_vlans = []
            port.warnings = []
            skip = False
            i += 1
            while i < len(lines):
                pl = lines[i]
                pls = pl.strip()
                if not pl.startswith(' ') and pls:
                    break
                if re.match(r'vrf forwarding', pls):
                    skip = True  # management/routed port — not a switch port
                _parse_fs_port_line(pls, port, cfg)
                i += 1
            if not skip:
                cfg.ports[iface] = port
                # Register LAG membership
                if port.lacp_group is not None:
                    gid = port.lacp_group
                    if gid not in cfg.lacp_groups:
                        cfg.lacp_groups[gid] = LacpGroup(group_id=gid)
                    if iface not in cfg.lacp_groups[gid].members:
                        cfg.lacp_groups[gid].members.append(iface)
            continue

        i += 1

    return cfg


# ---------------------------------------------------------------------------
# Stub / Unknown
# ---------------------------------------------------------------------------

def _parse_stub(content):
    cfg = SwitchConfig(dialect='zyxel_json', vendor='Zyxel')
    cfg.warnings.append('zyxel_json dialect: parser not yet implemented')
    return cfg


def _parse_unknown(content):
    cfg = SwitchConfig(dialect='unknown')
    cfg.errors.append('Could not detect switch dialect. Config format not recognised.')
    return cfg


# ---------------------------------------------------------------------------
# Validation  (cross-dialect)
# ---------------------------------------------------------------------------

def _validate(cfg):
    """Add warnings for common data quality issues."""
    for pname, port in cfg.ports.items():
        if port.shutdown:
            continue
        if port.mode == 'access' and not port.access_vlan:
            port.warnings.append('access port with no VLAN assigned')
        if port.mode == 'trunk' and not port.trunk_all_vlans and not port.tagged_vlans:
            port.warnings.append('trunk port with no VLAN list')
        if port.mode == 'hybrid' and not port.access_vlan and not port.native_vlan:
            port.warnings.append('hybrid port with no native VLAN')
        if port.mode == 'unknown' and not port.shutdown and not port.lacp_group:
            port.warnings.append('port mode could not be determined')
        if port.lacp_group and port.lacp_group not in cfg.lacp_groups:
            port.warnings.append('references LACP group %r which is not defined' % port.lacp_group)
    for gid, grp in cfg.lacp_groups.items():
        if not grp.members:
            cfg.warnings.append('LACP group %r has no member ports' % gid)
