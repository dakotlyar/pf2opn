"""Tests for the secret-redaction policy (design §11, M0)."""

from __future__ import annotations

import logging

from lxml import etree

from pf2opn import secrets


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
