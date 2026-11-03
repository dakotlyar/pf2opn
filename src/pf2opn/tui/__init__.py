"""The pf2opn Textual TUI (design §15).

The application is :class:`~pf2opn.tui.app.Pf2OpnApp`; :func:`run` is the small
entry point the ``tui`` subcommand calls so ``textual`` stays an optional import
for the non-interactive commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import for type checkers only
    from ..context import SourceContext


def run(args, src: "SourceContext | None" = None) -> int:
    """Run the TUI for *args* and an optional preloaded *src*; return the exit code."""
    from .app import Pf2OpnApp

    app = Pf2OpnApp(args, src)
    app.run()
    return app.return_code if app.return_code is not None else 0


__all__ = ["run"]
