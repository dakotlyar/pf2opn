"""Importing this package registers every translator (design §5).

Each milestone adds its modules here; the import is the registration side effect.
"""

from . import interfaces, system, vlans  # noqa: F401
