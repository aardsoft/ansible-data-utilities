"""
SwitchSession: paramiko invoke_shell() based persistent SSH session for switches.

Requires paramiko. Install it into the ansible venv:
    pip install paramiko

Uses Ansible's compat shim when available so the same code works both as a
standalone tool and inside Ansible modules/connection plugins:
    from ansible.module_utils.compat.paramiko import paramiko

Handles per-dialect:
  - Legacy cipher negotiation (HPE ProCurve needs aes128-cbc)
  - Interactive unlock sequences (HPE _cmdline-mode on + password)
  - Config mode entry/exit (some commands only work outside config mode)
  - Paging disable
  - ANSI/PTY escape sequence stripping (RouterOS)
"""

import re
import socket
import time

from switch_utils.detect import _ANSI_RE, _CTRL_RE, detect_from_banner

# Dialect profiles: per-dialect SSH and CLI behaviour
DIALECT_PROFILES = {
    'routeros': {
        # RouterOS has no config mode; all commands are top-level
        'preferred_ciphers': [],
        'preferred_kex': [],
        'disabled_algorithms': {},
        'unlock_sequence': None,
        'config_mode_cmd': None,
        'config_mode_exit': None,
        'config_mode_prompt': None,
        # RouterOS prompt ends with '> '
        'prompt_pattern': r'>\s*$',
        # No pager; output ends at next prompt
        'pager_pattern': None,
        'pager_dismiss': None,
        'ansi_strip': True,
    },
    'hpe_procurve': {
        # Requires aes128-cbc; full CLI gated behind _cmdline-mode on.
        # Prompt is <HP 1920G Switch> or <hostname> — may contain spaces.
        # IMPORTANT: pattern must anchor to end-of-line ($) so that the echoed
        # command line "<HP 1920G Switch>display current-configuration" is NOT
        # mistaken for a prompt (the prompt text is embedded in the echo).
        'preferred_ciphers': ['aes128-cbc', 'aes256-cbc', 'aes128-ctr', 'aes256-ctr'],
        'preferred_kex': [],
        'disabled_algorithms': {},
        # Unlock flow (consistent across all known HPE/H3C variants):
        #   _cmdline-mode on  ->  Continue? [Y/N]  ->  y
        #                     ->  Please input password:  ->  Jinhua1920unauthorized
        #                     ->  Warning: ...  ->  <prompt>
        'unlock_sequence': [
            ('_cmdline-mode on\n', r'\[Y/N\]',            5),
            ('y\n',                r'[Pp]assword[:\s]*$', 5),
            ('Jinhua1920unauthorized\n', r'<[^>]+>\s*$',  10),
        ],
        'config_mode_cmd': 'system-view\n',
        'config_mode_exit': 'quit\n',
        'config_mode_prompt': r'^\[.*\]\s*$',
        # Must be end-of-line to avoid matching prompt embedded in echo
        'prompt_pattern': r'<[^>]+>\s*$',
        # HPE uses ---- More ---- pager
        'pager_pattern': r'---- More ----',
        'pager_dismiss': ' ',
        'ansi_strip': True,
    },
    'fs_generic': {
        'preferred_ciphers': [],
        'preferred_kex': [],
        'disabled_algorithms': {},
        'unlock_sequence': None,
        'config_mode_cmd': 'configure\n',
        'config_mode_exit': 'end\n',
        'config_mode_prompt': r'\(config\S*\)#\s*$',
        'prompt_pattern': r'#\s*$',
        'pager_pattern': r'--More--',
        'pager_dismiss': ' ',
        'ansi_strip': False,
    },
    # FS S3400 series: starts in user exec mode (Switch>), saved config is
    # 'show configuration' rather than 'show startup-config'.
    'fs_s3400': {
        'preferred_ciphers': [],
        'preferred_kex': [],
        'disabled_algorithms': {},
        'unlock_sequence': None,
        'enable_cmd': 'enable\n',
        'config_mode_cmd': 'configure\n',
        'config_mode_exit': 'end\n',
        'config_mode_prompt': r'\(config\S*\)#\s*$',
        'prompt_pattern': r'[>#]\s*$',
        'pager_pattern': r'--More--',
        'pager_dismiss': ' ',
        'ansi_strip': False,
    },
    'fs_gigaeth': {
        'preferred_ciphers': [],
        'preferred_kex': [],
        'disabled_algorithms': {},
        'unlock_sequence': None,
        'config_mode_cmd': 'configure\n',
        'config_mode_exit': 'end\n',
        'config_mode_prompt': r'\(config\S*\)#\s*$',
        'prompt_pattern': r'#\s*$',
        'pager_pattern': r'--More--',
        'pager_dismiss': ' ',
        'ansi_strip': False,
    },
    'fs_vrp': {
        'preferred_ciphers': [],
        'preferred_kex': [],
        'disabled_algorithms': {},
        'unlock_sequence': None,
        'config_mode_cmd': 'system-view\n',
        'config_mode_exit': 'quit\n',
        'config_mode_prompt': r'\[.*\]$',
        'prompt_pattern': r'<[^>]+>\s*$|>\s*$|\[.*\]\s*$',
        'pager_pattern': r'---- More ----',
        'pager_dismiss': ' ',
        'ansi_strip': True,
    },
    'fs_eth0': {
        'preferred_ciphers': [],
        'preferred_kex': [],
        'disabled_algorithms': {},
        'unlock_sequence': None,
        'config_mode_cmd': 'configure\n',
        'config_mode_exit': 'end\n',
        'config_mode_prompt': r'\(config\S*\)#\s*$',
        'prompt_pattern': r'#\s*$',
        'pager_pattern': r'--More--',
        'pager_dismiss': ' ',
        'ansi_strip': False,
    },
    'cisco_ios': {
        'preferred_ciphers': [],
        'preferred_kex': [],
        'disabled_algorithms': {},
        'unlock_sequence': None,
        # Cisco uses 'configure terminal', not bare 'configure'
        'config_mode_cmd': 'configure terminal\n',
        'config_mode_exit': 'end\n',
        'config_mode_prompt': r'\(config\S*\)#\s*$',
        'prompt_pattern': r'#\s*$',
        'pager_pattern': r'--More--',
        'pager_dismiss': ' ',
        'ansi_strip': False,
    },
    'dell_os9': {
        'preferred_ciphers': [],
        'preferred_kex': [],
        'disabled_algorithms': {},
        'unlock_sequence': None,
        'config_mode_cmd': 'configure\n',
        'config_mode_exit': 'end\n',
        'config_mode_prompt': r'\(conf\S*\)#\s*$',
        'prompt_pattern': r'#\s*$',
        'pager_pattern': r'--More--',
        'pager_dismiss': ' ',
        'ansi_strip': False,
    },
    'zyxel_xgs': {
        'preferred_ciphers': [],
        # Zyxel SFTP needs diffie-hellman-group1-sha1 — add here if connecting
        # via SFTP; SSH interactive login typically works without it
        'preferred_kex': [],
        'disabled_algorithms': {},
        'unlock_sequence': None,
        'config_mode_cmd': None,
        'config_mode_exit': None,
        'config_mode_prompt': None,
        'prompt_pattern': r'#\s*$|\$\s*$',
        'pager_pattern': r'--More--',
        'pager_dismiss': ' ',
        'ansi_strip': False,
    },
}

