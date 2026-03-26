# Why paramiko/invoke_shell() instead of ansible.netcommon / network_cli:
#
# 1. HPE ProCurve requires an interactive unlock sequence (_cmdline-mode on +
#    password) before any CLI commands work.  network_cli's send_command /
#    match-prompt model has no mechanism for mid-session interactive prompts
#    that change the CLI state.
#
# 2. Legacy cipher negotiation (HPE needs aes128-cbc) requires direct control
#    over paramiko Transport settings; network_cli does not expose this.
#
# 3. RouterOS has no existing network_cli terminal plugin, and its PTY output
#    includes ANSI/box-drawing characters that need custom stripping.
#
# 4. Writing terminal plugins for 6+ dialects in network_cli would be
#    equivalent implementation work but with tighter constraints and less
#    control over the session lifecycle.
#
# 5. paramiko is already an Ansible transitive dependency; no new deps needed.
#
# The Ansible compat shim (ansible.module_utils.compat.paramiko) is used so
# the plugin works whether paramiko is installed directly or via Ansible.

from __future__ import absolute_import, division, print_function
__metaclass__ = type

# Ensure the role root (parent of connection_plugins/) is on sys.path so that
# switch_utils is importable regardless of where Ansible loads this plugin from.
import os as _os
import sys as _sys
_ROLE_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROLE_ROOT not in _sys.path:
    _sys.path.insert(0, _ROLE_ROOT)
del _os, _ROLE_ROOT

DOCUMENTATION = '''
name: switch_ssh
short_description: Persistent SSH connection for multi-dialect network switches
description:
  - Maintains a persistent paramiko invoke_shell() session across tasks.
  - Handles per-dialect legacy ciphers, interactive unlock sequences (HPE),
    config-mode switching, paging, and ANSI output sanitization.
options:
  ansible_host:
    description: Hostname or IP of the switch.
    vars:
      - name: ansible_host
  ansible_port:
    description: SSH port.
    default: 22
    vars:
      - name: ansible_port
  ansible_user:
    description: SSH username.
    vars:
      - name: ansible_user
  ansible_password:
    description: SSH password.
    vars:
      - name: ansible_password
    no_log: true
  ansible_ssh_private_key_file:
    description: Path to SSH private key.
    vars:
      - name: ansible_ssh_private_key_file
  switch_dialect:
    description: >
      Switch CLI dialect.  One of: routeros, hpe_procurve, cisco_ios,
      fs_generic, fs_s3400, fs_gigaeth, fs_vrp, fs_eth0, dell_os9, zyxel_xgs.
      Defaults to auto-detect from ansible_network_os if set, otherwise
      auto-detected from the SSH banner.
    vars:
      - name: switch_dialect
      - name: ansible_network_os
  switch_host:
    description: >
      Hostname or IP to connect to, overriding ansible_host entirely.
      Useful when ansible_host is set to an FQDN by a group_vars/all.yml
      template that should not apply to switches.
      Supports Jinja2 templates evaluated at task time, so
      "{{ inventory_hostname }}" works here even when it does not in ansible_host.
    default: null
    vars:
      - name: switch_host
  switch_domain:
    description: >
      DNS domain appended to the base hostname to form the switch FQDN.
      The base hostname is switch_host if set, otherwise ansible_host.
      Appended only when the base hostname contains no dot (i.e. is bare).
      Example: set to "switches.example.com" so that inventory host "sw-core"
      connects to "sw-core.switches.example.com".
    default: ''
    vars:
      - name: switch_domain
  switch_command_timeout:
    description: Seconds to wait for a command to return its output.
    default: 30
    vars:
      - name: switch_command_timeout
  switch_connect_timeout:
    description: Seconds for the initial SSH connection.
    default: 10
    vars:
      - name: switch_connect_timeout
  switch_enable_password:
    description: >
      Password sent in response to the 'enable' prompt on switches that start
      in user exec mode (e.g. some FS switches).  Defaults to ansible_password
      when not set.
    vars:
      - name: switch_enable_password
    no_log: true
  switch_allow_agent:
    description: >
      Whether to allow SSH agent forwarding for key-based auth.
      Defaults to true when ansible_password is not set, false otherwise.
      Set explicitly to true to force agent use alongside a password.
    default: null
    vars:
      - name: switch_allow_agent
'''

