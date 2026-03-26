"""Text summary formatter for SwitchConfig."""

from .utils import compress_list


def format_summary(cfg, verbose=False):
    """Return a multi-line text summary of a SwitchConfig."""
    lines = []

    # Header
    vendor = cfg.vendor or 'Unknown vendor'
    model = cfg.model or 'unknown model'
    fw = cfg.firmware or 'unknown firmware'
    lines.append('=== Switch: %s (%s, %s) ===' % (model, vendor, fw))
    if cfg.hostname:
        lines.append('Hostname: %s' % cfg.hostname)
    lines.append('Dialect: %s' % cfg.dialect)
    lines.append('')

    # VLANs
    lines.append('VLANs (%d):' % len(cfg.vlans))
    if cfg.vlans:
        lines.append('  %-6s  %s' % ('ID', 'Name / Description'))
        for vid in sorted(cfg.vlans):
            v = cfg.vlans[vid]
            label = v.name or v.description or '-'
            lines.append('  %-6d  %s' % (vid, label))
    else:
        lines.append('  (none detected)')
    lines.append('')

    # Ports
    active = [p for p in cfg.ports.values() if not p.shutdown]
    shutdown = [p for p in cfg.ports.values() if p.shutdown]
    trunks = [p for p in active if p.mode in ('trunk', 'uplink')]

    lines.append('Ports (%d active, %d shutdown, %d trunks):' % (
        len(active), len(shutdown), len(trunks)))

    if cfg.ports:
        # Column widths
        name_w = max(len(p.name) for p in cfg.ports.values())
        name_w = max(name_w, 4)
        desc_w = max((len(p.description or '') for p in cfg.ports.values()), default=0)
        desc_w = max(desc_w, 4) if desc_w else 4

        hdr = '  %-*s  %-*s  %-8s  %-6s  %-25s  %s' % (
            name_w, 'Port', desc_w, 'Desc', 'Mode', 'PVID', 'Tagged VLANs', 'LACP')
        lines.append(hdr)
        lines.append('  ' + '-' * (len(hdr) - 2))

        for pname in sorted(cfg.ports, key=_port_sort_key):
            port = cfg.ports[pname]
            desc = port.description or '-'
            mode = port.mode
            pvid = str(port.native_vlan or port.access_vlan or '-')

            if port.trunk_all_vlans:
                vlans_str = 'all'
            elif port.tagged_vlans:
                vlans_str = compress_list(port.tagged_vlans)
                if len(vlans_str) > 25:
                    vlans_str = vlans_str[:22] + '...'
            else:
                vlans_str = '-'

            lacp_str = port.lacp_group or '-'

            lines.append('  %-*s  %-*s  %-8s  %-6s  %-25s  %s' % (
                name_w, pname, desc_w, desc, mode, pvid, vlans_str, lacp_str))

            if verbose and port.warnings:
                for w in port.warnings:
                    lines.append('    [!] %s' % w)
    else:
        lines.append('  (none detected)')
    lines.append('')

    # LACP
    lines.append('LACP Groups (%d):' % len(cfg.lacp_groups))
    if cfg.lacp_groups:
        for gid in sorted(cfg.lacp_groups):
            grp = cfg.lacp_groups[gid]
            mode_str = ' (%s)' % grp.mode if grp.mode else ''
            members_str = ', '.join(grp.members) if grp.members else '(no members)'
            lines.append('  Group %s%s: %s' % (gid, mode_str, members_str))
    else:
        lines.append('  none')
    lines.append('')

    # Warnings and errors
    all_warnings = list(cfg.warnings)
    for port in cfg.ports.values():
        for w in port.warnings:
            all_warnings.append('[port %s] %s' % (port.name, w))

    lines.append('Warnings (%d):' % len(all_warnings))
    if all_warnings:
        for w in all_warnings:
            lines.append('  - %s' % w)
    else:
        lines.append('  none')

    if cfg.errors:
        lines.append('')
        lines.append('Errors (%d):' % len(cfg.errors))
        for e in cfg.errors:
            lines.append('  ! %s' % e)

    if verbose and cfg.unhandled:
        lines.append('')
        lines.append('Unhandled sections (%d):' % len(cfg.unhandled))
        for u in cfg.unhandled[:20]:
            lines.append('  %s' % u)
        if len(cfg.unhandled) > 20:
            lines.append('  ... (%d more)' % (len(cfg.unhandled) - 20))

    return '\n'.join(lines)


def _port_sort_key(name):
    """Sort ports naturally: 1 < 2 < 10 < ether1 < GigabitEthernet1/0/1"""
    import re
    parts = re.split(r'(\d+)', name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]
