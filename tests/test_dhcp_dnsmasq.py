"""Tests for the Dnsmasq DHCPv4 translators (design §12.1.11, M7)."""

from __future__ import annotations

from lxml import etree

from pf2opn import registry
from pf2opn.context import SourceContext, WriteContext
from pf2opn.ifmap import default_ifmap
from pf2opn.model import Mode, Status
from pf2opn.plan import build_plan
from pf2opn.validate import validate
from pf2opn.writer import build_output

_BASE = "tests/fixtures/opnsense/base_26_7.xml"


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == section}


def _subnet(*, ipaddr: str, subnet: str, body: str, hostname: str = "x") -> str:
    return (
        "<pfsense><version>24.0</version>"
        f"<system><hostname>{hostname}</hostname><domain>example.test</domain></system>"
        "<interfaces><lan><if>em1</if><descr>LAN</descr><enable></enable>"
        f"<ipaddr>{ipaddr}</ipaddr><subnet>{subnet}</subnet></lan></interfaces>"
        f"<dhcpd><lan><enable></enable>{body}</lan></dhcpd></pfsense>"
    )


# --------------------------------------------------------------------------- #
# Ranges (§12.1.11)
# --------------------------------------------------------------------------- #


def test_range_statuses_and_deps(dhcp_plan) -> None:
    _src, plan = dhcp_plan
    items = _items(plan, "dhcp_range")

    assert items["dhcp:lan"].status == Status.PARTIAL
    assert items["dhcp:lan"].warnings == [
        "domain search list is not RFC 1035-encoded; verify option 119 by hand",
        "max lease time not represented",
    ]
    assert items["dhcp:lan"].deps == ["iface:lan"]
    assert items["dhcp:opt1"].status == Status.PARTIAL
    assert items["dhcp:opt1"].warnings == [
        "pfSense deny-unknown has no Dnsmasq equivalent; the dynamic range is still served",
        "dnsmasq HA uses split pools + dhcp/reply_delay, not a failover peer",
    ]


