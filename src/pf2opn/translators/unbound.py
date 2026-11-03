"""Unbound translators (design §12.1.13).

``dns_general``     ``pf:unbound``                -> ``OPNsense/unboundplus/{general,advanced,dots,forwarding}``
``dns_host``        repeated ``pf:unbound/hosts`` -> ``OPNsense/unboundplus/hosts/host`` (+ aliases)
``dns_static_host`` ``pf:dhcpd/*/staticmap``      -> ``OPNsense/unboundplus/hosts/host`` (fallback, D6)
``dns_domain``      repeated ``pf:unbound/domainoverrides`` -> ``OPNsense/unboundplus/dots/dot``
``dns_acl``         repeated ``pf:unbound/acls`` -> ``OPNsense/unboundplus/acls/acl``

pfSense repeats the *plural* container once per entry (``<hosts>`` x N,
``<acls>`` x N), so discovery iterates the elements themselves, never a single
``hosts/host`` container.
"""

from __future__ import annotations

import ipaddress

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, opn_bool, pf_desc, pf_list, pf_text, sanitize_name, sub

#: pfSense ACL action -> OPNsense ``acls/acl/action`` (§12.1.13).
ACL_ACTIONS: dict[str, str] = {
    "allow": "allow",
    "deny": "deny",
    "refuse": "refuse",
    "allow snoop": "allow_snoop",
    "deny nonlocal": "deny_non_local",
    "refuse nonlocal": "refuse_non_local",
}

#: pf ``advanced`` field -> OPNsense ``advanced`` field (presence -> 1/0).
_ADVANCED_BOOLS: tuple[tuple[str, str], ...] = (
    ("hideidentity", "hideidentity"),
    ("hideversion", "hideversion"),
    ("dnssecstripped", "dnssecstripped"),
    ("prefetch", "prefetch"),
    ("prefetchkey", "prefetchkey"),
    ("qname-minimisation-strict", "qnameminstrict"),
    ("serveexpired", "serveexpired"),
)

#: pf ``advanced`` field -> OPNsense ``advanced`` field (copy when non-empty).
_ADVANCED_VALUES: tuple[tuple[str, str], ...] = (
    ("msgcachesize", "msgcachesize"),
    ("outgoing_num_tcp", "outgoingnumtcp"),
    ("incoming_num_tcp", "incomingnumtcp"),
    ("cache_max_ttl", "cachemaxttl"),
    ("cache_min_ttl", "cacheminttl"),
)

_REGDHCP_WARNING = (
    "Unbound's DHCP registration reads ISC DHCP leases "
    "(`/var/dhcpd/var/db/dhcpd.leases`); this box has none. Dnsmasq registers "
    "lease names \u2014 reach them via the local-domain query-forwarding the DHCP "
    "translator writes."
)
_REGDHCPSTATIC_WARNING = (
    "Unbound's static registration reads the ISC/Kea static_mapping plugin hook, "
    "which Dnsmasq does not provide; the static maps live in the Dnsmasq hosts table."
)


def _is_ipv6(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).version == 6
    except ValueError:
        return False


def _self_interface_addresses(
    src: SourceContext,
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """The source box's own interface addresses (§12.1.13 loopback/self skip)."""
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for iface in src.pf_ifaces.values():
        for raw in (iface.ipaddr, iface.ipaddrv6):
            raw = (raw or "").strip()
            if not raw:
                continue
            try:
                addresses.add(ipaddress.ip_address(raw))
            except ValueError:
                continue
    return addresses


def _is_loopback_or_self(
    value: str, self_addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address]
) -> bool:
    """True for a loopback/unspecified upstream or one of the box's own IPs.

    pfSense lists its own resolver (`127.0.0.1` and/or an interface IP) among
    ``system/dnsserver`` because it runs Unbound itself; rendering that as an
    OPNsense upstream points at nothing that listens (§12.1.13).
    """
    value = (value or "").strip()
    if not value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified or address in self_addresses


def _map_interface_list(raw: str, ifmap: IfaceMap) -> tuple[str, list[str]]:
    """Map a pfSense Unbound interface list; returns (value, dropped keys)."""
    value = (raw or "").strip()
    if not value or value == "all":
        return "", []
    kept: list[str] = []
    dropped: list[str] = []
    for token in (part.strip() for part in value.split(",")):
        if not token:
            continue
        if token in {"lo0", "localhost"}:
            kept.append("lo0")
        elif token in ifmap.included_keys():
            kept.append(token)
        else:
            dropped.append(token)
    return ",".join(kept), dropped


