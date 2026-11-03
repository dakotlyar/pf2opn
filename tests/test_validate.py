"""Tests for validation checks 1-2 (design §13)."""

from __future__ import annotations

from lxml import etree

from pf2opn.validate import validate
from pf2opn.writer import build_output


def _el(xml: str) -> etree._Element:
    return etree.fromstring(xml)


def _errors(findings):
    return [finding for finding in findings if finding.level == "error"]


def test_valid_output_has_no_errors(minimal_plan) -> None:
    src, plan = minimal_plan
    out = build_output(plan, src)
    assert _errors(validate(out, plan)) == []


def test_wrong_root_element_is_an_error(minimal_plan) -> None:
    _src, plan = minimal_plan
    findings = validate(_el("<notopnsense/>"), plan)
    assert any(finding.level == "error" and "opnsense" in finding.message for finding in findings)


def test_duplicate_uuid_is_an_error(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el('<opnsense><a uuid="abc"/><b uuid="abc"/></opnsense>')
    findings = _errors(validate(out, plan))
    assert len(findings) == 1
    assert "duplicate uuid abc" in findings[0].message


def test_unique_uuids_pass(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el('<opnsense><a uuid="one"/><b uuid="two"/></opnsense>')
    assert _errors(validate(out, plan)) == []


def test_gateway_on_missing_interface_is_an_error(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        '<opnsense><interfaces><wan><if>igc0</if></wan></interfaces>'
        "<OPNsense><Gateways><gateway_item><name>G</name>"
        "<interface>opt9</interface></gateway_item></Gateways></OPNsense></opnsense>"
    )
    findings = _errors(validate(out, plan))
    assert any("opt9" in finding.message for finding in findings)


def test_route_with_unknown_gateway_is_an_error(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><staticroutes><route><network>10.0.0.0/8</network>"
        "<gateway>GHOST</gateway></route></staticroutes></opnsense>"
    )
    findings = _errors(validate(out, plan))
    assert any("GHOST" in finding.message for finding in findings)


def test_duplicate_alias_name_is_an_error(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><Firewall><Alias><aliases>"
        "<alias><name>X</name><type>host</type><content>1.2.3.4</content></alias>"
        "<alias><name>X</name><type>host</type><content>5.6.7.8</content></alias>"
        "</aliases></Alias></Firewall></OPNsense></opnsense>"
    )
    findings = _errors(validate(out, plan))
    assert any("duplicate alias name X" in finding.message for finding in findings)


def test_alias_with_unknown_nested_alias_is_an_error(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><Firewall><Alias><aliases>"
        "<alias><name>X</name><type>host</type><content>GHOST</content></alias>"
        "</aliases></Alias></Firewall></OPNsense></opnsense>"
    )
    findings = _errors(validate(out, plan))
    assert any("references unknown alias GHOST" in finding.message for finding in findings)


def test_dnsmasq_static_range_with_end_addr_is_an_error(minimal_plan) -> None:
    """§13 check 8: `mode=static` must not coexist with a non-empty `end_addr`."""
    _src, plan = minimal_plan
    out = _el(
        '<opnsense><dnsmasq><dhcp_ranges uuid="a">'
        "<interface>lan</interface><start_addr>192.168.1.0</start_addr>"
        "<end_addr>192.168.1.10</end_addr><mode>static</mode>"
        "</dhcp_ranges></dnsmasq></opnsense>"
    )
    findings = _errors(validate(out, plan))
    assert any("mode=static with a non-empty end_addr" in f.message for f in findings)


def test_dnsmasq_set_tag_must_be_a_row_uuid(minimal_plan) -> None:
    """§13 check 8: tag relations compare against the dhcp_tags row uuids, not the text."""
    _src, plan = minimal_plan
    out = _el(
        '<opnsense><dnsmasq>'
        '<dhcp_tags uuid="tag-uuid"><tag>mytag</tag></dhcp_tags>'
        '<hosts uuid="h"><ip>192.168.1.10</ip>'
        "<set_tag>mytag</set_tag></hosts></dnsmasq></opnsense>"
    )
    findings = _errors(validate(out, plan))
    assert any("unknown tag mytag" in f.message for f in findings)


def test_dnsmasq_set_tag_as_row_uuid_passes(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        '<opnsense><dnsmasq>'
        '<dhcp_tags uuid="tag-uuid"><tag>mytag</tag></dhcp_tags>'
        '<hosts uuid="h"><ip>192.168.1.10</ip>'
        "<set_tag>tag-uuid</set_tag></hosts></dnsmasq></opnsense>"
    )
    assert _errors(validate(out, plan)) == []


def test_system_dns_gateway_unknown_is_an_error(minimal_plan) -> None:
    """§13 check 12: system/dns{N}gw must name an output gateway."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><interfaces><wan><if>igc0</if></wan></interfaces>"
        "<system><dns1gw>GHOST</dns1gw></system>"
        "<OPNsense><Gateways><gateway_item><name>G</name>"
        "<interface>wan</interface></gateway_item></Gateways></OPNsense></opnsense>"
    )
    findings = _errors(validate(out, plan))
    assert any("GHOST" in f.message for f in findings)


def test_system_dns_gateway_none_is_ignored(minimal_plan) -> None:
    """pfSense uses `none` as "no gateway"; it must not trip check 12 (§12.1.1)."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><system><dns1gw>none</dns1gw></system></opnsense>"
    )
    assert _errors(validate(out, plan)) == []


def test_system_dns_gateway_known_passes(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><interfaces><wan><if>igc0</if></wan></interfaces>"
        "<system><dns1gw>G</dns1gw></system>"
        "<OPNsense><Gateways><gateway_item><name>G</name>"
        "<interface>wan</interface></gateway_item></Gateways></OPNsense></opnsense>"
    )
    assert _errors(validate(out, plan)) == []


def test_m4_outputs_validate_without_errors(aliases_plan, misc_plan) -> None:
    for src, plan in (aliases_plan, misc_plan):
        out = build_output(plan, src)
        assert _errors(validate(out, plan)) == []


def test_version_mismatch_is_a_warning(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        '<opnsense><OPNsense><Firewall><Filter version="1.0.0"/></Firewall></OPNsense></opnsense>'
    )
    findings = validate(out, plan)
    warnings = [finding for finding in findings if finding.level == "warning"]
    assert any("1.0.0" in warning.message and "1.0.6" in warning.message for warning in warnings)
    assert _errors(findings) == []


# --------------------------------------------------------------------------- #
# §13 checks 3, 4, 9, 10 (direct coverage)
# --------------------------------------------------------------------------- #


def test_interface_without_device_is_an_error(minimal_plan) -> None:
    """§13 check 3: every interfaces/*/if must be non-empty."""
    _src, plan = minimal_plan
    out = _el("<opnsense><interfaces><wan><if></if></wan></interfaces></opnsense>")
    assert any("wan has no device" in f.message for f in _errors(validate(out, plan)))


def test_duplicate_interface_device_is_an_error(minimal_plan) -> None:
    """§13 check 3: devices are unique."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><interfaces><wan><if>igc0</if></wan>"
        "<lan><if>igc0</if></lan></interfaces></opnsense>"
    )
    assert any(
        "igc0 is used by more than one interface" in f.message
        for f in _errors(validate(out, plan))
    )


def test_undefined_vlan_device_is_an_error(minimal_plan) -> None:
    """§13 check 3: a ``vlan0N`` device must exist in ``vlans/vlan/vlanif``."""
    _src, plan = minimal_plan
    out = _el("<opnsense><interfaces><opt1><if>vlan099</if></opt1></interfaces></opnsense>")
    assert any(
        "vlan099, which is not a defined VLAN" in f.message
        for f in _errors(validate(out, plan))
    )


def test_undefined_wireguard_device_is_an_error(minimal_plan) -> None:
    """§13 check 3: a ``wgN`` device must match a server instance."""
    _src, plan = minimal_plan
    out = _el("<opnsense><interfaces><opt1><if>wg9</if></opt1></interfaces></opnsense>")
    assert any(
        "wg9, which is not a WireGuard server" in f.message
        for f in _errors(validate(out, plan))
    )


def test_vlan_parent_not_assigned_is_an_error(minimal_plan) -> None:
    """§13 check 4: every VLAN parent is an assigned interface or a target NIC."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><interfaces><wan><if>igc0</if></wan></interfaces>"
        "<vlans><vlan><vlanif>vlan01</vlanif><if>igcX</if></vlan></vlans></opnsense>"
    )
    assert any(
        "VLAN vlan01 parent igcX is not assigned" in f.message
        for f in _errors(validate(out, plan))
    )


