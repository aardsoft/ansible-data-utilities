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
        description: a file to dump parsed inventory to
        type: string
        default: ""
        ini:
          - key: dump_file
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
'''

import os
import re
import json

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
            "hosts_templates": self.get_option('hosts_key')+"_templates"
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
        else:
            raise AnsibleParserError("No hosts key (%s) found, can't continue." % valid_keys['hosts'])

        if self.get_option('dump_file') != "":
            with open(self.get_option('dump_file'), "w") as inventoryDump:
                inventoryDump.write(json.dumps(parsed_data, indent=4, sort_keys = True))

        if valid_keys['default_vars'] in keys:
            parser=self._add_default_vars(parsed_data, valid_keys, parser)

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

            for host in hosts:
                system=hosts[host]

                # apply a template, if needed, before doing further processing
                if 'template' in system:
                    self.apply_template(parser, k['hosts'], host, data)

                enforced_types={"server", "ilo", "ipmi", "workstation", "notebook"}

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

                        # TODO, add proper validation to addresses in structs as well
                        if network.get('addresses') != None:
                            for address in network.get('addresses'):
                                address_struct=network['addresses'][address]
                                if address_struct != None:
                                    print("non-none address %s %s" % (address, address_struct))
                                    if address_struct.get('fqdn') == None:
                                        pass
                                    #placeholder for address specific dns/vlan/... resolution

                                if address in items['ips'] and address not in dups and network.get('duplicate_ip') != True:
                                    parser['errors'].append("%s: duplicate IP %s" % (host, address))
                                items['ips'].add(address)

                # this should be configurable to 'warning'
                # also fallback to a default type
                if system.get('type') == None:
                    parser['errors'].append("%s is missing type key" % host)
                    continue
                elif system.get('type') not in enforced_types:
                    # this is a somwhat ugly workaround for now - but is the
                    # same level of validation we got from the old script
                    # instead of skipping system types this should be set
                    # by flags in pre-defined system types
                    #print("skipping system %s" % host)
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

    def _parse_hosts(self, data, parser, dryrun, valid_keys):
        ''' Add hosts with appropriate groups and host vars. '''

        hosts=data[valid_keys['hosts']]

        for host in hosts:
            if (hosts[host].get('ansible_managed') == None or hosts[host].get('ansible_managed' == True)):
                groups=self._get_hostgroups(hosts[host])

                if host in self.inventory.groups:
                    parser['errors'].append("%s exists as host and group name, rename one" % host)
                    continue

                if dryrun == False:
                    self.inventory.add_host(host=host)

                    if len(groups) != 0:
                        for group in groups:
                            self.inventory.add_child(group.replace("-", "_"), host)

                    self.inventory.set_variable(host, "site_parser_warnings", parser['warnings'])
                    self.inventory.set_variable(host, "site_parser_errors", parser['errors'])

                    self.inventory.set_variable(host, "network_nodes", data[valid_keys['hosts']])

                    host_vars=hosts[host].get('host_vars')
                    if host_vars != None:
                        for var in host_vars:
                            self.inventory.set_variable(host, var, host_vars[var])

        return parser,hosts


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
