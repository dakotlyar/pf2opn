"""Tests for the writer (design §10)."""

from __future__ import annotations

import stat
from pathlib import Path

from lxml import etree

from pf2opn.context import WriteContext
from pf2opn.model import Item, Mode, Placement, Status
from pf2opn.translators.base import Translator
from pf2opn.writer import apply, apply_base_policy, build_output, stamp_versions, write_output


def _el(xml: str) -> etree._Element:
    return etree.fromstring(xml)


class _MergeTranslator(Translator):
    """Minimal ``merge`` translator used to exercise the base policy (§10.2)."""

    section = "merge_test"
    title = "Merge test"
    tier = 1
    order = 1
    base_policy = "merge"
    container = "things"

    def discover(self, src, ifmap):  # pragma: no cover - not exercised here
        return []

    def identity(self, item: Item) -> str | None:
        if item.source is None:
            return None
        return item.source.findtext("name") or None


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
    apply(out, [Placement("foo/bar", Mode.APPEND, element=etree.Element("baz"))])
    assert out.find("foo/bar/baz") is not None


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


def test_merge_policy_replaces_matching_base_entry(minimal_plan) -> None:
    src, plan = minimal_plan
    out = _el(
        "<opnsense><things><thing><name>a</name></thing>"
        "<thing><name>b</name></thing></things></opnsense>"
    )
    item = Item(
        id="merge_test:a",
        section="merge_test",
        label="a",
        source=_el("<thing><name>a</name></thing>"),
        status=Status.OK,
    )

    apply_base_policy(out, _MergeTranslator(), [item], WriteContext(src, plan))

    assert [thing.findtext("name") for thing in out.findall("things/thing")] == ["b"]
    assert item.warnings == ["replaced base entry a"]


def test_merge_policy_keeps_unrelated_base_entries_and_adds_no_warning(minimal_plan) -> None:
    src, plan = minimal_plan
    out = _el("<opnsense><things><thing><name>b</name></thing></things></opnsense>")
    item = Item(
        id="merge_test:a",
        section="merge_test",
        label="a",
        source=_el("<thing><name>a</name></thing>"),
        status=Status.OK,
    )

    apply_base_policy(out, _MergeTranslator(), [item], WriteContext(src, plan))

    assert [thing.findtext("name") for thing in out.findall("things/thing")] == ["b"]
    assert item.warnings == []


def test_merge_policy_skips_items_without_identity(minimal_plan) -> None:
    src, plan = minimal_plan
    out = _el("<opnsense><things><thing><name>a</name></thing></things></opnsense>")
    item = Item(
        id="merge_test:a",
        section="merge_test",
        label="a",
        source=None,
        status=Status.OK,
    )

    apply_base_policy(out, _MergeTranslator(), [item], WriteContext(src, plan))

    assert out.findall("things/thing") != []
    assert item.warnings == []


def test_merge_policy_is_idempotent_on_warnings(minimal_plan) -> None:
    src, plan = minimal_plan
    out = _el("<opnsense><things><thing><name>a</name></thing></things></opnsense>")
    item = Item(
        id="merge_test:a",
        section="merge_test",
        label="a",
        source=_el("<thing><name>a</name></thing>"),
        status=Status.OK,
    )
    translator = _MergeTranslator()
    ctx = WriteContext(src, plan)

    apply_base_policy(out, translator, [item], ctx)
    apply_base_policy(out, translator, [item], ctx)

    assert item.warnings == ["replaced base entry a"]


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