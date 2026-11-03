"""Tests for the NAT translators (design §12.1.8-10, M6)."""

from __future__ import annotations

from lxml import etree

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.deps import closure
from pf2opn.ifmap import default_ifmap
from pf2opn.model import Mode, Status
from pf2opn.plan import build_plan
from pf2opn.validate import validate
from pf2opn.writer import build_output

_PORT_FORWARD = "pf:nat_60000000000000.00000001"


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == section}


# --------------------------------------------------------------------------- #
# Port forwards (§12.1.8)
# --------------------------------------------------------------------------- #


def test_port_forward_statuses_and_deps(nat_plan) -> None:
    _src, plan = nat_plan
    items = _items(plan, "nat_pf")

    assert items[_PORT_FORWARD].status == Status.OK
    assert items[_PORT_FORWARD].deps == ["iface:wan", "rule:2000"]
    assert items["pf:1"].status == Status.OK
    assert items["pf:1"].deps == ["alias:WEBSERVER", "iface:lan"]
    assert items["pf:2"].status == Status.PARTIAL
    assert items["pf:2"].warnings == [
        "NAT+proxy/pfsync reflection has no equivalent, using pure NAT"
    ]


def test_port_forward_pulls_in_associated_filter_rule(nat_plan) -> None:
    _src, plan = nat_plan
    assert "rule:2000" in closure({_PORT_FORWARD}, plan)
    assert plan.by_id["rule:2000"].id == "rule:2000"


def test_port_forward_render(nat_plan) -> None:
    src, plan = nat_plan
    translator = registry.get("nat_pf")
    ctx = WriteContext(src, plan)

    placement = translator.render(plan.by_id[_PORT_FORWARD], ctx)[0]
    assert placement.mode == Mode.APPEND
    assert placement.path == "nat"
    rule = placement.element
    assert rule.findtext("sequence") == "10"
    assert rule.findtext("interface") == "wan"
    assert rule.findtext("ipprotocol") == "inet"
    assert rule.findtext("protocol") == "tcp"
    assert rule.findtext("destination/network") == "wanip"
    assert rule.findtext("destination/port") == "8443"
    assert rule.findtext("target") == "192.168.1.50"
    assert rule.findtext("local-port") == "443"
    # D8: no associated-rule-id is written
    assert rule.find("associated-rule-id") is None

    reflected = translator.render(plan.by_id["pf:2"], ctx)[0].element
    assert reflected.findtext("natreflection") == "purenat"


# --------------------------------------------------------------------------- #
# Outbound NAT (§12.1.9)
# --------------------------------------------------------------------------- #


def test_outbound_rules(nat_plan) -> None:
    _src, plan = nat_plan
    items = _items(plan, "nat_out")

    assert items["snat:mode"].label == "Outbound NAT mode: hybrid"
    assert items["snat:mode"].status == Status.OK

    assert items["snat#0"].status == Status.OK
    assert items["snat#0"].data["target"] == ""
    assert items["snat#1"].data["target"] == "opt7ip"
    assert items["snat#3"].data["target"] == "192.0.2.5/24"

    assert items["snat#2"].status == Status.UNSUPPORTED
    assert items["snat#2"].reason == "OpenVPN not migrated"

    assert items["snat#4"].status == Status.PARTIAL
    assert items["snat#4"].warnings == [
        "outbound NAT pool options not migrated; re-select the pool option in the GUI"
    ]
    assert "snat:mode" in items["snat#0"].deps


def test_outbound_render_poolopts_is_empty(nat_plan) -> None:
    src, plan = nat_plan
    translator = registry.get("nat_out")
    ctx = WriteContext(src, plan)

    element = translator.render(plan.by_id["snat#4"], ctx)[0].element
    assert element.findtext("poolopts") == ""
    assert element.findtext("protocol") == "any"
    assert element.findtext("source_net") == "lan"

    opt7 = translator.render(plan.by_id["snat#1"], ctx)[0].element
    assert opt7.findtext("target") == "opt7ip"


def test_outbound_mode_is_set(nat_plan) -> None:
    src, plan = nat_plan
    out = build_output(plan, src)
    assert out.findtext("nat/outbound/mode") == "hybrid"


def test_outbound_disabled_mode_marks_rules_partial(nat_plan) -> None:
    src, _plan = nat_plan
    src.pf.find("nat/outbound/mode").text = "disabled"
    ifmap = default_ifmap(
        src, {"em0": "igc0", "em1": "igc1", "em2": "igc2", "em3": "igc3"}
    )
    plan2 = build_plan(src, ifmap)
    items = _items(plan2, "nat_out")
    assert items["snat#0"].status == Status.PARTIAL
    assert "inactive in mode disabled" in items["snat#0"].warnings[0]


# --------------------------------------------------------------------------- #
# 1:1 NAT (§12.1.10)
# --------------------------------------------------------------------------- #


def test_one_to_one_rules(nat_plan) -> None:
    _src, plan = nat_plan
    items = _items(plan, "nat_1to1")
    assert set(items) == {"binat#0", "binat#1"}

    first = items["binat#0"]
    assert first.status == Status.OK
    assert first.data["source"] == "192.168.1.10/32"
    assert first.data["destination"] == "any"
    assert first.data["external"] == "203.0.113.10/32"

    second = items["binat#1"]
    assert second.data["source"] == "10.0.5.0/24"
    assert second.data["external"] == "203.0.113.0/24"


def test_one_to_one_render(nat_plan) -> None:
    src, plan = nat_plan
    translator = registry.get("nat_1to1")
    ctx = WriteContext(src, plan)

    placement = translator.render(plan.by_id["binat#1"], ctx)[0]
    assert placement.path == "OPNsense/Firewall/Filter/onetoone"
    rule = placement.element
    assert rule.findtext("type") == "binat"
    assert rule.findtext("interface") == "wan"
    assert rule.findtext("source_net") == "10.0.5.0/24"
    assert rule.findtext("destination_net") == "any"
    assert rule.findtext("external") == "203.0.113.0/24"
    assert rule.findtext("natreflection") == "disable"


def test_one_to_one_incomplete_is_unsupported(nat_plan) -> None:
    _src, plan = nat_plan
    translator = registry.get("nat_1to1")
    fake_src = type(
        "Src", (), {"pf": etree.fromstring("<pfsense><nat><onetoone/></nat></pfsense>")}
    )()
    items = translator.discover(fake_src, plan.ifmap)
    assert items[0].status == Status.UNSUPPORTED
    assert items[0].reason == "incomplete 1:1 rule"


# --------------------------------------------------------------------------- #
# Validation and goldens
# --------------------------------------------------------------------------- #


def test_nat_output_validates_without_errors(nat_plan) -> None:
    src, plan = nat_plan
    out = build_output(plan, src)
    errors = [finding for finding in validate(out, plan) if finding.level == "error"]
    assert errors == []


def test_port_forward_golden(
    nat_plan, golden_checker, section_placements, wrap_placements
) -> None:
    src, plan = nat_plan
    placements = section_placements(src, plan, "nat_pf")
    golden_checker("nat_pf/nat.xml", wrap_placements(placements))


def test_outbound_golden(nat_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = nat_plan
    placements = section_placements(src, plan, "nat_out")
    golden_checker("nat_out/nat.xml", wrap_placements(placements))


def test_one_to_one_golden(nat_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = nat_plan
    placements = section_placements(src, plan, "nat_1to1")
    golden_checker("nat_1to1/nat.xml", wrap_placements(placements))
