"""The interface-mapping screen and its NIC picker (design §15, §12.2)."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Select, Static
from textual.widgets._data_table import ColumnKey

from ...ifmap import target_nics, validate_ifmap
from . import AppScreen


class NicPickerModal(ModalScreen[str | None]):
    """Pick an OPNsense NIC for one pfSense NIC (§15)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, pf_nic: str, current: str, choices: list[str]) -> None:
        super().__init__()
        self.pf_nic = pf_nic
        self.current = current
        self.choices = list(choices)

    def compose(self) -> ComposeResult:
        options = [(choice, choice) for choice in self.choices]
        with Vertical(id="nic-picker"):
            yield Static(
                f"Target NIC for pfSense device {self.pf_nic!r}", id="nic-picker-title"
            )
            if self.current in self.choices:
                yield Select(
                    options, prompt="Base NIC", value=self.current, id="nic-select"
                )
            else:
                yield Select(options, prompt="Base NIC", id="nic-select")
            yield Input(placeholder="or type a NIC name", id="nic-input")
            with Horizontal():
                yield Button("Apply", variant="primary", id="nic-apply")
                yield Button("Cancel", id="nic-cancel")

    def _value(self) -> str | None:
        typed = self.query_one("#nic-input", Input).value.strip()
        if typed:
            return typed
        selected = self.query_one("#nic-select", Select).value
        if isinstance(selected, str) and selected:
            return selected
        return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "nic-cancel":
            self.dismiss(None)
        else:
            self.dismiss(self._value())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self._value())

    def action_cancel(self) -> None:
        self.dismiss(None)


class IfaceMapScreen(AppScreen):
    """Map every included pfSense interface onto an OPNsense device."""

    BINDINGS = [
        ("space", "toggle_include", "Toggle"),
        ("c", "continue_", "Continue"),
        ("enter", "pick_nic", "Pick NIC"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(
            "Interface map \u2014 space toggles Incl, enter picks a NIC for a physical "
            "row, c continues.",
            id="ifmap-hint",
        )
        table = DataTable(id="ifmap-table")
        table.cursor_type = "row"
        yield table
        yield Static(id="ifmap-footer")
        yield Footer()

    def on_mount(self) -> None:
        self._row_keys: list[str] = []
        self._incl_col: ColumnKey | None = None
        self._refresh_table()
        self.query_one("#ifmap-table", DataTable).focus()

    def _refresh_table(self) -> None:
        table = self.query_one("#ifmap-table", DataTable)
        table.clear(columns=True)
        self._incl_col = table.add_column("Incl")
        table.add_column("Key")
        table.add_column("Description")
        table.add_column("pfSense device")
        table.add_column("Kind")
        table.add_column("OPNsense device")
        self._row_keys = []
        for key, target in self.pfapp.ifmap.targets.items():
            self._row_keys.append(key)
            device = target.opn_device or ""
            device_cell: Text | str = (
                Text(device, style="dim") if target.kind in {"vlan", "wireguard"} else device
            )
            table.add_row(
                self._incl_text(target.kind, target.include),
                key,
                target.descr,
                target.pf_device,
                target.kind,
                device_cell,
                key=key,
            )

    @staticmethod
    def _incl_text(kind: str, include: bool) -> str:
        if kind == "unsupported":
            return "\u2013"
        return "[x]" if include else "[ ]"

    def _cursor_key(self) -> str | None:
        table = self.query_one("#ifmap-table", DataTable)
        index = table.cursor_row
        if 0 <= index < len(self._row_keys):
            return self._row_keys[index]
        return None

    def action_toggle_include(self) -> None:
        key = self._cursor_key()
        if key is None:
            return
        target = self.pfapp.ifmap.targets[key]
        if target.kind == "unsupported":
            self.notify(
                f"Interface {key} uses an unsupported device type and cannot be included.",
                severity="warning",
            )
            return
        target.include = not target.include
        self.pfapp.mark_changed()
        if self._incl_col is not None:
            self.query_one("#ifmap-table", DataTable).update_cell(
                key, self._incl_col, self._incl_text(target.kind, target.include)
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self._open_picker(str(event.row_key.value))

    def action_pick_nic(self) -> None:
        key = self._cursor_key()
        if key is not None:
            self._open_picker(key)

    def _open_picker(self, key: str) -> None:
        target = self.pfapp.ifmap.targets.get(key)
        if target is None or target.kind != "physical":
            return
        extras = [
            pair.split("=", 1)[1]
            for pair in getattr(self.pfapp.args, "map_nic", [])
            if "=" in pair
        ]
        choices = target_nics(self.pfapp.src, extras)
        modal = NicPickerModal(
            target.pf_device, target.opn_device or "", choices
        )
        self.app.push_screen(
            modal, lambda nic: self._apply_nic(target.pf_device, nic)
        )

    def _apply_nic(self, pf_nic: str, opn_nic: str | None) -> None:
        if not opn_nic:
            return
        self.pfapp.ifmap.nic_map[pf_nic] = opn_nic
        for target in self.pfapp.ifmap.targets.values():
            if target.kind == "physical" and target.pf_device == pf_nic:
                target.opn_device = opn_nic
        self.pfapp.mark_changed()
        self._refresh_table()

    def action_continue_(self) -> None:
        errors = validate_ifmap(self.pfapp.ifmap)
        footer = self.query_one("#ifmap-footer", Static)
        if errors:
            footer.update("\n".join(f"\u2716 {error}" for error in errors))
            self.notify("Fix the interface map before continuing.", severity="error")
            return
        footer.update("")
        self.pfapp.rebuild_plan()
        self.pfapp.show_select()