def test_vlan_parent_assigned_passes(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><interfaces><wan><if>igc0</if></wan></interfaces>"
        "<vlans><vlan><vlanif>vlan01</vlanif><if>igc0</if></vlan></vlans></opnsense>"
    )
    assert _errors(validate(out, plan)) == []


def test_wireguard_server_unknown_peer_is_an_error(minimal_plan) -> None:
    """§13 check 9: every server ``peers`` uuid exists in clients."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><wireguard><server><servers>"
        "<server><instance>1</instance><name>a</name>"
        "<peers>ghost-uuid</peers></server>"
        "</servers></server></wireguard></OPNsense></opnsense>"
    )
    assert any(
        "references unknown peer ghost-uuid" in f.message
        for f in _errors(validate(out, plan))
    )


def test_unbound_alias_unknown_host_is_an_error(minimal_plan) -> None:
    """§13 check 10: every Unbound alias ``host`` relation resolves."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><unboundplus><aliases>"
        "<alias><hostname>al</hostname><domain>example.test</domain>"
        "<host>missing-uuid</host></alias>"
        "</aliases></unboundplus></OPNsense></opnsense>"
    )
    assert any(
        "al.example.test references unknown host missing-uuid" in f.message
        for f in _errors(validate(out, plan))
    )


# --------------------------------------------------------------------------- #
# M14: actionable messages (§13)
# --------------------------------------------------------------------------- #


