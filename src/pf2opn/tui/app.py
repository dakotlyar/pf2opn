"""The pf2opn Textual application (design §15)."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..cli import _parse_nic_pairs, _report_path
from ..context import SourceContext
from ..deps import closure
from ..ifmap import default_ifmap
from ..model import IfaceMap, Plan, Status
from ..plan import build_plan
from ..selection import Selection, compute_selection, load_selection
from .screens.load import LoadScreen


class ConfirmQuitModal(ModalScreen[bool]):
    """Confirm quitting when the selection has changed but nothing was written."""

    BINDINGS = [("y", "yes", "Yes"), ("n", "no", "No"), ("escape", "no", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-quit"):
            yield Static(
                "The selection has changed and nothing has been written.\n"
                "Quit without writing?",
                id="confirm-quit-text",
            )
            with Horizontal():
                yield Button("Quit", variant="error", id="quit-yes")
                yield Button("Cancel", id="quit-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "quit-yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class Pf2OpnApp(App[None]):
    """The interactive migration app: Load → IfaceMap → Select → Review → Done."""

    CSS_PATH = "app.tcss"
    TITLE = "pf2opn"
    SUB_TITLE = "pfSense → OPNsense migration"
    BINDINGS = [("q", "request_quit", "Quit")]

    def __init__(self, src: SourceContext, args) -> None:
        super().__init__()
        self.src = src
        self.args = args

        self.output_path = Path(args.output)
        self.report_path = _report_path(args.output, getattr(args, "report", None))
        self.force = bool(getattr(args, "force", False))

        selection_path = getattr(args, "selection", None)
        self.selection_path = Path(selection_path) if selection_path else None
        self.loaded_selection: Selection | None = None
        if self.selection_path is not None and self.selection_path.exists():
            self.loaded_selection = load_selection(self.selection_path)

        nic_map: dict[str, str] = {}
        if self.loaded_selection is not None:
            nic_map.update(self.loaded_selection.nic_map)
        nic_map.update(_parse_nic_pairs(list(getattr(args, "map_nic", []) or [])))

        self.ifmap: IfaceMap = default_ifmap(src, nic_map)
        if self.loaded_selection is not None:
            for key, spec in self.loaded_selection.interfaces.items():
                target = self.ifmap.targets.get(key)
                if target is not None:
                    target.include = bool(spec.get("include", True))

        self.plan: Plan = build_plan(src, self.ifmap)
        if self.loaded_selection is not None:
            try:
                self.plan.selected = compute_selection(self.plan, self.loaded_selection)
            except ValueError:
                # A stale selection (source changed) leaves the defaults in place.
                pass

        #: True once the user has changed the selection or the interface map.
        self.selection_changed = False
        #: Populated by the review screen.
        self.output = None
        self.findings: list = []
        self.written = False

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    def on_mount(self) -> None:
        self.push_screen(LoadScreen())

    def show_select(self) -> None:
        """Open the select screen (imported lazily so the shell stays decoupled)."""
        from .screens.select import SelectScreen

        self.push_screen(SelectScreen())

    def rebuild_plan(self) -> Plan:
        """Re-run ``build_plan`` for the current ifmap, keeping the selection by id."""
        kept = set(self.plan.selected)
        plan = build_plan(self.src, self.ifmap)
        kept = {item_id for item_id in kept if item_id in plan.by_id}
        self._sync_interfaces(plan, kept)
        plan.selected = {
            item_id
            for item_id in closure(kept, plan)
            if plan.by_id[item_id].status != Status.UNSUPPORTED
        }
        self.plan = plan
        return plan

    def _sync_interfaces(self, plan: Plan, kept: set[str]) -> None:
        for key, target in self.ifmap.targets.items():
            item_id = f"iface:{key}"
            if item_id not in plan.by_id:
                continue
            if target.include and plan.by_id[item_id].status != Status.UNSUPPORTED:
                kept.add(item_id)
            else:
                kept.discard(item_id)

    def mark_changed(self) -> None:
        self.selection_changed = True

    # ------------------------------------------------------------------ #
    # Quit
    # ------------------------------------------------------------------ #

    def action_request_quit(self) -> None:
        if isinstance(self.screen, ModalScreen):
            # A modal owns the keyboard; don't stack a second confirm on top.
            return
        if self.selection_changed and not self.written:
            self.push_screen(ConfirmQuitModal(), self._after_quit_confirm)
        else:
            self.exit()

    def _after_quit_confirm(self, quit_: bool | None) -> None:
        if quit_:
            self.exit()