def test_range_render_replaces_base_range_not_duplicates(dhcp_plan) -> None:
    src, plan = dhcp_plan
    translator = registry.get("dhcp_range")
    ctx = WriteContext(src, plan)
    placements = translator.render(plan.by_id["dhcp:lan"], ctx)

    assert placements[0].mode == Mode.REMOVE
    assert placements[0].path == "dnsmasq/dhcp_ranges[interface='lan']"

    rows = [
        p.element
        for p in placements
        if p.mode == Mode.APPEND and p.element is not None and p.element.tag == "dhcp_ranges"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row.tag == "dhcp_ranges"
    assert row.findtext("interface") == "lan"
    assert row.findtext("start_addr") == "192.168.1.100"
    assert row.findtext("end_addr") == "192.168.1.199"
    assert row.findtext("domain_type") == "interface"
    assert row.findtext("domain") == "lan.example.test"
    assert row.findtext("lease_time") == "7200"
    assert row.find("mode") is None
    assert row.findtext("description") == "pf2opn: LAN"


def test_denyunknown_keeps_dynamic_range_without_static_mode(dhcp_plan) -> None:
    """`denyunknown` alone never sets `mode=static`; the range is still served (§12.1.11)."""
    src, plan = dhcp_plan
    translator = registry.get("dhcp_range")
    ctx = WriteContext(src, plan)
    rows = [
        p.element
        for p in translator.render(plan.by_id["dhcp:opt1"], ctx)
        if p.mode == Mode.APPEND and p.element is not None and p.element.tag == "dhcp_ranges"
    ]
    assert [row.findtext("start_addr") for row in rows] == ["10.0.10.100", "10.0.10.200"]
    assert all(row.find("mode") is None for row in rows)
    # pool rows carry no domain
    assert rows[1].find("domain") is None
    assert (
        "pfSense deny-unknown has no Dnsmasq equivalent; the dynamic range is still served"
        in plan.by_id["dhcp:opt1"].warnings
    )


def test_static_only_subnet_uses_network_address(tmp_path) -> None:
    path = tmp_path / "static_only.xml"
    path.write_text(
        _subnet(
            ipaddr="192.168.5.1",
            subnet="24",
            body="<staticmap><mac>aa:bb:cc:dd:ee:01</mac>"
            "<ipaddr>192.168.5.10</ipaddr><hostname>only</hostname></staticmap>",
        ),
        encoding="utf-8",
    )
    src = SourceContext.load(path, _BASE)
    plan = build_plan(src, default_ifmap(src, {"em1": "igc1"}))
    assert plan.by_id["dhcp:lan"].status == Status.OK

    ctx = WriteContext(src, plan)
    rows = [
        p.element
        for p in registry.get("dhcp_range").render(plan.by_id["dhcp:lan"], ctx)
        if p.mode == Mode.APPEND and p.element is not None and p.element.tag == "dhcp_ranges"
    ]
    assert rows[0].findtext("start_addr") == "192.168.5.0"
    assert rows[0].findtext("mode") == "static"
    assert rows[0].find("end_addr").text in (None, "")


def test_per_interface_options_render_as_dhcp_options_and_boot(dhcp_plan) -> None:
    src, plan = dhcp_plan
    translator = registry.get("dhcp_range")
    ctx = WriteContext(src, plan)
    placements = translator.render(plan.by_id["dhcp:lan"], ctx)

    options = {
        p.element.findtext("option"): p.element
        for p in placements
        if p.element is not None and p.element.tag == "dhcp_options"
    }
    assert options["3"].findtext("value") == "192.168.1.1"
    assert options["6"].findtext("value") == "192.168.1.1,9.9.9.9"
    assert options["15"].findtext("value") == "lan.example.test"
    assert options["119"].findtext("value") == "lan.example.test,example.test"
    assert options["42"].findtext("value") == "192.168.1.1"
    assert all(o.findtext("type") == "set" for o in options.values())

    opt1 = translator.render(plan.by_id["dhcp:opt1"], ctx)
    boot = [p.element for p in opt1 if p.element is not None and p.element.tag == "dhcp_boot"]
    assert len(boot) == 1
    assert boot[0].findtext("filename") == "pxelinux.0"
    assert boot[0].findtext("servername") == "10.0.10.5"
    assert boot[0].findtext("address") == "10.0.10.5"


# --------------------------------------------------------------------------- #
# Static reservations (§12.1.11)
# --------------------------------------------------------------------------- #


def test_static_statuses_deps_and_mac_lowercasing(dhcp_plan) -> None:
    _src, plan = dhcp_plan
    items = _items(plan, "dhcp_static")

    missing = items["dhcpres:lan:aa:bb:cc:dd:ee:01"]
    assert missing.status == Status.UNSUPPORTED
    assert missing.reason == "Dnsmasq reservation needs an IP address"

    underscore = items["dhcpres:lan:8c:ce:4e:4b:9e:52"]
    assert underscore.status == Status.OK
    assert underscore.deps == ["dhcp:lan"]
    assert underscore.data["mac"] == "8c:ce:4e:4b:9e:52"


def test_static_render_lowercases_mac_and_keeps_ip(dhcp_plan) -> None:
    src, plan = dhcp_plan
    ctx = WriteContext(src, plan)
    host = registry.get("dhcp_static").render(
        plan.by_id["dhcpres:lan:8c:ce:4e:4b:9e:52"], ctx
    )[0].element
    assert host.findtext("hwaddr") == "8c:ce:4e:4b:9e:52"
    assert host.findtext("ip") == "192.168.1.11"
    assert host.findtext("host") == "host_underscore"
    assert host.find("set_tag") is None


def test_per_host_options_create_tag_and_tagged_rows(dhcp_plan) -> None:
    src, plan = dhcp_plan
    ctx = WriteContext(src, plan)
    placements = registry.get("dhcp_static").render(
        plan.by_id["dhcpres:lan:8c:ce:4e:4b:9e:53"], ctx
    )
    paths = [p.path for p in placements]
    assert all(path == "dnsmasq" for path in paths)
    tag_row = next(p.element for p in placements if p.element.tag == "dhcp_tags")
    assert tag_row.findtext("tag") == "pf2opn_lan_8c_ce_4e_4b_9e_53"
    # set_tag/tag are relations to the dhcp_tags row uuid, never the tag text (§12.1.11).
    tag_uuid = tag_row.get("uuid")

    host = next(p.element for p in placements if p.element.tag == "hosts")
    assert host.findtext("set_tag") == tag_uuid
    assert host.findtext("set_tag") != "pf2opn_lan_8c_ce_4e_4b_9e_53"
    assert host.findtext("domain") == "perhost.example.test"

    tagged_options = [
        p.element for p in placements if p.element.tag == "dhcp_options"
    ]
    assert {o.findtext("option") for o in tagged_options} == {"3", "6", "15"}
    assert all(o.findtext("tag") == tag_uuid for o in tagged_options)
    assert all(o.find("interface") is None for o in tagged_options)


# --------------------------------------------------------------------------- #
# Finalize: section settings + Unbound glue (§12.1.11)
# --------------------------------------------------------------------------- #


def test_finalize_sets_enable_fqdn_and_interface_list(dhcp_plan) -> None:
    src, plan = dhcp_plan
    out = build_output(plan, src)
    assert out.findtext("dnsmasq/enable") == "1"
    assert out.findtext("dnsmasq/dhcp/fqdn") == "1"
    assert out.findtext("dnsmasq/interface") == "lan,opt1"


def test_base_default_range_is_gone_after_build(dhcp_plan) -> None:
    src, plan = dhcp_plan
    out = build_output(plan, src)
    rows = out.findall("dnsmasq/dhcp_ranges")
    lan_rows = [row for row in rows if row.findtext("interface") == "lan"]
    assert len(lan_rows) == 1
    # the base's default 192.168.1.100-199 row was replaced by ours, not kept
    assert lan_rows[0].get("uuid") != "33333333-3333-4333-8333-333333333333"


def test_dnsmasq_entries_are_direct_siblings(dhcp_plan) -> None:
    """§2.1/§12.1.11: ArrayField rows are direct children of <dnsmasq>, no wrapper."""
    src, plan = dhcp_plan
    out = build_output(plan, src)
    dnsmasq = out.find("dnsmasq")

    ranges = out.findall("dnsmasq/dhcp_ranges")
    hosts = out.findall("dnsmasq/hosts")
    options = out.findall("dnsmasq/dhcp_options")
    tags = out.findall("dnsmasq/dhcp_tags")
    assert ranges and hosts and options and tags
    # not nested under a same-named wrapper element
    for field in ("dhcp_ranges", "hosts", "dhcp_options", "dhcp_tags"):
        assert dnsmasq.find(f"{field}/{field}") is None
    for row in ranges + hosts + options + tags:
        assert row.getparent() is dnsmasq


def test_unbound_query_forward_is_emitted_and_ports_do_not_collide(dhcp_plan) -> None:
    src, plan = dhcp_plan
    out = build_output(plan, src)

    forwards = [
        dot
        for dot in out.findall("OPNsense/unboundplus/dots/dot")
        if dot.findtext("type") == "forward" and dot.findtext("server") == "127.0.0.1"
    ]
    assert len(forwards) == 1
    assert forwards[0].findtext("domain") == "lan.example.test"
    assert forwards[0].findtext("port") == "53053"
    assert forwards[0].findtext("description") == "pf2opn: local DHCP domain via Dnsmasq"

    unbound_port = out.findtext("OPNsense/unboundplus/general/port")
    assert out.findtext("dnsmasq/port") != unbound_port


def test_port_is_moved_off_unbound_port_when_it_collides(dhcp_plan) -> None:
    src, plan = dhcp_plan
    src.base.find("dnsmasq/port").text = "53"
    out = build_output(plan, src)
    assert out.findtext("dnsmasq/port") == "53053"


def test_interface_list_is_unioned_with_base(dhcp_plan) -> None:
    src, plan = dhcp_plan
    etree.SubElement(src.base.find("dnsmasq"), "interface").text = "opt9"
    out = build_output(plan, src)
    assert out.findtext("dnsmasq/interface") == "lan,opt1,opt9"


def test_unbound_disabled_uses_port_53_and_no_forwards(dhcp_plan) -> None:
    src, plan = dhcp_plan
    src.base.find("OPNsense/unboundplus/general/enabled").text = "0"
    out = build_output(plan, src)
    assert out.findtext("dnsmasq/port") == "53"
    assert out.findall("OPNsense/unboundplus/dots/dot") == []


def test_kea_base_adds_a_warning(dhcp_plan) -> None:
    src, plan = dhcp_plan
    kea = etree.SubElement(
        etree.SubElement(src.base.find("OPNsense"), "Kea"), "dhcp4"
    )
    etree.SubElement(etree.SubElement(kea, "general"), "enabled").text = "1"
    build_output(plan, src)
    assert "dnsmasq will not start while Kea holds the DHCP ports" in plan.by_id["dhcp:lan"].warnings


# --------------------------------------------------------------------------- #
# Validation and goldens
# --------------------------------------------------------------------------- #


def test_dhcp_output_validates_without_errors(dhcp_plan) -> None:
    src, plan = dhcp_plan
    out = build_output(plan, src)
    errors = [finding for finding in validate(out, plan) if finding.level == "error"]
    assert errors == []


def test_dhcp_range_golden(
    dhcp_plan, golden_checker, section_placements, wrap_placements
) -> None:
    src, plan = dhcp_plan
    placements = section_placements(src, plan, "dhcp_range")
    golden_checker("dhcp_range/dhcp.xml", wrap_placements(placements))


def test_dhcp_static_golden(
    dhcp_plan, golden_checker, section_placements, wrap_placements
) -> None:
    src, plan = dhcp_plan
    placements = section_placements(src, plan, "dhcp_static")
    golden_checker("dhcp_static/dhcp.xml", wrap_placements(placements))
