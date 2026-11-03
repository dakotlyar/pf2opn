"""The Load screen: a static summary of source and base (design §15)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Static

from ...model import Status
from ...registry import all_translators
from ...versions import MODEL_VERSIONS
from . import AppScreen
from .ifmap import IfaceMapScreen


def _section_counts(plan) -> list[tuple[str, int, int, int]]:
    counts: list[tuple[str, int, int, int]] = []
    for translator in all_translators():
        items = [item for item in plan.items if item.section == translator.section]
        if not items:
            continue
        ok = sum(1 for item in items if item.status == Status.OK)
        partial = sum(1 for item in items if item.status == Status.PARTIAL)
        unsupported = sum(1 for item in items if item.status == Status.UNSUPPORTED)
        counts.append((translator.title, ok, partial, unsupported))
    return counts


def base_version_warnings(src) -> list[str]:
    """The §10.3 base-model version mismatches worth flagging up front."""
    warnings: list[str] = []
    for path, expected in MODEL_VERSIONS.items():
        matches = src.base.xpath(path)
        if not matches:
            continue
        actual = matches[0].get("version")
        if actual is not None and actual != expected:
            warnings.append(
                f"base model {path} is version {actual}, pf2opn was written for {expected}"
            )
    return warnings


class LoadScreen(AppScreen):
    """Show what was read from the two config files, then continue."""

    BINDINGS = [("enter", "continue_", "Continue")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="load-body"):
            yield Static(id="load-summary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#load-summary", Static).update(self._summary())
        self.query_one("#load-summary", Static).focus()

    def _summary(self) -> str:
        src = self.pfapp.src
        plan = self.pfapp.plan
        lines = [
            "[b]pf2opn[/b] — review the migration before writing anything.",
            "",
            f"Source: {src.source_path}",
            f"  pfSense version: {src.pf_version or '(unknown)'}",
            f"  hostname: {src.pf_hostname or '(unknown)'}",
            "",
            f"Base: {src.base_path}",
            f"  OPNsense hostname: {src.base_hostname or '(unknown)'}",
            "",
            "Sections found:",
        ]
        for title, ok, partial, unsupported in _section_counts(plan):
            lines.append(
                f"  {title}: {ok + partial + unsupported} item(s) "
                f"({ok} ok, {partial} partial, {unsupported} unsupported)"
            )
        problems = base_version_warnings(src)
        if problems:
            lines.append("")
            lines.append("Base version warnings:")
            lines.extend(f"  - {warning}" for warning in problems)
        lines.append("")
        lines.append("Press [b]enter[/b] to continue to the interface map.")
        return "\n".join(lines)

    def action_continue_(self) -> None:
        self.app.push_screen(IfaceMapScreen())
