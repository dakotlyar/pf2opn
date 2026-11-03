"""Structural validation of the output (design §13).

M2 implements checks 1 (well-formed round trip) and 2 (unique uuids). Later
milestones add the referential checks; the warning about a base model version
mismatch (§10.3) is emitted here because it needs the output tree.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from lxml import etree

from .model import Plan
from .translators.aliases import ALIAS_NAME_RE
from .versions import MODEL_VERSIONS

_VLAN_DEVICE = re.compile(r"^vlan0\d+$")
_WG_DEVICE = re.compile(r"^wg(\d+)$")

#: Interface tokens the firewall accepts that are not output interface keys.
_VPN_IFACE_TOKENS = frozenset({"wireguard", "openvpn", "enc0"})


@dataclass
class Finding:
    level: str  # "error" | "warning"
    message: str
    path: str | None = None


def _path_of(el: etree._Element) -> str:
    parts: list[str] = []
    node: etree._Element | None = el
    while node is not None and isinstance(node.tag, str):
        parts.append(node.tag)
        node = node.getparent()
    return "/".join(reversed(parts))


def validate(out: etree._Element, plan: Plan) -> list[Finding]:
    """Return every finding for *out*; ``error`` findings block writing."""
    findings: list[Finding] = []
    findings.extend(_check_well_formed(out))
    findings.extend(_check_unique_uuids(out))
    findings.extend(_check_interfaces(out))
    findings.extend(_check_vlan_parents(out, plan))
    findings.extend(_check_gateways(out))
    findings.extend(_check_routes(out))
    findings.extend(_check_aliases(out))
    findings.extend(_check_filter_refs(out))
    findings.extend(_check_nat_refs(out))
    findings.extend(_check_dnsmasq(out))
    findings.extend(_check_versions(out))
    return findings


def _is_ip_or_cidr(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return True


def _net_token_ok(token: str, iface_keys: set[str], aliases: set[str]) -> bool:
    """One ``*_net``/network token from §13 check 5."""
    token = token.strip()
    if not token or token in {"any", "(self)"}:
        return True
    base = token[:-2] if token.endswith("ip") else token
    if base in iface_keys or base == "lo0":
        return True
    if token in aliases:
        return True
    return _is_ip_or_cidr(token)


def _port_token_ok(token: str, aliases: set[str]) -> bool:
    """One port token from §13 check 5: numeric, a range, or a port alias."""
    token = token.strip()
    if not token:
        return True
    if re.fullmatch(r"\d+", token):
        return True
    if re.fullmatch(r"\d+[-:]\d+", token):
        return True
    return token in aliases


def _interface_elements(out: etree._Element) -> list[etree._Element]:
    return [
        iface
        for iface in out.findall("interfaces/*")
        if isinstance(iface.tag, str)
    ]


def _check_interfaces(out: etree._Element) -> list[Finding]:
    """§13 check 3: interface devices present, unique, VLANs/WG defined."""
    findings: list[Finding] = []
    vlanifs = {(vlan.findtext("vlanif") or "").strip() for vlan in out.findall("vlans/vlan")}
    instances = {
        (server.findtext("instance") or "").strip()
        for server in out.findall("OPNsense/wireguard/server/servers/server")
    }
    seen: set[str] = set()
    for iface in _interface_elements(out):
        value = (iface.findtext("if") or "").strip()
        if not value:
            findings.append(
                Finding("error", f"interface {iface.tag} has no device", _path_of(iface))
            )
            continue
        if value in seen:
            findings.append(
                Finding(
                    "error",
                    f"device {value} is used by more than one interface",
                    _path_of(iface),
                )
            )
            continue
        seen.add(value)
        wg = _WG_DEVICE.match(value)
        if _VLAN_DEVICE.match(value) and value not in vlanifs:
            findings.append(
                Finding(
                    "error",
                    f"interface {iface.tag} uses {value}, which is not a defined VLAN",
                    _path_of(iface),
                )
            )
        elif wg and wg.group(1) not in instances:
            findings.append(
                Finding(
                    "error",
                    f"interface {iface.tag} uses {value}, which is not a WireGuard server",
                    _path_of(iface),
                )
            )
    return findings


def _check_vlan_parents(out: etree._Element, plan: Plan) -> list[Finding]:
    """§13 check 4: every VLAN parent is an assigned interface or a target NIC."""
    findings: list[Finding] = []
    assigned = {(iface.findtext("if") or "").strip() for iface in _interface_elements(out)}
    target_nics = set(plan.ifmap.nic_map.values())
    target_nics |= set(plan.ifmap.vlan_names.values())
    target_nics |= set(plan.ifmap.wg_names.values())
    target_nics.discard("")
    for vlan in out.findall("vlans/vlan"):
        parent = (vlan.findtext("if") or "").strip()
        if parent and parent not in assigned and parent not in target_nics:
            findings.append(
                Finding(
                    "error",
                    f"VLAN {vlan.findtext('vlanif')} parent {parent} is not assigned",
                    _path_of(vlan),
                )
            )
    return findings


def _check_well_formed(out: etree._Element) -> list[Finding]:
    try:
        data = etree.tostring(out)
        reparsed = etree.fromstring(data)
    except etree.XMLSyntaxError as exc:
        return [Finding("error", f"output is not well-formed XML: {exc}")]
    if reparsed.tag != "opnsense":
        return [Finding("error", f"root element is <{reparsed.tag}>, expected <opnsense>")]
    return []


def _check_unique_uuids(out: etree._Element) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for el in out.iter():
        if not isinstance(el.tag, str):
            continue
        uuid = el.get("uuid")
        if uuid is None:
            continue
        if uuid in seen:
            findings.append(Finding("error", f"duplicate uuid {uuid}", _path_of(el)))
        else:
            seen.add(uuid)
    return findings


def _check_gateways(out: etree._Element) -> list[Finding]:
    """§13 check 6: gateway names are unique and their interfaces are assigned."""
    findings: list[Finding] = []
    interfaces = {iface.tag for iface in _interface_elements(out)}
    seen: set[str] = set()
    for gateway in out.findall("OPNsense/Gateways/gateway_item"):
        name = (gateway.findtext("name") or "").strip()
        if name in seen:
            findings.append(
                Finding("error", f"duplicate gateway name {name}", _path_of(gateway))
            )
        seen.add(name)
        interface = (gateway.findtext("interface") or "").strip()
        if interface and interface not in interfaces:
            findings.append(
                Finding(
                    "error",
                    f"gateway {name} uses interface {interface}, which is not assigned",
                    _path_of(gateway),
                )
            )
    return findings


def _check_routes(out: etree._Element) -> list[Finding]:
    """§13 check 7: every static route gateway exists."""
    findings: list[Finding] = []
    gateways = {
        (gateway.findtext("name") or "").strip()
        for gateway in out.findall("OPNsense/Gateways/gateway_item")
    }
    for route in out.findall("staticroutes/route"):
        gateway = (route.findtext("gateway") or "").strip()
        if gateway and gateway not in gateways:
            findings.append(
                Finding(
                    "error",
                    f"route {route.findtext('network')} uses unknown gateway {gateway}",
                    _path_of(route),
                )
            )
    return findings


def _check_aliases(out: etree._Element) -> list[Finding]:
    """§13 check 11: alias names are unique and nested references exist."""
    findings: list[Finding] = []
    aliases = out.findall("OPNsense/Firewall/Alias/aliases/alias")
    names: set[str] = set()
    for alias in aliases:
        name = (alias.findtext("name") or "").strip()
        if name in names:
            findings.append(Finding("error", f"duplicate alias name {name}", _path_of(alias)))
        names.add(name)
    for alias in aliases:
        if (alias.findtext("type") or "").strip() in {"url", "urltable"}:
            continue
        name = (alias.findtext("name") or "").strip()
        for entry in (alias.findtext("content") or "").splitlines():
            entry = entry.strip()
            if not entry or entry in names:
                continue
            if ALIAS_NAME_RE.match(entry):
                findings.append(
                    Finding(
                        "error",
                        f"alias {name} references unknown alias {entry}",
                        _path_of(alias),
                    )
                )
    return findings


def _output_iface_keys(out: etree._Element) -> set[str]:
    return {iface.tag for iface in _interface_elements(out)}


def _output_alias_names(out: etree._Element) -> set[str]:
    return {
        (alias.findtext("name") or "").strip()
        for alias in out.findall("OPNsense/Firewall/Alias/aliases/alias")
    }


def _output_gateway_names(out: etree._Element) -> set[str]:
    return {
        (gateway.findtext("name") or "").strip()
        for gateway in out.findall("OPNsense/Gateways/gateway_item")
    }


def _check_filter_refs(out: etree._Element) -> list[Finding]:
    """§13 check 5 (filter part): interfaces, nets, ports and gateways resolve."""
    findings: list[Finding] = []
    iface_keys = _output_iface_keys(out)
    aliases = _output_alias_names(out)
    gateways = _output_gateway_names(out)
    for rule in out.findall("OPNsense/Firewall/Filter/rules/rule"):
        path = _path_of(rule)
        raw_interface = (rule.findtext("interface") or "").strip()
        for token in (part.strip() for part in raw_interface.split(",")):
            if token and token not in iface_keys and token not in _VPN_IFACE_TOKENS:
                findings.append(
                    Finding("error", f"filter rule references unknown interface {token}", path)
                )
        for field in ("source_net", "destination_net"):
            value = (rule.findtext(field) or "").strip()
            for token in (part.strip() for part in value.split(",")):
                if token and not _net_token_ok(token, iface_keys, aliases):
                    findings.append(
                        Finding(
                            "error",
                            f"filter rule {field} references unknown token {token}",
                            path,
                        )
                    )
        for field in ("source_port", "destination_port"):
            value = (rule.findtext(field) or "").strip()
            if value and not _port_token_ok(value, aliases):
                findings.append(
                    Finding("error", f"filter rule {field} is not a valid port {value!r}", path)
                )
        gateway = (rule.findtext("gateway") or "").strip()
        if gateway and gateway not in gateways:
            findings.append(
                Finding("error", f"filter rule uses unknown gateway {gateway}", path)
            )
    return findings


def _check_interface_tokens(
    value: str, iface_keys: set[str], findings: list[Finding], path: str
) -> None:
    for token in (part.strip() for part in value.split(",")):
        if token and token not in iface_keys and token not in _VPN_IFACE_TOKENS:
            findings.append(
                Finding("error", f"rule references unknown interface {token}", path)
            )


def _check_net_fields(
    element: etree._Element,
    fields: tuple[str, ...],
    iface_keys: set[str],
    aliases: set[str],
    findings: list[Finding],
    path: str,
) -> None:
    for field in fields:
        value = (element.findtext(field) or "").strip()
        for token in (part.strip() for part in value.split(",")):
            if token and not _net_token_ok(token, iface_keys, aliases):
                findings.append(
                    Finding(
                        "error",
                        f"rule {field} references unknown token {token}",
                        path,
                    )
                )


def _check_port_fields(
    element: etree._Element,
    fields: tuple[str, ...],
    aliases: set[str],
    findings: list[Finding],
    path: str,
) -> None:
    for field in fields:
        value = (element.findtext(field) or "").strip()
        if value and not _port_token_ok(value, aliases):
            findings.append(
                Finding("error", f"rule {field} is not a valid port {value!r}", path)
            )


def _check_nat_refs(out: etree._Element) -> list[Finding]:
    """§13 check 5 (NAT part): DNat, source-NAT and 1:1 references resolve."""
    findings: list[Finding] = []
    iface_keys = _output_iface_keys(out)
    aliases = _output_alias_names(out)

    for rule in out.findall("nat/rule"):
        path = _path_of(rule)
        _check_interface_tokens(
            (rule.findtext("interface") or "").strip(), iface_keys, findings, path
        )
        _check_net_fields(
            rule,
            ("source/network", "destination/network", "target"),
            iface_keys,
            aliases,
            findings,
            path,
        )
        _check_port_fields(
            rule, ("source/port", "destination/port", "local-port"), aliases, findings, path
        )

    for rule in out.findall("OPNsense/Firewall/Filter/snatrules/rule"):
        path = _path_of(rule)
        _check_interface_tokens(
            (rule.findtext("interface") or "").strip(), iface_keys, findings, path
        )
        _check_net_fields(
            rule,
            ("source_net", "destination_net", "target"),
            iface_keys,
            aliases,
            findings,
            path,
        )
        _check_port_fields(
            rule,
            ("source_port", "destination_port", "target_port"),
            aliases,
            findings,
            path,
        )

    for rule in out.findall("OPNsense/Firewall/Filter/onetoone/rule"):
        path = _path_of(rule)
        _check_interface_tokens(
            (rule.findtext("interface") or "").strip(), iface_keys, findings, path
        )
        _check_net_fields(
            rule,
            ("source_net", "destination_net", "external"),
            iface_keys,
            aliases,
            findings,
            path,
        )
    return findings


def _dnsmasq_tags(out: etree._Element) -> set[str]:
    return {
        (tag.findtext("tag") or "").strip()
        for tag in out.findall("dnsmasq/dhcp_tags/dhcp_tags")
        if (tag.findtext("tag") or "").strip()
    }


def _interface_networks(
    out: etree._Element,
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for iface in _interface_elements(out):
        ipaddr = (iface.findtext("ipaddr") or "").strip()
        mask = (iface.findtext("subnet") or "").strip()
        if not ipaddr or not mask:
            continue
        try:
            networks.append(ipaddress.ip_network(f"{ipaddr}/{mask}", strict=False))
        except ValueError:
            continue
    return networks


def _check_dnsmasq(out: etree._Element) -> list[Finding]:
    """§13 check 8: Dnsmasq range interfaces, reservation IPs and tag references."""
    findings: list[Finding] = []
    iface_keys = _output_iface_keys(out)
    tags = _dnsmasq_tags(out)

    for row in out.findall("dnsmasq/dhcp_ranges/dhcp_ranges"):
        interface = (row.findtext("interface") or "").strip()
        if interface and interface not in iface_keys:
            findings.append(
                Finding(
                    "error",
                    f"Dnsmasq DHCP range uses unknown interface {interface}",
                    _path_of(row),
                )
            )

    for field, kind in (
        ("dnsmasq/dhcp_options/dhcp_options", "option"),
        ("dnsmasq/dhcp_boot/dhcp_boot", "boot"),
    ):
        for row in out.findall(field):
            tag = (row.findtext("tag") or "").strip()
            if tag and tag not in tags:
                findings.append(
                    Finding(
                        "error",
                        f"Dnsmasq DHCP {kind} references unknown tag {tag}",
                        _path_of(row),
                    )
                )

    for host in out.findall("dnsmasq/hosts/hosts"):
        tag = (host.findtext("set_tag") or "").strip()
        if tag and tag not in tags:
            findings.append(
                Finding(
                    "error",
                    f"Dnsmasq reservation references unknown tag {tag}",
                    _path_of(host),
                )
            )

    for option in out.findall("dnsmasq/dhcp_options/dhcp_options"):
        option_type = (option.findtext("type") or "").strip()
        if option_type != "set":
            findings.append(
                Finding(
                    "error",
                    f"Dnsmasq DHCP option type {option_type!r} is not 'set'",
                    _path_of(option),
                )
            )

    networks = _interface_networks(out)
    seen: set[str] = set()
    for host in out.findall("dnsmasq/hosts/hosts"):
        raw = (host.findtext("ip") or "").strip()
        for ip in (part.strip() for part in raw.split(",")):
            if not ip:
                continue
            path = _path_of(host)
            if ip in seen:
                findings.append(Finding("error", f"duplicate Dnsmasq reservation IP {ip}", path))
                continue
            seen.add(ip)
            try:
                address = ipaddress.ip_address(ip)
            except ValueError:
                findings.append(
                    Finding("error", f"Dnsmasq reservation IP {ip!r} is invalid", path)
                )
                continue
            if networks and not any(address in network for network in networks):
                findings.append(
                    Finding(
                        "error",
                        f"Dnsmasq reservation IP {ip} is outside every interface subnet",
                        path,
                    )
                )
    return findings


def _check_versions(out: etree._Element) -> list[Finding]:
    findings: list[Finding] = []
    for path, expected in MODEL_VERSIONS.items():
        matches = out.xpath(path)
        if not matches:
            continue
        actual = matches[0].get("version")
        if actual is not None and actual != expected:
            findings.append(
                Finding(
                    "warning",
                    f"base model {path} is version {actual}, pf2opn was written for {expected}",
                    path,
                )
            )
    return findings