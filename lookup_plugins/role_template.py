''' Lookup plugin: render a Jinja2 template from a named role's templates/ dir.

Unlike the built-in ``template`` lookup, this plugin sets the Jinja2
FileSystemLoader to the role's own templates/ directory so that
``{% include %}`` directives inside the template resolve correctly
relative to that role rather than the calling role or playbook.

Usage::

    lookup('role_template', 'filename.j2', role='rolename')

Required keyword argument:

  role  -- name of the role whose templates/ directory to search

The template is rendered with the full current Ansible variable scope and
all standard Ansible Jinja2 filters/tests are available.
'''

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
  name: role_template
  author: ansible-roles
  short_description: render a template from a named role's templates/ dir
  description:
    - Locates the named role on the configured roles_path, then renders
      the given template file from that role's C(templates/) directory.
    - Sets the Jinja2 FileSystemLoader to the role's C(templates/)
      directory so C({% include %}) directives in the template resolve
      correctly relative to the role.
  options:
    _terms:
      description: Template filename(s) relative to the role's templates/ dir.
      required: true
    role:
      description: Name of the role whose templates/ directory to search.
      required: true
'''

EXAMPLES = r'''
- name: render a template from an external role
  debug:
    msg: "{{ lookup('role_template', 'myfile.j2', role='myrole') }}"
'''

RETURN = r'''
  _raw:
    description: Rendered template strings.
    type: list
    elements: str
'''

import os
import jinja2

from ansible import constants as C
from ansible.errors import AnsibleLookupError
from ansible.plugins.lookup import LookupBase
from ansible.template import Templar


class LookupModule(LookupBase):

    def _find_role_path(self, role_name):
        ''' Return the filesystem path of a named role, or None if not found. '''
        for search_path in C.DEFAULT_ROLES_PATH:
            role_path = os.path.join(os.path.expanduser(search_path), role_name)
            if os.path.isdir(role_path):
                return role_path
        return None

    def run(self, terms, variables=None, **kwargs):
        role = kwargs.get('role')
        if not role:
            raise AnsibleLookupError(
                'role_template: role= keyword argument is required')

        role_path = self._find_role_path(role)
        if not role_path:
            raise AnsibleLookupError(
                "role_template: role '%s' not found in roles path" % role)

        templates_dir = os.path.join(role_path, 'templates')
        if not os.path.isdir(templates_dir):
            raise AnsibleLookupError(
                "role_template: role '%s' has no templates/ directory" % role)

        render_vars = dict(variables or {})
        if 'ansible_managed' not in render_vars:
            render_vars['ansible_managed'] = C.DEFAULT_MANAGED_STR
        templar = Templar(loader=self._loader, variables=render_vars)

        results = []
        for term in terms:
            template_path = os.path.join(templates_dir, term)
            if not os.path.isfile(template_path):
                raise AnsibleLookupError(
                    "role_template: '%s' not found in '%s'" % (term, templates_dir))

            try:
                with open(template_path, 'r') as fh:
                    source = fh.read()
            except Exception as exc:
                raise AnsibleLookupError(
                    "role_template: failed to read '%s': %s" % (template_path, exc))

            # Temporarily replace the Jinja2 environment loader with a
            # FileSystemLoader for the role's templates/ directory so that
            # {% include %} directives inside the template resolve correctly.
            old_loader = templar.environment.loader
            templar.environment.loader = jinja2.FileSystemLoader(templates_dir)
            try:
                rendered = templar.template(source, fail_on_undefined=True)
            except Exception as exc:
                raise AnsibleLookupError(
                    "role_template: failed to render '%s' from role '%s': %s"
                    % (term, role, exc))
            finally:
                templar.environment.loader = old_loader

            results.append(rendered)

        return results
