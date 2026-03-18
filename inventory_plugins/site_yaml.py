# check with ansible-doc -t inventory -l|grep site_yaml if ansible can
# find the plugin
#
# https://docs.ansible.com/ansible/latest/dev_guide/developing_inventory.html
#
# TODO:
# - shared port validation
# - add network and host templates
#
# vlan_mode should eventually switch to vlan per default. Currently only device
# names are validated, meaning that the list of additional vlans to configure
# on a device just points to device names which may specify differently named
# vlans - i.e., this makes the list in the physical interface unsuitable without
# additional lookup. Now with the ability to normalise data in this plugin
# arbitrary interface names for the vlan interface should be fine, with
# validation happening based on the vlan key in the interface.

DOCUMENTATION = '''
    name: site_yaml
    short_description: Build inventory using yaml formatted site files.
    options:
      allowed_duplicate_ips:
        description: a list of IPs which are valid on multiple hosts
        type: list
        default:
          - 127.0.0.1
          - 127.0.0.1/8
          - ::1
        ini:
          - key: allowed_duplicate_ips
            section: site_yaml
      dump_file:
        description: >
          Write the parsed site.yaml data structure (after sanitisation and
          preprocessing but before inventory population) to this file as JSON.
          Useful for inspecting raw host/network/group data and for comparing
          data normalisation changes.
        type: string
        default: ""
        ini:
          - key: dump_file
            section: site_yaml
      inventory_dump_file:
        description: >
          Write the fully-populated Ansible inventory (all hosts, groups and
          their variables, including derived variables set by extensions such
          as network_pods) to this file as JSON after all parsing is complete.
          Produces output comparable to C(ansible-inventory --list) and is
          useful for debugging variable resolution and detecting regressions
          when changing data normalisation logic.
        type: string
        default: ""
        ini:
          - key: inventory_dump_file
            section: site_yaml
      html_dump_file:
        description: >
          Write a self-contained HTML inventory visualisation to this file.
        type: string
        default: ""
        ini:
          - key: html_dump_file
            section: site_yaml
      site_files:
        description: a list of valid filenames for the site inventory
        type: list
        default:
          - site.yml
          - site.yaml
        ini:
          - key: site_files
            section: site_yaml
      sited_name:
        description: name of the site.d directory for extra files
        type: string
        default: site.d
        ini:
          - key: sited_name
            section: site_yaml
      ipmi_vlan:
        description: name of the VLAN containing IPMI interfaces
        type: string
        default: ipmi
        ini:
          - key: ipmi_vlan
            section: site_yaml
      default_vars_key:
        description: key name containing the default variables
        type: string
        default: default_vars
        ini:
          - key: default_vars_key
            section: site_yaml
      hosts_key:
        description: key name containing the host definitions
        type: string
        default: hosts
        ini:
          - key: hosts_key
            section: site_yaml
      groups_key:
        description: key name containing the group definitions
        type: string
        default: groups
        ini:
          - key: groups_key
            section: site_yaml
      networks_key:
        description: key name containing the network definitions
        type: string
        default: networks
        ini:
          - key: networks_key
            section: site_yaml
      generate_dhcp_networks:
        description: generate the legacy dhcp_networks variable out of networks
        type: bool
        default: True
        ini:
          - key: generate_dhcp_networks
            section: site_yaml
      generate_vlans:
        description: generate the legacy vlans variable out of networks
        type: bool
        default: True
        ini:
          - key: generate_vlans
            section: site_yaml
      missing_vlan_id:
        description: a fallback ID to set if vlan ID is not configured
        type: string
        default: "5000"
        ini:
          - key: missing_vlan_id
            section: site_yaml
      dynamic_groups:
        description: allow dynamically creating the groups list
        type: bool
        default: False
        ini:
          - key: dynamic_groups
            section: site_yaml
      check_system_type:
        description: check for type attribute
        type: bool
        default: True
        ini:
          - key: check_system_type
            section: site_yaml
      debug:
        description: enable debug logging
        type: bool
        default: False
        ini:
          - key: debug
            section: site_yaml
      system_types:
        description: list of recognised system types
        type: dict
        default:
          server:
            rack_mounted: True
          workstation:
            rack_mounted: False
          lxc:
            rack_mounted: False
          kvm:
            rack_mounted: False
          ilo:
            rack_mounted: False
          ipmi:
            rack_mounted: False
          dns:
            rack_mounted: False
      vlan_mode:
        description: how to resolve vlans, supported values are 'ifname' and 'vlan'
        type: string
        default: ifname
        ini:
          - key: vlan_mode
            section: site_yaml
      require_valid_ports:
        description: report invalid network ports either as warning or error
        type: bool
        default: False
        ini:
          - key: require_valid_ports
            section: site_yaml
      mandatory_field_marker:
        description: marker for mandatory template fields
        type: string
        default: "[[!]]"
        ini:
          - key: mandatory_field_marker
            section: site_yaml
      warnings_are_errors:
        description: treat warnings as fatal errors
        type: bool
        default: False
      ignore_errors:
        description: don't die on errors. This is useful for handling errors in playbooks.
        type: bool
        default: False
      roles_path:
        description:
          - List of directories to search for Ansible roles when resolving pod
            snippets (C(k3s.snippets) in k3s-pod host definitions).
          - When unset, falls back to the Ansible default roles path
            (C(DEFAULT_ROLES_PATH) from ansible.cfg / C(ANSIBLE_ROLES_PATH)).
        type: list
        default: []
        ini:
          - key: roles_path
            section: site_yaml
'''

import importlib.util
import ipaddress
import os
import re
import json

import ansible.constants as C

from ansible.errors import AnsibleError, AnsibleParserError
from ansible.module_utils.common._collections_compat import MutableMapping
from ansible.plugins.inventory import BaseInventoryPlugin

NoneType = type(None)

