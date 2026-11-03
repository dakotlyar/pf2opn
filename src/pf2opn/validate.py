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
from .translators.base import sanitize_ifdescr
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
    findings.extend(_check_wireguard(out))
    findings.extend(_check_unbound(out))
    findings.extend(_check_system_dns_gateways(out))
    findings.extend(_check_versions(out))
    findings.extend(_check_sanitized_descriptions(out, plan))
    findings.extend(_check_outbound_mode(out))
    return findings


def _rule_ident(rule: etree._Element, kind: str) -> str:
    """A human-usable label for a filter/NAT rule (never a secret value)."""
    sequence = (rule.findtext("sequence") or "").strip()
    descr = (rule.findtext("description") or rule.findtext("descr") or "").strip()
    bits: list[str] = []
    if sequence:
        bits.append(f"sequence {sequence}")
    if descr:
        bits.append(f'"{descr[:60]}"')
    suffix = f" ({', '.join(bits)})" if bits else ""
    return f"{kind} rule{suffix}"


def _mac_of(host: etree._Element) -> str:
    """The ``hwaddr`` of a Dnsmasq reservation, for actionable messages."""
    return (host.findtext("hwaddr") or "").strip() or "(no MAC)"


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
            findings.append(
                Finding(
                    "error",
                    f"duplicate uuid {uuid} on element <{el.tag}>",
                    _path_of(el),
                )
            )
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


