"""Pilot tests for the Load screen collecting the three paths (design §15, §17 M11)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from textual.widgets import Input, Static

from pf2opn import cli
from pf2opn.context import SourceContext
from pf2opn.tui.app import Pf2OpnApp
from pf2opn.tui.screens.ifmap import IfaceMapScreen
from pf2opn.tui.screens.load import LoadScreen


def _args(
    *,
    source: str | None = None,
    base: str | None = None,
    output: str | None = None,
    report: str | None = None,
    selection: str | None = None,
    force: bool = False,
) -> Namespace:
    return Namespace(
        source=source,
        base=base,
        output=output,
        report=report,
        selection=selection,
        map_nic=[],
        force=force,
    )


async def _settle(pilot, app: Pf2OpnApp) -> None:
    """Wait past the parse debounce and let the load worker finish."""
    await pilot.pause(0.6)
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_tui_parser_paths_are_optional() -> None:
    """``tui`` takes no paths; ``migrate``/``inspect`` still require them."""
    parser = cli.build_parser()
    args = parser.parse_args(["tui"])
    assert args.source is None
    assert args.base is None
    assert args.output is None

    for command in ("inspect", "migrate"):
        try:
            parser.parse_args([command])
        except SystemExit as exc:  # argparse exits 2 on a missing required arg
            assert exc.code == 2
        else:  # pragma: no cover - the required args must fail
            raise AssertionError(f"{command} accepted no paths")


def test_tui_flag_source_failure_exits_2_without_tui(tmp_path: Path, opnsense_base: Path, capsys) -> None:
    """A flag-passed source that is not a ``<pfsense>`` config exits 2 (§9)."""
    bad = tmp_path / "bad.xml"
    bad.write_text("<opnsense/>", encoding="utf-8")
    code = cli.main(["tui", str(bad), "--base", str(opnsense_base)])
    assert code == 2
    assert "expected <pfsense>" in capsys.readouterr().err


async def test_no_arg_app_reaches_load_and_enter_is_gated(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """With no arguments the app starts on Load; ``enter`` is gated until valid."""
    app = Pf2OpnApp(_args())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, LoadScreen)
        assert app.src is None

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, LoadScreen)
        assert app.src is None

        app.screen.query_one("#load-source", Input).value = str(pfsense_dir / "minimal.xml")
        app.screen.query_one("#load-base", Input).value = str(opnsense_base)
        app.screen.query_one("#load-output", Input).value = str(tmp_path / "out.xml")
        await _settle(pilot, app)

        assert app.src is not None
        assert app.output_path == tmp_path / "out.xml"

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, IfaceMapScreen)


async def test_bad_source_root_shows_inline_error(
    opnsense_base: Path, tmp_path: Path
) -> None:
    """A source that parses but has the wrong root shows the error and blocks."""
    bad = tmp_path / "not-pfsense.xml"
    bad.write_text("<opnsense/>", encoding="utf-8")
    app = Pf2OpnApp(
        _args(
            source=str(bad),
            base=str(opnsense_base),
            output=str(tmp_path / "out.xml"),
        )
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(pilot, app)

        assert app.src is None
        error = str(app.screen.query_one("#load-source-error", Static).content)
        assert "expected <pfsense>" in error

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, LoadScreen)


async def test_existing_output_requires_force(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """An existing output is an inline error until ``--force`` is set (§15)."""
    fixture = pfsense_dir / "minimal.xml"
    out = tmp_path / "out.xml"
    out.write_text("<existing/>", encoding="utf-8")

    app = Pf2OpnApp(
        _args(source=str(fixture), base=str(opnsense_base), output=str(out)),
        SourceContext.load(fixture, opnsense_base),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        error = str(app.screen.query_one("#load-output-error", Static).content)
        assert "--force" in error

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, LoadScreen)

    forced = Pf2OpnApp(
        _args(source=str(fixture), base=str(opnsense_base), output=str(out), force=True),
        SourceContext.load(fixture, opnsense_base),
    )
    async with forced.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert str(forced.screen.query_one("#load-output-error", Static).content) == ""
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(forced.screen, IfaceMapScreen)


async def test_flag_paths_prefill_and_edit_supersedes(
    pfsense_dir: Path, opnsense_base: Path, tmp_path: Path
) -> None:
    """Flags pre-fill the fields; an edit is what the run uses (§9, §15)."""
    fixture = pfsense_dir / "minimal.xml"
    old_out = tmp_path / "out.xml"
    new_out = tmp_path / "renamed.xml"
    app = Pf2OpnApp(
        _args(source=str(fixture), base=str(opnsense_base), output=str(old_out)),
        SourceContext.load(fixture, opnsense_base),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#load-source", Input).value == str(fixture)
        assert app.screen.query_one("#load-base", Input).value == str(opnsense_base)
        assert app.screen.query_one("#load-output", Input).value == str(old_out)
        assert app.report_path == old_out.with_name("out.report.md")

        # Editing the output supersedes the flag and moves the report default.
        app.screen.query_one("#load-output", Input).value = str(new_out)
        await pilot.pause()

        assert app.output_path == new_out
        assert app.report_path == new_out.with_name("renamed.report.md")
        assert str(app.src.source_path) == str(fixture)

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, IfaceMapScreen)