def test_filter_rule_error_names_the_rule(minimal_plan) -> None:
    """§13 check 5: a bad filter reference names the rule, not just the token."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><Firewall><Filter><rules>"
        "<rule><sequence>30</sequence><description>bad rule</description>"
        "<interface>GHOST</interface></rule>"
        "</rules></Filter></Firewall></OPNsense></opnsense>"
    )
    errors = _errors(validate(out, plan))
    assert any(
        "filter rule" in f.message and "sequence 30" in f.message and "GHOST" in f.message
        for f in errors
    )


def test_nat_rule_error_names_the_rule(minimal_plan) -> None:
    """§13 check 5: an outbound-NAT reference names the rule's sequence."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><Firewall><Filter><snatrules>"
        "<rule><sequence>20</sequence><description>pool option rule</description>"
        "<interface>GHOST</interface></rule>"
        "</snatrules></Filter></Firewall></OPNsense></opnsense>"
    )
    errors = _errors(validate(out, plan))
    assert any(
        "outbound NAT rule" in f.message and "sequence 20" in f.message and "GHOST" in f.message
        for f in errors
    )


def test_duplicate_reservation_names_ip_and_both_macs(minimal_plan) -> None:
    """§13 check 8: the real-config conflict must name the IP and both MACs."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><interfaces><lan><if>igc1</if>"
        "<ipaddr>192.168.1.1</ipaddr><subnet>24</subnet></lan></interfaces>"
        "<dnsmasq>"
        '<hosts uuid="h1"><ip>192.168.1.10</ip>'
        "<hwaddr>aa:bb:cc:dd:ee:01</hwaddr></hosts>"
        '<hosts uuid="h2"><ip>192.168.1.10</ip>'
        "<hwaddr>aa:bb:cc:dd:ee:02</hwaddr></hosts>"
        "</dnsmasq></opnsense>"
    )
    errors = _errors(validate(out, plan))
    message = next(f.message for f in errors if "duplicate" in f.message)
    assert "192.168.1.10" in message
    assert "aa:bb:cc:dd:ee:01" in message
    assert "aa:bb:cc:dd:ee:02" in message


def test_reservation_outside_subnet_names_ip_and_mac(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><interfaces><lan><if>igc1</if>"
        "<ipaddr>192.168.1.1</ipaddr><subnet>24</subnet></lan></interfaces>"
        "<dnsmasq>"
        '<hosts uuid="h1"><ip>10.9.9.9</ip>'
        "<hwaddr>aa:bb:cc:dd:ee:03</hwaddr></hosts>"
        "</dnsmasq></opnsense>"
    )
    errors = _errors(validate(out, plan))
    assert any(
        "10.9.9.9" in f.message and "aa:bb:cc:dd:ee:03" in f.message for f in errors
    )


def test_sanitized_interface_description_is_a_warning(minimal_plan) -> None:
    """§13 warning: a description changed by sanitizing surfaces as a Finding."""
    _src, plan = minimal_plan
    plan.ifmap.targets["lan"].descr = "My LAN"
    out = _el("<opnsense><interfaces><lan><if>igc1</if></lan></interfaces></opnsense>")
    findings = validate(out, plan)
    warnings = [f for f in findings if f.level == "warning"]
    assert any(
        "lan" in f.message and "My LAN" in f.message and "sanitized" in f.message
        for f in warnings
    )


def test_outbound_automatic_with_rules_is_a_warning(minimal_plan) -> None:
    """§13 warning: automatic outbound mode with source-NAT rules present."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><nat><outbound><mode>automatic</mode></outbound></nat>"
        "<OPNsense><Firewall><Filter><snatrules><rule><sequence>10</sequence></rule>"
        "</snatrules></Filter></Firewall></OPNsense></opnsense>"
    )
    warnings = [f for f in validate(out, plan) if f.level == "warning"]
    assert any("automatic" in f.message and "inactive" in f.message for f in warnings)


