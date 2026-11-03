"""Tests for the Unbound translators (design §12.1.13, M8)."""

from __future__ import annotations

from pf2opn import registry
from pf2opn.context import SourceContext, WriteContext
from pf2opn.ifmap import default_ifmap
from pf2opn.model import Status
from pf2opn.plan import build_plan
from pf2opn.translators.unbound import ACL_ACTIONS
from pf2opn.validate import validate
from pf2opn.writer import build_output

_BASE = "tests/fixtures/opnsense/base_26_7.xml"


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == section}


# --------------------------------------------------------------------------- #
# General settings (§12.1.13 dns:general)
# --------------------------------------------------------------------------- #


def test_general_status_warnings(unbound_plan) -> None:
    _src, plan = unbound_plan
    item = plan.by_id["dns:general"]
    assert item.status == Status.PARTIAL
    assert item.warnings[0] == "interface(s) opt9 not migrated; dropped from Unbound active_interface"
    assert any("reads ISC DHCP leases" in warning for warning in item.warnings)
    assert any("static_mapping" in warning for warning in item.warnings)
    assert "custom options present (3 lines) not migrated" in item.warnings
    assert item.followups == [
        "re-create custom Unbound options in /usr/local/etc/unbound.opnsense.d/*.conf"
    ]


def test_general_render_maps_fields_and_renames(unbound_plan) -> None:
    src, plan = unbound_plan
    ctx = WriteContext(src, plan)
    placements = registry.get("dns_general").render(plan.by_id["dns:general"], ctx)
    by_path = {p.path: p.text for p in placements}

    assert by_path["OPNsense/unboundplus/general/enabled"] == "1"
    assert by_path["OPNsense/unboundplus/general/port"] == "5353"
    assert by_path["OPNsense/unboundplus/general/active_interface"] == "lan,lo0"
    assert by_path["OPNsense/unboundplus/general/outgoing_interface"] == "wan"
    assert by_path["OPNsense/unboundplus/general/dnssec"] == "1"
    # §12.1.13 knock-on: both forced to 0 with the corrected reasons
    assert by_path["OPNsense/unboundplus/general/regdhcp"] == "0"
    assert by_path["OPNsense/unboundplus/general/regdhcpstatic"] == "0"
    # advanced field renames
    assert by_path["OPNsense/unboundplus/advanced/outgoingnumtcp"] == "10"
    assert by_path["OPNsense/unboundplus/advanced/incomingnumtcp"] == "20"
    assert by_path["OPNsense/unboundplus/advanced/cachemaxttl"] == "86400"
    assert by_path["OPNsense/unboundplus/advanced/cacheminttl"] == "60"
    assert by_path["OPNsense/unboundplus/advanced/qnameminstrict"] == "1"
    assert by_path["OPNsense/unboundplus/forwarding/enabled"] == "0"


def test_dot_upstreams_have_verify_hostnames(unbound_plan) -> None:
    src, plan = unbound_plan
    ctx = WriteContext(src, plan)
    placements = [
        p for p in registry.get("dns_general").render(plan.by_id["dns:general"], ctx)
        if p.path.endswith("/dots")
    ]
    assert len(placements) == 2
    dots = [p.element for p in placements]
    assert dots[0].findtext("type") == "dot"
    assert dots[0].findtext("server") == "1.1.1.1"
    assert dots[0].findtext("port") == "853"
    assert dots[0].findtext("verify") == "one.one.one.one"
    assert dots[1].findtext("verify") == "dns.google"


def test_regdhcp_flags_are_zero_in_output(unbound_plan) -> None:
    src, plan = unbound_plan
    out = build_output(plan, src)
    assert out.findtext("OPNsense/unboundplus/general/regdhcp") == "0"
    assert out.findtext("OPNsense/unboundplus/general/regdhcpstatic") == "0"


# --------------------------------------------------------------------------- #
# Host overrides (§12.1.13 dns_host)
# --------------------------------------------------------------------------- #


