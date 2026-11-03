"""Tests for the VLAN translator (design §12.1.3, M3)."""

from __future__ import annotations

from lxml import etree

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status
from pf2opn.writer import build_output


def _items(plan):
    return {item.id: item for item in plan.items if item.section == "vlans"}


def test_vlan_items(interfaces_vlans_plan) -> None:
    _src, plan = interfaces_vlans_plan
    items = _items(plan)
    assert set(items) == {"vlan:em1.10", "vlan:em1.20", "vlan:em1.30"}
    assert all(item.status == Status.OK for item in items.values())


def test_vlan_render(interfaces_vlans_plan) -> None:
    src, plan = interfaces_vlans_plan
    translator = registry.get("vlans")
    ctx = WriteContext(src, plan)
    placements = translator.render(plan.by_id["vlan:em1.10"], ctx)

    assert len(placements) == 1
    placement = placements[0]
    assert placement.mode == Mode.APPEND
    assert placement.path == "vlans"
    assert placement.element.findtext("if") == "igc1"
    assert placement.element.findtext("tag") == "10"
    assert placement.element.findtext("vlanif") == "vlan01"
    assert placement.element.findtext("pcp") == "0"
    assert placement.element.find("proto") is not None
    assert placement.element.findtext("proto") in (None, "")
    assert placement.element.get("uuid") == ctx.uuid("vlan:em1.10")


def test_vlans_golden(interfaces_vlans_plan, golden_checker, wrap_placements) -> None:
    src, plan = interfaces_vlans_plan
    translator = registry.get("vlans")
    ctx = WriteContext(src, plan)
    placements = []
    for item in plan.items:
        if item.section != "vlans" or item.id not in plan.selected:
            continue
        placements.extend(translator.render(item, ctx))
    golden_checker("vlans/interfaces_vlans.xml", wrap_placements(placements))


def test_vlan_parent_not_mapped_is_unsupported(opnsense_base, pfsense_dir) -> None:
    from pf2opn.context import SourceContext
    from pf2opn.ifmap import default_ifmap
    from pf2opn.plan import build_plan

    src = SourceContext.load(pfsense_dir / "interfaces_vlans.xml", opnsense_base)
    for iface in src.base.findall("interfaces/*"):
        src.base.find("interfaces").remove(iface)

    plan = build_plan(src, default_ifmap(src, {"em0": "igc0"}))
    items = _items(plan)
    assert items["vlan:em1.10"].status == Status.UNSUPPORTED
    assert "em1" in items["vlan:em1.10"].reason


def test_vlan_output_keeps_base_vlan(opnsense_base, pfsense_dir) -> None:
    from pf2opn.context import SourceContext
    from pf2opn.ifmap import default_ifmap
    from pf2opn.plan import build_plan

    src = SourceContext.load(pfsense_dir / "interfaces_vlans.xml", opnsense_base)
    vlans = src.base.find("vlans")
    if vlans is None:
        vlans = etree.SubElement(src.base, "vlans")
    existing = etree.SubElement(vlans, "vlan")
    existing.set("uuid", "base-vlan-uuid")
    for tag, text in (("if", "igc1"), ("tag", "99"), ("vlanif", "vlan09")):
        etree.SubElement(existing, tag).text = text

    plan = build_plan(src, default_ifmap(src, {"em0": "igc0", "em1": "igc1"}))
    out = build_output(plan, src)

    vlanifs = [vlan.findtext("vlanif") for vlan in out.findall("vlans/vlan")]
    assert "vlan09" in vlanifs
    # A base vlan with no matching pfSense vlan is kept (merge).
    assert vlanifs.count("vlan09") == 1
