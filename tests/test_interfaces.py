"""Tests for the interfaces translator (design §12.1.2, M3)."""

from __future__ import annotations

from lxml import etree

from pf2opn import cli, registry
from pf2opn.context import SourceContext, WriteContext
from pf2opn.ifmap import default_ifmap
from pf2opn.model import Status
from pf2opn.plan import build_plan
from pf2opn.writer import build_output

_BASE = "tests/fixtures/opnsense/base_26_7.xml"


def _items(plan):
    return {item.id: item for item in plan.items if item.section == "interfaces"}


def test_interfaces_statuses_and_deps(interfaces_vlans_plan) -> None:
    _src, plan = interfaces_vlans_plan
    items = _items(plan)

    assert items["iface:wan"].status == Status.OK
    assert items["iface:lan"].status == Status.OK
    assert items["iface:opt1"].status == Status.PARTIAL
    assert any("description" in warning for warning in items["iface:opt1"].warnings)
    assert items["iface:opt1"].deps == ["vlan:em1.10"]
    assert items["iface:opt2"].deps == ["vlan:em1.20"]
    assert items["iface:opt3"].status == Status.UNSUPPORTED
    assert "not migrated" in items["iface:opt3"].reason


def test_interfaces_golden(interfaces_vlans_plan, golden_checker, wrap_placements) -> None:
    src, plan = interfaces_vlans_plan
    translator = registry.get("interfaces")
    ctx = WriteContext(src, plan)
    placements = []
    for item in plan.items:
        if item.section != "interfaces" or item.id not in plan.selected:
            continue
        placements.extend(translator.render(item, ctx))
    golden_checker("interfaces/interfaces_vlans.xml", wrap_placements(placements))


def test_interfaces_merge_replaces_base_entry(interfaces_vlans_plan) -> None:
    src, plan = interfaces_vlans_plan
    out = build_output(plan, src)

    assert out.findtext("interfaces/wan/if") == "igc0"
    assert out.findtext("interfaces/lan/if") == "igc1"
    assert out.findtext("interfaces/opt1/if") == "vlan01"
    assert out.findtext("interfaces/opt1/descr") == "Home___Lab"
    assert "replaced base entry wan" in plan.by_id["iface:wan"].warnings


def test_interfaces_descriptions_are_sanitized(interfaces_vlans_plan) -> None:
    src, plan = interfaces_vlans_plan
    out = build_output(plan, src)
    assert out.findtext("interfaces/opt1/descr") == "Home___Lab"


def test_unmapped_nic_exits_2(tmp_path, minimal_path, capsys) -> None:
    base = tmp_path / "base.xml"
    base.write_text("<opnsense><system><hostname>B</hostname></system></opnsense>", encoding="utf-8")

    code = cli.main(
        [
            "migrate",
            str(minimal_path),
            "--base",
            str(base),
            "-o",
            str(tmp_path / "out.xml"),
            "--select-all-ok",
        ]
    )

    assert code == 2
    assert "not mapped" in capsys.readouterr().err
    assert not (tmp_path / "out.xml").exists()


def test_interfaces_output_is_well_formed(interfaces_vlans_plan) -> None:
    src, plan = interfaces_vlans_plan
    out = build_output(plan, src)
    reparsed = etree.fromstring(etree.tostring(out))
    assert reparsed.findtext("interfaces/opt2/descr") == "Guests"


def _gateway_source(tmp_path, gateway_name: str):
    path = tmp_path / "gw.xml"
    path.write_text(
        "<pfsense><version>24.0</version>"
        "<system><hostname>x</hostname><domain>example.test</domain></system>"
        "<interfaces><lan><if>em1</if><descr>LAN</descr><enable/>"
        "<ipaddr>192.168.1.1</ipaddr><subnet>24</subnet>"
        f"<gateway>{gateway_name}</gateway></lan></interfaces>"
        "<gateways><gateway_item><name>LAN_GW</name><interface>lan</interface>"
        "<gateway>192.168.1.254</gateway></gateway_item></gateways></pfsense>",
        encoding="utf-8",
    )
    src = SourceContext.load(path, _BASE)
    return src, build_plan(src, default_ifmap(src, {"em1": "igc1"}))


def test_interface_declares_gateway_dependency(tmp_path) -> None:
    """A copied `gateway` must resolve to an output gateway (§12.1.2)."""
    _src, plan = _gateway_source(tmp_path, "LAN_GW")
    item = next(i for i in plan.items if i.id == "iface:lan")
    assert "gw:LAN_GW" in item.deps
    assert item.status == Status.OK


def test_interface_with_unknown_gateway_is_unsupported(tmp_path) -> None:
    _src, plan = _gateway_source(tmp_path, "GHOST_GW")
    item = next(i for i in plan.items if i.id == "iface:lan")
    assert item.status == Status.UNSUPPORTED
    assert "GHOST_GW" in item.reason
