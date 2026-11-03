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

    def __init__(self, args, src: SourceContext | None = None) -> None:
        super().__init__()
        self.args = args
        self.force = bool(getattr(args, "force", False))

        selection_path = getattr(args, "selection", None)
        self.selection_path = Path(selection_path) if selection_path else None
        self.loaded_selection: Selection | None = None
        if self.selection_path is not None and self.selection_path.exists():
            self.loaded_selection = load_selection(self.selection_path)

        #: Set once a source context has been adopted (preloaded here, or
        #: collected on the Load screen).
        self.src: SourceContext | None = None
        self.ifmap: IfaceMap | None = None
        self.plan: Plan | None = None
        self.output_path: Path | None = None
        self.report_path: Path | None = None

        #: A loaded selection whose source hash no longer matches (README §9).
        self.selection_sha_mismatch = False

        #: True once the user has changed the selection or the interface map.
        self.selection_changed = False
        #: Populated by the review screen.
        self.output = None
        self.findings: list = []
        self.written = False

        if src is not None:
            ifmap, plan = self.make_plan(src)
            self.apply_loaded(src, ifmap, plan, getattr(args, "output", None))

    # ------------------------------------------------------------------ #
    # Source adoption (§15)
    # ------------------------------------------------------------------ #

    def make_plan(self, src: SourceContext) -> tuple[IfaceMap, Plan]:
        """Build the initial interface map and plan for *src* (pure; safe in a worker)."""
        nic_map: dict[str, str] = {}
        if self.loaded_selection is not None:
            nic_map.update(self.loaded_selection.nic_map)
        nic_map.update(_parse_nic_pairs(list(getattr(self.args, "map_nic", []) or [])))

        ifmap: IfaceMap = default_ifmap(src, nic_map)
        if self.loaded_selection is not None:
            for key, spec in self.loaded_selection.interfaces.items():
                target = ifmap.targets.get(key)
                if target is not None:
                    target.include = bool(spec.get("include", True))

        plan: Plan = build_plan(src, ifmap)
        if self.loaded_selection is not None:
            try:
                plan.selected = compute_selection(plan, self.loaded_selection)
            except ValueError:
                # A stale selection (source changed) leaves the defaults in place.
                pass
        return ifmap, plan

    def apply_loaded(
        self,
        src: SourceContext,
        ifmap: IfaceMap,
        plan: Plan,
        output: str | Path | None = None,
    ) -> None:
        """Adopt a source context and its plan, and (re)derive the write paths."""
        self.src = src
        self.ifmap = ifmap
        self.plan = plan
        self.selection_sha_mismatch = bool(
            self.loaded_selection is not None
            and self.loaded_selection.source_sha256
            and self.loaded_selection.source_sha256 != src.source_sha256
        )
        self.set_output(output)

    def set_output(self, output: str | Path | None) -> None:
        self.output_path = Path(output) if output else None
        report_arg = getattr(self.args, "report", None)
        if self.output_path is not None:
            self.report_path = _report_path(str(self.output_path), report_arg)
        elif report_arg:
            self.report_path = Path(report_arg)
        else:
            self.report_path = None

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    def on_mount(self) -> None:
        self.push_screen(LoadScreen())
        if self.selection_sha_mismatch:
            self.notify(
                "the loaded selection's source_sha256 does not match the source file",
                severity="warning",
            )

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
