"""Tests for the safe XML reader (design §5, §11, M0)."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from pf2opn import secrets, xmlio

_XXE_DOC = """<?xml version="1.0"?>
<!DOCTYPE pfsense [
  <!ENTITY xxe SYSTEM "file://{secret_path}">
]>
<pfsense>
  <system>
    <hostname>&xxe;</hostname>
    <password>&xxe;</password>
  </system>
</pfsense>
"""


def test_parse_readonly_returns_root(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.xml"
    cfg.write_text("<pfsense><version>24.0</version></pfsense>", encoding="utf-8")
    tree = xmlio.parse_readonly(cfg)
    assert tree.getroot().tag == "pfsense"
    assert tree.getroot().findtext("version") == "24.0"


def test_xxe_payload_is_not_expanded(tmp_path: Path) -> None:
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("TOP_SECRET_VALUE_1234567890", encoding="utf-8")
    cfg = tmp_path / "cfg.xml"
    cfg.write_text(_XXE_DOC.format(secret_path=secret_file), encoding="utf-8")

    tree = xmlio.parse_readonly(cfg)
    serialized = xmlio.to_string(tree)

    # The external file is never read: its content is not in the parsed tree.
    assert "TOP_SECRET_VALUE_1234567890" not in serialized
    assert tree.getroot().find("system/hostname").text is None
    # The entity reference survives as an unresolved entity, not as file content.
    entity_nodes = [
        el for el in tree.getroot().iter() if not isinstance(el.tag, str)
    ]
    assert entity_nodes, "expected an unresolved entity node"
    assert all(el.text == "&xxe;" for el in entity_nodes)


def test_to_string_round_trips(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.xml"
    cfg.write_text("<pfsense><system><hostname>box</hostname></system></pfsense>", encoding="utf-8")
    tree = xmlio.parse_readonly(cfg)
    text = xmlio.to_string(tree.getroot())
    reparsed = etree.fromstring(text)
    assert reparsed.tag == "pfsense"
    assert reparsed.findtext("system/hostname") == "box"


def test_parse_registers_secret_values(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.xml"
    cfg.write_text(
        "<pfsense><system><user><bcrypt-hash>$2y$10$abcdefghijklmno</bcrypt-hash>"
        "</user></system></pfsense>",
        encoding="utf-8",
    )
    xmlio.parse_readonly(cfg)
    assert "$2y$10$abcdefghijklmno" in secrets.known_secret_values()


def test_parse_bytes_registers_secret_values_and_checks_root() -> None:
    root = xmlio.parse_bytes(
        b"<opnsense><system><user><password>$2y$10$parsebytesuniquevalue12</password>"
        b"</user></system></opnsense>",
        root="opnsense",
    )
    assert root.tag == "opnsense"
    assert "$2y$10$parsebytesuniquevalue12" in secrets.known_secret_values()