import os
import re

from ansible.errors import AnsibleError, AnsibleConnectionFailure
from ansible.plugins.connection import ConnectionBase
from ansible.utils.display import Display

# Use Ansible's paramiko compat shim so we work whether paramiko is installed
# standalone or pulled in as an Ansible dependency.
try:
    from ansible.module_utils.compat.paramiko import paramiko, PARAMIKO_IMPORT_ERR
except ImportError:
    paramiko = None
    PARAMIKO_IMPORT_ERR = 'ansible.module_utils.compat.paramiko not found'

display = Display()

# Lines to strip from the top of saved-config output before storing.
# These are informational preambles printed by the switch before the config.
_SAVED_CONFIG_PREAMBLE_RE = re.compile(
    r'^\s*Using \d+ out of \d+ bytes[^\n]*\n',
    re.MULTILINE,
)


def _strip_saved_config_preamble(text):
    return _SAVED_CONFIG_PREAMBLE_RE.sub('', text)


class Connection(ConnectionBase):
    """Persistent SSH connection plugin for multi-dialect network switches."""

    transport = 'switch_ssh'
    has_pipelining = False
    # Switches are not POSIX hosts; standard file transfer doesn't apply.
    has_tty = False

    def __init__(self, *args, **kwargs):
        super(Connection, self).__init__(*args, **kwargs)
        self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_option_or(self, *names, **kwargs):
        """Return first non-None option value, or kwargs['default']."""
        default = kwargs.get('default', None)
        for name in names:
            try:
                val = self.get_option(name)
                if val is not None:
                    return val
            except KeyError:
                pass
        return default

    def _build_session(self):
        from switch_utils.session import SwitchSession, SwitchSessionError  # noqa: F401

        dialect = self._get_option_or('switch_dialect', 'ansible_network_os',
                                      default=None)
        # dialect=None is valid: SwitchSession will auto-detect from the SSH banner.
        # A value of 'auto' is treated the same as None.
        if dialect == 'auto':
            dialect = None

        switch_host = self._get_option_or('switch_host')
        hostname = switch_host if switch_host else self._play_context.remote_addr
        switch_domain = self._get_option_or('switch_domain', default='')
        if switch_domain and '.' not in hostname:
            hostname = '%s.%s' % (hostname, switch_domain)
        username = self._play_context.remote_user
        # Prefer get_option() for ansible_password — _play_context.password is
        # not reliably populated from group_vars for custom connection plugins.
        password = (self._get_option_or('ansible_password')
                    or self._play_context.password)
        display.vvv('switch_ssh: user=%s password_len=%d hostname=%s'
                    % (username, len(password or ''), hostname),
                    host=hostname)
        key_file = (self._get_option_or('ansible_ssh_private_key_file')
                    or self._play_context.private_key_file)
        port = int(self._play_context.port or 22)
        connect_timeout = int(self._get_option_or('switch_connect_timeout',
                                                   default=10))
        cmd_timeout = int(self._get_option_or('switch_command_timeout',
                                              default=30))
        enable_password = self._get_option_or('switch_enable_password')
        allow_agent = self._get_option_or('switch_allow_agent')
        # None means auto (SwitchSession enables agent when no password is set)
        if allow_agent is not None:
            allow_agent = bool(allow_agent)

        session = SwitchSession(
            hostname=hostname,
            username=username,
            dialect=dialect,
            password=password,
            key_filename=key_file or None,
            port=port,
            timeout=connect_timeout,
            command_timeout=cmd_timeout,
            enable_password=enable_password or None,
            allow_agent=allow_agent,
        )
        return session

    # ------------------------------------------------------------------
    # ConnectionBase interface
    # ------------------------------------------------------------------

    def _connect(self):
        if paramiko is None:
            raise AnsibleError(
                'paramiko is required for the switch_ssh connection plugin. '
                'Install it with: pip install paramiko\n'
                'Import error was: %s' % PARAMIKO_IMPORT_ERR
            )

        if self._session is not None:
            return self

        from switch_utils.session import SwitchSessionError

        display.vvv('switch_ssh: connecting to %s' % self._play_context.remote_addr,
                    host=self._play_context.remote_addr)
        try:
            session = self._build_session()
            session.connect()
        except SwitchSessionError as exc:
            raise AnsibleConnectionFailure('switch_ssh connect failed: %s' % exc)
        except Exception as exc:
            raise AnsibleConnectionFailure('switch_ssh unexpected error: %s' % exc)

        self._session = session
        self._connected = True
        display.vvv('switch_ssh: connected, dialect=%s' % session.dialect,
                    host=self._play_context.remote_addr)
        return self

    # Saved-config command per dialect.  Used by the __fetch_saved_config__ meta-command
    # so playbooks don't need to know the dialect to pull startup config.
    _SAVED_CONFIG_CMD = {
        'hpe_procurve': 'display saved-configuration',
        'cisco_ios':    'show startup-config',
        'fs_generic':   'show startup-config',
        'fs_s3400':     'show configuration',
        'fs_gigaeth':   'show startup-config',
        'fs_vrp':       'display saved-configuration',
        'fs_eth0':      'show startup-config',
        'dell_os9':     'show startup-config',
        'routeros':     '/export compact',
        'zyxel_xgs':    'show startup-config',
    }

    def exec_command(self, cmd, in_data=None, sudoable=False):
        """Send a command to the switch and return (rc, stdout, stderr).

        Special meta-commands (not forwarded to the switch):
          __get_dialect__         Return the active dialect name as stdout.
          __fetch_saved_config__  Run the dialect-appropriate saved-config command.
          config:<cmd>            Run <cmd> in config mode.
        """
        self._connect()

        # Meta: return the detected/configured dialect name
        if cmd.strip() == '__get_dialect__':
            return 0, self._session.dialect.encode('utf-8'), b''

        # Meta: fetch saved (startup) config using the dialect-appropriate command
        _strip_preamble = False
        if cmd.strip() == '__fetch_saved_config__':
            _strip_preamble = True
            cmd = self._SAVED_CONFIG_CMD.get(self._session.dialect, 'show startup-config')
            display.vvv('switch_ssh __fetch_saved_config__: dialect=%s cmd=%r'
                        % (self._session.dialect, cmd),
                        host=self._play_context.remote_addr)

        # Commands prefixed with 'config:' are run in config mode.
        # This is a simple convention for tasks; modules may also call
        # session methods directly via connection._session.
        in_config_mode = False
        if cmd.startswith('config:'):
            cmd = cmd[len('config:'):]
            in_config_mode = True

        from switch_utils.session import SwitchSessionError

        display.vvvv('switch_ssh exec_command: %r (config_mode=%s)' % (cmd, in_config_mode),
                     host=self._play_context.remote_addr)
        try:
            output = self._session.send_command(cmd.strip(), in_config_mode=in_config_mode)
        except SwitchSessionError as exc:
            return 1, b'', str(exc).encode()
        except Exception as exc:
            return 1, b'', ('Unexpected error: %s' % exc).encode()

        if _strip_preamble:
            output = _strip_saved_config_preamble(output)
        return 0, output.encode('utf-8', errors='replace'), b''

    def put_file(self, in_path, out_path):
        raise AnsibleError(
            'switch_ssh does not support file transfer (put_file). '
            'Switches are managed via CLI commands only.'
        )

    def fetch_file(self, in_path, out_path):
        raise AnsibleError(
            'switch_ssh does not support file transfer (fetch_file). '
            'Switches are managed via CLI commands only.'
        )

    def close(self):
        if self._session is not None:
            display.vvv('switch_ssh: closing connection to %s' % self._play_context.remote_addr,
                        host=self._play_context.remote_addr)
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        self._connected = False
