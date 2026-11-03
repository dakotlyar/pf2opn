"""Importing this package registers every translator (design §5).

Each milestone adds its modules here; the import is the registration side effect.
"""

from . import (  # noqa: F401
    aliases,
    filter,
    gateways,
    interfaces,
    nat,
    routes,
    system,
    vips,
    vlans,
)
