"""Pilot tests for the Review / Write / Done flow (design §15, §9, §17 M13)."""

from __future__ import annotations

import stat
from argparse import Namespace
from pathlib import Path

from lxml import etree
from textual.widgets import Input, Static

from pf2opn import cli
from pf2opn.context import SourceContext
from pf2opn.tui.app import Pf2OpnApp
from pf2opn.tui.screens.done import DoneScreen
from pf2opn.tui.screens.review import ReviewScreen, SaveSelectionModal
from pf2opn.validate import Finding


def _app(fixture: Path, base: Path, output: Path, *, map_nic=(), selection=None, force=False):
    src = SourceContext.load(fixture, base)
    args = Namespace(
        output=str(output),
        report=None,
        selection=selection,
        map_nic=list(map_nic),
        force=force,
    )
    return Pf2OpnApp(src, args)


async def _goto_review(pilot, app: Pf2OpnApp) -> ReviewScreen:
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    await pilot.press("c")
    await pilot.pause()
    await pilot.press("r")
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    assert isinstance(app.screen, ReviewScreen)
    return app.screen


def test_tui_subcommand_refuses_output_onto_source(
    minimal_path: Path, opnsense_base: Path
) -> None:
    """The ``tui`` subcommand is wired and validates OUT before launching (§9)."""
    code = cli.main(
        ["tui", str(minimal_path), "--base", str(opnsense_base), "-o", str(minimal_path)]
    )
    assert code == 2


def _without_revision_time(path: Path) -> bytes:
    tree = etree.fromstring(path.read_bytes())
    timestamp = tree.find("revision/time")
    if timestamp is not None:
        timestamp.text = "X"
    return etree.tostring(tree)


async def test_review_writes_output_and_report(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.xml"
    app = _app(
        pfsense_dir / "minimal.xml",
        opnsense_base,
        out,
        map_nic=["em0=igc0", "em1=igc1"],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        review = await _goto_review(pilot, app)
        errors = str(review.query_one("#review-errors", Static).content)
        assert "No validation errors." in errors

        await pilot.press("w")
        await pilot.pause()

        assert isinstance(app.screen, DoneScreen)
        assert app.written is True
        assert out.exists()
        assert stat.S_IMODE(out.stat().st_mode) == 0o600
        assert (tmp_path / "out.report.md").exists()
        assert etree.fromstring(out.read_bytes()).tag == "opnsense"


async def test_saved_selection_reproduces_the_output(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """§17 M13: ``migrate --selection`` reproduces the TUI output byte-for-byte."""
    fixture = pfsense_dir / "minimal.xml"
    out = tmp_path / "out.xml"
    selection = tmp_path / "sel.json"
    app = _app(fixture, opnsense_base, out, map_nic=["em0=igc0", "em1=igc1"])

    async with app.run_test(size=(120, 40)) as pilot:
        await _goto_review(pilot, app)

        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SaveSelectionModal)
        path_input = app.screen.query_one("#save-selection-path", Input)
        path_input.value = str(selection)
        path_input.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert selection.exists()

        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, DoneScreen)
        assert out.exists()

    second = tmp_path / "out2.xml"
    code = cli.main(
        [
            "migrate",
            str(fixture),
            "--base",
            str(opnsense_base),
            "-o",
            str(second),
            "--selection",
            str(selection),
        ]
    )
    assert code == 0
    assert _without_revision_time(out) == _without_revision_time(second)


async def test_review_blocks_write_on_errors(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """``w`` is disabled and the error is shown when validation fails (§15)."""
    out = tmp_path / "out.xml"
    app = _app(
        pfsense_dir / "minimal.xml",
        opnsense_base,
        out,
        map_nic=["em0=igc0", "em1=igc1"],
    )
    async with app.run_test(size=(120, 40)) as pilot:
        review = await _goto_review(pilot, app)

        # No fixture reaches this through the UI (dependency closure keeps the
        # selection consistent), so inject the finding the worker would report.
        review.pfapp.findings = [Finding("error", "injected validation error")]
        review._show_results()
        await pilot.pause()

        assert "injected validation error" in str(
            review.query_one("#review-errors", Static).content
        )
        assert review.check_action("write", None) is False

        await pilot.press("w")
        await pilot.pause()
        assert not out.exists()
        assert isinstance(app.screen, ReviewScreen)
