# -*- coding: utf-8 -*-

# Make beets-extrafiles extend the beetsplug namespace package.
#
# See:
# - https://beets.readthedocs.io/en/stable/dev/plugins.html
# - https://peps.python.org/pep-0420/
# - https://packaging.python.org/en/latest/guides/packaging-namespace-packages/

# Don't change/add anything beyond this comment!
from pkgutil import extend_path
__path__ = extend_path(__path__, __name__)
