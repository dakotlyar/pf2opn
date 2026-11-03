"""Tests for the contexts (design §7.4)."""

from __future__ import annotations

import uuid as uuidlib

from pf2opn.context import PF2OPN_NS, SourceContext, WriteContext


def test_pf2opn_namespace_is_the_documented_value() -> None:
    assert PF2OPN_NS == uuidlib.UUID("6f2c1d2e-8a4b-4c1e-9a43-7d0c5e0f2a11")


def test_uuid_is_deterministic_uuid5(minimal_plan) -> None:
    src, plan = minimal_plan
    ctx = WriteContext(src, plan)
    assert ctx.uuid("alias:RFC1918") == str(uuidlib.uuid5(PF2OPN_NS, "alias:RFC1918/"))
    assert ctx.uuid("dhcp:lan", "r1") == str(uuidlib.uuid5(PF2OPN_NS, "dhcp:lan/r1"))
    assert ctx.uuid("alias:RFC1918") == ctx.uuid("alias:RFC1918")


def test_source_context_loads_minimal(minimal_path, opnsense_base) -> None:
    src = SourceContext.load(minimal_path, opnsense_base)
    assert src.base_is_default is False
    assert list(src.pf_ifaces) == ["wan", "lan"]
    assert src.pf_ifaces["wan"].device == "em0"
    assert src.pf_ifaces["lan"].device == "em1"
    assert src.pf_ifaces["lan"].ipaddr == "192.168.1.1"
    assert src.pf_version == "24.0"
    assert src.pf_hostname == "pfbox"
    assert src.base_hostname == "OPNsense"
    assert len(src.source_sha256) == 64
    assert src.alias_names == set()
    assert src.gateway_names == set()


def test_source_context_loads_with_default_base(minimal_path) -> None:
    """No ``--base`` means the bundled factory base, flagged ``base_is_default``."""
    src = SourceContext.load(minimal_path)
    assert src.base_is_default is True
    assert src.base_path is None
    assert src.base.tag == "opnsense"
    assert src.base_hostname == "OPNsense"
    assert src.base.find("trigger_initial_wizard") is None


def test_write_context_iface_helpers(minimal_plan) -> None:
    src, plan = minimal_plan
    ctx = WriteContext(src, plan)
    assert ctx.map_iface("lan") == "lan"
    plan.ifmap.targets["lan"].include = False
    assert ctx.map_iface("lan") is None
    assert ctx.device("em1") == "igc1"
    assert ctx.device("does-not-exist") is None
