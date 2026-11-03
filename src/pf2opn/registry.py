"""Translator registry (design §7.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid an import cycle: base does not import registry
    from .translators.base import Translator

_REGISTRY: list[type["Translator"]] = []


def register(cls: type["Translator"]) -> type["Translator"]:
    """Class decorator: add a translator class to the registry."""
    _REGISTRY.append(cls)
    return cls


def all_translators() -> list["Translator"]:
    """Every registered translator, instantiated and sorted by ``order``."""
    return [cls() for cls in sorted(_REGISTRY, key=lambda c: c.order)]


def get(section: str) -> "Translator":
    """The registered translator for *section*; raises KeyError if unknown."""
    for cls in _REGISTRY:
        if cls.section == section:
            return cls()
    raise KeyError(section)


def sections() -> list[str]:
    """Registered section keys in registry order."""
    return [cls.section for cls in sorted(_REGISTRY, key=lambda c: c.order)]