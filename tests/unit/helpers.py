"""Shared helpers for data-utilities unit tests.

These are plain functions (not pytest fixtures) so that test classes can call
them directly to build plugin instances with custom options.
"""
import sys
import os
from unittest.mock import MagicMock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, 'inventory_plugins'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'filter_plugins'))
sys.path.insert(0, REPO_ROOT)  # for switch_utils and other top-level packages

# Default option values matching the plugin's DOCUMENTATION defaults
DEFAULT_OPTIONS = {
    'allowed_duplicate_ips': ['127.0.0.1', '127.0.0.1/8', '::1'],
    'check_system_type': True,
    'debug': False,
    'default_domain': '',
    'default_vars_key': 'default_vars',
    'dump_file': '',
    'dynamic_groups': False,
    'generate_dhcp_networks': True,
    'generate_vlans': True,
    'groups_key': 'groups',
    'hosts_key': 'hosts',
    'inventory_dump_file': '',
    'ipmi_vlan': 'ipmi',
    'missing_vlan_id': '?',
    'networks_key': 'networks',
    'roles_path': [],
    'sited_name': 'site.d',
}


def make_plugin(**options):
    """Return a configured InventoryModule with mocked Ansible infrastructure."""
    from site_yaml import InventoryModule
    plugin = InventoryModule()
    opts = dict(DEFAULT_OPTIONS)
    opts.update(options)
    plugin.get_option = lambda key: opts.get(key)
    plugin.inventory = MagicMock()
    plugin._extension_registry = {}
    return plugin


def make_valid_keys():
    """Build a valid_keys dict as constructed by the plugin at parse time."""
    return {
        'default_vars': DEFAULT_OPTIONS['default_vars_key'],
        'networks': DEFAULT_OPTIONS['networks_key'],
        'hosts': DEFAULT_OPTIONS['hosts_key'],
        'groups': DEFAULT_OPTIONS['groups_key'],
        'sites': 'sites',
        'networks_templates': DEFAULT_OPTIONS['networks_key'] + '_templates',
        'groups_templates': DEFAULT_OPTIONS['groups_key'] + '_templates',
        'hosts_templates': DEFAULT_OPTIONS['hosts_key'] + '_templates',
    }
