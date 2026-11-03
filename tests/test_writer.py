"""Tests for the writer (design §10)."""

from __future__ import annotations

import stat
from pathlib import Path

from lxml import etree

from pf2opn.model import Mode, Placement
from pf2opn.writer import apply, build_output, stamp_versions, write_output


def _el(xml: str) -> etree._Element:
    return etree.fromstring(xml)


def test_apply_set_creates_and_updates() -> None:
    out = _el("<opnsense><system><hostname>old</hostname></system></opnsense>")
    apply(out, [Placement("system/hostname", Mode.SET, text="new")])
    assert out.findtext("system/hostname") == "new"
    apply(out, [Placement("system/timezone", Mode.SET, text="Etc/UTC")])
    assert out.findtext("system/timezone") == "Etc/UTC"


def test_apply_set_with_predicate_segment() -> None:
    out = _el(
        "<opnsense><system><user><name>root</name><password>old</password>"
        "</user></system></opnsense>"
    )
    apply(out, [Placement("system/user[name='root']/password", Mode.SET, text="new")])
    assert out.findtext("system/user/password") == "new"


def test_apply_append() -> None:
    out = _el("<opnsense><system/></opnsense>")
    apply(out, [Placement("system", Mode.APPEND, element=etree.Element("dnsserver"))])
    out.find("system/dnsserver").text = "1.1.1.1"
    assert out.findtext("system/dnsserver") == "1.1.1.1"


def test_apply_append_creates_missing_container() -> None:
    out = _el("<opnsense/>")
    apply(out, [Placement("dnsmasq/hosts", Mode.APPEND, element=etree.Element("hosts"))])
    assert out.find("dnsmasq/hosts/hosts") is not None


def test_apply_replace_keeps_position() -> None:
    out = _el("<opnsense><ntpd><server>a</server><ispool>old</ispool><server>b</server></ntpd></opnsense>")
    apply(out, [Placement("ntpd/ispool", Mode.REPLACE, element=etree.Element("ispool"))])
    out.find("ntpd/ispool").text = "new"
    assert out.findtext("ntpd/ispool") == "new"
    assert [child.tag for child in out.find("ntpd")] == ["server", "ispool", "server"]


def test_apply_replace_creates_when_missing() -> None:
    out = _el("<opnsense/>")
    element = etree.Element("ispool")
    element.text = "pool"
    apply(out, [Placement("ntpd/ispool", Mode.REPLACE, element=element)])
    assert out.findtext("ntpd/ispool") == "pool"


def test_apply_remove() -> None:
    out = _el(
        "<opnsense><system><dnsserver>a</dnsserver><dnsserver>b</dnsserver>"
        "<hostname>x</hostname></system></opnsense>"
    )
    apply(out, [Placement("system/dnsserver", Mode.REMOVE)])
    assert out.findall("system/dnsserver") == []
    assert out.findtext("system/hostname") == "x"


def test_stamp_versions_keeps_existing_and_fills_missing() -> None:
    out = _el('<opnsense><OPNsense><Firewall><Filter version="1.0.6"/></Firewall></OPNsense><dnsmasq/></opnsense>')
    stamp_versions(out)
    assert out.find("OPNsense/Firewall/Filter").get("version") == "1.0.6"
    assert out.find("dnsmasq").get("version") == "1.0.9"


def test_build_output_applies_system_and_revision(minimal_plan) -> None:
    src, plan = minimal_plan
    out = build_output(plan, src)
    assert out.tag == "opnsense"
    assert out.findtext("system/hostname") == "pfbox"
    assert out.findtext("system/domain") == "example.test"
    assert out.findtext("system/ssh/enabled") == "enabled"
    assert out.findtext("revision/username") == "pf2opn"
    assert out.findtext("revision/description") == "pf2opn: migrated from pfSense pfbox"
    # root_password is not default-selected: the base hash is untouched.
    assert out.find("system/user[name='root']/password").text.startswith("$2y$10$BASE")
    # the base's default LAN rules survive (filter is not a M2 translator).
    assert len(out.findall("OPNsense/Firewall/Filter/rules/rule")) == 2


def test_build_output_does_not_mutate_base(minimal_plan) -> None:
    src, plan = minimal_plan
    build_output(plan, src)
    assert src.base.findtext("system/hostname") == "OPNsense"


def test_write_output_is_mode_0600(tmp_path: Path, minimal_plan) -> None:
    src, plan = minimal_plan
    out = build_output(plan, src)
    path = tmp_path / "out.xml"
    write_output(out, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    reparsed = etree.fromstring(path.read_bytes())
    assert reparsed.tag == "opnsense"
    assert not (tmp_path / "out.xml.tmp").exists()