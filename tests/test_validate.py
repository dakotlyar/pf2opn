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
        '<opnsense><dnsmasq><dhcp_ranges><dhcp_ranges uuid="a">'
        "<interface>lan</interface><start_addr>192.168.1.0</start_addr>"
        "<end_addr>192.168.1.10</end_addr><mode>static</mode>"
        "</dhcp_ranges></dhcp_ranges></dnsmasq></opnsense>"
    )
    findings = _errors(validate(out, plan))
    assert any("mode=static with a non-empty end_addr" in f.message for f in findings)


def test_dnsmasq_set_tag_must_be_a_row_uuid(minimal_plan) -> None:
    """§13 check 8: tag relations compare against the dhcp_tags row uuids, not the text."""
    _src, plan = minimal_plan
    out = _el(
        '<opnsense><dnsmasq><dhcp_tags><dhcp_tags uuid="tag-uuid">'
        "<tag>mytag</tag></dhcp_tags></dhcp_tags>"
        '<hosts><hosts uuid="h"><ip>192.168.1.10</ip>'
        "<set_tag>mytag</set_tag></hosts></hosts></dnsmasq></opnsense>"
    )
    findings = _errors(validate(out, plan))
    assert any("unknown tag mytag" in f.message for f in findings)


def test_dnsmasq_set_tag_as_row_uuid_passes(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        '<opnsense><dnsmasq><dhcp_tags><dhcp_tags uuid="tag-uuid">'
        "<tag>mytag</tag></dhcp_tags></dhcp_tags>"
        '<hosts><hosts uuid="h"><ip>192.168.1.10</ip>'
        "<set_tag>tag-uuid</set_tag></hosts></hosts></dnsmasq></opnsense>"
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