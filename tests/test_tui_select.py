"""Pilot tests for the Select screen (design §15, §8, §17 M12)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from textual.widgets import Static, TextArea

from pf2opn.context import SourceContext
from pf2opn.tui.app import Pf2OpnApp
from pf2opn.tui.screens.select import ConfirmDeselectModal, SelectScreen


def _app(fixture: Path, base: Path, output: Path, *, map_nic=(), selection=None) -> Pf2OpnApp:
    src = SourceContext.load(fixture, base)
    args = Namespace(
        output=str(output),
        report=None,
        selection=selection,
        map_nic=list(map_nic),
        force=False,
    )
    return Pf2OpnApp(src, args)


async def _goto_select(pilot, app: Pf2OpnApp) -> SelectScreen:
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    await pilot.press("c")
    await pilot.pause()
    assert isinstance(app.screen, SelectScreen)
    return app.screen


def _cursor(screen: SelectScreen, kind: str, value: str) -> None:
    node = screen._find((kind, value))
    assert node is not None, f"no {kind} node {value!r} in the tree"
    screen._tree.move_cursor(node)


async def test_toggle_item(pfsense_dir: Path, opnsense_base: Path, tmp_path: Path) -> None:
    app = _app(
        pfsense_dir / "filter.xml",
        opnsense_base,
        tmp_path / "out.xml",
        map_nic=["em0=igc0", "em1=igc1", "em2=igc2"],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _goto_select(pilot, app)
        _cursor(screen, "item", "rule:1003")
        await pilot.pause()

        assert "rule:1003" in app.plan.selected
        await pilot.press("space")
        await pilot.pause()
        assert "rule:1003" not in app.plan.selected

        # the leaf label reflects the toggle
        node = screen._find(("item", "rule:1003"))
        assert str(node.label).startswith("[ ]")

        await pilot.press("space")
        await pilot.pause()
        assert "rule:1003" in app.plan.selected


async def test_cascade_confirm_on_deselect(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """Deselecting an alias a selected rule depends on asks first (§8)."""
    app = _app(
        pfsense_dir / "filter.xml",
        opnsense_base,
        tmp_path / "out.xml",
        map_nic=["em0=igc0", "em1=igc1", "em2=igc2"],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _goto_select(pilot, app)
        assert "alias:RFC1918" in app.plan.selected
        assert "rule:1005" in app.plan.selected

        _cursor(screen, "item", "alias:RFC1918")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDeselectModal)
        title = str(app.screen.query_one("#confirm-deselect-title", Static).content)
        assert "deselect 1" in title

        # "n" cancels: nothing changes
        await pilot.press("n")
        await pilot.pause()
        assert "alias:RFC1918" in app.plan.selected
        assert "rule:1005" in app.plan.selected

        # "y" drops both
        _cursor(screen, "item", "alias:RFC1918")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert "alias:RFC1918" not in app.plan.selected
        assert "rule:1005" not in app.plan.selected


async def test_selecting_pulls_dependencies(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    app = _app(
        pfsense_dir / "filter.xml",
        opnsense_base,
        tmp_path / "out.xml",
        map_nic=["em0=igc0", "em1=igc1", "em2=igc2"],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _goto_select(pilot, app)
        # drop the alias (and its rule) first
        _cursor(screen, "item", "alias:RFC1918")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert "alias:RFC1918" not in app.plan.selected

        _cursor(screen, "item", "rule:1005")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert "rule:1005" in app.plan.selected
        assert "alias:RFC1918" in app.plan.selected


async def test_section_toggle(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    app = _app(
        pfsense_dir / "filter.xml",
        opnsense_base,
        tmp_path / "out.xml",
        map_nic=["em0=igc0", "em1=igc1", "em2=igc2"],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _goto_select(pilot, app)
        aliases = [
            item.id
            for item in app.plan.items
            if item.section == "aliases" and item.id in app.plan.selected
        ]
        assert aliases, "the aliases section should start selected"

        _cursor(screen, "section", "aliases")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDeselectModal)
        await pilot.press("y")
        await pilot.pause()

        assert not [
            item.id for item in app.plan.items if item.section == "aliases" and item.id in app.plan.selected
        ]

        # pressing space on the section again re-selects everything
        _cursor(screen, "section", "aliases")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert {
            item.id for item in app.plan.items if item.section == "aliases" and item.id in app.plan.selected
        } >= {"alias:RFC1918", "alias:WEBPORTS"}


async def test_filter_cycles(pfsense_dir: Path, opnsense_base: Path, tmp_path: Path) -> None:
    app = _app(
        pfsense_dir / "filter.xml",
        opnsense_base,
        tmp_path / "out.xml",
        map_nic=["em0=igc0", "em1=igc1", "em2=igc2"],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _goto_select(pilot, app)
        # drop a rule so the "selected" filter can hide it
        _cursor(screen, "item", "rule:1003")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert screen.filter_mode == "all"

        await pilot.press("f")
        await pilot.pause()
        assert screen.filter_mode == "selected"
        assert screen._find(("item", "rule:1003")) is None
        assert screen._find(("item", "rule:1002")) is not None

        await pilot.press("f")
        await pilot.pause()
        assert screen.filter_mode == "problems"
        # an UNSUPPORTED item is a problem and stays visible
        assert screen._find(("item", "alias:pfB_ALIAS")) is not None

        await pilot.press("f")
        await pilot.pause()
        assert screen.filter_mode == "all"


async def test_search_by_label(pfsense_dir: Path, opnsense_base: Path, tmp_path: Path) -> None:
    app = _app(pfsense_dir / "trust_users.xml", opnsense_base, tmp_path / "out.xml")
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _goto_select(pilot, app)
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("c", "e", "r", "t")
        await pilot.pause()

        assert screen.search == "cert"
        assert screen._find(("item", "cert:cert1")) is not None
        assert screen._find(("item", "ca:ca1")) is None

        await pilot.press("escape")
        await pilot.pause()
        assert screen.search == ""
        assert screen._find(("item", "ca:ca1")) is not None


async def test_detail_tabs_show_redacted_text(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """§11: the Source and Preview tabs never contain a secret value."""
    app = _app(pfsense_dir / "trust_users.xml", opnsense_base, tmp_path / "out.xml")
    bcrypt = "$2y$10$alicealicealicealicealicealicealicealicealiceali"
    authorized = (
        "c3NoLXJzYSBBQUFBQjNOemFDMXljMkVBQUFBREFRQUJBQUFCb3dUZXN0S2V5"
        "T25seU5vdFJlYWw="
    )
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _goto_select(pilot, app)
        _cursor(screen, "item", "user:alice")
        await pilot.pause()

        source = screen.query_one("#source", TextArea).text
        preview = screen.query_one("#preview", TextArea).text
        assert bcrypt not in source
        assert authorized not in source
        assert bcrypt not in preview
        assert authorized not in preview
        assert "\u00abredacted" in source
        assert "\u00abredacted" in preview

        notes = str(screen.query_one("#notes", Static).content)
        assert "Status:" in notes


async def test_notes_lists_dependencies(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    app = _app(
        pfsense_dir / "filter.xml",
        opnsense_base,
        tmp_path / "out.xml",
        map_nic=["em0=igc0", "em1=igc1", "em2=igc2"],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _goto_select(pilot, app)
        _cursor(screen, "item", "rule:1005")
        await pilot.pause()
        notes = str(screen.query_one("#notes", Static).content)
        assert "Depends on:" in notes
        assert "RFC1918" in notes
        assert "[selected]" in notes
