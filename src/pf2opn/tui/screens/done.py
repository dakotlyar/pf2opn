"""The Done screen: what was written (design §15)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Static

from . import AppScreen


class DoneScreen(AppScreen):
    """Confirm the write and point at the report."""

    BINDINGS = [("q", "quit_now", "Quit")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="done-body"):
            yield Static(id="done-summary")
        yield Footer()

    def on_mount(self) -> None:
        plan = self.pfapp.plan
        migrated = sum(1 for item in plan.items if item.id in plan.selected)
        plugins = sorted(
            {
                item.requires_plugin
                for item in plan.items
                if item.id in plan.selected and item.requires_plugin
            }
        )
        lines = [
            "[b]Migration written.[/b]",
            "",
            f"Output: {self.pfapp.output_path}",
            f"Report: {self.pfapp.report_path}",
            f"Items migrated: {migrated}",
            "",
            "Plugins to install first:",
        ]
        if plugins:
            lines.extend(f"  - {plugin}" for plugin in plugins)
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append(f"open report: {self.pfapp.report_path}")
        lines.append("")
        lines.append("Press [b]q[/b] to quit.")
        self.query_one("#done-summary", Static).update("\n".join(lines))

    def action_quit_now(self) -> None:
        self.app.exit()