def _host_identity(el: etree._Element | None) -> str | None:
    """Host identity key shared by pf ``host`` and OPNsense ``hostname`` shapes."""
    if el is None:
        return None
    host = pf_text(el, "host") or pf_text(el, "hostname")
    domain = pf_text(el, "domain")
    if not host:
        return None
    return f"{host.lower()}|{domain.lower()}"


# --------------------------------------------------------------------------- #
# General settings (§12.1.13 `dns:general`)
# --------------------------------------------------------------------------- #


@register
class DnsGeneral(Translator):
    section = "dns_general"
    title = "Unbound resolver settings"
    tier = 1
    order = 120
    base_policy = "none"
    container = None

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        unbound = src.pf.find("unbound")
        if unbound is None:
            return []
        warnings: list[str] = []
        followups: list[str] = []

        for pf_tag, field in (
            ("active_interface", "active_interface"),
            ("outgoing_interface", "outgoing_interface"),
        ):
            _value, dropped = _map_interface_list(pf_text(unbound, pf_tag), ifmap)
            if dropped:
                warnings.append(
                    f"interface(s) {', '.join(dropped)} not migrated; dropped from Unbound {field}"
                )

        if unbound.find("regdhcp") is not None:
            warnings.append(_REGDHCP_WARNING)
        if unbound.find("regdhcpstatic") is not None:
            warnings.append(_REGDHCPSTATIC_WARNING)

        custom = pf_text(unbound, "custom_options")
        if custom:
            lines = len([line for line in custom.splitlines() if line.strip()])
            warnings.append(f"custom options present ({lines} lines) not migrated")
            followups.append(
                "re-create custom Unbound options in /usr/local/etc/unbound.opnsense.d/*.conf"
            )

        if (
            unbound.find("enablessl") is not None
            or pf_text(unbound, "sslcertref")
            or pf_text(unbound, "tlsport")
        ):
            warnings.append("DNS-over-TLS listener not migrated")

        if unbound.find("forwarding") is not None:
            servers = pf_list(src.pf, "system/dnsserver")
            self_addresses = _self_interface_addresses(src)
            skipped = [
                server for server in servers if _is_loopback_or_self(server, self_addresses)
            ]
            if skipped:
                warnings.append(
                    f"{len(skipped)} loopback/self Unbound upstream(s) skipped"
                )
            if servers and len(skipped) == len(servers):
                followups.append(
                    "all Unbound upstreams are loopback/self; set the real upstream "
                    "by hand under Services \u25b8 Unbound \u25b8 Query Forwarding / DNS-over-TLS"
                )

        return [
            Item(
                "dns:general",
                self.section,
                "Unbound resolver settings",
                unbound,
                Status.PARTIAL if warnings else Status.OK,
                warnings=warnings,
                followups=followups,
            )
        ]

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        unbound = item.source
        if unbound is None:
            return []
        base = "OPNsense/unboundplus"
        placements: list[Placement] = [
            Placement(
                f"{base}/general/enabled",
                Mode.SET,
                text=opn_bool(unbound.find("enable") is not None),
            ),
            Placement(f"{base}/general/port", Mode.SET, text=pf_text(unbound, "port") or "53"),
        ]
        ifmap = ctx.plan.ifmap
        for pf_tag, field in (
            ("active_interface", "active_interface"),
            ("outgoing_interface", "outgoing_interface"),
        ):
            value, _dropped = _map_interface_list(pf_text(unbound, pf_tag), ifmap)
            placements.append(Placement(f"{base}/general/{field}", Mode.SET, text=value))

        placements.append(
            Placement(
                f"{base}/general/dnssec",
                Mode.SET,
                text=opn_bool(unbound.find("dnssec") is not None),
            )
        )
        placements.append(Placement(f"{base}/general/regdhcp", Mode.SET, text="0"))
        placements.append(Placement(f"{base}/general/regdhcpstatic", Mode.SET, text="0"))
        placements.append(
            Placement(
                f"{base}/general/local_zone_type",
                Mode.SET,
                text=pf_text(unbound, "system_domain_local_zone_type") or "transparent",
            )
        )

        for pf_tag, opn_tag in _ADVANCED_BOOLS:
            placements.append(
                Placement(
                    f"{base}/advanced/{opn_tag}",
                    Mode.SET,
                    text=opn_bool(unbound.find(pf_tag) is not None),
                )
            )
        for pf_tag, opn_tag in _ADVANCED_VALUES:
            value = pf_text(unbound, pf_tag)
            if value:
                placements.append(Placement(f"{base}/advanced/{opn_tag}", Mode.SET, text=value))

        if unbound.find("forwarding") is not None:
            servers = pf_list(ctx.src.pf, "system/dnsserver")
            self_addresses = _self_interface_addresses(ctx.src)
            if unbound.find("forward_tls_upstream") is not None:
                placements.append(Placement(f"{base}/forwarding/enabled", Mode.SET, text="0"))
                for index, server in enumerate(servers):
                    if _is_loopback_or_self(server, self_addresses):
                        continue
                    placements.append(
                        Placement(
                            f"{base}/dots",
                            Mode.APPEND,
                            element=_dot_upstream(ctx, index, server),
                            item_id=item.id,
                        )
                    )
            else:
                # A plain forward to a loopback/self address points at nothing;
                # when every upstream is skipped, leave forwarding off so Unbound
                # falls back to recursive resolution (§12.1.13).
                enabled = (
                    "0"
                    if servers
                    and all(_is_loopback_or_self(server, self_addresses) for server in servers)
                    else "1"
                )
                placements.append(Placement(f"{base}/forwarding/enabled", Mode.SET, text=enabled))
        return placements


