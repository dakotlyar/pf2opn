"""Tests for the filter translator (design §12.1.7, M5)."""

from __future__ import annotations

from lxml import etree

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status
from pf2opn.validate import validate
from pf2opn.writer import build_output


def _items(plan):
    return {item.id: item for item in plan.items if item.section == "filter"}


def test_filter_statuses_and_deps(filter_plan) -> None:
    _src, plan = filter_plan
    items = _items(plan)

    assert items["rule:1000"].status == Status.OK
    assert items["rule:1001"].status == Status.OK
    assert items["rule:1002"].status == Status.OK
    assert items["rule:1003"].status == Status.OK
    assert items["rule:1004"].deps == ["gw:OPT1_GW", "iface:lan"]
    assert items["rule:1005"].deps == ["alias:RFC1918", "iface:lan"]
    assert items["rule:1006"].deps == ["alias:WEBPORTS", "iface:lan"]
    assert items["rule:1007"].deps == ["iface:wan"]
    assert items["rule:1008"].deps == ["iface:lan", "iface:opt1"]
    assert items["rule:1012"].warnings == [
        "was the auto rule of a NAT port forward; now a standalone rule"
    ]


def test_filter_unsupported_cases(filter_plan) -> None:
    _src, plan = filter_plan
    items = _items(plan)

    assert items["rule:1011"].status == Status.UNSUPPORTED
    assert "L2TP/PPPoE" in items["rule:1011"].reason
    # the pfB_ alias rule cascades to UNSUPPORTED through its alias dependency
    assert items["rule:1013"].status == Status.UNSUPPORTED
    assert "cannot be migrated" in items["rule:1013"].reason
    assert items["rule:1013"].default_selected is False


def test_filter_junk_state_policy_is_partial(filter_plan) -> None:
    _src, plan = filter_plan
    item = _items(plan)["rule:1014"]
    assert item.status == Status.PARTIAL
    assert item.warnings == [
        "state policy 'bogus-policy' has no OPNsense equivalent, dropped"
    ]


def test_filter_floating_first_ordering(filter_plan) -> None:
    _src, plan = filter_plan
    entries = [
        item
        for item in plan.items
        if item.section == "filter" and "sequence" in item.data
    ]
    sequences = {item.id: item.data["sequence"] for item in entries}
    assert sequences["rule:1000"] == 10
    assert sequences["rule:1001"] == 20
    rest = sorted(value for key, value in sequences.items() if key not in {"rule:1000", "rule:1001"})
    assert rest[0] == 30
    assert rest == sorted(rest)
    assert min(rest) > max(sequences["rule:1000"], sequences["rule:1001"])


def test_filter_enabled_disabled_and_fields(filter_plan) -> None:
    src, plan = filter_plan
    translator = registry.get("filter")
    ctx = WriteContext(src, plan)

    disabled = translator.render(plan.by_id["rule:1002"], ctx)[0].element
    assert disabled.findtext("enabled") == "0"
    assert disabled.findtext("action") == "pass"

    enabled_on = translator.render(plan.by_id["rule:1003"], ctx)[0].element
    assert enabled_on.findtext("enabled") == "1"

    floating = translator.render(plan.by_id["rule:1000"], ctx)[0].element
    assert floating.findtext("interface") == "wan,lan"
    assert floating.findtext("quick") == "0"
    assert floating.findtext("statetype") == "keep"
    assert floating.findtext("sequence") == "10"

    icmp = translator.render(plan.by_id["rule:1009"], ctx)[0].element
    assert icmp.findtext("protocol") == "ICMP"
    assert icmp.findtext("icmptype") == ""
    assert icmp.find("icmptype") is not None

    wireguard = translator.render(plan.by_id["rule:1010"], ctx)[0].element
    assert wireguard.findtext("interface") == "wireguard"

    opt1 = translator.render(plan.by_id["rule:1008"], ctx)[0].element
    assert opt1.findtext("destination_net") == "opt1ip"

    placement = translator.render(plan.by_id["rule:1000"], ctx)[0]
    assert placement.mode == Mode.APPEND
    assert placement.path == "OPNsense/Firewall/Filter/rules"


def test_filter_service_name_port_is_migrated(filter_plan) -> None:
    src, plan = filter_plan
    translator = registry.get("filter")
    ctx = WriteContext(src, plan)
    element = translator.render(plan.by_id["rule:1006"], ctx)[0].element
    assert element.findtext("destination_port") == "WEBPORTS"


def test_filter_any_interface_and_expand_fallback(filter_plan) -> None:
    src, plan = filter_plan
    translator = registry.get("filter")
    ctx = WriteContext(src, plan)

    element = translator.render(plan.by_id["rule:1001"], ctx)[0].element
    assert element.findtext("interface") == ""

    plan.options["expand_any_interface"] = True
    element = translator.render(plan.by_id["rule:1001"], ctx)[0].element
    assert element.findtext("interface") == "wan,lan,opt1"


def test_filter_separator_item_is_a_partial_note(filter_plan) -> None:
    _src, plan = filter_plan
    separator = _items(plan)["filter:separator"]
    assert separator.status == Status.PARTIAL
    assert separator.warnings == [
        "1 pfSense separator block(s) dropped (MVC rules have no separators)"
    ]
    # it renders nothing
    src, _ = filter_plan
    ctx = WriteContext(src, plan)
    assert registry.get("filter").render(separator, ctx) == []