# Fallback profile for unknown or stub dialects
_DEFAULT_PROFILE = {
    'preferred_ciphers': [],
    'preferred_kex': [],
    'disabled_algorithms': {},
    'unlock_sequence': None,
    'config_mode_cmd': None,
    'config_mode_exit': None,
    'config_mode_prompt': None,
    'prompt_pattern': r'[#>$]\s*$',
    'pager_pattern': r'--More--|---- More ----',
    'pager_dismiss': ' ',
    'ansi_strip': True,
}

# Cipher set broad enough to connect to any switch for auto-detection
_AUTO_DETECT_CIPHERS = [
    'aes128-ctr', 'aes256-ctr', 'aes128-cbc', 'aes256-cbc',
    '3des-cbc', 'aes192-ctr',
]


def _strip_ansi(text):
    text = _ANSI_RE.sub('', text)
    text = _CTRL_RE.sub('', text)
    return text


def _import_paramiko():
    """Return the paramiko module, or raise SwitchSessionError if unavailable."""
    try:
        from ansible.module_utils.compat.paramiko import paramiko as _pm
    except ImportError:
        _pm = None

    if _pm is None:
        try:
            import paramiko as _pm
        except ImportError:
            _pm = None

    if _pm is None:
        raise SwitchSessionError(
            'paramiko is not installed. Install it with: pip install paramiko\n'
            '(If using an ansible venv, activate it first or pip install there.)'
        )
    return _pm