def _dot_upstream(ctx: WriteContext, index: int, server: str) -> etree._Element:
    dot = E("dot", uuid=ctx.uuid("dns:general", f"dot{index}"))
    sub(dot, "enabled", "1")
    sub(dot, "type", "dot")
    sub(dot, "domain", "")
    sub(dot, "server", server)
    sub(dot, "port", "853")
    sub(dot, "verify", pf_text(ctx.src.pf, f"system/dns{index + 1}host"))
    sub(dot, "forward_tcp_upstream", "0")
    sub(dot, "forward_first", "0")
    sub(dot, "description", "pf2opn DoT upstream")
    return dot


# --------------------------------------------------------------------------- #
# Host overrides (§12.1.13 `dns_host`)
# --------------------------------------------------------------------------- #


@register
class DnsHost(Translator):
    section = "dns_host"
    title = "Unbound host overrides"
    tier = 1
    order = 121
    base_policy = "merge"
    container = "OPNsense/unboundplus/hosts"

    def identity(self, item: Item) -> str | None:
        return _host_identity(item.source)

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        unbound = src.pf.find("unbound")
        if unbound is None:
            return []
        items: list[Item] = []
        for index, host in enumerate(unbound.findall("hosts")):
            name = pf_text(host, "host")
            domain = pf_text(host, "domain")
            if not name and not domain:
                continue
            ips = [part.strip() for part in pf_text(host, "ip").split(",") if part.strip()]
            warnings: list[str] = []
            if len(ips) > 1:
                warnings.append(
                    f"multiple IPs for {name}.{domain}; created one host entry per IP"
                )
            items.append(
                Item(
                    f"dnshost:{name}.{domain}",
                    self.section,
                    f"{name}.{domain}" or f"Host override #{index}",
                    host,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    data={"host": name, "domain": domain, "ips": ips},
                )
            )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        host = item.source
        if host is None:
            return []
        base = "OPNsense/unboundplus"
        placements: list[Placement] = []
        first_uuid = ""
        for index, ip in enumerate(item.data["ips"]):
            suffix = "" if index == 0 else f"ip{index + 1}"
            host_uuid = ctx.uuid(item.id, suffix)
            if not first_uuid:
                first_uuid = host_uuid
            placements.append(
                Placement(
                    f"{base}/hosts",
                    Mode.APPEND,
                    element=_host_element(
                        host_uuid,
                        hostname=item.data["host"],
                        domain=item.data["domain"],
                        ip=ip,
                        description=pf_desc(host),
                    ),
                    item_id=item.id,
                )
            )
        for index, alias in enumerate(host.findall("aliases/item")):
            name = (alias.text or "").strip()
            if not name:
                continue
            placements.append(
                Placement(
                    f"{base}/aliases",
                    Mode.APPEND,
                    element=_alias_element(
                        ctx.uuid(item.id, f"alias{index}"),
                        host_uuid=first_uuid,
                        hostname=name,
                        domain=item.data["domain"],
                        description=pf_desc(host),
                    ),
                    item_id=item.id,
                )
            )
        return placements


