"""
HTML inventory visualization generator for the site_yaml inventory plugin.

Generates a self-contained single-file HTML page embedding the parsed
inventory JSON and building three views with vanilla JS + vis-network:
  - Overview: summary stats + sortable/filterable host table
  - By Rack:  hosts grouped by rack value
  - Network Diagram: hosts and networks as a force-directed graph

This is just a very small shim to make sure we can build a self-contained HTML
file from the HTML template - all of our dashboard logic is implemented as JS
inside of the HTML template.
"""

import json as _json
import os as _os

_DIR        = _os.path.dirname(__file__)
_VENDOR_DIR = _os.path.join(_DIR, 'vendor')
_TEMPLATE_FILE = _os.path.join(_DIR, 'inventory_html_template.html')


def generate(parsed_data):
    """Return a self-contained HTML string visualizing the inventory."""
    data_json = _json.dumps(parsed_data, sort_keys=True, default=str)
    # Escape </script> so the JSON blob cannot accidentally close the tag.
    data_json = data_json.replace('</', r'<\/')
    with open(_TEMPLATE_FILE) as _f:
        template = _f.read()
    with open(_os.path.join(_VENDOR_DIR, 'vis-network.min.js')) as _f:
        vis_js = _f.read()
    return (template
            .replace('__INVENTORY_JSON__', data_json)
            .replace('__VIS_NETWORK_JS__', vis_js))