def test_repeated_hosts_are_parsed_as_n_entries(unbound_plan) -> None:
    _src, plan = unbound_plan
    items = _items(plan, "dns_host")
    assert set(items) == {
        "dnshost:alderaan.home.asmartass.me",
        "dnshost:tatooine.home.asmartass.me",
        "dnshost:v6host.example.test",
    }


def test_multi_ip_host_yields_one_row_per_ip(unbound_plan) -> None:
    src, plan = unbound_plan
    item = plan.by_id["dnshost:tatooine.home.asmartass.me"]
    assert item.status == Status.PARTIAL
    assert item.warnings == [
        "multiple IPs for tatooine.home.asmartass.me; created one host entry per IP"
    ]
    ctx = WriteContext(src, plan)
    rows = [p.element for p in registry.get("dns_host").render(item, ctx)]
    assert [row.findtext("server") for row in rows] == ["192.168.1.11", "192.168.1.12"]
    assert len({row.get("uuid") for row in rows}) == 2


def test_ipv6_host_uses_aaaa(unbound_plan) -> None:
    src, plan = unbound_plan
    ctx = WriteContext(src, plan)
    row = registry.get("dns_host").render(
        plan.by_id["dnshost:v6host.example.test"], ctx
    )[0].element
    assert row.findtext("rr") == "AAAA"
    assert row.findtext("server") == "2001:db8::5"


def test_inline_aliases_render_top_level_relation(unbound_plan) -> None:
    src, plan = unbound_plan
    ctx = WriteContext(src, plan)
    placements = registry.get("dns_host").render(
        plan.by_id["dnshost:alderaan.home.asmartass.me"], ctx
    )
    host_uuid = placements[0].element.get("uuid")
    aliases = [p.element for p in placements if p.path.endswith("/aliases")]
    assert [a.findtext("hostname") for a in aliases] == ["www", "gw"]
    assert all(a.findtext("host") == host_uuid for a in aliases)
    assert all(a.findtext("domain") == "home.asmartass.me" for a in aliases)


# --------------------------------------------------------------------------- #
# Domain overrides (§12.1.13 dns_domain)
# --------------------------------------------------------------------------- #


def test_domain_overrides_forward_and_dot(unbound_plan) -> None:
    src, plan = unbound_plan
    ctx = WriteContext(src, plan)
    translator = registry.get("dns_domain")

    forward = translator.render(plan.by_id["dnsdom:corp.example.test"], ctx)[0].element
    assert forward.findtext("type") == "forward"
    assert forward.findtext("server") == "10.0.0.53"
    assert forward.find("port").text in (None, "")

    dot = translator.render(plan.by_id["dnsdom:doh.example.test"], ctx)[0].element
    assert dot.findtext("type") == "dot"
    assert dot.findtext("server") == "10.0.0.54"
    assert dot.findtext("port") == "5353"
    assert dot.findtext("verify") == "doh.example.test"


# --------------------------------------------------------------------------- #
# ACLs (§12.1.13 dns_acl)
# --------------------------------------------------------------------------- #


def test_repeated_acls_are_parsed_as_n_entries(unbound_plan) -> None:
    _src, plan = unbound_plan
    items = _items(plan, "dns_acl")
    assert set(items) == {"dnsacl:1", "dnsacl:2"}


def test_acl_action_map_covers_every_action() -> None:
    assert ACL_ACTIONS == {
        "allow": "allow",
        "deny": "deny",
        "refuse": "refuse",
        "allow snoop": "allow_snoop",
        "deny nonlocal": "deny_non_local",
        "refuse nonlocal": "refuse_non_local",
    }


def test_acl_render_networks_and_actions(unbound_plan) -> None:
    src, plan = unbound_plan
    ctx = WriteContext(src, plan)

    first = registry.get("dns_acl").render(plan.by_id["dnsacl:1"], ctx)[0].element
    assert first.findtext("name") == "lan_clients"
    assert first.findtext("action") == "allow"
    assert first.findtext("networks") == "192.168.1.0/24,2001:db8::/32"

    second = registry.get("dns_acl").render(plan.by_id["dnsacl:2"], ctx)[0].element
    assert second.findtext("action") == "refuse_non_local"

    assert plan.by_id["dnsacl:1"].status == Status.PARTIAL
    assert plan.by_id["dnsacl:1"].warnings == ["per-row ACL descriptions dropped"]


