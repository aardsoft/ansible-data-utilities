import re


# ANSI escape sequence pattern (also used by session.py for PTY output stripping)
_ANSI_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
# Box-drawing and other non-ASCII junk from RouterOS PTY
_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def detect_from_banner(banner):
    """Infer switch dialect from SSH banner / initial PTY output.

    Returns a dialect string or None if the dialect cannot be determined.
    Strips ANSI before matching so RouterOS box-drawing chars don't interfere.
    HPE prompt may contain spaces: <HP 1920G Switch>.
    """
    clean = _ANSI_RE.sub('', banner)
    clean = _CTRL_RE.sub('', clean)

    # RouterOS: detect from banner text first (prompt may not yet have arrived),
    # then fall back to the [user@hostname] > prompt pattern.
    if re.search(r'MikroTik RouterOS', clean) or re.search(r'\[\S+@\S+\]\s*>', clean):
        return 'routeros'

    # FS VRP (N5850 etc): <hostname> prompt but banner mentions FS or specific models.
    # Check before HPE because both use angle-bracket prompts.
    if re.search(r'<[^>]+>', clean) and re.search(r'\bFS\b|N5850|N8560|S5800', clean):
        return 'fs_vrp'

    # HPE ProCurve / H3C ComWare: <...> prompt or explicit vendor strings.
    # Prompt may be <HP 1920G Switch> (spaces inside angle brackets).
    if re.search(r'<[^>]+>', clean) or re.search(
            r'H3C|Comware|HPE|Hewlett-Packard|ProCurve', clean, re.IGNORECASE):
        return 'hpe_procurve'

    # Zyxel XGS
    if re.search(r'XGS|ZyXEL|Zyxel', clean, re.IGNORECASE):
        return 'zyxel_xgs'

    # Dell OS9 (FTOS / Force10) — explicit vendor strings in banner text
    if re.search(r'Dell EMC|Force10|FTOS|Dell Networking|Dell Real Time|Dell Operating System',
                 clean, re.IGNORECASE):
        return 'dell_os9'

    # Cisco IOS/IOS-XE: vendor strings in MOTD/login banner (not always present).
    if re.search(r'Cisco IOS|cisco Systems', clean, re.IGNORECASE):
        return 'cisco_ios'

    # FS S3400 series: "Welcome to FS S3400..." banner + user exec prompt.
    # Must be checked before the generic FS+> fallback below.
    if re.search(r'\bFS S3\d', clean) and re.search(r'^\S+>\s*$', clean, re.MULTILINE):
        return 'fs_s3400'

    # Other FS switches starting in user exec mode (Switch>).
    if re.search(r'\bFS\b', clean) and re.search(r'^\S+>\s*$', clean, re.MULTILINE):
        return 'fs_generic'

    # FS switches with # prompt — most generic; caller can refine.
    # NOTE: Dell OS9 also uses bare 'hostname#' prompt; if no vendor strings are
    # in the banner the dialect will be refined later from the running-config content.
    if re.search(r'^\S+#\s*$', clean, re.MULTILINE):
        return 'fs_generic'

    return None


def detect_dialect(content):
    """Detect switch config dialect from content.

    Returns one of: routeros, zyxel_xgs, zyxel_json, fs_generic, fs_s3400,
    fs_gigaeth, fs_vrp, hpe_procurve, cisco_ios, or unknown.
    """
    lines = content.split('\n')
    nonempty = [l for l in lines if l.strip()]
    if not nonempty:
        return 'unknown'

    first = nonempty[0].strip()

    if first.startswith('# ') and 'RouterOS' in first:
        return 'routeros'

    if first.startswith('; Product Name = XGS'):
        return 'zyxel_xgs'

    if first.startswith('{'):
        return 'zyxel_json'

    # FS variants all use ! but differ significantly
    if first.startswith('!<Version>'):
        return 'fs_generic'

    if first.startswith('!version') and 'J' in first:
        # fs_s3400 also uses !version NNJ but has GigaEthernet (no 'bit') and
        # aggregator-group instead of channel-group.
        if 'aggregator-group' in content:
            return 'fs_s3400'
        return 'fs_gigaeth'

    # Dell EMC OS9: '! Version 9.x(x.x)' — space after ! and capital V.
    # When fetching live the output starts with 'Current Configuration ...'
    # before the version line, so scan the first few non-empty lines.
    # Also match 'boot system stack-unit' which is exclusively Dell OS9.
    for line in nonempty[:6]:
        s = line.strip()
        if re.match(r'! Version \d+\.\d+', s):
            return 'dell_os9'
        if re.match(r'boot system stack-unit', s):
            return 'dell_os9'

    # Cisco IOS/IOS-XE: 'version X.X' near the top, no '!' prefix, no 'Release' suffix.
    # Must be checked before HPE because both use bare 'version' lines, but HPE
    # always has 'Release' in the version string.
    # Cisco running-config may be preceded by 'Building configuration...' and
    # 'Current configuration : N bytes', so scan the first 8 non-empty lines.
    for line in nonempty[:8]:
        s = line.strip()
        if re.match(r'^version \d+\.\d+$', s):
            return 'cisco_ios'

    # HPE ProCurve: version line near top (no leading !)
    for line in nonempty[:10]:
        s = line.strip()
        if s.startswith('version ') and 'Release' in s:
            return 'hpe_procurve'

    # FS N5850 / VRP-like
    header = '\n'.join(lines[:5])
    if 'System startup configuration' in header:
        body_sample = '\n'.join(lines[:150]).lower()
        if ('eth-trunk' in body_sample or '10gigabitethernet' in body_sample
                or '10gigaethernet' in body_sample):
            return 'fs_vrp'

    # FS eth-0 format: no version header, interface eth-0-N naming
    body = content.lower()
    if 'interface eth-0-' in body and 'vlan database' in body:
        return 'fs_eth0'

    return 'unknown'
