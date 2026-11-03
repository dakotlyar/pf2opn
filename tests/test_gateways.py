"""Tests for the gateways translator (design §12.1.4, M4)."""

from __future__ import annotations

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status


def _items(plan):
    return {item.id: item for item in plan.items if item.section == "gateways"}


def test_gateway_statuses(misc_plan) -> None:
    _src, plan = misc_plan
    items = _items(plan)

    assert items["gw:WAN_DHCP"].status == Status.OK
    assert items["gw:WAN_DHCP"].deps == ["iface:wan"]
    assert items["gw:OPT1_GW"].status == Status.PARTIAL
    assert any("monitoring action" in warning for warning in items["gw:OPT1_GW"].warnings)
    assert items["gw:QSOL_WGGW"].status == Status.UNSUPPORTED
    assert items["gw:QSOL_WGGW"].reason == "interface opt11 not migrated"
    assert items["gw:bad name"].status == Status.UNSUPPORTED
    assert "invalid" in items["gw:bad name"].reason


def test_gateway_render_dynamic_and_default(misc_plan) -> None:
    src, plan = misc_plan
    translator = registry.get("gateways")
    ctx = WriteContext(src, plan)

    placement = translator.render(plan.by_id["gw:WAN_DHCP"], ctx)[0]
    root = placement.element
    assert placement.mode == Mode.APPEND
    assert placement.path == "OPNsense/Gateways"
    assert root.findtext("name") == "WAN_DHCP"
    assert root.findtext("interface") == "wan"
    assert root.findtext("gateway") in (None, "")
    assert root.findtext("ipprotocol") == "inet"
    assert root.findtext("weight") == "1"
    assert root.findtext("defaultgw") == "1"

    opt1 = translator.render(plan.by_id["gw:OPT1_GW"], ctx)[0].element
    assert opt1.findtext("gateway") == "10.0.10.254"
    assert opt1.findtext("weight") == "3"
    assert opt1.findtext("latencylow") == "100"
    assert opt1.findtext("defaultgw") == "0"


def test_gateways_golden(misc_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = misc_plan
    placements = section_placements(src, plan, "gateways")
    golden_checker("gateways/misc.xml", wrap_placements(placements))
