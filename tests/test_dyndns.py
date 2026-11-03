"""Tests for the dynamic DNS translator (design §12.1.18, M10)."""

from __future__ import annotations

import types

from lxml import etree

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import IfaceMap, Mode, Status

_EMPTY_IFMAP = IfaceMap(targets={}, nic_map={}, vlan_names={}, wg_names={})


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == section}


def _discover(xml: str):
    src = types.SimpleNamespace(pf=etree.fromstring(xml))
    return registry.get("dyndns").discover(src, _EMPTY_IFMAP)


def test_ddns_item_and_plugin(misc_plan) -> None:
    _src, plan = misc_plan
    item = _items(plan, "dyndns")["ddns:0"]
    assert item.status == Status.OK
    assert item.requires_plugin == "os-ddclient"
    assert item.data["hostname"] == "edge.example.test"


def test_host_dynamic_is_the_apex(edge_cases_plan) -> None:
    _src, plan = edge_cases_plan
    item = _items(plan, "dyndns")["ddns:0"]
    assert item.data["hostname"] == "edge.example.test"
    assert item.data["zone"] == "edge.example.test"


def test_custom_provider_uses_updateurl() -> None:
    item = _discover(
        "<pfsense><dyndnses><dyndns><service>custom</service><host>h</host>"
        "<domainname>d.test</domainname><updateurl>https://example.test/update</updateurl>"
        "</dyndns></dyndnses></pfsense>"
    )[0]
    assert item.status == Status.PARTIAL
    assert item.data["service"] == "custom"
    assert item.data["server"] == "https://example.test/update"


def test_cloudflare_v6_maps_to_cloudflare() -> None:
    item = _discover(
        "<pfsense><dyndnses><dyndns><service>cloudflare-v6</service><host>@</host>"
        "<domainname>d.test</domainname></dyndns></dyndnses></pfsense>"
    )[0]
    assert item.data["service"] == "cloudflare"
    assert item.data["hostname"] == "d.test"
    assert item.warnings


def test_unknown_provider_is_unsupported() -> None:
    item = _discover(
        "<pfsense><dyndnses><dyndns><service>nope</service><host>h</host>"
        "<domainname>d.test</domainname></dyndns></dyndnses></pfsense>"
    )[0]
    assert item.status == Status.UNSUPPORTED
    assert "not mapped" in item.reason


def test_ddns_render(misc_plan) -> None:
    src, plan = misc_plan
    placement = registry.get("dyndns").render(plan.by_id["ddns:0"], WriteContext(src, plan))[0]
    assert placement.mode == Mode.APPEND
    root = placement.element
    assert root.findtext("service") == "cloudflare"
    assert root.findtext("hostnames") == "edge.example.test"
    assert root.findtext("zone") == "example.test"
    assert root.findtext("checkip") == "if"
    assert root.findtext("ttl") == "300"
    assert root.findtext("force_ssl") == "1"
    assert root.findtext("wildcard") == "0"


def test_ddns_finalize(misc_plan) -> None:
    src, plan = misc_plan
    ctx = WriteContext(src, plan)
    selected = [item for item in plan.items if item.section == "dyndns" and item.id in plan.selected]
    placements = registry.get("dyndns").finalize(selected, ctx)
    assert [(p.path, p.text) for p in placements] == [
        ("OPNsense/DynDNS/general/enabled", "1"),
        ("OPNsense/DynDNS/general/backend", "opnsense"),
    ]


def test_dyndns_golden(misc_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = misc_plan
    placements = section_placements(src, plan, "dyndns")
    golden_checker("dyndns/misc.xml", wrap_placements(placements))
