"""Tests for the cron classifier (design §12.1.16, D12, M10)."""

from __future__ import annotations

import types

from lxml import etree

from pf2opn import registry
from pf2opn.model import Status


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == "cron"}


def _classify(command: str):
    pf = etree.fromstring(
        f"<pfsense><cron><item><minute>0</minute><hour>0</hour><mday>*</mday>"
        f"<month>*</month><wday>*</wday><command>{command}</command></item></cron></pfsense>"
    )
    return registry.get("cron").discover(types.SimpleNamespace(pf=pf), None)[0]


def test_misc_cron_classified(misc_plan) -> None:
    _src, plan = misc_plan
    items = _items(plan, "cron")
    assert items["cron#0"].status == Status.UNSUPPORTED
    assert "system job" in items["cron#0"].reason
    assert "pfBlockerNG" in items["cron#1"].reason
    assert "os-acme-client" in items["cron#2"].reason


def test_custom_script_wraps_in_configd_action(edge_cases_plan) -> None:
    _src, plan = edge_cases_plan
    item = _items(plan, "cron")["cron#0"]
    assert item.status == Status.UNSUPPORTED
    assert "configd action" in item.reason
    assert item.followups


def test_classifications() -> None:
    assert "system job" in _classify("/etc/rc.periodic daily").reason
    assert "pfBlockerNG" in _classify("/usr/local/pkg/pfblockerng/pfblockerng.sh").reason
    assert "os-acme-client" in _classify("/usr/local/pkg/acme/acme.sh renew").reason
    assert "Monit" in _classify("/usr/local/bin/servicewatchdog_check").reason
    assert "configd action" in _classify("/usr/local/bin/other.sh").reason


def test_cron_item_is_unsupported_and_never_renders(edge_cases_plan) -> None:
    _src, plan = edge_cases_plan
    item = _items(plan, "cron")["cron#0"]
    assert registry.get("cron").render(item, None) == []
    assert item.default_selected is False or item.status == Status.UNSUPPORTED
