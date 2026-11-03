"""Importing this package registers every translator (design §5).

Each milestone adds its modules here; the import is the registration side effect.
"""

from . import aliases, gateways, interfaces, routes, system, vips, vlans  # noqa: F401
