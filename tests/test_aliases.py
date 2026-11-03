"""Tests for the aliases translator (design §12.1.6, M4)."""

from __future__ import annotations

from lxml import etree

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status
from pf2opn.writer import build_output


def _items(plan):
    return {item.id: item for item in plan.items if item.section == "aliases"}


def test_alias_statuses(aliases_plan) -> None:
    _src, plan = aliases_plan
    items = _items(plan)

    assert items["alias:RFC1918"].status == Status.OK
    assert items["alias:SERVER1"].status == Status.OK
    assert items["alias:WEBPORTS"].status == Status.OK
    assert items["alias:BLOCKLIST_URLS"].status == Status.OK
    assert items["alias:MYGROUP"].status == Status.PARTIAL
    assert items["alias:MYGROUP"].deps == ["alias:SERVER1"]
    assert items["alias:MYGROUP"].warnings == ["per-entry descriptions dropped"]


def test_alias_unsupported_cases(aliases_plan) -> None:
    _src, plan = aliases_plan
    items = _items(plan)

    assert items["alias:pfB_ALIAS"].status == Status.UNSUPPORTED
    assert "pfBlockerNG" in items["alias:pfB_ALIAS"].reason
    assert items["alias:any"].status == Status.UNSUPPORTED
    assert "reserved" in items["alias:any"].reason
    assert items["alias:1abc"].status == Status.UNSUPPORTED
    assert "invalid" in items["alias:1abc"].reason
    assert items["alias:URL_PORTS"].status == Status.UNSUPPORTED
    assert "URL port lists" in items["alias:URL_PORTS"].reason


def test_alias_render_content_and_updatefreq(aliases_plan) -> None:
    src, plan = aliases_plan
    translator = registry.get("aliases")
    ctx = WriteContext(src, plan)

    network = translator.render(plan.by_id["alias:RFC1918"], ctx)[0]
    assert network.mode == Mode.APPEND
    assert network.path == "OPNsense/Firewall/Alias/aliases"
    assert network.element.findtext("enabled") == "1"
    assert network.element.findtext("type") == "network"
    assert network.element.findtext("content") == "10.0.0.0/8\n172.16.0.0/12\n192.168.0.0/16"
    assert network.element.findtext("counters") == "0"
    assert network.element.find("proto") is not None

    urltable = translator.render(plan.by_id["alias:BLOCKLIST_URLS"], ctx)[0]
    assert urltable.element.findtext("type") == "urltable"
    assert urltable.element.findtext("updatefreq") == "7"
    assert urltable.element.findtext("content") == "https://example.test/list.txt"


def test_alias_merge_replaces_matching_base_entry(aliases_plan) -> None:
    src, plan = aliases_plan
    container = src.base.find("OPNsense/Firewall/Alias/aliases")
    existing = etree.SubElement(container, "alias")
    existing.set("uuid", "base-alias-uuid")
    etree.SubElement(existing, "name").text = "RFC1918"
    etree.SubElement(existing, "type").text = "network"

    out = build_output(plan, src)

    names = [
        alias.findtext("name")
        for alias in out.findall("OPNsense/Firewall/Alias/aliases/alias")
    ]
    assert names.count("RFC1918") == 1
    assert "replaced base entry RFC1918" in plan.by_id["alias:RFC1918"].warnings


def test_aliases_golden(aliases_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = aliases_plan
    placements = section_placements(src, plan, "aliases")
    golden_checker("aliases/aliases.xml", wrap_placements(placements))
