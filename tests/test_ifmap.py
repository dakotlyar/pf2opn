"""Tests for interface mapping (design §12.2, M3)."""

from __future__ import annotations

from lxml import etree

from pf2opn.context import SourceContext
from pf2opn.ifmap import classify, default_ifmap, target_nics, validate_ifmap


def _load(name: str, opnsense_base, pfsense_dir) -> SourceContext:
    return SourceContext.load(pfsense_dir / name, opnsense_base)


def test_classify(opnsense_base, pfsense_dir) -> None:
    src = _load("interfaces_vlans.xml", opnsense_base, pfsense_dir)
    assert classify("em0", src) == "physical"
    assert classify("em1.10", src) == "vlan"
    assert classify("ovpns1", src) == "unsupported"
    assert classify("tun_wg0", src) == "wireguard"


def test_default_ifmap_suggests_base_nics(opnsense_base, pfsense_dir) -> None:
    src = _load("interfaces_vlans.xml", opnsense_base, pfsense_dir)
    m = default_ifmap(src)
    assert m.nic_map == {"em0": "igc0", "em1": "igc1"}
    assert m.targets["wan"].opn_device == "igc0"
    assert m.targets["lan"].opn_device == "igc1"
    assert m.targets["opt3"].kind == "unsupported"
    assert m.targets["opt3"].include is False


def test_default_ifmap_vlan_names(opnsense_base, pfsense_dir) -> None:
    src = _load("interfaces_vlans.xml", opnsense_base, pfsense_dir)
    m = default_ifmap(src)
    assert m.vlan_names == {"em1.10": "vlan01", "em1.20": "vlan02", "em1.30": "vlan03"}


def test_vlan_naming_continues_after_base_vlans(opnsense_base, pfsense_dir) -> None:
    src = _load("interfaces_vlans.xml", opnsense_base, pfsense_dir)
    vlans = etree.SubElement(src.base, "vlans")
    for name in ("vlan01", "vlan02"):
        vlan = etree.SubElement(vlans, "vlan")
        etree.SubElement(vlan, "vlanif").text = name

    m = default_ifmap(src)

    assert m.vlan_names == {"em1.10": "vlan03", "em1.20": "vlan04", "em1.30": "vlan05"}


def test_target_nics(opnsense_base, pfsense_dir) -> None:
    src = _load("minimal.xml", opnsense_base, pfsense_dir)
    assert target_nics(src) == ["igc0", "igc1"]
    assert target_nics(src, ["em9"]) == ["em9", "igc0", "igc1"]


def test_validate_ifmap_reports_unmapped_physical(opnsense_base, pfsense_dir) -> None:
    src = _load("interfaces_vlans.xml", opnsense_base, pfsense_dir)
    for iface in src.base.findall("interfaces/*"):
        src.base.find("interfaces").remove(iface)

    errors = validate_ifmap(default_ifmap(src, {"em0": "igc0"}))

    assert any("em1" in error and "not mapped" in error for error in errors)


def test_validate_ifmap_reports_duplicate_targets(opnsense_base, pfsense_dir) -> None:
    src = _load("minimal.xml", opnsense_base, pfsense_dir)
    errors = validate_ifmap(default_ifmap(src, {"em0": "igc0", "em1": "igc0"}))
    assert any("igc0" in error for error in errors)


def test_validate_ifmap_accepts_a_complete_map(interfaces_vlans_plan) -> None:
    _src, plan = interfaces_vlans_plan
    assert validate_ifmap(plan.ifmap) == []