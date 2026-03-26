def expand_range(range_str):
    """Expand '1-3,5,7-9' to [1,2,3,5,7,8,9]. Handles comma-separated ranges with - separator."""
    result = []
    for part in str(range_str).split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                start, end = part.split('-', 1)
                result.extend(range(int(start.strip()), int(end.strip()) + 1))
            except ValueError:
                pass
        else:
            try:
                result.append(int(part))
            except ValueError:
                pass
    return sorted(result)


def expand_hpe_range(range_str):
    """Expand HPE/H3C 'to' format: '1 to 2 1998 to 2004 3036 4087 to 4090'"""
    result = []
    tokens = range_str.split()
    i = 0
    while i < len(tokens):
        if i + 2 < len(tokens) and tokens[i + 1] == 'to':
            try:
                result.extend(range(int(tokens[i]), int(tokens[i + 2]) + 1))
            except ValueError:
                pass
            i += 3
        else:
            try:
                result.append(int(tokens[i]))
            except ValueError:
                pass
            i += 1
    return sorted(result)


def expand_dell_port_list(spec):
    """Expand a Dell OS9 tagged/untagged port spec into a list of port name strings.

    Handles:
      'Port-channel 1-9,21-26,44'        -> ['Port-channel 1', ..., 'Port-channel 26', ...]
      'GigabitEthernet 1/4-1/6'          -> ['GigabitEthernet 1/4', ..., '1/6']
      'GigabitEthernet 1/13-1/14,2/14'   -> mixed
      'TenGigabitEthernet 1/39'          -> ['TenGigabitEthernet 1/39']
    """
    import re
    spec = spec.strip()
    m = re.match(r'(\S+)\s+(.+)', spec)
    if not m:
        return [spec] if spec else []

    iface_type = m.group(1)
    ranges_str = m.group(2)
    result = []

    for part in ranges_str.split(','):
        part = part.strip()
        if not part:
            continue
        # slot/port-slot/port  e.g. 1/4-1/6 or 2/16-2/18
        m2 = re.match(r'(\d+)/(\d+)-(\d+)/(\d+)$', part)
        if m2:
            s1, p1 = int(m2.group(1)), int(m2.group(2))
            s2, p2 = int(m2.group(3)), int(m2.group(4))
            if s1 == s2:
                for p in range(p1, p2 + 1):
                    result.append('%s %d/%d' % (iface_type, s1, p))
            else:
                result.append('%s %d/%d' % (iface_type, s1, p1))
                result.append('%s %d/%d' % (iface_type, s2, p2))
            continue
        # slot/port  e.g. 1/4
        if re.match(r'\d+/\d+$', part):
            result.append('%s %s' % (iface_type, part))
            continue
        # simple range  e.g. 1-9
        m2 = re.match(r'(\d+)-(\d+)$', part)
        if m2:
            for n in range(int(m2.group(1)), int(m2.group(2)) + 1):
                result.append('%s %d' % (iface_type, n))
            continue
        # single number
        if re.match(r'\d+$', part):
            result.append('%s %s' % (iface_type, part))

    return result


def compress_list(ids):
    """Compress [1,2,3,5,7,8,9] to '1-3,5,7-9'."""
    if not ids:
        return ''
    ids = sorted(set(ids))
    ranges = []
    start = ids[0]
    end = ids[0]
    for n in ids[1:]:
        if n == end + 1:
            end = n
        else:
            ranges.append(str(start) if start == end else '%d-%d' % (start, end))
            start = end = n
    ranges.append(str(start) if start == end else '%d-%d' % (start, end))
    return ','.join(ranges)
