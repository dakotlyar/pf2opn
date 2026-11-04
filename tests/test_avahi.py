"""Tests for the mDNS reflector / Avahi translator (design §12.1.20, M15)."""

from __future__ import annotations

import types

from lxml import etree

from pf2opn import registry
from pf2opn.model import Mode, Status


def _iface_map(*keys):
    return types.SimpleNamespace(included_keys=lambda: set(keys))


def _config(inner: str) -> str:
    return (
        "<pfsense><installedpackages><avahi><config>"
        f"{inner}"
        "</config></avahi></installedpackages></pfsense>"
    )


def _discover(xml: str, *keys):
    src = types.SimpleNamespace(pf=etree.fromstring(xml))
    return registry.get("avahi").discover(src, _iface_map(*keys))


def _item(xml: str, *keys):
    items = _discover(xml, *keys)
    return items[0] if items else None


# --------------------------------------------------------------------------- #
# The reference config (the real box's shape)
# --------------------------------------------------------------------------- #


def test_reference_config_is_ok(avahi_plan) -> None:
    _src, plan = avahi_plan
    item = plan.by_id["avahi:config"]
    assert item.status == Status.OK
    assert item.default_selected is True
    assert item.requires_plugin == "os-mdns-repeater"
    assert item.warnings == []
    assert item.data == {
        "enabled": True,
        "enablecarp": False,
        "interfaces": "opt1,opt2,opt3,opt5",
    }


def test_reference_config_renders_mdnsrepeater(avahi_plan) -> None:
    _src, plan = avahi_plan
    placements = registry.get("avahi").render(plan.by_id["avahi:config"], None)
    by_path = {p.path: p for p in placements}
    assert set(by_path) == {
        "OPNsense/MDNSRepeater/enabled",
        "OPNsense/MDNSRepeater/enablecarp",
        "OPNsense/MDNSRepeater/interfaces",
    }
    assert all(p.mode == Mode.SET for p in placements)
    assert by_path["OPNsense/MDNSRepeater/enabled"].text == "1"
    assert by_path["OPNsense/MDNSRepeater/enablecarp"].text == "0"
    assert by_path["OPNsense/MDNSRepeater/interfaces"].text == "opt1,opt2,opt3,opt5"
    # blocklist is never written
    assert not any(p.path.endswith("blocklist") for p in placements)


