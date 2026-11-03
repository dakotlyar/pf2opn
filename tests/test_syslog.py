"""Tests for the syslog translator (design §12.1.19, M10)."""

from __future__ import annotations

import types

from lxml import etree

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == section}


def test_remote_and_local_items(misc_plan) -> None:
    _src, plan = misc_plan
    items = _items(plan, "syslog")
    assert items["syslog:remote1"].status == Status.OK
    assert items["syslog:local"].status == Status.UNSUPPORTED
    assert "local log settings" in items["syslog:local"].reason


def test_remote_render(misc_plan) -> None:
    src, plan = misc_plan
    placement = registry.get("syslog").render(plan.by_id["syslog:remote1"], WriteContext(src, plan))[0]
    assert placement.mode == Mode.APPEND
    root = placement.element
    assert root.findtext("transport") == "udp4"
    assert root.findtext("hostname") == "logs.example.test"
    assert root.findtext("port") == "514"
    assert root.findtext("rfc5424") == "0"
    assert root.findtext("description") == "pf2opn remote syslog"


def test_ipv6_transport() -> None:
    pf = etree.fromstring(
        "<pfsense><syslog><enable></enable><ipproto>ipv6</ipproto>"
        "<remoteserver>logs.example.test:5514</remoteserver></syslog></pfsense>"
    )
    src = types.SimpleNamespace(pf=pf)
    item = registry.get("syslog").discover(src, None)[0]
    ctx = types.SimpleNamespace(uuid=lambda item_id, suffix="": f"uuid:{item_id}")
    root = registry.get("syslog").render(item, ctx)[0].element
    assert root.findtext("transport") == "udp6"
    assert root.findtext("port") == "5514"


def test_disabled_or_empty_remotes_are_skipped() -> None:
    pf = etree.fromstring(
        "<pfsense><syslog><remoteserver>host:514</remoteserver></syslog></pfsense>"
    )
    src = types.SimpleNamespace(pf=pf)
    assert registry.get("syslog").discover(src, None) == []


def test_syslog_golden(misc_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = misc_plan
    placements = section_placements(src, plan, "syslog")
    golden_checker("syslog/misc.xml", wrap_placements(placements))
