"""Tests for the unsupported catalogue and the "not applicable" report (design §12.4, M10)."""

from __future__ import annotations

import types

from lxml import etree

from pf2opn import cli, registry
from pf2opn.model import Status
from pf2opn.report import render_report
from pf2opn.translators.unsupported import ignored_sections


def _items(plan):
    return {item.id: item for item in plan.items if item.section == "unsupported"}


def _discover(xml: str):
    return registry.get("unsupported").discover(
        types.SimpleNamespace(pf=etree.fromstring(xml)), None
    )


def test_misc_pfblockerng_unsupported(misc_plan) -> None:
    _src, plan = misc_plan
    assert "unsup:pfblockerng" in _items(plan)
    assert _items(plan)["unsup:pfblockerng"].status == Status.UNSUPPORTED


def test_snmpd_without_enable_is_detected(edge_cases_plan) -> None:
    _src, plan = edge_cases_plan
    assert "unsup:snmpd" in _items(plan)


def test_ipsec_mobile_clients_are_detected(edge_cases_plan) -> None:
    _src, plan = edge_cases_plan
    assert "unsup:ipsec" in _items(plan)


def test_snmpd_detector_ignores_a_bare_section() -> None:
    assert "unsup:snmpd" not in {item.id for item in _discover("<pfsense><snmpd/></pfsense>")}


def test_ipsec_detector_needs_content() -> None:
    assert "unsup:ipsec" not in {item.id for item in _discover("<pfsense><ipsec/></pfsense>")}


def test_ignored_sections_ovpnserver(edge_cases_plan) -> None:
    src, _plan = edge_cases_plan
    labels = [label for label, _reason in ignored_sections(src.pf)]
    assert "ovpnserver" in labels


def test_ignored_sections_package_stub(misc_plan) -> None:
    src, _plan = misc_plan
    labels = [label for label, _reason in ignored_sections(src.pf)]
    assert "installedpackages/package" in labels
    # pfblockerng is a real (unsupported) item, not an ignored section
    assert "pfblockerng" not in labels


def test_report_lists_not_applicable(edge_cases_plan, tmp_path) -> None:
    src, plan = edge_cases_plan
    text = render_report(plan, [], src, tmp_path / "out.xml")
    assert "## Not applicable" in text
    assert "ovpnserver" in text


def test_inspect_lists_unsupported_items(edge_cases_plan, tmp_path, capsys) -> None:
    src, _plan = edge_cases_plan
    code = cli.main(
        ["inspect", str(src.source_path), "--base", str(src.base_path), "--json"]
    )
    assert code == 0
    import json

    ids = {entry["id"] for entry in json.loads(capsys.readouterr().out)}
    assert "unsup:snmpd" in ids
    assert "unsup:ipsec" in ids