def _host_element(
    uuid: str, *, hostname: str, domain: str, ip: str, description: str
) -> etree._Element:
    el = E("host", uuid=uuid)
    sub(el, "enabled", "1")
    sub(el, "hostname", hostname)
    sub(el, "domain", domain)
    sub(el, "rr", "AAAA" if _is_ipv6(ip) else "A")
    sub(el, "mxprio", "")
    sub(el, "mx", "")
    sub(el, "ttl", "")
    sub(el, "server", ip)
    sub(el, "txtdata", "")
    sub(el, "addptr", "1")
    sub(el, "description", description)
    return el


def _alias_element(
    uuid: str, *, host_uuid: str, hostname: str, domain: str, description: str
) -> etree._Element:
    el = E("alias", uuid=uuid)
    sub(el, "enabled", "1")
    sub(el, "host", host_uuid)
    sub(el, "hostname", hostname)
    sub(el, "domain", domain)
    sub(el, "description", description)
    return el


# --------------------------------------------------------------------------- #
# Static-map hosts — fallback, D6 (§12.1.13 `dns_static_host`)
# --------------------------------------------------------------------------- #


@register
class DnsStaticHost(Translator):
    section = "dns_static_host"
    title = "Unbound static-map hosts (fallback)"
    tier = 1
    order = 122
    base_policy = "merge"
    container = "OPNsense/unboundplus/hosts"

    def identity(self, item: Item) -> str | None:
        return _host_identity(item.source)

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        unbound = src.pf.find("unbound")
        explicit = {
            _host_identity(host)
            for host in (unbound.findall("hosts") if unbound is not None else [])
        }
        system_domain = pf_text(src.pf, "system/domain")

        candidates: list[tuple[str, etree._Element, etree._Element]] = []
        for subnet in src.pf.findall("dhcpd/*"):
            if not isinstance(subnet.tag, str) or subnet.tag == "dhcpddata":
                continue
            for staticmap in subnet.findall("staticmap"):
                if pf_text(staticmap, "hostname") and pf_text(staticmap, "ipaddr"):
                    candidates.append((subnet.tag, subnet, staticmap))

        discovered = unbound is not None and unbound.find("regdhcpstatic") is not None
        if not discovered and not candidates:
            return []

        items: list[Item] = []
        for key, subnet, staticmap in candidates:
            name = pf_text(staticmap, "hostname")
            mac = pf_text(staticmap, "mac").lower()
            item_id = f"dnsstatic:{key}:{mac}"
            domain = (
                pf_text(staticmap, "domain")
                or pf_text(subnet, "domain")
                or system_domain
            )
            deps = [f"dhcpres:{key}:{mac}"]
            if f"{name.lower()}|{domain.lower()}" in explicit:
                items.append(
                    Item(
                        item_id,
                        self.section,
                        f"{name}.{domain}",
                        staticmap,
                        Status.UNSUPPORTED,
                        reason="duplicate of explicit host override",
                        deps=deps,
                        default_selected=False,
                    )
                )
                continue
            items.append(
                Item(
                    item_id,
                    self.section,
                    f"{name}.{domain}",
                    staticmap,
                    Status.PARTIAL,
                    warnings=["fallback path: enable only if Dnsmasq DNS is disabled"],
                    deps=deps,
                    default_selected=False,
                    data={"host": name, "domain": domain, "ipaddr": pf_text(staticmap, "ipaddr")},
                )
            )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        if item.source is None:
            return []
        return [
            Placement(
                "OPNsense/unboundplus/hosts",
                Mode.APPEND,
                element=_host_element(
                    ctx.uuid(item.id),
                    hostname=item.data["host"],
                    domain=item.data["domain"],
                    ip=item.data["ipaddr"],
                    description="from DHCP static map",
                ),
                item_id=item.id,
            )
        ]


# --------------------------------------------------------------------------- #
# Domain overrides (§12.1.13 `dns_domain`)
# --------------------------------------------------------------------------- #


