"""Tests for the static routes translator (design §12.1.5, M4)."""

from __future__ import annotations

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status


def _items(plan):
    return {item.id: item for item in plan.items if item.section == "routes"}


def test_route_items(misc_plan) -> None:
    _src, plan = misc_plan
    items = _items(plan)
    assert set(items) == {"route:10.9.0.0/16", "route:10.8.0.0/16"}
    assert items["route:10.9.0.0/16"].status == Status.OK
    assert items["route:10.9.0.0/16"].deps == ["gw:OPT1_GW"]


def test_route_render_enabled_and_disabled(misc_plan) -> None:
    src, plan = misc_plan
    translator = registry.get("routes")
    ctx = WriteContext(src, plan)

    enabled = translator.render(plan.by_id["route:10.9.0.0/16"], ctx)[0]
    assert enabled.mode == Mode.APPEND
    assert enabled.path == "staticroutes"
    assert enabled.element.findtext("network") == "10.9.0.0/16"
    assert enabled.element.findtext("gateway") == "OPT1_GW"
    assert enabled.element.findtext("enabled") == "1"

    disabled = translator.render(plan.by_id["route:10.8.0.0/16"], ctx)[0]
    assert disabled.element.findtext("enabled") == "0"


def test_routes_golden(misc_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = misc_plan
    placements = section_placements(src, plan, "routes")
    golden_checker("routes/misc.xml", wrap_placements(placements))