def test_filter_replaces_base_rules(filter_plan) -> None:
    src, plan = filter_plan
    assert len(src.base.findall("OPNsense/Firewall/Filter/rules/rule")) == 2

    out = build_output(plan, src)

    descriptions = [
        rule.findtext("description")
        for rule in out.findall("OPNsense/Firewall/Filter/rules/rule")
    ]
    assert "default allow LAN to any rule" not in descriptions
    assert len(descriptions) == 13  # 15 rules minus the 2 UNSUPPORTED ones


def test_filter_keeps_base_rules_with_option(filter_plan) -> None:
    src, plan = filter_plan
    plan.options["keep_base_rules"] = True

    out = build_output(plan, src)

    descriptions = [
        rule.findtext("description")
        for rule in out.findall("OPNsense/Firewall/Filter/rules/rule")
    ]
    assert "default allow LAN to any rule" in descriptions
    assert len(descriptions) == 15  # 2 base + 13 migrated


def test_filter_output_validates_without_errors(filter_plan) -> None:
    src, plan = filter_plan
    out = build_output(plan, src)
    errors = [finding for finding in validate(out, plan) if finding.level == "error"]
    assert errors == []


def test_filter_unknown_interface_is_unsupported(filter_plan) -> None:
    src, plan = filter_plan
    translator = registry.get("filter")
    rule = etree.fromstring(
        "<rule><type>pass</type><interface>opt9</interface><tracker>9999</tracker></rule>"
    )
    item = translator._discover_rule(rule, 0, 10, src, plan.ifmap)
    assert item.status == Status.UNSUPPORTED
    assert "opt9" in item.reason


def test_filter_categories_are_dropped_with_a_warning(filter_plan) -> None:
    """`categories` is a uuid relation; the pfSense names must not be copied (§12.1.7)."""
    src, plan = filter_plan
    translator = registry.get("filter")
    rule = etree.fromstring(
        "<rule><type>pass</type><interface>lan</interface>"
        "<category>WEB</category><tracker>2001</tracker>"
        "<descr>categorised</descr></rule>"
    )
    item = translator._discover_rule(rule, 0, 10, src, plan.ifmap)
    assert item.status == Status.PARTIAL
    assert any("categories not migrated" in warning for warning in item.warnings)

    ctx = WriteContext(src, plan)
    element = translator.render(item, ctx)[0].element
    assert element.find("categories") is None


def test_filter_overload_writes_the_alias_uuid(filter_plan) -> None:
    src, plan = filter_plan
    plan.selected.add("alias:RFC1918")
    translator = registry.get("filter")
    rule = etree.fromstring(
        "<rule><type>pass</type><interface>lan</interface>"
        "<overload>RFC1918</overload><tracker>2002</tracker>"
        "<descr>overloaded</descr></rule>"
    )
    item = translator._discover_rule(rule, 0, 10, src, plan.ifmap)
    assert "alias:RFC1918" in item.deps

    ctx = WriteContext(src, plan)
    element = translator.render(item, ctx)[0].element
    assert element.findtext("overload") == ctx.uuid("alias:RFC1918")
    assert element.findtext("overload") != "RFC1918"


def test_filter_overload_absent_alias_is_dropped_in_discover(filter_plan) -> None:
    src, plan = filter_plan
    translator = registry.get("filter")
    rule = etree.fromstring(
        "<rule><type>pass</type><interface>lan</interface>"
        "<overload>GHOST</overload><tracker>2004</tracker>"
        "<descr>ghost overload</descr></rule>"
    )
    item = translator._discover_rule(rule, 0, 10, src, plan.ifmap)
    assert item.status == Status.PARTIAL
    assert any("overload alias not migrated" in warning for warning in item.warnings)
    assert "alias:GHOST" not in item.deps

    ctx = WriteContext(src, plan)
    element = translator.render(item, ctx)[0].element
    assert element.find("overload") is None


def test_filter_overload_dropped_when_alias_not_migrated(filter_plan) -> None:
    src, plan = filter_plan
    plan.selected.discard("alias:RFC1918")
    translator = registry.get("filter")
    rule = etree.fromstring(
        "<rule><type>pass</type><interface>lan</interface>"
        "<overload>RFC1918</overload><tracker>2003</tracker>"
        "<descr>overloaded</descr></rule>"
    )
    item = translator._discover_rule(rule, 0, 10, src, plan.ifmap)

    ctx = WriteContext(src, plan)
    element = translator.render(item, ctx)[0].element
    assert element.find("overload") is None
    assert item.status == Status.PARTIAL
    assert any("overload alias not migrated" in warning for warning in item.warnings)


def test_filter_golden(filter_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = filter_plan
    placements = section_placements(src, plan, "filter")
    golden_checker("filter/filter.xml", wrap_placements(placements))


def test_filter_base_policy_default_is_replace(minimal_plan) -> None:
    src, plan = minimal_plan
    translator = registry.get("filter")
    ctx = WriteContext(src, plan)
    assert translator.effective_base_policy(ctx) == "replace"
    plan.options["keep_base_rules"] = True
    assert translator.effective_base_policy(ctx) == "none"