def test_acl_do_not_edit_is_not_default_selected(tmp_path) -> None:
    path = tmp_path / "acl.xml"
    path.write_text(
        "<pfsense><version>24.0</version>"
        "<system><hostname>x</hostname><domain>example.test</domain></system>"
        "<unbound><acls><aclid>9</aclid><aclname>managed</aclname>"
        "<aclaction>allow</aclaction><description>DO NOT EDIT - package</description>"
        "<row><acl_network>10.0.0.0</acl_network><mask>8</mask></row></acls></unbound>"
        "</pfsense>",
        encoding="utf-8",
    )
    src = SourceContext.load(path, _BASE)
    plan = build_plan(src, default_ifmap(src, {}))
    assert plan.by_id["dnsacl:9"].default_selected is False


# --------------------------------------------------------------------------- #
# Static-map hosts — fallback, D6 (§12.1.13 dns_static_host)
# --------------------------------------------------------------------------- #


def _static_map_source(tmp_path, *, explicit_host: str, static_host: str):
    path = tmp_path / "static.xml"
    path.write_text(
        "<pfsense><version>24.0</version>"
        "<system><hostname>x</hostname><domain>example.test</domain></system>"
        "<interfaces><lan><if>em1</if><descr>LAN</descr><enable></enable>"
        "<ipaddr>192.168.1.1</ipaddr><subnet>24</subnet></lan></interfaces>"
        "<unbound><regdhcpstatic></regdhcpstatic>"
        f"<hosts><host>{explicit_host}</host><domain>home.example.test</domain>"
        "<ip>192.168.1.10</ip></hosts></unbound>"
        "<dhcpd><lan><enable></enable>"
        "<staticmap><mac>aa:bb:cc:dd:ee:01</mac><ipaddr>192.168.1.20</ipaddr>"
        f"<hostname>{static_host}</hostname><domain>home.example.test</domain></staticmap>"
        "</lan></dhcpd></pfsense>",
        encoding="utf-8",
    )
    src = SourceContext.load(path, _BASE)
    return src, build_plan(src, default_ifmap(src, {"em1": "igc1"}))


def test_static_host_fallback_is_discovered_but_off(tmp_path) -> None:
    _src, plan = _static_map_source(tmp_path, explicit_host="explicit", static_host="other")
    item = plan.by_id["dnsstatic:lan:aa:bb:cc:dd:ee:01"]
    assert item.default_selected is False
    assert item.status == Status.PARTIAL
    assert item.warnings == ["fallback path: enable only if Dnsmasq DNS is disabled"]
    assert item.deps == ["dhcpres:lan:aa:bb:cc:dd:ee:01"]


def test_static_host_dedups_against_explicit_override(tmp_path) -> None:
    _src, plan = _static_map_source(tmp_path, explicit_host="clash", static_host="clash")
    item = plan.by_id["dnsstatic:lan:aa:bb:cc:dd:ee:01"]
    assert item.status == Status.UNSUPPORTED
    assert item.reason == "duplicate of explicit host override"


# --------------------------------------------------------------------------- #
# Validation and goldens
# --------------------------------------------------------------------------- #


def test_unbound_output_validates_without_errors(unbound_plan) -> None:
    src, plan = unbound_plan
    out = build_output(plan, src)
    errors = [finding for finding in validate(out, plan) if finding.level == "error"]
    assert errors == []


def _golden(section, case="unbound.xml"):
    def test(unbound_plan, golden_checker, section_placements, wrap_placements):
        src, plan = unbound_plan
        placements = section_placements(src, plan, section)
        golden_checker(f"{section}/{case}", wrap_placements(placements))

    test.__name__ = f"test_{section}_golden"
    return test


test_dns_general_golden = _golden("dns_general")
test_dns_host_golden = _golden("dns_host")
test_dns_domain_golden = _golden("dns_domain")
test_dns_acl_golden = _golden("dns_acl")