class SwitchSessionError(Exception):
    pass


class SwitchSession:
    """
    Persistent paramiko invoke_shell() session for a network switch.

    Usage::

        sess = SwitchSession(hostname='192.0.2.1', username='admin',
                             password='secret', dialect='hpe_procurve')
        sess.connect()
        output = sess.send_command('display vlan all')
        sess.close()

    Pass dialect=None or dialect='auto' to infer dialect from the SSH banner.
    """

    def __init__(self, hostname, username, dialect=None,
                 password=None, key_filename=None,
                 port=22, timeout=10, command_timeout=30,
                 extra_ssh_options=None, no_unlock=False, debug=False,
                 enable_password=None, allow_agent=None):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.port = port
        self.timeout = timeout
        self.command_timeout = command_timeout
        self.extra_ssh_options = extra_ssh_options or {}
        # None or 'auto' both trigger banner-based detection at connect time
        self.dialect = dialect
        self.profile = DIALECT_PROFILES.get(dialect, _DEFAULT_PROFILE)
        # Skip dialect-specific unlock sequence (e.g. HPE _cmdline-mode on)
        self.no_unlock = no_unlock
        self.debug = debug
        # Optional separate password for 'enable' (falls back to self.password)
        self.enable_password = enable_password
        # None = auto: enable agent + key lookup when no password is set.
        self.allow_agent = allow_agent

        self._client = None
        self._shell = None
        self._in_config_mode = False

    def connect(self):
        """Open SSH connection, start shell, run unlock sequence if needed."""
        paramiko = _import_paramiko()

        auto_detect = self.dialect in (None, 'auto')

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # When no password is set, fall back to key-based auth automatically.
        # allow_agent=None means auto: True when no password, False otherwise.
        no_password = not self.password
        _allow_agent = self.allow_agent if self.allow_agent is not None else no_password
        _look_for_keys = bool(self.key_filename) or no_password

        connect_kwargs = dict(
            hostname=self.hostname,
            port=self.port,
            username=self.username,
            timeout=self.timeout,
            look_for_keys=_look_for_keys,
            allow_agent=_allow_agent,
        )
        if self.password:
            connect_kwargs['password'] = self.password
        if self.key_filename:
            connect_kwargs['key_filename'] = self.key_filename

        if auto_detect:
            # Broad cipher list so legacy switches (HPE etc.) are reachable
            # before we know the dialect
            connect_kwargs['disabled_algorithms'] = {'pubkeys': []}
        elif self.profile.get('disabled_algorithms'):
            connect_kwargs['disabled_algorithms'] = self.profile['disabled_algorithms']

        connect_kwargs.update(self.extra_ssh_options)

        try:
            client.connect(**connect_kwargs)
        except Exception as exc:
            raise SwitchSessionError(
                'SSH connection to %s failed: %s' % (self.hostname, exc)
            )

        if not auto_detect and self.profile.get('preferred_ciphers'):
            try:
                client.get_transport().preferred_ciphers = tuple(
                    self.profile['preferred_ciphers'])
            except Exception:
                pass  # non-fatal; already negotiated

        shell = client.invoke_shell(term='dumb', width=220, height=50)
        shell.settimeout(self.command_timeout)

        self._client = client
        self._shell = shell

        if auto_detect:
            # Collect the initial banner without trying to match a prompt pattern.
            # Matching prompts during banner collection causes false positives: RouterOS
            # sends OSC title sequences (e.g. \x1b]0;[admin@host] > \x07) that leave
            # '> ' in the buffer after ANSI stripping, which matches prompt patterns
            # and truncates the banner before dialect-identifying text arrives.
            #
            # Strategy: read until 1 second of silence (the initial burst is done),
            # then detect dialect from the accumulated text, then drain to the real
            # prompt using the dialect-specific pattern.
            _banner_raw = ''
            _banner_deadline = time.monotonic() + self.timeout
            self._shell.settimeout(1.0)  # short per-recv timeout to detect end of burst
            while time.monotonic() < _banner_deadline:
                try:
                    chunk = self._shell.recv(4096).decode('utf-8', errors='replace')
                    if not chunk:
                        break  # connection closed
                    _banner_raw += chunk
                    # RouterOS probes terminal dimensions before sending the banner by
                    # moving the cursor to extremes and issuing cursor-position requests
                    # (\x1b[6n / DSR).  Respond to every occurrence with the declared
                    # terminal size so RouterOS considers the handshake complete.
                    cpr_count = chunk.count('\x1b[6n')
                    for _ in range(cpr_count):
                        self._shell.send('\x1b[50;220R')
                    if '\x1bZ' in chunk:     # DECID: terminal identification request
                        self._shell.send('\x1b[?0c')
                except socket.timeout:
                    if _strip_ansi(_banner_raw).strip():
                        break  # silence after meaningful data — burst complete
                    continue   # no data yet (or only CR/whitespace) — keep waiting
            self._shell.settimeout(self.command_timeout)  # restore normal timeout
            _initial = _strip_ansi(_banner_raw)
            if self.debug:
                print('[session debug] banner raw:     %r' % _banner_raw[:400])
                print('[session debug] banner stripped: %r' % _initial[:400])
            detected = detect_from_banner(_initial)
            if not detected:
                raise SwitchSessionError(
                    'Could not detect switch dialect from banner.\n'
                    'Banner received:\n%s\n'
                    'Use --dialect to specify it explicitly.' % _initial.strip()
                )
            self.dialect = detected
            self.profile = DIALECT_PROFILES.get(detected, _DEFAULT_PROFILE)
            if self.debug:
                print('[session debug] detected dialect: %s' % self.dialect)
            # Drain to the real prompt if it hasn't arrived yet (e.g. RouterOS
            # shows the banner before the [user@host] > prompt).
            if not re.search(self.profile['prompt_pattern'], _initial, re.MULTILINE):
                if self.debug:
                    print('[session debug] prompt not yet seen, draining...')
                _initial += self._recv_until(self.profile['prompt_pattern'],
                                             timeout=self.timeout, strip_ansi=True)
                if self.debug:
                    found = bool(re.search(self.profile['prompt_pattern'], _initial, re.MULTILINE))
                    print('[session debug] after drain: prompt_found=%s tail=%r'
                          % (found, _initial[-100:]))
        else:
            _initial = self._recv_until(self.profile['prompt_pattern'], timeout=self.timeout)
            if self.debug:
                print('[session debug] initial drain: %r' % _initial[:300])

        profile = self.profile

        # If the switch started in user exec mode (prompt ends with '>') and
        # the dialect supports enable mode, enter privileged mode now.
        if profile.get('enable_cmd') and _initial.rstrip().endswith('>'):
            if self.debug:
                print('[session debug] user exec mode detected, entering enable mode')
            self._do_enable()

        # Run dialect-specific unlock sequence (e.g. HPE _cmdline-mode on)
        if profile['unlock_sequence'] and not self.no_unlock:
            if self.debug:
                print('[session debug] running unlock sequence for %s' % self.dialect)
            self._run_unlock_sequence(profile['unlock_sequence'])
        elif self.no_unlock and self.debug:
            print('[session debug] skipping unlock sequence (--no-unlock)')

        # Disable paging
        self._disable_paging()

    def _recv_until(self, pattern, timeout=None, strip_ansi=None):
        """Read from shell until pattern matches or timeout expires."""
        if timeout is None:
            timeout = self.command_timeout
        if strip_ansi is None:
            strip_ansi = self.profile.get('ansi_strip', False)

        buf = ''
        deadline = time.monotonic() + timeout
        pat = re.compile(pattern, re.MULTILINE)

        while time.monotonic() < deadline:
            try:
                raw = self._shell.recv(4096).decode('utf-8', errors='replace')
            except socket.timeout:
                break
            if not raw:
                break
            chunk = _strip_ansi(raw) if strip_ansi else raw
            buf += chunk
            pager_pat = self.profile.get('pager_pattern')
            if pager_pat and re.search(pager_pat, buf):
                self._shell.sendall(self.profile['pager_dismiss'].encode())
                buf = buf[:buf.rfind('\n') + 1]
                continue
            if pat.search(buf):
                break
        return buf

    def _run_unlock_sequence(self, sequence):
        """Run a series of (send_str, wait_pattern, timeout) unlock steps."""
        for send_str, wait_pattern, step_timeout in sequence:
            if self.debug:
                print('[session debug] unlock send: %r  wait: %r' % (send_str, wait_pattern))
            self._shell.sendall(send_str.encode())
            resp = self._recv_until(wait_pattern, timeout=step_timeout)
            if self.debug:
                print('[session debug] unlock recv: %r' % resp[:200])

    def _do_enable(self):
        """Send 'enable' to enter privileged mode from user exec mode.

        Handles an optional enable-password prompt.  Uses self.enable_password
        if set, falling back to self.password.  Raises SwitchSessionError if
        the privileged '#' prompt is not reached.
        """
        enable_cmd = self.profile.get('enable_cmd', 'enable\n')
        self._shell.sendall(enable_cmd.encode())
        resp = self._recv_until(r'[Pp]assword[:\s]*$|#\s*$', timeout=self.timeout)
        if self.debug:
            print('[session debug] enable response: %r' % resp[:200])
        if re.search(r'[Pp]assword[:\s]*$', resp):
            pw = self.enable_password or self.password or ''
            self._shell.sendall((pw + '\n').encode())
            resp = self._recv_until(r'#\s*$', timeout=self.timeout)
            if self.debug:
                print('[session debug] enable after password: %r' % resp[:200])
        if not re.search(r'#\s*$', resp):
            raise SwitchSessionError(
                'Failed to enter enable mode: no "#" prompt after enable.\n'
                'Response was: %s' % resp.strip()
            )

    def _disable_paging(self):
        """Send dialect-appropriate command to disable output paging."""
        cmds = {
            'routeros': None,
            'hpe_procurve': 'screen-length disable\n',
            'cisco_ios':  'terminal length 0\n',
            'fs_generic': 'terminal length 0\n',
            'fs_s3400':   'terminal length 0\n',
            'fs_gigaeth': 'terminal length 0\n',
            'fs_vrp': 'screen-length 0 temporary\n',
            'fs_eth0': 'terminal length 0\n',
            'dell_os9': 'terminal length 0\n',
            'zyxel_xgs': None,
        }
        cmd = cmds.get(self.dialect)
        if cmd:
            if self.debug:
                print('[session debug] disable paging: %r' % cmd)
            self._shell.sendall(cmd.encode())
            resp = self._recv_until(self.profile['prompt_pattern'], timeout=self.timeout)
            if self.debug:
                print('[session debug] paging resp: %r' % resp[:200])

    def send_command(self, command, in_config_mode=False):
        """Send a single command and return its output.

        in_config_mode=True:  ensure we are in config mode before sending;
                              wait for config-mode prompt in response.
        in_config_mode=False: ensure we are in user/normal mode before sending;
                              wait for normal prompt in response.
        in_config_mode=None:  no mode management — send as-is and accept either
                              prompt in response.  Used by the interactive shell
                              so the user controls mode transitions themselves.
        """
        # RouterOS runs its CLI via a full-screen terminal UI on any PTY session,
        # making it impossible to parse command output via the shell channel.
        # Always use a non-PTY exec channel for clean plain text output.
        # Interactive menu navigation (/interface, /ip) returns empty immediately;
        # use fully-qualified commands (/interface print) or a real SSH session.
        if self.dialect == 'routeros':
            return self._routeros_exec(command.strip())

        if in_config_mode is True:
            self._ensure_config_mode()
            wait_pattern = (self.profile.get('config_mode_prompt')
                            or self.profile['prompt_pattern'])
        elif in_config_mode is False:
            self._ensure_normal_mode()
            wait_pattern = self.profile['prompt_pattern']
        else:
            # Pass-through: accept either prompt
            cfg = self.profile.get('config_mode_prompt')
            if cfg:
                wait_pattern = r'(?:%s)|(?:%s)' % (self.profile['prompt_pattern'], cfg)
            else:
                wait_pattern = self.profile['prompt_pattern']

        if not command.endswith('\n'):
            command = command + '\n'
        if self.debug:
            print('[session debug] send_command: %r wait=%r' % (command.rstrip(), wait_pattern))
        self._shell.sendall(command.encode())
        output = self._recv_until(wait_pattern)
        if self.debug:
            print('[session debug] send_command raw output (%d chars): %r'
                  % (len(output), output[:300]))
        lines = output.splitlines()
        if lines and lines[0].strip().endswith(command.strip()):
            lines = lines[1:]
        if lines and re.search(wait_pattern, lines[-1]):
            lines = lines[:-1]
        return '\n'.join(lines)

    def _routeros_exec(self, command):
        """Run a command on RouterOS via a non-PTY exec channel.

        RouterOS always uses its full-screen terminal UI on PTY sessions,
        making shell-channel output unparseable after ANSI stripping.  A
        plain exec channel bypasses the terminal UI and returns raw text.
        RouterOS has no config mode, so in_config_mode is irrelevant here.
        """
        if self.debug:
            print('[session debug] routeros_exec: %r' % command)
        chan = self._client.get_transport().open_session()
        chan.settimeout(1.0)
        chan.exec_command(command)
        output = b''
        deadline = time.monotonic() + self.command_timeout
        while time.monotonic() < deadline:
            try:
                chunk = chan.recv(4096)
                if not chunk:
                    break  # EOF — command finished
                output += chunk
            except socket.timeout:
                # No data in last 1s; stop if command is done or channel is closed
                if chan.exit_status_ready() or not chan.active:
                    break
        chan.close()
        result = output.decode('utf-8', errors='replace')
        if self.debug:
            print('[session debug] routeros_exec result (%d chars): %r'
                  % (len(result), result[:200]))
        return result.strip('\r\n')

    def _ensure_config_mode(self):
        if self._in_config_mode:
            return
        cmd = self.profile.get('config_mode_cmd')
        if not cmd:
            return
        self._shell.sendall(cmd.encode())
        prompt = self.profile.get('config_mode_prompt') or self.profile['prompt_pattern']
        self._recv_until(prompt, timeout=self.timeout)
        self._in_config_mode = True

    def _ensure_normal_mode(self):
        if not self._in_config_mode:
            return
        cmd = self.profile.get('config_mode_exit')
        if not cmd:
            return
        self._shell.sendall(cmd.encode())
        self._recv_until(self.profile['prompt_pattern'], timeout=self.timeout)
        self._in_config_mode = False

    def enter_config_mode(self):
        self._ensure_config_mode()

    def exit_config_mode(self):
        self._ensure_normal_mode()

    def close(self):
        try:
            if self._in_config_mode:
                self._ensure_normal_mode()
        except Exception:
            pass
        try:
            if self._shell:
                self._shell.close()
        except Exception:
            pass
        try:
            if self._client:
                self._client.close()
        except Exception:
            pass
        self._shell = None
        self._client = None
        self._in_config_mode = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
