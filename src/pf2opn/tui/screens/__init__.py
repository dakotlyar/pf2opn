"""The TUI screens (design §15): Load → IfaceMap → Select → Review → Done."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.screen import Screen

if TYPE_CHECKING:  # pragma: no cover - import for type checkers only
    from ..app import Pf2OpnApp


class AppScreen(Screen):
    """A screen bound to :class:`~pf2opn.tui.app.Pf2OpnApp`."""

    @property
    def pfapp(self) -> "Pf2OpnApp":
        return self.app  # type: ignore[return-value]


__all__ = ["AppScreen"]
