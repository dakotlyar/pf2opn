"""Tests for the system translator and ``inspect`` (design §12.1.1, M1)."""

from __future__ import annotations

import copy
import json

from lxml import etree

from pf2opn import cli, registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status

EXPECTED_FACETS = {
    "system:hostname",
    "system:timezone",
    "system:ntp",
    "system:dns",
    "system:offload",
    "system:ssh",
    "system:console",
    "system:webgui",
    "system:root_password",
}


def _by_id(plan):
    return {item.id: item for item in plan.items if item.section == "system"}


def test_discovers_every_facet(minimal_plan) -> None:
    _src, plan = minimal_plan
    assert set(_by_id(plan)) == EXPECTED_FACETS


def test_statuses_and_defaults(minimal_plan) -> None:
    _src, plan = minimal_plan
    items = _by_id(plan)
    assert items["system:hostname"].status == Status.OK
    assert items["system:hostname"].default_selected is True
    assert items["system:ntp"].status == Status.OK
    assert items["system:ssh"].status == Status.PARTIAL
    assert items["system:ssh"].warnings
    assert items["system:ssh"].followups
    assert items["system:webgui"].status == Status.PARTIAL
    assert items["system:webgui"].default_selected is False
    assert items["system:root_password"].status == Status.PARTIAL
    assert items["system:root_password"].default_selected is False


def test_no_labels_leak_secrets(minimal_plan) -> None:
    _src, plan = minimal_plan
    for item in plan.items:
        assert "$2y$" not in item.label
        for warning in item.warnings:
            assert "$2y$" not in warning


def test_inspect_text_lists_facets(minimal_path, opnsense_base, capsys) -> None:
    code = cli.main(["inspect", str(minimal_path), "--base", str(opnsense_base)])
    assert code == 0
    out = capsys.readouterr().out
    assert "System" in out
    for facet in EXPECTED_FACETS:
        assert facet in out


def test_inspect_json(minimal_path, opnsense_base, capsys) -> None:
    code = cli.main(["inspect", str(minimal_path), "--base", str(opnsense_base), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    ids = {entry["id"] for entry in payload}
    assert EXPECTED_FACETS <= ids
    hostname = next(entry for entry in payload if entry["id"] == "system:hostname")
    assert hostname["status"] == "ok"
    assert hostname["section"] == "system"


def _wrap_placements(placements) -> str:
    root = etree.Element("golden")
    for placement in placements:
        node = etree.SubElement(root, "placement")
        node.set("path", placement.path)
        node.set("mode", placement.mode.value)
        if placement.element is not None:
            node.append(copy.deepcopy(placement.element))
        elif placement.text is not None:
            node.text = placement.text
    return etree.tostring(root, pretty_print=True, encoding="unicode")


def test_system_golden(minimal_plan, golden_checker) -> None:
    src, plan = minimal_plan
    translator = registry.get("system")
    plan.selected = {
        item.id
        for item in plan.items
        if item.section == "system" and item.default_selected and item.status != Status.UNSUPPORTED
    }
    ctx = WriteContext(src, plan)
    placements = []
    for item in plan.items:
        if item.section != "system" or item.id not in plan.selected:
            continue
        placements.extend(translator.render(item, ctx))
    golden_checker("system/minimal.xml", _wrap_placements(placements))


def test_root_password_render(minimal_plan) -> None:
    src, plan = minimal_plan
    plan.selected = {"system:root_password"}
    placements = registry.get("system").render(plan.by_id["system:root_password"], WriteContext(src, plan))
    assert len(placements) == 1
    assert placements[0].mode == Mode.SET
    assert placements[0].path == "system/user[name='root']/password"
    assert placements[0].text.startswith("$2y$")


def test_inspect_rejects_bad_source(tmp_path, opnsense_base, capsys) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text("<not-pfsense/>", encoding="utf-8")
    code = cli.main(["inspect", str(bad), "--base", str(opnsense_base)])
    assert code == 2
    assert "expected <pfsense>" in capsys.readouterr().err