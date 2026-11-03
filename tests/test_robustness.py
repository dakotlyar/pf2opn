"""Robustness / fuzz-ish tests (design §7.2, §16.3, §17 M14).

``discover`` must never raise on odd input, and the CLI must turn parse errors
into exit code 2 rather than a traceback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pf2opn import cli

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pfsense"
_FIXTURE_NAMES = sorted(path.name for path in _FIXTURES.glob("*.xml"))

_MAP = [
    "--map-nic",
    "em0=igc0",
    "--map-nic",
    "em1=igc1",
    "--map-nic",
    "em2=igc2",
    "--map-nic",
    "em3=igc3",
]

#: Deliberately malformed / edge snippets: (name, xml, expected inspect code).
_SNIPPETS = [
    ("empty", "<pfsense/>", 0),
    (
        "no-interfaces",
        "<pfsense><version>24.0</version><system><hostname>h</hostname></system></pfsense>",
        0,
    ),
    (
        "blank-device",
        "<pfsense><version>24.0</version><interfaces>"
        "<wan><if></if></wan></interfaces></pfsense>",
        0,
    ),
    (
        "duplicate-device",
        "<pfsense><version>24.0</version><interfaces>"
        "<wan><if>em0</if></wan><lan><if>em0</if></lan></interfaces></pfsense>",
        0,
    ),
    (
        "alias-missing-name",
        "<pfsense><aliases><alias><type>host</type>"
        "<address>1.2.3.4</address></alias></aliases></pfsense>",
        0,
    ),
    (
        "vlan-without-parent",
        "<pfsense><version>24.0</version><vlans><vlan><if>em0</if>"
        "<tag>10</tag><vlanif>em0.10</vlanif></vlan></vlans></pfsense>",
        0,
    ),
    ("unparseable", "<pfsense><unclosed>", 2),
    ("wrong-root", "<notpfsense/>", 2),
]


@pytest.mark.parametrize("name", _FIXTURE_NAMES)
def test_fixture_inspect_and_migrate_do_not_raise(
    name: str, opnsense_base: Path, tmp_path: Path
) -> None:
    fixture = _FIXTURES / name
    assert cli.main(["inspect", str(fixture), "--base", str(opnsense_base)]) == 0

    out = tmp_path / f"{name}.out.xml"
    code = cli.main(
        [
            "migrate",
            str(fixture),
            "--base",
            str(opnsense_base),
            "-o",
            str(out),
            "--select-all-ok",
            *_MAP,
        ]
    )
    assert code in (0, 1)  # written, or blocked by validation errors — never a crash


@pytest.mark.parametrize("name", _FIXTURE_NAMES)
def test_migrate_refuses_to_overwrite_input_or_existing_output(
    name: str, opnsense_base: Path, tmp_path: Path
) -> None:
    """§16.3: OUT == SOURCE, or an existing OUT without --force, is exit 2."""
    fixture = _FIXTURES / name
    before = fixture.read_bytes()

    onto_input = cli.main(
        [
            "migrate",
            str(fixture),
            "--base",
            str(opnsense_base),
            "-o",
            str(fixture),
            "--select-all-ok",
            "--force",
            *_MAP,
        ]
    )
    assert onto_input == 2
    assert fixture.read_bytes() == before  # the input is never modified

    out = tmp_path / f"{name}.exists.xml"
    out.write_text("<existing/>", encoding="utf-8")
    existing = cli.main(
        [
            "migrate",
            str(fixture),
            "--base",
            str(opnsense_base),
            "-o",
            str(out),
            "--select-all-ok",
            *_MAP,
        ]
    )
    assert existing == 2
    assert out.read_text(encoding="utf-8") == "<existing/>"


@pytest.mark.parametrize("name,xml,inspect_code", _SNIPPETS)
def test_edge_snippets_do_not_raise(
    name: str, xml: str, inspect_code: int, opnsense_base: Path, tmp_path: Path
) -> None:
    snippet = tmp_path / f"{name}.xml"
    snippet.write_text(xml, encoding="utf-8")

    assert cli.main(["inspect", str(snippet), "--base", str(opnsense_base)]) == inspect_code

    out = tmp_path / f"{name}.out.xml"
    code = cli.main(
        [
            "migrate",
            str(snippet),
            "--base",
            str(opnsense_base),
            "-o",
            str(out),
            "--select-all-ok",
            *_MAP,
        ]
    )
    if inspect_code == 2:
        assert code == 2
    else:
        assert code in (0, 1, 2)  # parseable: may write, validate-fail, or need mapping
