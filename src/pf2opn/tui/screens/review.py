"""The Review screen: build, validate and write (design §15, §9)."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Static

from ...model import Plan, Selection, Status
from ...registry import all_translators
from ...report import render_report
from ...selection import save_selection
from ...validate import validate
from ...writer import build_output, write_output
from . import AppScreen
from .done import DoneScreen


def _summary_lines(plan: Plan) -> list[str]:
    lines = ["section  | migrated | skipped | unsupported"]
    for translator in all_translators():
        items = [item for item in plan.items if item.section == translator.section]
        if not items:
            continue
        unsupported = sum(1 for item in items if item.status == Status.UNSUPPORTED)
        migrated = sum(
            1
            for item in items
            if item.status != Status.UNSUPPORTED and item.id in plan.selected
        )
        skipped = len(items) - unsupported - migrated
        lines.append(
            f"{translator.title} | {migrated} | {skipped} | {unsupported}"
        )
    return lines


def build_selection(app) -> Selection:
    """The §9 selection JSON that reproduces the app's current plan."""
    return Selection(
        nic_map={key: value for key, value in app.ifmap.nic_map.items() if value},
        interfaces={
            key: {"include": target.include}
            for key, target in app.ifmap.targets.items()
        },
        selected=sorted(app.plan.selected),
        deselected=[],
        options={"keep_base_rules": bool(app.plan.options.get("keep_base_rules", False))},
        source_sha256=app.src.source_sha256,
    )


class SaveSelectionModal(ModalScreen[str | None]):
    """Prompt for the selection-JSON path (§15)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, default: Path) -> None:
        super().__init__()
        self.default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="save-selection"):
            yield Static("Save selection JSON", id="save-selection-title")
            yield Input(value=str(self.default), id="save-selection-path")
            with Horizontal():
                yield Button("Save", variant="primary", id="save-selection-ok")
                yield Button("Cancel", id="save-selection-cancel")

    def on_mount(self) -> None:
        self.query_one("#save-selection-path", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-selection-cancel":
            self.dismiss(None)
        else:
            self.dismiss(self.query_one("#save-selection-path", Input).value.strip() or None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmOverwriteModal(ModalScreen[bool]):
    """Confirm overwriting an existing output when ``--force`` is set (§9)."""

    BINDINGS = [("y", "yes", "Yes"), ("n", "no", "No"), ("escape", "no", "Cancel")]

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-overwrite"):
            yield Static(f"Overwrite {self.path}?", id="confirm-overwrite-text")
            with Horizontal():
                yield Button("Overwrite", variant="warning", id="overwrite-yes")
                yield Button("Cancel", id="overwrite-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "overwrite-yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class ReviewScreen(AppScreen):
    """Build the output, validate it, then write on ``w``."""

    BINDINGS = [
        ("w", "write", "Write"),
        ("s", "save_selection", "Save selection"),
        ("b", "back", "Back"),
        ("escape", "back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="review-body"):
            yield Static(id="review-summary")
            yield Static(id="review-errors")
            yield Static(id="review-warnings")
            yield Static(id="review-paths")
        yield Footer()

    def on_mount(self) -> None:
        self.ready = False
        self.compute()

    # ------------------------------------------------------------------ #
    # Build + validate in a worker thread (§15)
    # ------------------------------------------------------------------ #

    @work(thread=True, exclusive=True, group="review")
    def compute(self) -> None:
        plan = self.pfapp.plan
        out = build_output(plan, self.pfapp.src)
        findings = validate(out, plan)
        self.pfapp.output = out
        self.pfapp.findings = findings
        self.app.call_from_thread(self._show_results)

    def _errors(self) -> list:
        return [finding for finding in self.pfapp.findings if finding.level == "error"]

    def _show_results(self) -> None:
        if not self.is_mounted:
            return
        plan = self.pfapp.plan
        findings = self.pfapp.findings
        errors = [finding for finding in findings if finding.level == "error"]
        warnings = [finding for finding in findings if finding.level == "warning"]
        self.query_one("#review-summary", Static).update("\n".join(_summary_lines(plan)))
        self.query_one("#review-errors", Static).update(
            "\n".join(f"\u2716 {error.message}" for error in errors)
            if errors
            else "No validation errors."
        )
        self.query_one("#review-warnings", Static).update(
            "\n".join(f"\u26a0 {warning.message}" for warning in warnings)
            if warnings
            else "No validation warnings."
        )
        self.query_one("#review-paths", Static).update(
            f"Output: {self.pfapp.output_path}\nReport: {self.pfapp.report_path}"
        )
        self.ready = True
        self.refresh_bindings()

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "write" and getattr(self, "ready", False) and self._errors():
            return False
        return True

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def action_write(self) -> None:
        if not self.ready:
            self.notify("Still preparing the output\u2026", severity="warning")
            return
        if self._errors():
            self.notify("Cannot write while there are validation errors.", severity="error")
            return
        out_path = self.pfapp.output_path
        if out_path.exists() and not self.pfapp.force:
            self.notify(
                f"{out_path} exists; restart with --force to overwrite.",
                severity="error",
            )
            return
        if out_path.exists() and self.pfapp.force:
            self.app.push_screen(
                ConfirmOverwriteModal(out_path), self._after_overwrite
            )
            return
        self._write()

    def _after_overwrite(self, overwrite: bool | None) -> None:
        if overwrite:
            self._write()

    def _write(self) -> None:
        out_path = self.pfapp.output_path
        try:
            write_output(self.pfapp.output, out_path)
            report = render_report(
                self.pfapp.plan, self.pfapp.findings, self.pfapp.src, out_path
            )
            self.pfapp.report_path.write_text(report, encoding="utf-8")
        except OSError as exc:
            self.notify(f"Write failed: {exc}", severity="error")
            return
        self.pfapp.written = True
        self.pfapp.selection_changed = False
        self.app.push_screen(DoneScreen())

    def action_save_selection(self) -> None:
        default = self.pfapp.selection_path or Path(
            str(self.pfapp.output_path) + ".selection.json"
        )
        self.app.push_screen(SaveSelectionModal(default), self._after_save)

    def _after_save(self, path: str | None) -> None:
        if not path:
            return
        try:
            save_selection(path, build_selection(self.pfapp))
        except OSError as exc:
            self.notify(f"Could not save the selection: {exc}", severity="error")
            return
        self.notify(f"Selection saved to {path}")

    def action_back(self) -> None:
        self.app.pop_screen()