def _check_system_dns_gateways(out: etree._Element) -> list[Finding]:
    """§13 check 12: every ``system/dns{N}gw`` names an output gateway."""
    findings: list[Finding] = []
    gateways = _output_gateway_names(out)
    for n in range(1, 9):
        value = (out.findtext(f"system/dns{n}gw") or "").strip()
        if value and value != "none" and value not in gateways:
            findings.append(
                Finding(
                    "error",
                    f"system/dns{n}gw names unknown gateway {value}",
                    f"system/dns{n}gw",
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
        ident = _rule_ident(rule, "filter")
        raw_interface = (rule.findtext("interface") or "").strip()
        for token in (part.strip() for part in raw_interface.split(",")):
            if token and token not in iface_keys and token not in _VPN_IFACE_TOKENS:
                findings.append(
                    Finding("error", f"{ident}: unknown interface {token}", path)
                )
        for field in ("source_net", "destination_net"):
            value = (rule.findtext(field) or "").strip()
            for token in (part.strip() for part in value.split(",")):
                if token and not _net_token_ok(token, iface_keys, aliases):
                    findings.append(
                        Finding("error", f"{ident}: {field} references unknown token {token}", path)
                    )
        for field in ("source_port", "destination_port"):
            value = (rule.findtext(field) or "").strip()
            if value and not _port_token_ok(value, aliases):
                findings.append(
                    Finding("error", f"{ident}: {field} is not a valid port {value!r}", path)
                )
        gateway = (rule.findtext("gateway") or "").strip()
        if gateway and gateway not in gateways:
            findings.append(
                Finding("error", f"{ident}: unknown gateway {gateway}", path)
            )
    return findings


def _check_interface_tokens(
    value: str, iface_keys: set[str], findings: list[Finding], path: str, ident: str
) -> None:
    for token in (part.strip() for part in value.split(",")):
        if token and token not in iface_keys and token not in _VPN_IFACE_TOKENS:
            findings.append(
                Finding("error", f"{ident}: unknown interface {token}", path)
            )


def _check_net_fields(
    element: etree._Element,
    fields: tuple[str, ...],
    iface_keys: set[str],
    aliases: set[str],
    findings: list[Finding],
    path: str,
    ident: str,
) -> None:
    for field in fields:
        value = (element.findtext(field) or "").strip()
        for token in (part.strip() for part in value.split(",")):
            if token and not _net_token_ok(token, iface_keys, aliases):
                findings.append(
                    Finding(
                        "error",
                        f"{ident}: {field} references unknown token {token}",
                        path,
                    )
                )


def _check_port_fields(
    element: etree._Element,
    fields: tuple[str, ...],
    aliases: set[str],
    findings: list[Finding],
    path: str,
    ident: str,
) -> None:
    for field in fields:
        value = (element.findtext(field) or "").strip()
        if value and not _port_token_ok(value, aliases):
            findings.append(
                Finding("error", f"{ident}: {field} is not a valid port {value!r}", path)
            )


def _check_nat_refs(out: etree._Element) -> list[Finding]:
    """§13 check 5 (NAT part): DNat, source-NAT and 1:1 references resolve."""
    findings: list[Finding] = []
    iface_keys = _output_iface_keys(out)
    aliases = _output_alias_names(out)

    for rule in out.findall("nat/rule"):
        path = _path_of(rule)
        ident = _rule_ident(rule, "port-forward")
        _check_interface_tokens(
            (rule.findtext("interface") or "").strip(), iface_keys, findings, path, ident
        )
        _check_net_fields(
            rule,
            ("source/network", "destination/network", "target"),
            iface_keys,
            aliases,
            findings,
            path,
            ident,
        )
        _check_port_fields(
            rule,
            ("source/port", "destination/port", "local-port"),
            aliases,
            findings,
            path,
            ident,
        )

    for rule in out.findall("OPNsense/Firewall/Filter/snatrules/rule"):
        path = _path_of(rule)
        ident = _rule_ident(rule, "outbound NAT")
        _check_interface_tokens(
            (rule.findtext("interface") or "").strip(), iface_keys, findings, path, ident
        )
        _check_net_fields(
            rule,
            ("source_net", "destination_net", "target"),
            iface_keys,
            aliases,
            findings,
            path,
            ident,
        )
        _check_port_fields(
            rule,
            ("source_port", "destination_port", "target_port"),
            aliases,
            findings,
            path,
            ident,
        )

    for rule in out.findall("OPNsense/Firewall/Filter/onetoone/rule"):
        path = _path_of(rule)
        ident = _rule_ident(rule, "1:1 NAT")
        _check_interface_tokens(
            (rule.findtext("interface") or "").strip(), iface_keys, findings, path, ident
        )
        _check_net_fields(
            rule,
            ("source_net", "destination_net", "external"),
            iface_keys,
            aliases,
            findings,
            path,
            ident,
        )
    return findings


def _dnsmasq_tag_uuids(out: etree._Element) -> set[str]:
    """The uuid attrs of the ``dhcp_tags`` rows (the relation targets, §13 check 8)."""
    return {
        uuid
        for tag in out.findall("dnsmasq/dhcp_tags/dhcp_tags")
        if (uuid := tag.get("uuid"))
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
    tags = _dnsmasq_tag_uuids(out)

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
        # Dnsmasq.php rejects `mode=static` with a non-empty end_addr.
        if (row.findtext("mode") or "").strip() == "static" and (
            row.findtext("end_addr") or ""
        ).strip():
            findings.append(
                Finding(
                    "error",
                    "Dnsmasq DHCP range is mode=static with a non-empty end_addr",
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
    #: reservation IP -> the MAC that claimed it first (a duplicate names both).
    seen: dict[str, str] = {}
    for host in out.findall("dnsmasq/hosts/hosts"):
        mac = _mac_of(host)
        raw = (host.findtext("ip") or "").strip()
        for ip in (part.strip() for part in raw.split(",")):
            if not ip:
                continue
            path = _path_of(host)
            if ip in seen:
                findings.append(
                    Finding(
                        "error",
                        f"duplicate Dnsmasq reservation IP {ip}: "
                        f"MAC {seen[ip]} and MAC {mac} both claim it",
                        path,
                    )
                )
                continue
            seen[ip] = mac
            try:
                address = ipaddress.ip_address(ip)
            except ValueError:
                findings.append(
                    Finding(
                        "error",
                        f"Dnsmasq reservation for MAC {mac} has invalid IP {ip!r}",
                        path,
                    )
                )
                continue
            if networks and not any(address in network for network in networks):
                iface_list = ", ".join(
                    sorted({str(network) for network in networks})
                )
                findings.append(
                    Finding(
                        "error",
                        f"Dnsmasq reservation IP {ip} (MAC {mac}) is outside every "
                        f"interface subnet ({iface_list})",
                        path,
                    )
                )
    return findings


def _check_wireguard(out: etree._Element) -> list[Finding]:
    """§13 check 9: server peers resolve; instances and names are unique."""
    findings: list[Finding] = []
    servers = out.findall("OPNsense/wireguard/server/servers/server")
    clients = out.findall("OPNsense/wireguard/client/clients/client")
    client_uuids = {client.get("uuid") for client in clients}

    instances: set[str] = set()
    names: set[str] = set()
    for server in servers:
        path = _path_of(server)
        name = (server.findtext("name") or "").strip()
        instance = (server.findtext("instance") or "").strip()
        if instance in instances:
            findings.append(
                Finding(
                    "error",
                    f"duplicate WireGuard instance {instance} (server {name!r})",
                    path,
                )
            )
        instances.add(instance)
        if name in names:
            findings.append(
                Finding("error", f"duplicate WireGuard server name {name!r}", path)
            )
        names.add(name)
        for token in (part.strip() for part in (server.findtext("peers") or "").split(",")):
            if token and token not in client_uuids:
                findings.append(
                    Finding(
                        "error",
                        f"WireGuard server {name!r} references unknown peer {token}",
                        path,
                    )
                )

    client_names: set[str] = set()
    for client in clients:
        name = (client.findtext("name") or "").strip()
        if name in client_names:
            findings.append(
                Finding(
                    "error", f"duplicate WireGuard peer name {name!r}", _path_of(client)
                )
            )
        client_names.add(name)
    return findings


def _check_unbound(out: etree._Element) -> list[Finding]:
    """§13 check 10: every Unbound alias ``host`` relation resolves."""
    findings: list[Finding] = []
    host_uuids = {
        host.get("uuid") for host in out.findall("OPNsense/unboundplus/hosts/host")
    }
    for alias in out.findall("OPNsense/unboundplus/aliases/alias"):
        host = (alias.findtext("host") or "").strip()
        if host and host not in host_uuids:
            hostname = (alias.findtext("hostname") or "").strip()
            domain = (alias.findtext("domain") or "").strip()
            label = f"{hostname}.{domain}".strip(".") or "(unnamed alias)"
            findings.append(
                Finding(
                    "error",
                    f"Unbound alias {label} references unknown host {host}",
                    _path_of(alias),
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


def _check_sanitized_descriptions(out: etree._Element, plan: Plan) -> list[Finding]:
    """§13 warning: an included interface description changed by sanitizing.

    OPNsense's ``IfDescField`` rejects anything outside ``[A-Za-z0-9_]``, so the
    translator rewrites the pfSense description; the user is told which interface
    and what the original was.
    """
    findings: list[Finding] = []
    for iface in _interface_elements(out):
        target = plan.ifmap.targets.get(iface.tag)
        if target is None or not target.descr:
            continue
        if sanitize_ifdescr(target.descr) != target.descr:
            findings.append(
                Finding(
                    "warning",
                    f'interface {iface.tag} description "{target.descr}" was sanitized '
                    "for OPNsense",
                    f"interfaces/{iface.tag}",
                )
            )
    return findings


def _check_outbound_mode(out: etree._Element) -> list[Finding]:
    """§13 warning: ``nat/outbound/mode`` automatic while source-NAT rules exist."""
    mode = (out.findtext("nat/outbound/mode") or "").strip()
    rules = out.findall("OPNsense/Firewall/Filter/snatrules/rule")
    if mode == "automatic" and rules:
        return [
            Finding(
                "warning",
                f"nat/outbound/mode is 'automatic' while {len(rules)} source NAT rule(s) "
                "are present; those rules are inactive",
                "nat/outbound/mode",
            )
        ]
    return []
