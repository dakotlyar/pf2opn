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
from pf2opn.tui.widgets import ConfigPathField, scan_xml_roots


def _args(
    *,
    source: str | None = None,
    base: str | None = None,
    output: str | None = None,
    report: str | None = None,
    selection: str | None = None,
    force: bool = False,
    search_path: list[str] | None = None,
) -> Namespace:
    return Namespace(
        source=source,
        base=base,
        output=output,
        report=report,
        selection=selection,
        map_nic=[],
        force=force,
        search_path=search_path or [],
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


# --------------------------------------------------------------------------- #
# Load-screen config search (§15, §16.5)
# --------------------------------------------------------------------------- #


async def _scan_settled(pilot, app: Pf2OpnApp) -> None:
    """Wait for the one-shot scan worker and its UI callback to land."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _option_ids(field: ConfigPathField) -> list[str]:
    lst = field.option_list
    return [lst.get_option_at_index(i).id for i in range(lst.option_count)]


async def test_load_search_filters_and_enter_picks(tmp_path: Path) -> None:
    """Typing filters the candidate list; Enter on an entry fills the input (§15)."""
    search_dir = tmp_path / "search"
    search_dir.mkdir()
    alpha = search_dir / "pf2opn_alpha.xml"
    beta = search_dir / "pf2opn_beta.xml"
    alpha.write_text("<pfsense/>", encoding="utf-8")
    beta.write_text("<pfsense/>", encoding="utf-8")

    app = Pf2OpnApp(
        _args(search_path=[str(search_dir)], output=str(tmp_path / "out.xml"))
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await _scan_settled(pilot, app)
        field = app.screen.query_one("#load-source-field", ConfigPathField)
        assert str(alpha) in _option_ids(field)
        assert str(beta) in _option_ids(field)

        field.input.value = "alpha"
        await pilot.pause(0.3)  # past the ~0.15 s filter debounce
        assert _option_ids(field) == [str(alpha)]

        field.option_list.focus()
        await pilot.pause()
        await pilot.press("down")  # highlight the single entry
        await pilot.press("enter")
        await pilot.pause()
        assert field.value == str(alpha)
        assert isinstance(app.screen, LoadScreen)  # pick does not continue


async def test_load_no_matches_row_renders(tmp_path: Path) -> None:
    """An empty result set shows the non-selectable "no matches" row (§15)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    app = Pf2OpnApp(_args(search_path=[str(empty)]))
    async with app.run_test(size=(120, 40)) as pilot:
        await _scan_settled(pilot, app)
        field = app.screen.query_one("#load-source-field", ConfigPathField)
        field.input.value = "zzz-no-such-config-zzz"
        await pilot.pause(0.3)

        lst = field.option_list
        assert lst.option_count == 1
        option = lst.get_option_at_index(0)
        assert option.disabled
        assert "No matching" in str(option.prompt)


async def test_load_typed_direct_path_validates_without_scan_match(
    tmp_path: Path,
) -> None:
    """A typed full path is used verbatim, whether or not it was scanned (§15)."""
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    direct = deep / "direct.xml"
    direct.write_text(
        "<pfsense><version>24.0</version><interfaces>"
        "<wan><if>em0</if></wan></interfaces></pfsense>",
        encoding="utf-8",
    )
    # The output lives in tmp_path, so its parent is scanned depth 1 — but the
    # config is two levels down and is therefore never a candidate.
    app = Pf2OpnApp(_args(output=str(tmp_path / "out.xml")))
    async with app.run_test(size=(120, 40)) as pilot:
        await _scan_settled(pilot, app)
        field = app.screen.query_one("#load-source-field", ConfigPathField)
        assert str(direct) not in _option_ids(field)

        field.input.value = str(direct)
        await _settle(pilot, app)

        assert app.src is not None
        assert str(app.src.source_path) == str(direct)
        assert str(app.screen.query_one("#load-source-error", Static).content) == ""


async def test_empty_base_uses_default_and_continues(
    pfsense_dir: Path, tmp_path: Path
) -> None:
    """An empty base field is valid: it means the built-in base (§15, §5.1)."""
    fixture = pfsense_dir / "minimal.xml"
    app = Pf2OpnApp(_args(source=str(fixture), output=str(tmp_path / "out.xml")))
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(pilot, app)

        assert app.src is not None
        assert app.src.base_is_default is True
        assert str(app.screen.query_one("#load-base-error", Static).content) == ""

        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, IfaceMapScreen)


async def test_r_types_into_the_input_not_rescan(pfsense_dir: Path) -> None:
    """`r` rescans, but the letter must still be typeable in a text input (§15)."""
    app = Pf2OpnApp(_args())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        field = app.screen.query_one("#load-source-field", ConfigPathField)
        field.input.focus()
        await pilot.press("r")
        await pilot.pause()
        assert field.input.value == "r"


def test_scan_xml_roots_is_depth_one_and_skips_hidden(tmp_path: Path) -> None:
    """§15: depth 1, hidden/symlinked dirs skipped, only ``*.xml`` files."""
    (tmp_path / "keep.xml").write_text("<x/>", encoding="utf-8")
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.xml").write_text("<x/>", encoding="utf-8")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.xml").write_text("<x/>", encoding="utf-8")
    deeper = sub / "deeper"
    deeper.mkdir()
    (deeper / "too_deep.xml").write_text("<x/>", encoding="utf-8")
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(sub, target_is_directory=True)
    except OSError:  # pragma: no cover - platform dependent
        pass

    found = scan_xml_roots([tmp_path, tmp_path])
    assert {path.name for path in found} == {"keep.xml", "nested.xml"}
    # the symlinked directory is skipped, so nothing is found through it
    assert all(path.parent.name != "linked" for path in found)