def test_outbound_advanced_with_rules_is_not_warned(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><nat><outbound><mode>advanced</mode></outbound></nat>"
        "<OPNsense><Firewall><Filter><snatrules><rule><sequence>10</sequence></rule>"
        "</snatrules></Filter></Firewall></OPNsense></opnsense>"
    )
    warnings = [f for f in validate(out, plan) if f.level == "warning"]
    assert not any("inactive" in f.message for f in warnings)


def test_nat_ipv4_address_in_inet_rule_is_ok(minimal_plan) -> None:
    """§13 check 13: a colon-free address matches ipprotocol inet."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><Firewall><Filter><snatrules><rule>"
        "<sequence>10</sequence><ipprotocol>inet</ipprotocol>"
        "<source_net>192.168.1.0/24</source_net></rule>"
        "</snatrules></Filter></Firewall></OPNsense></opnsense>"
    )
    assert _errors(validate(out, plan)) == []


def test_nat_ipv6_address_in_inet6_rule_is_ok(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><Firewall><Filter><snatrules><rule>"
        "<sequence>10</sequence><ipprotocol>inet6</ipprotocol>"
        "<source_net>::1/128</source_net></rule>"
        "</snatrules></Filter></Firewall></OPNsense></opnsense>"
    )
    assert _errors(validate(out, plan)) == []


def test_nat_ipv6_source_in_inet_rule_is_an_error(minimal_plan) -> None:
    """§13 check 13: `nat ... inet from ::1/128` must be flagged before pfctl."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><Firewall><Filter><snatrules><rule>"
        "<sequence>10</sequence><description>loopback</description>"
        "<ipprotocol>inet</ipprotocol>"
        "<source_net>::1/128</source_net></rule>"
        "</snatrules></Filter></Firewall></OPNsense></opnsense>"
    )
    errors = _errors(validate(out, plan))
    assert any(
        f.path and f.path.endswith("snatrules/rule")
        and "::1/128" in f.message
        and "inet6" in f.message
        for f in errors
    )


def test_nat_port_forward_address_endpoint_family_is_checked(minimal_plan) -> None:
    """§13 check 13: an IPv6 ``<address>`` endpoint in an inet port forward errors.

    The DNat endpoint stores its address in either ``network`` or ``address``;
    reading ``network`` alone would silently miss this.
    """
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><nat><rule><sequence>10</sequence>"
        "<ipprotocol>inet</ipprotocol>"
        "<source><network>any</network></source>"
        "<destination><address>::1</address></destination>"
        "<target>192.168.1.10</target></rule></nat></opnsense>"
    )
    errors = _errors(validate(out, plan))
    assert any(
        "destination/address" in f.message and "::1" in f.message and "inet6" in f.message
        for f in errors
    )


def test_nat_port_forward_address_endpoint_matching_family_is_ok(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><nat><rule><sequence>10</sequence>"
        "<ipprotocol>inet</ipprotocol>"
        "<source><network>any</network></source>"
        "<destination><address>192.168.1.10</address></destination>"
        "<target>192.168.1.10</target></rule></nat></opnsense>"
    )
    assert _errors(validate(out, plan)) == []


def test_onetoone_is_a_check_13_no_op(minimal_plan) -> None:
    """§13 check 13: the 1:1 model has no ``ipprotocol``; it must not be checked."""
    _src, plan = minimal_plan
    out = _el(
        "<opnsense><OPNsense><Firewall><Filter><onetoone><rule>"
        "<sequence>10</sequence><ipprotocol>inet6</ipprotocol>"
        "<source_net>192.168.1.10/32</source_net>"
        "<destination_net>any</destination_net>"
        "<external>203.0.113.10/32</external></rule>"
        "</onetoone></Filter></Firewall></OPNsense></opnsense>"
    )
    assert _errors(validate(out, plan)) == []


def test_hybrid_outbound_maps_to_advanced_warning(minimal_plan) -> None:
    """§13 warning: pfSense hybrid -> OPNsense advanced is surfaced."""
    src, plan = minimal_plan
    plan.items.append(
        type(
            "Item",
            (),
            {
                "section": "nat_out",
                "id": "snat:mode",
                "data": {"raw_mode": "hybrid", "mode": "advanced"},
            },
        )()
    )
    out = _el(
        "<opnsense><nat><outbound><mode>advanced</mode></outbound></nat>"
        "<OPNsense><Firewall><Filter><snatrules><rule><sequence>10</sequence></rule>"
        "</snatrules></Filter></Firewall></OPNsense></opnsense>"
    )
    warnings = [f for f in validate(out, plan) if f.level == "warning"]
    assert any("hybrid" in f.message and "advanced" in f.message for f in warnings)