@register
class DnsDomain(Translator):
    section = "dns_domain"
    title = "Unbound domain overrides"
    tier = 1
    order = 123
    base_policy = "none"
    container = None

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        unbound = src.pf.find("unbound")
        if unbound is None:
            return []
        items: list[Item] = []
        self_addresses = _self_interface_addresses(src)
        for index, override in enumerate(unbound.findall("domainoverrides")):
            domain = pf_text(override, "domain")
            if not domain:
                continue
            server, _, _port = pf_text(override, "ip").partition("@")
            if _is_loopback_or_self(server, self_addresses):
                # Skipped like a loopback/self system upstream (§12.1.13); it
                # emits no dot, so it is a note rather than a migrated object.
                items.append(
                    Item(
                        f"dnsdom:{domain}",
                        self.section,
                        domain,
                        override,
                        Status.PARTIAL,
                        warnings=[
                            f"loopback/self target {server} skipped; set the real "
                            "upstream by hand under Services \u25b8 Unbound "
                            "\u25b8 Query Forwarding"
                        ],
                        emits=False,
                    )
                )
                continue
            items.append(
                Item(
                    f"dnsdom:{domain}",
                    self.section,
                    domain,
                    override,
                    Status.OK,
                )
            )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        override = item.source
        if override is None:
            return []
        ip = pf_text(override, "ip")
        server, _, port = ip.partition("@")
        if _is_loopback_or_self(server, _self_interface_addresses(ctx.src)):
            return []
        tls_hostname = pf_text(override, "tls_hostname")
        dot = E("dot", uuid=ctx.uuid(item.id))
        sub(dot, "enabled", "1")
        sub(dot, "type", "dot" if tls_hostname else "forward")
        sub(dot, "domain", pf_text(override, "domain"))
        sub(dot, "server", server)
        sub(dot, "port", port)
        sub(dot, "verify", tls_hostname)
        sub(dot, "forward_tcp_upstream", "0")
        sub(dot, "forward_first", "0")
        sub(dot, "description", pf_desc(override))
        return [
            Placement("OPNsense/unboundplus/dots", Mode.APPEND, element=dot, item_id=item.id)
        ]


# --------------------------------------------------------------------------- #
# ACLs (§12.1.13 `dns_acl`)
# --------------------------------------------------------------------------- #


@register
class DnsAcl(Translator):
    section = "dns_acl"
    title = "Unbound ACLs"
    tier = 1
    order = 124
    base_policy = "none"
    container = None

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        unbound = src.pf.find("unbound")
        if unbound is None:
            return []
        items: list[Item] = []
        for index, acl in enumerate(unbound.findall("acls")):
            aclname = pf_text(acl, "aclname")
            aclid = pf_text(acl, "aclid") or str(index)
            action = pf_text(acl, "aclaction")
            item_id = f"dnsacl:{aclid}"
            label = aclname or f"ACL #{index}"

            if action and action not in ACL_ACTIONS:
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        acl,
                        Status.UNSUPPORTED,
                        reason=f"unknown ACL action {action!r}",
                    )
                )
                continue

            warnings: list[str] = []
            if any(pf_text(row, "description") for row in acl.findall("row")):
                warnings.append("per-row ACL descriptions dropped")
            description = pf_text(acl, "description")
            default_selected = "DO NOT EDIT" not in aclname and "DO NOT EDIT" not in description
            items.append(
                Item(
                    item_id,
                    self.section,
                    label,
                    acl,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    default_selected=default_selected,
                )
            )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        acl = item.source
        if acl is None:
            return []
        networks = []
        for row in acl.findall("row"):
            network = pf_text(row, "acl_network")
            if not network:
                continue
            mask = pf_text(row, "mask")
            networks.append(f"{network}/{mask}" if mask else network)
        el = E("acl", uuid=ctx.uuid(item.id))
        sub(el, "enabled", "1")
        sub(el, "name", sanitize_name(pf_text(acl, "aclname")))
        sub(el, "action", ACL_ACTIONS.get(pf_text(acl, "aclaction"), "allow"))
        sub(el, "networks", ",".join(networks))
        sub(el, "description", pf_desc(acl, "description"))
        return [
            Placement("OPNsense/unboundplus/acls", Mode.APPEND, element=el, item_id=item.id)
        ]
