"""Pilot tests for the interface-mapping screen (design §15, §17 M11)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from textual.widgets import DataTable, Input, Static

from pf2opn.context import SourceContext
from pf2opn.ifmap import validate_ifmap
from pf2opn.tui.app import Pf2OpnApp
from pf2opn.tui.screens.ifmap import IfaceMapScreen, NicPickerModal
from pf2opn.tui.screens.load import LoadScreen


def _app(fixture: Path, base: Path, output: Path, *, map_nic=()) -> Pf2OpnApp:
    src = SourceContext.load(fixture, base)
    args = Namespace(
        output=str(output),
        report=None,
        selection=None,
        map_nic=list(map_nic),
        force=False,
    )
    return Pf2OpnApp(args, src)


async def _goto_ifmap(pilot, app: Pf2OpnApp) -> None:
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, IfaceMapScreen)


async def test_validation_blocks_until_nics_are_mapped(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """An unmapped physical NIC keeps the screen on the map with errors shown."""
    app = _app(pfsense_dir / "nat.xml", opnsense_base, tmp_path / "out.xml")
    async with app.run_test(size=(120, 40)) as pilot:
        await _goto_ifmap(pilot, app)

        await pilot.press("c")
        await pilot.pause()

        assert isinstance(app.screen, IfaceMapScreen)
        footer = str(app.screen.query_one("#ifmap-footer", Static).content)
        assert "opt1" in footer and "not mapped" in footer

        # map opt1 through the NIC picker
        table = app.screen.query_one("#ifmap-table", DataTable)
        table.move_cursor(row=app.screen._row_keys.index("opt1"))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, NicPickerModal)
        modal = app.screen
        modal.query_one("#nic-input", Input).value = "igc2"
        modal.query_one("#nic-input", Input).focus()
        await pilot.press("enter")
        await pilot.pause()

        assert app.ifmap.nic_map["em2"] == "igc2"
        assert app.ifmap.targets["opt1"].opn_device == "igc2"
        assert table.get_row("opt1")[-1] == "igc2"

        # exclude opt7 with space: the map is now valid
        table.move_cursor(row=app.screen._row_keys.index("opt7"))
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert app.ifmap.targets["opt7"].include is False

        assert validate_ifmap(app.ifmap) == []


async def test_nic_picker_applies_to_the_shared_pf_nic(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """The picker updates ``nic_map`` and the target device for the chosen NIC."""
    app = _app(pfsense_dir / "nat.xml", opnsense_base, tmp_path / "out.xml")
    async with app.run_test(size=(120, 40)) as pilot:
        await _goto_ifmap(pilot, app)

        table = app.screen.query_one("#ifmap-table", DataTable)
        table.move_cursor(row=app.screen._row_keys.index("opt7"))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, NicPickerModal)

        # use the Select rather than the free-text input
        from textual.widgets import Select

        app.screen.query_one("#nic-select", Select).value = "igc0"
        await pilot.pause()
        await pilot.click("#nic-apply")
        await pilot.pause()

        assert app.ifmap.nic_map["em3"] == "igc0"
        assert app.ifmap.targets["opt7"].opn_device == "igc0"


async def test_space_toggles_include(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    app = _app(pfsense_dir / "nat.xml", opnsense_base, tmp_path / "out.xml")
    async with app.run_test(size=(120, 40)) as pilot:
        await _goto_ifmap(pilot, app)
        table = app.screen.query_one("#ifmap-table", DataTable)
        table.move_cursor(row=app.screen._row_keys.index("opt1"))
        await pilot.pause()

        assert app.ifmap.targets["opt1"].include is True
        await pilot.press("space")
        await pilot.pause()
        assert app.ifmap.targets["opt1"].include is False
        assert table.get_row("opt1")[0] == "[ ]"


async def test_back_returns_to_load(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """§15: ``b`` / ``Esc`` goes back one screen (to Load)."""
    app = _app(pfsense_dir / "minimal.xml", opnsense_base, tmp_path / "out.xml")
    async with app.run_test(size=(120, 40)) as pilot:
        await _goto_ifmap(pilot, app)
        await pilot.press("b")
        await pilot.pause()
        assert isinstance(app.screen, LoadScreen)

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, IfaceMapScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, LoadScreen)


async def test_space_refused_for_unsupported_device(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """``opt3`` is ``ovpns1`` (unsupported); space must not include it."""
    app = _app(pfsense_dir / "interfaces_vlans.xml", opnsense_base, tmp_path / "out.xml")
    async with app.run_test(size=(120, 40)) as pilot:
        await _goto_ifmap(pilot, app)
        table = app.screen.query_one("#ifmap-table", DataTable)
        assert app.ifmap.targets["opt3"].kind == "unsupported"
        table.move_cursor(row=app.screen._row_keys.index("opt3"))
        await pilot.pause()

        assert app.ifmap.targets["opt3"].include is False
        await pilot.press("space")
        await pilot.pause()
        assert app.ifmap.targets["opt3"].include is False
        assert table.get_row("opt3")[0] == "\u2013"
