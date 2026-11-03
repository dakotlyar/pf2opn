"""Tests for the secret-redaction policy (design §11, M0 and §16.4, M14)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from lxml import etree

from pf2opn import cli, secrets

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pfsense"
_FIXTURE_NAMES = sorted(path.name for path in _FIXTURES.glob("*.xml"))
_NIC = {"em0": "igc0", "em1": "igc1", "em2": "igc2", "em3": "igc3"}


def _secret_values(path: Path) -> set[str]:
    """Every SECRET_TAGS text longer than 8 chars in *path* (the §16.4 set)."""
    root = etree.fromstring(path.read_bytes())
    values: set[str] = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el.tag).localname in secrets.SECRET_TAGS and el.text:
            if len(el.text) > 8:
                values.add(el.text)
    return values


def _el(xml: str) -> etree._Element:
    return etree.fromstring(xml)


def test_is_secret_recognises_known_tags() -> None:
    assert secrets.is_secret("bcrypt-hash")
    assert secrets.is_secret("prv")
    assert secrets.is_secret("presharedkey")
    assert secrets.is_secret("crt")
    assert not secrets.is_secret("descr")
    assert not secrets.is_secret("hostname")


def test_redact_element_replaces_secret_text() -> None:
    el = _el(
        "<user><name>admin</name>"
        "<bcrypt-hash>$2y$10$abcdefghijklmno</bcrypt-hash>"
        "<descr>the admin</descr></user>"
    )
    red = secrets.redact_element(el)
    assert red.findtext("name") == "admin"
    assert red.findtext("descr") == "the admin"
    assert red.findtext("bcrypt-hash") == "«redacted 22 chars»"


def test_redact_element_does_not_mutate_original() -> None:
    el = _el("<prv>-----BEGIN PRIVATE KEY-----secret</prv>")
    secrets.redact_element(el)
    assert el.text == "-----BEGIN PRIVATE KEY-----secret"


def test_redact_element_handles_nested_and_empty() -> None:
    el = _el("<a><prv>supersecretvalue</prv><empty></empty><b><psk>x1234567890</psk></b></a>")
    red = secrets.redact_element(el)
    assert red.findtext("prv") == "«redacted 16 chars»"
    assert red.findtext("b/psk") == "«redacted 11 chars»"
    assert red.find("empty").text is None


def test_redacted_xml_is_serializable_and_clean() -> None:
    el = _el("<a><prv>my-secret-key-value</prv><name>ok</name></a>")
    text = secrets.redacted_xml(el)
    assert "my-secret-key-value" not in text
    assert "<name>ok</name>" in text


def test_redacting_filter_masks_registered_values() -> None:
    secrets.register_secret("super-secret-value")
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "log line: %s", ("super-secret-value",), None
    )
    assert secrets.RedactingFilter().filter(record) is True
    assert record.getMessage() == f"log line: {secrets._REDACTED}"


def test_cli_main_installs_the_redacting_filter(minimal_path, opnsense_base, capsys) -> None:
    """§11 rule 5: `cli.main()` attaches the filter before parsing anything."""
    from pf2opn import cli

    root = logging.getLogger()
    saved = list(root.filters)
    root.filters = [f for f in root.filters if not isinstance(f, secrets.RedactingFilter)]
    try:
        assert cli.main(["inspect", str(minimal_path), "--base", str(opnsense_base)]) == 0
        assert any(isinstance(f, secrets.RedactingFilter) for f in root.filters)
    finally:
        root.filters = saved
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# §16.4 whole-fixture secret-leak test
# --------------------------------------------------------------------------- #


def test_fixtures_do_contain_secrets() -> None:
    """The leak test below is only meaningful if the fixtures hold secrets."""
    assert any(_secret_values(_FIXTURES / name) for name in _FIXTURE_NAMES)


@pytest.mark.parametrize("name", _FIXTURE_NAMES)
def test_no_secret_leaks_from_a_fixture(
    name: str, opnsense_base: Path, tmp_path: Path, capsys
) -> None:
    """§16.4: no SECRET_TAGS value (>8 chars) in inspect, report, or TUI panes."""
    from pf2opn.context import SourceContext
    from pf2opn.ifmap import default_ifmap
    from pf2opn.plan import build_plan
    from pf2opn.report import render_report
    from pf2opn.tui.widgets import _preview_text
    from pf2opn.validate import validate
    from pf2opn.writer import build_output

    fixture = _FIXTURES / name
    hidden = _secret_values(fixture)

    # inspect (text and JSON) — the CLI never prints source XML.
    assert cli.main(["inspect", str(fixture), "--base", str(opnsense_base)]) == 0
    inspect_text = capsys.readouterr().out
    assert cli.main(["inspect", str(fixture), "--base", str(opnsense_base), "--json"]) == 0
    inspect_json = capsys.readouterr().out

    src = SourceContext.load(fixture, opnsense_base)
    plan = build_plan(src, default_ifmap(src, _NIC))

    # the TUI Source/Preview panes: the same helpers `DetailPane` calls (§11 rule 2).
    tui_parts: list[str] = []
    for item in plan.items:
        if item.source is not None:
            tui_parts.append(secrets.redacted_xml(item.source))
        tui_parts.append(_preview_text(item, plan, src))
        tui_parts.append(item.label)
        tui_parts.append(item.reason)
        tui_parts.extend(item.warnings)
        tui_parts.extend(item.followups)
    tui_text = "\n".join(tui_parts)

    out = build_output(plan, src)
    report_text = render_report(plan, validate(out, plan), src, tmp_path / "out.xml")

    surfaces = {
        "inspect": inspect_text,
        "inspect --json": inspect_json,
        "tui": tui_text,
        "report": report_text,
    }
    for surface, text in surfaces.items():
        for value in hidden:
            assert value not in text, f"{name}: secret leaked into {surface}"
