# check with ansible-doc -t inventory -l|grep site_yaml if ansible can
# find the plugin
#
# https://docs.ansible.com/ansible/latest/dev_guide/developing_inventory.html

DOCUMENTATION = '''
    name: site_yaml
    short_description: Build inventory using yaml formatted site files.
'''

import os
from ansible.plugins.inventory import BaseInventoryPlugin

class InventoryModule(BaseInventoryPlugin):

    NAME = 'site_yaml'