class InventoryModule(BaseInventoryPlugin):

    NAME = 'site_yaml'

    def __init__(self):

        super(InventoryModule, self).__init__()

    def verify_file(self, path):

        valid = False
        if super(InventoryModule, self).verify_file(path):
            dir_name, file_name = os.path.split(path)
            if file_name in self.get_option('site_files'):
                valid = True
        return valid

    def apply_template(self, parser, section_key, item_key, data):
        ''' apply a template to an item '''

        debug=self.get_option('debug')
        if debug:
            print("Applying template for '%s' in section '%s'" % (item_key, section_key))

        template_key = section_key+'_templates'

        if template_key in data:
            templates = data[template_key]

            if templates == None:
                parser['errors'].append("%s template section is invalid" % section_key)
                return parser, data

            template = data[section_key][item_key]['template']

            if template in templates:
                self.merge_template_elements(parser, data[section_key][item_key], templates[template], item_key)
            else:
                parser['errors'].append("%s template '%s' not found" % (section_key, template))
            # check if the template exists, throw error if not. otherwise, apply template recursively
        else:
            parser['errors'].append("No templates found for '%s' section" % section_key)

        return parser, data


    def merge_template_elements(self, parser, target, source, item):
        ''' iterate the source template, and set all keys not yet present on
        target side.

        Merge strategy is:

        - if the element is a dict for both source and target, merge recursively
        - if the elemement exists in both target and source, but is a dict in
          only one side, print a warning.
        - if the element does not exist in target, add
        - if an element is marked as mandatory, and does not exist in target
          record an error
        '''

        marker = self.get_option('mandatory_field_marker')

        for key in source:
            if source[key] == marker and key not in target:
                parser['errors'].append("Mandatory template key '%s' missing from %s " % (key, item))
            else:
                # having it initialised to an empty dict if it should be a dict
                # makes things simpler later on
                if (isinstance(source[key], dict) and
                    ((key in target and target[key] == None) or
                     key not in target)):
                    target[key] = {}

                # refactor this bit, probably first we'd need to do nonetype check
                if key in target and isinstance(target[key], dict) and isinstance(source[key], dict):
                    self.merge_template_elements(parser, target[key], source[key], item)
                elif key in target and isinstance(source[key], dict):
                    parser['warnings'].append("Key %s is present, but not a dict. Skipping, but this may mask mandatory key checks." % key)
                elif key not in target and isinstance(source[key], dict):
                    target[key] = {}
                    self.merge_template_elements(parser, target[key], source[key], item)
                elif key not in target:
                    target[key] = source[key]


    def load_sited(self, parser, section_key, path, data):
        ''' add files from site.d structure to inventory '''
        debug=self.get_option('debug')

        key_path = path+"/"+section_key

        # if all items of a section come from site.d site.yml would still require
        # the section key - which would resolve to null. Change it to an empty
        # dict so the logic expecting a dict doesn't break
        if data[section_key] == None:
            data[section_key] = {}

        if os.path.isdir(key_path):

            for file in os.listdir(key_path):
                if file.endswith(".yaml") or file.endswith(".yml"):
                    try:
                        file_data = self.loader.load_from_file(key_path+"/"+file, cache=False)
                    except Exception as e:
                        raise AnsibleParserError(e)

                    for key in file_data:
                        if key in data[section_key]:
                            parser['errors'].append("Duplicate item key '%s' added from file %s" % (key, file))
                        else:
                            data[section_key][key] = file_data[key]
                            if debug:
                                print("Adding %s in section %s from site.d" % (key, section_key))
        else:
            parser['warnings'].append("No site.d found for %s" % section_key)

        return parser, data


    def parse(self, inventory, loader, path, cache=True):
        ''' parses the inventory file '''

        super(InventoryModule, self).parse(inventory, loader, path)
        self.set_options()
        self._discover_extensions()

        try:
            vanilla_data = self.loader.load_from_file(path, cache=False)
        except Exception as e:
            raise AnsibleParserError(e)

        if not vanilla_data:
            raise AnsibleParserError('Parsed empty YAML file')
        elif not isinstance(vanilla_data, MutableMapping):
            raise AnsibleParserError('YAML inventory has invalid structure, it should be a dictionary, got: %s' % type(data))

        parser={}
        parser['errors']=[]
        parser['warnings']=[]

        valid_keys = {
            "default_vars": self.get_option('default_vars_key'),
            "sites": "sites",
            "networks": self.get_option('networks_key'),
            "groups": self.get_option('groups_key'),
            "hosts": self.get_option('hosts_key'),
            "networks_templates": self.get_option('networks_key')+"_templates",
            "groups_templates": self.get_option('groups_key')+"_templates",
            "hosts_templates": self.get_option('hosts_key')+"_templates",
            "annotation_types": "annotation_types",
            }

        keys=[]
        for key in vanilla_data:
            if key in valid_keys.values():
                keys.append(key)
                parser, vanilla_data = self.load_sited(parser, key, os.path.dirname(path)+"/"+self.get_option('sited_name'), vanilla_data)
            else:
                parser['warnings'].append("Unknown key found in site data: " + key)

        if valid_keys['networks'] in keys:
            parser, parsed_data=self._sanitise_networks_data(vanilla_data, valid_keys, parser)
        else:
            parsed_data = vanilla_data

        if valid_keys['hosts'] in keys:
            parser, parsed_data=self._sanitise_hosts_data(parsed_data, valid_keys, parser)
            parser, parsed_data=self._synthesize_host_network_metadata(parsed_data, valid_keys, parser)
            parser, parsed_data=self._preprocess_hosts(parsed_data, valid_keys, parser)
            parser, parsed_data=self._synthesize_host_topology(parsed_data, valid_keys, parser)
        else:
            raise AnsibleParserError("No hosts key (%s) found, can't continue." % valid_keys['hosts'])

        if self.get_option('dump_file') != "":
            with open(self.get_option('dump_file'), "w") as inventoryDump:
                inventoryDump.write(json.dumps(parsed_data, indent=4, sort_keys = True))

        if self.get_option('html_dump_file') != "":
            import importlib.util as _ilu, os as _os
            _spec = _ilu.spec_from_file_location(
                "inventory_html",
                _os.path.join(_os.path.dirname(__file__), "inventory_html.py"))
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            with open(self.get_option('html_dump_file'), "w") as _f:
                _f.write(_mod.generate(parsed_data))

        if valid_keys['default_vars'] in keys:
            parser=self._add_default_vars(parsed_data, valid_keys, parser)

        # Promote default_domain to the dns_domain inventory variable unless
        # already set explicitly via default_vars.
        _explicit_dns_domain = (parsed_data.get(valid_keys['default_vars']) or {}).get('dns_domain')
        if not _explicit_dns_domain and parsed_data.get('default_domain'):
            self.inventory.set_variable("all", "dns_domain", parsed_data['default_domain'])

        if valid_keys['groups'] in keys or self.get_option('dynamic_groups')==True:
            parser=self._add_groups(parsed_data, valid_keys, parser)

        # adding hosts here would not populate the all group, but all of the
        # other groups: run this once for error handling without adding hosts,
        # and a second time after error exit to add hosts to inventory if the
        # data is good to make sure playbooks don't run on bad data
        parser,hosts=self._parse_hosts(parsed_data, parser, True, valid_keys)

        if len(parser['warnings'])>0:
            print("\n\nWarnings found:")
            # AnsibleParserError strips newlines, so print warnings separately to make them readable
            print("\n%s\n" % "\n".join(parser['warnings']))
            if self.get_option('warnings_are_errors')==True:
                raise AnsibleParserError("Above warnings found parsing site data, and treating warnings as errors")

        if self.get_option('ignore_errors')==False and len(parser['errors'])>0:
            print("\n\nErrors found:")
            # AnsibleParserError strips newlines, so print warnings separately to make them readable
            print("\n\n%s\n" % "\n".join(parser['errors']))
            raise AnsibleParserError("Above errors found parsing site data")

        parser,hosts=self._parse_hosts(parsed_data, parser, False, valid_keys)

        if self.get_option('inventory_dump_file') != "":
            self._dump_inventory(self.get_option('inventory_dump_file'))


    def _dump_inventory(self, path):
        ''' Serialize the fully-populated Ansible inventory to a JSON file.

        Captures hosts, groups and all variables (including those set by
        extensions on group 'all', such as network_pods) after the complete
        parse cycle.  Non-JSON-serializable values are converted to strings.
        Errors are reported as warnings rather than failing the inventory run.
        '''

        out = {
            'hosts': {},
            'groups': {},
        }

        for host_name in sorted(self.inventory.hosts):
            host = self.inventory.hosts[host_name]
            out['hosts'][host_name] = {
                'vars': dict(host.vars),
                'groups': sorted(g.name for g in host.groups),
            }

        for group_name in sorted(self.inventory.groups):
            group = self.inventory.groups[group_name]
            out['groups'][group_name] = {
                'vars': dict(group.vars),
                'hosts': sorted(h.name for h in group.hosts),
                'children': sorted(c.name for c in group.child_groups),
            }

        try:
            with open(path, 'w') as f:
                f.write(json.dumps(out, indent=4, sort_keys=True, default=str))
        except Exception as e:
            self.display.warning(
                "Failed to write inventory dump to '%s': %s" % (path, e))


    def _sanitise_hosts_data(self, vanilla_data, k, parser):
        ''' This loops over the hosts structure to validate the data, and also
resolves implicit keys for easier consumption by roles building on the data
structures provided by this. '''

        data=vanilla_data
        debug=self.get_option('debug')

        items={}
        items['uuids']=set()
        items['ips']=set()

        if isinstance(data, (MutableMapping, NoneType)):
            hosts = data[k['hosts']]

            enforced_types = {"server", "ilo", "ipmi", "workstation", "notebook"}
            for type_name, ext in self._extension_registry.items():
                if getattr(ext, 'ENFORCED', False):
                    enforced_types.add(type_name)

            for host in hosts:
                system=hosts[host]

                # apply a template, if needed, before doing further processing
                if 'template' in system:
                    self.apply_template(parser, k['hosts'], host, data)

                uuid=system.get('uuid')
                if uuid != None:
                    if uuid in items['uuids']:
                        parser['errors'].append("%s: duplicate UUID %s" % (host, uuid))
                    items['uuids'].add(uuid)

                # make sure the system has a domain set at high level, if
                # domains are configured
                if system.get('domain') == None and data.get('default_domain') != None:
                    data[k['hosts']][host]['domain']=data.get('default_domain')
                if system.get('old_domain') == None and data.get('old_default_domain') != None:
                    data[k['hosts']][host]['old_domain']=data.get('old_default_domain')

                # currently there are two loops over the networks - this first
                # loop contains checks where system type checking doesn't
                # matter
                if system.get('networks') != None:
                    dups=self.get_option('allowed_duplicate_ips')
                    for if_key in system['networks']:
                        network=system['networks'][if_key]

                        # first resolve the systems host name and dns domain name,
                        # then the interface, and last the addresses

                        # checks below this are only relevant for actual systems,
                        # not DNS entries
                        if system.get('type') == "dns":
                            continue

                        if network.get('cfg_prefix') == None:
                            data[k['hosts']][host]['networks'][if_key]['cfg_prefix']='10'

                        # eventually we should switch to local_port and remote_port
                        # to be able to identify ports on the device as well.
                        # "port" becomes a short form of "remote_port"
                        if network.get('port') != None:
                            data[k['hosts']][host]['networks'][if_key]['remote_port']=network['port']
                        elif network.get('remote_port') != None:
                            data[k['hosts']][host]['networks'][if_key]['port']=network['remote_port']

                        if network.get('type') == None and (if_key == "ilo" or if_key == "ipmi"):
                           data[k['hosts']][host]['networks'][if_key]['type']="ipmi"

                        shared_port=network.get('shared-port')
                        if shared_port != None:
                            shared_port=shared_port.split(",", 1)
                            try:
                                data[k['hosts']][shared_port[0]]['networks'][shared_port[1]]
                            except KeyError:
                                parser['errors'].append("%s: configuration for shared port %s not found on %s" % (host, shared_port[1], shared_port[0]))
                            else:
                                shared_iface=data[k['hosts']][shared_port[0]]['networks'][shared_port[1]]
                                if shared_iface.get('port') != None:
                                    data[k['hosts']][host]['networks'][if_key]['port']=shared_iface['port']
                                    data[k['hosts']][host]['networks'][if_key]['remote_port']=shared_iface['port']

                        ipv4=network.get('ipv4')
                        if ipv4 != None:
                            ipv4 = re.sub(r'/.*$', "", ipv4)
                            if ipv4 in items['ips'] and ipv4 not in dups and network.get('duplicate_ip') != True:
                                parser['errors'].append("%s: duplicate IP %s" % (host, ipv4))
                            items['ips'].add(ipv4)

                        # ipv6 may be a plain address string ("fd42::/48") or a
                        # router-advertisement config dict ({send_ra:, prefixes:}).
                        # Only treat it as an IP address when it is a string.
                        ipv6=network.get('ipv6')
                        if isinstance(ipv6, str):
                            ipv6_bare = re.sub(r'/.*$', "", ipv6)
                            if ipv6_bare in items['ips'] and ipv6_bare not in dups and network.get('duplicate_ip') != True:
                                parser['errors'].append("%s: duplicate IP %s" % (host, ipv6_bare))
                            items['ips'].add(ipv6_bare)

                        # Merge ipv4/ipv6 scalar keys into addresses. Only string
                        # values represent addresses; dict values are RA config and
                        # are not included. host_duplicate suppresses the conflict
                        # warning for the same address appearing in both places.
                        if network.get('addresses') is None:
                            network['addresses'] = {}
                        if network.get('ipv4') is not None and network['ipv4'] not in network['addresses']:
                            network['addresses'][network['ipv4']] = {'host_duplicate': True}
                        if isinstance(ipv6, str) and ipv6 not in network['addresses']:
                            network['addresses'][ipv6] = {'host_duplicate': True}
                        if not network['addresses']:
                            # we don't want {} for hosts without any addresses configured
                            del network['addresses']

                        if network.get('addresses') is not None:
                            _valid_address_keys = {
                                'fqdn', 'dns', 'nodns', 'alias', 'host_duplicate',
                                'peer', 'broadcast', 'label', 'preferred_lifetime',
                                'home_address', 'duplicate_address_detection',
                                'manage_temporary_address', 'prefix_route',
                                'auto_join', 'gateway',
                            }
                            for address in network.get('addresses'):
                                address_struct = network['addresses'][address]
                                if address_struct is not None:
                                    unknown = set(address_struct.keys()) - _valid_address_keys
                                    if unknown:
                                        parser['errors'].append(
                                            "%s: unknown address struct key(s) for %s: %s"
                                            % (host, address, ', '.join(sorted(unknown))))
                                    for str_key in ('fqdn', 'dns'):
                                        if str_key in address_struct and not isinstance(address_struct[str_key], str):
                                            parser['errors'].append(
                                                "%s: %s for %s must be a string" % (host, str_key, address))
                                    for bool_key in ('nodns', 'alias'):
                                        if bool_key in address_struct and not isinstance(address_struct[bool_key], bool):
                                            parser['errors'].append(
                                                "%s: %s for %s must be a boolean" % (host, bool_key, address))

                                if address in items['ips'] and address not in dups and network.get('duplicate_ip') != True and not (address_struct or {}).get('host_duplicate'):
                                    parser['errors'].append("%s: duplicate IP %s" % (host, address))
                                items['ips'].add(address)

                # this should be configurable to 'warning'
                # also fallback to a default type
                if system.get('type') == None:
                    parser['errors'].append("%s is missing type key" % host)
                    continue
                elif system.get('type') not in enforced_types:
                    host_type = system.get('type')
                    if host_type in self._extension_registry:
                        ext = self._extension_registry[host_type]
                        if hasattr(ext, 'sanitise_host'):
                            ext.sanitise_host(self, host, system, data, k, parser)
                    continue
                else:
                    # check type specific keys
                    pass

                # check network configuration
                if system.get('networks') != None:
                    tp={}
                    tp['vlans']=set()
                    tp['bridges']=set()
                    tp['bonds']=set()

                    phy={}
                    phy['vlans']=set()
                    phy['bonds']=set()
                    phy['bridges']=set()

                    for if_key in system['networks']:
                        network=system['networks'][if_key]

                        # for interfaces without ports we need to check if it's
                        # non-physical port. If so, validate that. If it is a
                        # physical port, check if it does need a port number
                        # assigned, and if so, throw an error
                        phy,tp,parser=self._validate_network_port(if_key, host, system['networks'], phy, tp, parser)

                    for itype in "vlans", "bridges", "bonds":
                        only_tp=tp[itype]-phy[itype]
                        only_phy=phy[itype]-tp[itype]
                        if len(only_phy)>0:
                            print(tp[itype])
                            print(phy[itype])
                            parser['errors'].append("%s on '%s' only defined on physical interface: %s" % (itype, host, only_phy))
                        if len(only_tp)>0:
                            print(tp[itype])
                            print(phy[itype])
                            parser['errors'].append("%s on '%s' not defined on physical interface: %s" % (itype, host, only_tp))
                        # check ilo/ipmi interfaces

                    # compare interface lists

                # call extension sanitise_host for additional type-specific checks
                host_type = system.get('type')
                if host_type in self._extension_registry:
                    ext = self._extension_registry[host_type]
                    if hasattr(ext, 'sanitise_host'):
                        ext.sanitise_host(self, host, system, data, k, parser)

        else:
            self.display.warning("Skipping '%s' as this is not a valid host definition" % host)

        return parser, data

    def _sanitise_networks_data(self, vanilla_data, k, parser):
        ''' This loops over the networks structure to validate the data, and also
resolves implicit keys for easier consumption by roles building on the data
structures provided by this. '''

        data=vanilla_data
        debug=self.get_option('debug')

        vlans={}

        networks = data[k['networks']]
        for network_name in networks:
            network=networks[network_name]

            # due to subnet key relatively high up network templates are probably
            # less useful than host templates. Disable for now.
            #if 'template' in network:
            #    self.apply_template(parser, k['networks'], network, data)

            if debug:
                print("Network: %s" % network_name)

            if network.get('vlan_id') != None:
                vlans[network_name]=str(network.get('vlan_id'))
            else:
                vlans[network_name]=self.get_option('missing_vlan_id')

        default_vars = data.get(k['default_vars']) or {}
        dns_domain = default_vars.get('dns_domain') or data.get('default_domain')

        for net_name in networks:
            net = networks[net_name]
            if not isinstance(net, dict):
                continue
            # Pre-compute DHCP domain-name and domain-search option values
            if dns_domain:
                if net_name == 'default':
                    data[k['networks']][net_name]['dhcp_domain_name'] = '"%s"' % dns_domain
                    data[k['networks']][net_name]['dhcp_domain_search'] = '"%s", "%s.%s"' % (dns_domain, net_name, dns_domain)
                else:
                    data[k['networks']][net_name]['dhcp_domain_name'] = '"%s.%s"' % (net_name, dns_domain)
                    data[k['networks']][net_name]['dhcp_domain_search'] = '"%s.%s", "%s"' % (net_name, dns_domain, dns_domain)
            subnets = net.get('subnets')
            if not subnets:
                continue
            if isinstance(subnets, dict):
                subnet_list = list(subnets.keys())
            else:
                subnet_list = list(subnets)
            reverse_zones = {}
            new_subnets = {}
            for subnet in subnet_list:
                rz = self._subnet_reverse_zone(subnet)
                if rz:
                    reverse_zones[subnet] = rz
                # Pre-compute normalized subnet network address and netmask
                subnet_data = subnets.get(subnet) if isinstance(subnets, dict) else None
                new_subnet = dict(subnet_data) if isinstance(subnet_data, dict) else {}
                explicit_netmask = new_subnet.get('netmask')
                cidr = '%s/%s' % (subnet, explicit_netmask) if explicit_netmask else subnet
                try:
                    net_obj = ipaddress.ip_network(cidr, strict=False)
                    new_subnet['subnet_network'] = str(net_obj.network_address)
                    new_subnet['subnet_netmask'] = str(net_obj.netmask)
                except ValueError:
                    pass
                new_subnets[subnet] = new_subnet
            data[k['networks']][net_name]['subnets'] = new_subnets
            if reverse_zones:
                data[k['networks']][net_name]['reverse_zones'] = reverse_zones

        if self.get_option('generate_dhcp_networks') == True:
            self.inventory.set_variable("all", "dhcp_networks", data[k['networks']])

        if self.get_option('generate_vlans') == True:
            self.inventory.set_variable("all", "vlans", vlans)
            data['vlans'] = vlans

        if debug:
            print("vlans: %s" % vlans)

        return parser, data

    def _add_groups(self, data, valid_keys, parser):
        ''' Add groups and child groups to inventory. '''

        # first pass: create all groups
        groups=data.get(valid_keys['groups'])
        if groups != None:
            for group in groups:
                self.inventory.add_group(group.replace("-", "_"))

        groups=set()

        hosts = data[valid_keys['hosts']]
        for host in hosts:
            groups.update(self._get_hostgroups(hosts[host]))

        for group in groups:
            self.inventory.add_group(group.replace("-", "_"))

        # second pass: populate child groups
        groups=data.get(valid_keys['groups'])
        if groups != None:
            for group in groups:
                if groups[group] != None:
                    children=groups[group].get('children')
                    if children != None:
                        for child in children:
                            self.inventory.add_child(group, child)

        return parser



    def _add_default_vars(self, data, valid_keys, parser):
        ''' Add default variables to inventory. '''

        vars=data.get(valid_keys['default_vars'])
        if vars != None:
            for var in vars:
                self.inventory.set_variable("all", var, vars[var])

        return parser

    # this probably also should fill in additional port information
    def _validate_network_port(self, name, host, networks, phy, tp, parser):
        ''' '''

        debug = self.get_option('debug')
        network=networks[name]

        # collect vlans, bridges and bonds for later comparison
        # with phy interfaces. Collect in sets to avoid

        if network.get('vlans') != None:
            phy['vlans'].update(network.get('vlans'))

        if network.get('bridge') != None:
            phy['bridges'].add(network.get('bridge'))

        if network.get('bond') != None:
            phy['bonds'].add(network.get('bond'))

        if network.get('vlan') != None and network.get('network') != None:
            parser['errors'].append("%s: interface %s has both vlan and network tags, drop one" % (host, name))
        elif network.get('vlan') != None:
            network['network'] = network['vlan']
        elif network.get('network') != None:
            network['vlan'] = network['network']

        # site parser makes sure that specially named interfaces (ilo/ipmi)
        # without type get the ipmi type added
        if network.get('type') == "ipmi":
            ipmi_vlan=network.get('vlan')
            if ipmi_vlan == None:
                parser['errors'].append("%s: ilo interface %s without vlan" % (host, name))
            elif ipmi_vlan!=self.get_option('ipmi_vlan'):
                parser['errors'].append("%s: ilo interface %s in wrong vlan: %s" % (host, name, ipmi_vlan))

        if network.get('port') == None:
            #if network.get('shared-port') != None:
            #    if debug:
            #        print("shared port on %s" % name)

            if network.get('type') == None and network.get('manager') != "wg":
                if self.get_option('require_valid_ports') == True:
                    parser['errors'].append("invalid port on %s for iface %s" % (host, name))
                else:
                    parser['warnings'].append("invalid port on %s for iface %s" % (host, name))
            else:
                if network.get('type') == "vlan":
                    vlan_mode=self.get_option('vlan_mode')
                    if debug:
                        print("VLan: %s, %s" % (host, name))
                    if vlan_mode=="vlan":
                        tp['vlans'].add(network.get('vlan'))
                    elif vlan_mode=="ifname":
                        vlan_iface=re.sub(r'^vl\.', '', name)
                        tp['vlans'].add(vlan_iface)
                    else:
                        parser['errors'].append("vlan mode %s not supported" %vlan_mode)

                elif network.get('type') == "bridge":
                    if network.get('empty') != None:
                        if debug:
                            print("Ignoring empty bridge: %s, %s" % (host, name))
                    else:
                        if debug:
                            print("Bridge: %s, %s" % (host, name))
                        tp['bridges'].add(name)

                elif network.get('type') == "bond":
                    if debug:
                        print("Bond: %s, %s" % (host, name))
                    tp['bonds'].add(name)

                elif network.get('manager') == "wg":
                    pass

                elif network.get('type') == "dummy":
                    pass

                # also ignore dummy and wg
        return phy, tp, parser

    def _subnet_reverse_zone(self, subnet_str):
        ''' Compute the DNS reverse zone name for a subnet.

        Returns the in-addr.arpa or ip6.arpa zone name corresponding to the
        given subnet string.  For IPv4 prefixes longer than /24, returns a
        classless delegation zone name (e.g. "128/25.0.0.10.in-addr.arpa").
        Returns None if the subnet string is invalid.
        '''
        try:
            net = ipaddress.ip_network(subnet_str, strict=False)
        except ValueError:
            return None

        prefix = net.prefixlen
        labels = net.network_address.reverse_pointer.split('.')

        if net.version == 4:
            if prefix <= 8:
                return '.'.join(labels[3:])
            elif prefix <= 16:
                return '.'.join(labels[2:])
            elif prefix <= 24:
                return '.'.join(labels[1:])
            else:
                host_octet = str(net.network_address).split('.')[-1]
                parent_zone = '.'.join(labels[1:])
                return '%s/%s.%s' % (host_octet, prefix, parent_zone)
        else:
            nibbles = prefix // 4
            return '.'.join(labels[32 - nibbles:])

    def _resolve_iface_vlan(self, hosts, host_name, host_type, if_key, iface):
        ''' Resolve the VLAN ID for a host interface.

        Returns the VLAN ID as a string, or None if not resolvable.
        Resolution order:
          1. Direct vlan key on the interface itself (if not "default")
          2. Bridge: find sibling interface where bridge == if_key and take its vlan
          3. VM/LXC: follow machine + link to parent host, then check 1 and 2 there
        '''
        vlan = iface.get('vlan')
        if vlan is not None and vlan != 'default':
            return str(vlan)

        if iface.get('type') == 'bridge':
            host_nets = hosts.get(host_name, {}).get('networks', {})
            for sib_key, sib_iface in host_nets.items():
                if sib_iface.get('bridge') == if_key:
                    sib_vlan = sib_iface.get('vlan')
                    if sib_vlan is not None and sib_vlan != 'default':
                        return str(sib_vlan)

        if host_type in ('lxc', 'kvm'):
            machine = hosts.get(host_name, {}).get('machine')
            link = iface.get('link')
            if machine and link and machine in hosts:
                parent_iface = hosts[machine].get('networks', {}).get(link, {})
                parent_vlan = parent_iface.get('vlan')
                if parent_vlan is not None and parent_vlan != 'default':
                    return str(parent_vlan)
                if parent_iface.get('type') == 'bridge':
                    parent_nets = hosts[machine].get('networks', {})
                    for sib_key, sib_iface in parent_nets.items():
                        if sib_iface.get('bridge') == link:
                            sib_vlan = sib_iface.get('vlan')
                            if sib_vlan is not None and sib_vlan != 'default':
                                return str(sib_vlan)

        return None

    def _resolve_iface_dhcp_network(self, dhcp_networks, iface):
        ''' Return the DHCP network name whose subnet contains iface.ipv4.

        Strips the prefix from iface.ipv4 and tests the bare IP against every
        subnet entry in every network.  Uses pre-computed subnet_network and
        subnet_netmask when available, checks for explicit netmask key when not.
        Returns the first match, or None.
        '''
        ipv4 = iface.get('ipv4')
        if not ipv4:
            return None
        bare_ip = re.sub(r'/.*$', '', str(ipv4))
        try:
            addr = ipaddress.ip_address(bare_ip)
        except ValueError:
            return None
        for net_name, net in dhcp_networks.items():
            if not isinstance(net, dict):
                continue
            subnets = net.get('subnets', {})
            if isinstance(subnets, dict):
                for subnet_key, subnet_data in subnets.items():
                    if isinstance(subnet_data, dict) and subnet_data.get('subnet_network') and subnet_data.get('subnet_netmask'):
                        cidr = '%s/%s' % (subnet_data['subnet_network'], subnet_data['subnet_netmask'])
                    else:
                        cidr = subnet_key
                    try:
                        if addr in ipaddress.ip_network(cidr, strict=False):
                            return net_name
                    except ValueError:
                        continue
            else:
                for subnet in subnets:
                    try:
                        if addr in ipaddress.ip_network(subnet, strict=False):
                            return net_name
                    except ValueError:
                        continue
        return None

    def _synthesize_host_network_metadata(self, data, valid_keys, parser):
        ''' Synthesize derived metadata on each host interface.

        Adds the following keys where they can be resolved:
          resolved_vlan     - VLAN ID string (int as str), walked via bridge/VM chain
          dhcp_network      - name of the DHCP network whose subnet contains iface.ipv4
          dns_name          - FQDN for the interface (without trailing dot), built from
                              iface.dns / resolved_vlan / host_name + dns_domain
          dns_iface_domain  - domain portion for alias resolution (vlan.domain or domain)
          dns_aliases_fqdn  - list of fully-resolved alias FQDNs (no trailing dot),
                              built from iface.dns_aliases plus one alias per
                              legacy_domain entry (for graceful domain migrations)

        dns_domain is read from default_vars.dns_domain if present, otherwise
        from the top-level default_domain key.

        legacy_domains (optional list in default_vars) enables zero-downtime domain
        migrations: for each legacy domain, an alias is auto-generated for every
        interface by replacing dns_domain with the legacy domain in dns_name.

        Must be called after _sanitise_hosts_data so addresses are synthesized.
        '''
        hosts = data.get(valid_keys['hosts'], {})
        dhcp_networks = data.get(valid_keys['networks'], {})
        default_vars = data.get(valid_keys['default_vars']) or {}
        dns_domain = default_vars.get('dns_domain') or data.get('default_domain')
        legacy_domains_raw = default_vars.get('legacy_domains') or []
        if isinstance(legacy_domains_raw, str):
            legacy_domains_raw = [legacy_domains_raw]
        legacy_domains = [str(d).rstrip('.') for d in legacy_domains_raw]

        for host_name, host_def in hosts.items():
            if not isinstance(host_def, dict):
                continue
            host_type = host_def.get('type')
            networks = host_def.get('networks')
            if not networks:
                continue
            for if_key, iface in networks.items():
                if not isinstance(iface, dict):
                    continue
                vlan = self._resolve_iface_vlan(hosts, host_name, host_type, if_key, iface)
                if vlan is not None:
                    data[valid_keys['hosts']][host_name]['networks'][if_key]['resolved_vlan'] = vlan
                dn = self._resolve_iface_dhcp_network(dhcp_networks, iface)
                if dn is not None:
                    data[valid_keys['hosts']][host_name]['networks'][if_key]['dhcp_network'] = dn
                if dns_domain:
                    explicit_dns = iface.get('dns')
                    # dns_subdomain: false on the network suppresses the vlan label
                    net_cfg = dhcp_networks.get(vlan, {}) if vlan else {}
                    use_vlan = vlan and not (isinstance(net_cfg, dict) and net_cfg.get('dns_subdomain') == False)
                    if explicit_dns:
                        if str(explicit_dns).endswith('.'):
                            dns_name = str(explicit_dns).rstrip('.')
                            dns_iface_domain = dns_domain
                        elif use_vlan:
                            dns_name = '%s.%s.%s' % (explicit_dns, vlan, dns_domain)
                            dns_iface_domain = '%s.%s' % (vlan, dns_domain)
                        else:
                            dns_name = '%s.%s' % (explicit_dns, dns_domain)
                            dns_iface_domain = dns_domain
                    elif use_vlan:
                        dns_name = '%s.%s.%s' % (host_name, vlan, dns_domain)
                        dns_iface_domain = '%s.%s' % (vlan, dns_domain)
                    else:
                        dns_name = '%s.%s' % (host_name, dns_domain)
                        dns_iface_domain = dns_domain
                    data[valid_keys['hosts']][host_name]['networks'][if_key]['dns_name'] = dns_name
                    data[valid_keys['hosts']][host_name]['networks'][if_key]['dns_iface_domain'] = dns_iface_domain

                    # Resolve dns_aliases to FQDNs
                    aliases_fqdn = []
                    for alias in iface.get('dns_aliases') or []:
                        alias = str(alias)
                        if alias.endswith('.'):
                            aliases_fqdn.append(alias.rstrip('.'))
                        else:
                            aliases_fqdn.append('%s.%s' % (alias, dns_iface_domain))
                    # Auto-generate aliases for legacy domains (domain migration support)
                    for legacy_domain in legacy_domains:
                        if dns_name.endswith('.' + dns_domain):
                            prefix = dns_name[:-(len(dns_domain) + 1)]
                            aliases_fqdn.append('%s.%s' % (prefix, legacy_domain))
                    if aliases_fqdn:
                        data[valid_keys['hosts']][host_name]['networks'][if_key]['dns_aliases_fqdn'] = aliases_fqdn

        return parser, data

    def _find_role_path(self, role_name):
        ''' Return the filesystem path of a named role, or None if not found. '''
        configured = self.get_option('roles_path')
        search_paths = list(configured) if configured else list(C.DEFAULT_ROLES_PATH)
        for search_path in search_paths:
            role_path = os.path.join(os.path.expanduser(search_path), role_name)
            if os.path.isdir(role_path):
                return role_path
        return None

    def _discover_extensions(self):
        ''' Scan all roles paths for plugins/inventory_extension.py files, load
        them, and register each extension for the host types it declares in
        HANDLES_TYPES.  Only one extension per type is allowed; if a duplicate
        is found a warning is emitted and the second one is ignored. '''

        self._extension_registry = {}

        configured = self.get_option('roles_path')
        search_paths = list(configured) if configured else list(C.DEFAULT_ROLES_PATH)

        for roles_dir in search_paths:
            roles_dir = os.path.expanduser(roles_dir)
            if not os.path.isdir(roles_dir):
                continue
            for role_name in sorted(os.listdir(roles_dir)):
                ext_path = os.path.join(
                    roles_dir, role_name, 'plugins', 'inventory_extension.py')
                if not os.path.isfile(ext_path):
                    continue
                try:
                    spec = importlib.util.spec_from_file_location(
                        'inventory_extension_%s' % role_name, ext_path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                except Exception as e:
                    self.display.warning(
                        "Failed to load inventory extension from '%s': %s"
                        % (ext_path, e))
                    continue

                handles = getattr(module, 'HANDLES_TYPES', [])
                ext_class = getattr(module, 'InventoryExtension', None)
                if not ext_class or not handles:
                    continue

                try:
                    ext_instance = ext_class()
                except Exception as e:
                    self.display.warning(
                        "Failed to instantiate InventoryExtension from '%s': %s"
                        % (ext_path, e))
                    continue

                for type_name in handles:
                    if type_name in self._extension_registry:
                        self.display.warning(
                            "Duplicate inventory extension for type '%s' "
                            "found in role '%s', ignoring (already registered)"
                            % (type_name, role_name))
                    else:
                        self._extension_registry[type_name] = ext_instance

    def _preprocess_hosts(self, data, valid_keys, parser):
        ''' For each host whose type has a registered extension, call the
        extension's preprocess_host() method.  This runs after sanitisation
        and before _parse_hosts, so extensions can modify the host definition
        in place (e.g. merging pod snippets) before inventory vars are set. '''

        hosts = data[valid_keys['hosts']]
        for host in hosts:
            host_def = hosts[host]
            host_type = host_def.get('type')
            if host_type in self._extension_registry:
                ext = self._extension_registry[host_type]
                if hasattr(ext, 'preprocess_host'):
                    ext.preprocess_host(self, host, host_def, data, valid_keys, parser)
        return parser, data

    def _synthesize_host_topology(self, data, valid_keys, parser):
        ''' Synthesize topology metadata across hosts.

        1. Populate guests: list on physical hosts from machine: backlinks.
        2. Inherit rack for LXC/KVM from machine host
        3. Inherit racks for k3s-pod from the referenced k3s cluster server.
        4. Propagate host-level switch:/port: to interfaces lacking them.
        5. Inherit switch/port from uplink interfaces to virtual interfaces
           (VLAN, bridge) on the same host that lack explicit switch/port.

        Must be called after _preprocess_hosts.
        '''
        hosts = data.get(valid_keys['hosts'], {})

        # Step 1: build machine → guests index
        machine_guests = {}
        for host_name, host_def in hosts.items():
            if not isinstance(host_def, dict):
                continue
            machine = host_def.get('machine')
            if machine and machine in hosts:
                machine_guests.setdefault(machine, [])
                if host_name not in machine_guests[machine]:
                    machine_guests[machine].append(host_name)

        for machine_name, guests in machine_guests.items():
            data[valid_keys['hosts']][machine_name]['guests'] = sorted(guests)

        # Helper: chase machine chain to resolve rack
        def _get_machine_rack(host_name, visited=None):
            if visited is None:
                visited = set()
            if host_name in visited:
                return None
            visited.add(host_name)
            h = hosts.get(host_name)
            if not isinstance(h, dict):
                return None
            rack = h.get('rack')
            if rack is not None:
                return rack
            parent = h.get('machine')
            if parent:
                return _get_machine_rack(parent, visited)
            return None

        # Step 2: LXC/KVM rack inheritance from physical host
        virtual_types = {'lxc', 'kvm', 'vm'}
        for host_name, host_def in hosts.items():
            if not isinstance(host_def, dict):
                continue
            if host_def.get('type') in virtual_types and 'rack' not in host_def:
                machine = host_def.get('machine')
                if machine:
                    rack = _get_machine_rack(machine)
                    if rack is not None:
                        data[valid_keys['hosts']][host_name]['rack'] = rack

        # Step 3: k3s-pod racks from cluster server host
        # TODO, This should be implemented in the k3s plugin - but for a
        # quick and dirty proof of concept we stick it here for now
        for host_name, host_def in hosts.items():
            if not isinstance(host_def, dict):
                continue
            if host_def.get('type') != 'k3s-pod':
                continue
            k3s = host_def.get('k3s')
            if not isinstance(k3s, dict):
                continue
            cluster = k3s.get('cluster')
            if not cluster:
                continue
            # cluster value is the inventory hostname of the k3s server
            cluster_host = hosts.get(cluster)
            rack = cluster_host.get('rack') if isinstance(cluster_host, dict) else None
            if rack is not None:
                data[valid_keys['hosts']][host_name]['rack'] = rack
                data[valid_keys['hosts']][host_name]['racks'] = [rack]

        # Steps 4 & 5: switch/port propagation
        for host_name, host_def in hosts.items():
            if not isinstance(host_def, dict):
                continue
            networks = host_def.get('networks')
            if not isinstance(networks, dict):
                continue

            host_switch = host_def.get('switch')
            host_port = host_def.get('port')

            # Collect uplink interfaces: have explicit switch + port != -1
            uplinks = []
            for if_key, iface in networks.items():
                if not isinstance(iface, dict):
                    continue
                sw = iface.get('switch')
                pt = iface.get('port')
                if sw is not None and (pt is None or pt != -1):
                    uplinks.append((if_key, sw, pt))

            for if_key, iface in networks.items():
                if not isinstance(iface, dict):
                    continue
                sw = iface.get('switch')
                pt = iface.get('port')

                # Step 4: apply host-level switch/port
                if sw is None and host_switch is not None:
                    sw = host_switch
                    data[valid_keys['hosts']][host_name]['networks'][if_key]['switch'] = sw
                if pt is None and host_port is not None and host_port != -1:
                    pt = host_port
                    data[valid_keys['hosts']][host_name]['networks'][if_key]['port'] = pt

                # Step 5: inherit from physical uplinks (skip uplink ifaces themselves)
                if sw is None and uplinks:
                    other_uplinks = [(k, s, p) for k, s, p in uplinks if k != if_key]
                    if other_uplinks:
                        if len(other_uplinks) == 1:
                            sw = other_uplinks[0][1]
                            data[valid_keys['hosts']][host_name]['networks'][if_key]['switch'] = sw
                            if pt is None and other_uplinks[0][2] is not None and other_uplinks[0][2] != -1:
                                data[valid_keys['hosts']][host_name]['networks'][if_key]['port'] = other_uplinks[0][2]
                        else:
                            seen = set()
                            sw_list = [s for _, s, _ in other_uplinks
                                       if not (s in seen or seen.add(s))]
                            # Unwrap single-element list to a plain string
                            sw_val = sw_list[0] if len(sw_list) == 1 else sw_list
                            data[valid_keys['hosts']][host_name]['networks'][if_key]['switch'] = sw_val

        return parser, data

    def _parse_hosts(self, data, parser, dryrun, valid_keys):
        ''' Add hosts with appropriate groups and host vars. '''

        hosts = data[valid_keys['hosts']]
        # Accumulates global variable contributions from extensions keyed by
        # var name.  Dict-valued contributions are merged; the variable is set
        # on group 'all' after the host loop.
        global_vars = {}

        for host in hosts:
            if (hosts[host].get('ansible_managed') == None or hosts[host].get('ansible_managed' == True)):
                groups = self._get_hostgroups(hosts[host])
                host_type = hosts[host].get('type')

                if host in self.inventory.groups:
                    parser['errors'].append("%s exists as host and group name, rename one" % host)
                    continue

                # type-specific validation via registered extension (dryrun only)
                if dryrun == True and host_type in self._extension_registry:
                    self._extension_registry[host_type].validate_host(
                        self, host, hosts[host], hosts, parser)

                if dryrun == False:
                    self.inventory.add_host(host=host)

                    if len(groups) != 0:
                        for group in groups:
                            self.inventory.add_child(group.replace("-", "_"), host)

                    self.inventory.set_variable(host, "site_parser_warnings", parser['warnings'])
                    self.inventory.set_variable(host, "site_parser_errors", parser['errors'])
                    self.inventory.set_variable(host, "network_nodes", data[valid_keys['hosts']])

                    host_vars = hosts[host].get('host_vars')
                    if host_vars != None:
                        for var in host_vars:
                            self.inventory.set_variable(host, var, host_vars[var])

                    # type-specific host setup via registered extension
                    if host_type in self._extension_registry:
                        contributions = self._extension_registry[host_type].setup_host(
                            self, host, hosts[host], groups, data, valid_keys, parser)
                        for var_name, value in (contributions or {}).items():
                            if var_name not in global_vars:
                                global_vars[var_name] = {}
                            if isinstance(value, dict):
                                global_vars[var_name].update(value)

        if dryrun == False:
            for var_name, value in global_vars.items():
                self.inventory.set_variable("all", var_name, value)

        return parser, hosts


    def _get_hostgroups(self, host):
        ''' Return a list of groups for a host '''
        groups=set()

        _groups=host.get('groups')

        if _groups != None:
            if isinstance(_groups, list):
                groups.update(_groups)
            else:
                _groups=_groups.replace(" ","")
                groups.update(_groups.split(","))

        return groups
