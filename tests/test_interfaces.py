"""Tests for the interfaces translator (design §12.1.2, M3)."""

from __future__ import annotations

from lxml import etree

from pf2opn import cli, registry
from pf2opn.context import WriteContext
from pf2opn.model import Status
from pf2opn.writer import build_output


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