def test_avahi_golden(avahi_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = avahi_plan
    placements = section_placements(src, plan, "avahi")
    golden_checker("avahi/avahi.xml", wrap_placements(placements))


def test_avahi_is_not_reported_unsupported(avahi_plan) -> None:
    """M15 removes ``unsup:avahi`` from the catalogue (§12.4)."""
    _src, plan = avahi_plan
    assert "unsup:avahi" not in {item.id for item in plan.items}
    assert "unsup:avahi" not in {
        item.id
        for item in registry.get("unsupported").discover(
            types.SimpleNamespace(pf=etree.fromstring(_config("<enable>yes</enable>"))), None
        )
    }


# --------------------------------------------------------------------------- #
# Disabled
# --------------------------------------------------------------------------- #


def test_disabled_config_is_discovered_but_not_default_selected() -> None:
    item = _item(_config("<enable>no</enable><interfaces>opt1,opt2</interfaces>"), "opt1", "opt2")
    assert item.status == Status.OK
    assert item.default_selected is False
    assert item.warnings == []
    assert item.data["enabled"] is False
    assert item.data["interfaces"] == "opt1,opt2"
    placements = registry.get("avahi").render(item, None)
    enabled = next(p for p in placements if p.path.endswith("/enabled"))
    assert enabled.text == "0"


def test_empty_present_enable_is_on() -> None:
    """A present-but-empty ``<enable/>`` is on: §7.3 ``pf_flag`` semantics."""
    item = _item(
        _config("<enable/><interfaces>opt1,opt2</interfaces><reflection>yes</reflection>"),
        "opt1",
        "opt2",
    )
    assert item.status == Status.OK
    assert item.default_selected is True
    assert item.data["enabled"] is True
    placements = registry.get("avahi").render(item, None)
    enabled = next(p for p in placements if p.path.endswith("/enabled"))
    assert enabled.text == "1"


def test_disabled_config_has_no_warnings_even_with_truthy_no_equivalents() -> None:
    item = _item(
        _config(
            "<enable>no</enable><interfaces>opt1</interfaces>"
            "<disable_ipv4>on</disable_ipv4><reflection/>"
        ),
        "opt1",
        "opt2",
    )
    assert item.status == Status.OK
    assert item.warnings == []


# --------------------------------------------------------------------------- #
# action=deny and the interface list
# --------------------------------------------------------------------------- #


def test_action_deny_is_unsupported() -> None:
    item = _item(
        _config("<enable>yes</enable><action>deny</action><interfaces>opt1,opt2</interfaces>"),
        "opt1",
        "opt2",
    )
    assert item.status == Status.UNSUPPORTED
    assert "deny" in item.reason
    assert registry.get("avahi").render(item, None) == []


def test_action_allow_is_ignored() -> None:
    item = _item(
        _config(
            "<enable>yes</enable><action>allow</action><reflection>yes</reflection>"
            "<interfaces>opt1,opt2</interfaces>"
        ),
        "opt1",
        "opt2",
    )
    assert item.status == Status.OK


def test_empty_interfaces_with_enable_is_unsupported() -> None:
    item = _item(_config("<enable>yes</enable>"), "opt1", "opt2")
    assert item.status == Status.UNSUPPORTED
    assert "all interfaces" in item.reason


def test_all_interfaces_dropped_is_unsupported() -> None:
    item = _item(_config("<enable>yes</enable><interfaces>opt7,opt8</interfaces>"), "opt1", "opt2")
    assert item.status == Status.UNSUPPORTED
    assert "no migrated interfaces" in item.reason


def test_unmapped_interface_is_dropped_with_partial_warning() -> None:
    item = _item(_config("<enable>yes</enable><interfaces>opt1,opt9</interfaces>"), "opt1", "opt2")
    assert item.status == Status.PARTIAL
    assert item.data["interfaces"] == "opt1"
    assert any("opt9" in warning and "dropped" in warning for warning in item.warnings)


def test_single_interface_is_partial() -> None:
    item = _item(_config("<enable>yes</enable><interfaces>opt1</interfaces>"), "opt1", "opt2")
    assert item.status == Status.PARTIAL
    assert any("at least 2" in warning for warning in item.warnings)


def test_more_than_five_interfaces_is_partial() -> None:
    keys = [f"opt{n}" for n in range(1, 7)]
    item = _item(
        _config(f"<enable>yes</enable><interfaces>{','.join(keys)}</interfaces>"), *keys
    )
    assert item.status == Status.PARTIAL
    assert any("at most 5" in warning for warning in item.warnings)


# --------------------------------------------------------------------------- #
# The remaining no-equivalent fields
# --------------------------------------------------------------------------- #


def test_carpstatusvid_maps_to_enablecarp_with_partial() -> None:
    item = _item(
        _config(
            "<enable>yes</enable><carpstatusvid>10</carpstatusvid>"
            "<interfaces>opt1,opt2</interfaces>"
        ),
        "opt1",
        "opt2",
    )
    assert item.status == Status.PARTIAL
    assert item.data["enablecarp"] is True
    assert any("CARP" in warning for warning in item.warnings)


def test_truthy_no_equivalents_warn() -> None:
    item = _item(
        _config(
            "<enable>yes</enable><interfaces>opt1,opt2</interfaces>"
            "<disable_ipv4>on</disable_ipv4><override_hostname>host</override_hostname>"
            "<publishing>on</publishing><filtering>on</filtering>"
        ),
        "opt1",
        "opt2",
    )
    assert item.status == Status.PARTIAL
    assert len(item.warnings) >= 4


def test_reflection_off_warns() -> None:
    item = _item(
        _config("<enable>yes</enable><interfaces>opt1,opt2</interfaces><reflection>no</reflection>"),
        "opt1",
        "opt2",
    )
    assert item.status == Status.PARTIAL
    assert any("always reflects" in warning for warning in item.warnings)


def test_empty_no_equivalent_elements_are_silent() -> None:
    item = _item(
        _config(
            "<enable>yes</enable><interfaces>opt1,opt2</interfaces>"
            "<disable_ipv4/><disable_ipv6/><override_hostname/>"
            "<override_domain/><publishing/><filtering/><reflection>yes</reflection>"
        ),
        "opt1",
        "opt2",
    )
    assert item.status == Status.OK
    assert item.warnings == []


def test_falsey_nonempty_no_equivalents_warn() -> None:
    """Non-empty no-equivalent fields warn even when the text is falsey."""
    item = _item(
        _config(
            "<enable>yes</enable><interfaces>opt1,opt2</interfaces><reflection>yes</reflection>"
            "<override_hostname>0</override_hostname><publishing>0</publishing>"
            "<disable_ipv4>0</disable_ipv4>"
        ),
        "opt1",
        "opt2",
    )
    assert item.status == Status.PARTIAL
    assert any("hostname override" in warning for warning in item.warnings)
    assert any("publishing settings" in warning for warning in item.warnings)
    # disable_ipv4 keeps the truthy-by-text rule: ``0`` is off and silent.
    assert not any("disable" in warning.lower() for warning in item.warnings)


# --------------------------------------------------------------------------- #
# Discovery guard
# --------------------------------------------------------------------------- #


def test_empty_config_raises_no_item() -> None:
    assert _discover("<pfsense><installedpackages><avahi><config/></avahi>"
                     "</installedpackages></pfsense>") == []
    assert _discover("<pfsense><installedpackages><avahi/></installedpackages></pfsense>") == []
    assert _discover("<pfsense></pfsense>") == []
