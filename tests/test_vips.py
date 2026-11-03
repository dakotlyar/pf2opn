"""Tests for the virtual IP translator (design §12.1.12, M4)."""

from __future__ import annotations

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status


def _items(plan):
    return {item.id: item for item in plan.items if item.section == "vips"}


def test_vip_statuses(misc_plan) -> None:
    _src, plan = misc_plan
    items = _items(plan)

    dnsbl = items["vip:lo0:192.168.2.1"]
    assert dnsbl.default_selected is False
    assert any("DNSBL" in warning for warning in dnsbl.warnings)

    carp = items["vip:lan:192.168.1.2"]
    assert carp.status == Status.PARTIAL
    assert "CARP" in carp.warnings[0]
    assert carp.deps == ["iface:lan"]


def test_vip_render_ipalias(misc_plan) -> None:
    src, plan = misc_plan
    translator = registry.get("vips")
    ctx = WriteContext(src, plan)

    placement = translator.render(plan.by_id["vip:lo0:192.168.2.1"], ctx)[0]
    root = placement.element
    assert placement.mode == Mode.APPEND
    assert placement.path == "virtualip"
    assert root.findtext("mode") == "ipalias"
    assert root.findtext("interface") == "lo0"
    assert root.findtext("subnet") == "192.168.2.1"
    assert root.findtext("subnet_bits") == "24"
    assert root.findtext("nobind") == "0"
    assert root.find("vhid") is None


def test_vip_render_carp(misc_plan) -> None:
    src, plan = misc_plan
    translator = registry.get("vips")
    ctx = WriteContext(src, plan)

    root = translator.render(plan.by_id["vip:lan:192.168.1.2"], ctx)[0].element
    assert root.findtext("mode") == "carp"
    assert root.findtext("interface") == "lan"
    assert root.findtext("vhid") == "10"
    assert root.findtext("advbase") == "1"
    assert root.findtext("advskew") == "0"
    assert root.findtext("password") == "syntheticsecret"
    assert root.findtext("nosync") == "0"


def test_vips_golden(misc_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = misc_plan
    placements = section_placements(src, plan, "vips")
    golden_checker("vips/misc.xml", wrap_placements(placements))
