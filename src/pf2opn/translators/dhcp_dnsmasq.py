"""Dnsmasq DHCPv4 translators (design §12.1.11).

``dhcp_range``  ``pf:dhcpd/<ifkey>``           -> ``dnsmasq/dhcp_ranges`` (+ options/boot)
``dhcp_static`` ``pf:dhcpd/<ifkey>/staticmap`` -> ``dnsmasq/hosts`` (+ tags/options/boot)

Dnsmasq has no subnet object: a subnet is a *range* keyed by interface plus
per-interface tags on the DHCP options, and there is no per-interface enable
flag.  The ``dhcp_range`` finalize also emits the Unbound -> Dnsmasq local-domain
query-forward (D6): Dnsmasq registers lease names itself and Unbound forwards the
local domains to it on a non-DNS port.
"""

from __future__ import annotations

import ipaddress
import re

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, pf_desc, pf_list, pf_text, sub

#: IANA option codes for the pfSense per-subnet/per-host fields we map.
_OPT_GATEWAY = "3"
_OPT_DNSSERVER = "6"
_OPT_DOMAIN = "15"
_OPT_DOMAINSEARCH = "119"
_OPT_NTP = "42"

#: The dnsmasq DNS port to use when Unbound stays on the box's DNS port.
_DNSMASQ_DNS_PORT = "53053"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _subnets(src: SourceContext) -> list[etree._Element]:
    """Every ``pf:dhcpd/<ifkey>`` element, in document order (``dhcpddata`` skipped)."""
    container = src.pf.find("dhcpd")
    if container is None:
        return []
    return [el for el in container if isinstance(el.tag, str) and el.tag != "dhcpddata"]


def _enabled_subnets(src: SourceContext) -> list[etree._Element]:
    return [el for el in _subnets(src) if el.find("enable") is not None]


def _is_ipv4(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).version == 4
    except ValueError:
        return False


def _iface_network(ipaddr: str, subnet: str) -> ipaddress.IPv4Network | None:
    if not _is_ipv4(ipaddr) or not subnet:
        return None
    try:
        return ipaddress.ip_network(f"{ipaddr}/{subnet}", strict=False)
    except ValueError:
        return None


def _has_static_ipv4(src: SourceContext, key: str) -> bool:
    iface = src.pf_ifaces.get(key)
    if iface is None:
        return False
    return _iface_network(iface.ipaddr, iface.subnet) is not None


def _per_host_options(el: etree._Element) -> bool:
    """True when a staticmap carries its own dnsserver/gateway/domain/boot fields."""
    if pf_list(el, "dnsserver"):
        return True
    return any(
        pf_text(el, tag) for tag in ("gateway", "domain", "filename", "nextserver", "tftp")
    )


def _has_dynamic_range(el: etree._Element) -> bool:
    """True when the subnet has a main ``range`` or a ``pool`` with a range (§12.1.11)."""
    return el.find("range") is not None or bool(el.findall("pool"))


def _option_rows(el: etree._Element) -> list[tuple[str, str]]:
    """Read the DHCP option values shared by a subnet and a staticmap."""
    rows: list[tuple[str, str]] = []
    gateway = pf_text(el, "gateway")
    if gateway:
        rows.append((_OPT_GATEWAY, gateway))
    dnsservers = pf_list(el, "dnsserver")
    if dnsservers:
        rows.append((_OPT_DNSSERVER, ",".join(dnsservers)))
    domain = pf_text(el, "domain")
    if domain:
        rows.append((_OPT_DOMAIN, domain))
    search = pf_text(el, "domainsearchlist")
    if search:
        rows.append((_OPT_DOMAINSEARCH, search.replace(";", ",")))
    ntp = pf_list(el, "ntpserver")
    if ntp:
        rows.append((_OPT_NTP, ",".join(ntp)))
    return rows


def _option_placements(
    item: Item,
    ctx: WriteContext,
    el: etree._Element,
    *,
    interface: str,
    tag_uuid: str,
    suffix: str,
) -> list[Placement]:
    placements: list[Placement] = []
    for code, value in _option_rows(el):
        row = E("dhcp_options", uuid=ctx.uuid(item.id, f"{suffix}o{code}"))
        sub(row, "type", "set")
        sub(row, "option", code)
        if interface:
            sub(row, "interface", interface)
        if tag_uuid:
            sub(row, "tag", tag_uuid)
        sub(row, "value", value)
        placements.append(
            Placement("dnsmasq/dhcp_options", Mode.APPEND, element=row, item_id=item.id)
        )
    return placements


def _boot_placement(
    item: Item,
    ctx: WriteContext,
    el: etree._Element,
    *,
    interface: str,
    tag_uuid: str,
    suffix: str,
) -> Placement | None:
    filename = pf_text(el, "filename")
    if not filename:
        return None
    row = E("dhcp_boot", uuid=ctx.uuid(item.id, f"{suffix}boot"))
    if interface:
        sub(row, "interface", interface)
    if tag_uuid:
        sub(row, "tag", tag_uuid)
    sub(row, "filename", filename)
    sub(row, "servername", pf_text(el, "tftp"))
    sub(row, "address", pf_text(el, "nextserver"))
    return Placement("dnsmasq/dhcp_boot", Mode.APPEND, element=row, item_id=item.id)


def _option_warnings(el: etree._Element) -> list[str]:
    """The PARTIAL warnings for a subnet's / staticmap's option fields (§12.1.11)."""
    warnings: list[str] = []
    if el.find("denyunknown") is not None and _has_dynamic_range(el):
        warnings.append(
            "pfSense deny-unknown has no Dnsmasq equivalent; the dynamic range is still served"
        )
    if pf_text(el, "domainsearchlist"):
        warnings.append("domain search list is not RFC 1035-encoded; verify option 119 by hand")
    if pf_text(el, "failover_peerip"):
        warnings.append("dnsmasq HA uses split pools + dhcp/reply_delay, not a failover peer")
    if pf_text(el, "mac_allow") or pf_text(el, "mac_deny"):
        warnings.append("per-range MAC ACLs not represented; use tags")
    if any((child.tag or "").startswith("ddnsdomain") for child in el):
        warnings.append("RFC 2136 dynamic DNS updates are not supported by dnsmasq")
    if el.find("numberoptions/item") is not None:
        warnings.append("custom DHCP options not migrated (Tier 2)")
    if el.find("staticarp") is not None:
        warnings.append("static ARP entries not migrated")
    if pf_text(el, "ldap"):
        warnings.append("LDAP-backed DHCP configuration is not migrated")
    if pf_text(el, "rootpath"):
        warnings.append("root-path option not migrated (Tier 2)")
    if any(
        pf_text(el, tag)
        for tag in ("filename32", "filename64", "filename32arm", "filename64arm")
    ):
        warnings.append("architecture-specific boot filenames not migrated")
    if pf_text(el, "uefihttpboot"):
        warnings.append("UEFI HTTP boot not migrated")
    if el.find("ignoreclientuids") is not None:
        warnings.append("ignore client identifiers not migrated")
    if pf_text(el, "maxleasetime"):
        warnings.append("max lease time not represented")
    if pf_text(el, "nextserver") and not pf_text(el, "filename"):
        warnings.append(
            "next server without a boot filename cannot use dhcp_boot (filename is Required)"
        )
    if pf_text(el, "tftp") and not pf_text(el, "filename"):
        warnings.append(
            "tftp server without a boot filename cannot use dhcp_boot (filename is Required)"
        )
    return warnings


def _unbound_enabled(ctx: WriteContext) -> bool:
    """Whether Unbound is enabled in the *output* (design §12.1.11)."""
    general = ctx.plan.by_id.get("dns:general")
    if general is not None and general.id in ctx.plan.selected:
        return ctx.src.pf.find("unbound/enable") is not None
    return (ctx.src.base.findtext("OPNsense/unboundplus/general/enabled") or "").strip() == "1"


def _unbound_port(ctx: WriteContext) -> str:
    general = ctx.plan.by_id.get("dns:general")
    if general is not None and general.id in ctx.plan.selected:
        return pf_text(ctx.src.pf.find("unbound"), "port") or "53"
    return (ctx.src.base.findtext("OPNsense/unboundplus/general/port") or "").strip() or "53"


def _kea_enabled(base: etree._Element) -> bool:
    kea = base.find("OPNsense/Kea/dhcp4")
    if kea is None:
        return False
    enabled = pf_text(kea, "general/enabled")
    if enabled:
        return enabled.strip() not in {"0", "no", "off", "false"}
    return kea.find("enabled") is not None


def _forward_exists(base: etree._Element, domain: str) -> bool:
    for dot in base.findall("OPNsense/unboundplus/dots/dot"):
        if (
            pf_text(dot, "type") == "forward"
            and pf_text(dot, "domain") == domain
            and pf_text(dot, "server") == "127.0.0.1"
        ):
            return True
    return False


def _item_network(item: Item, ctx: WriteContext) -> str:
    key = item.data["interface"]
    iface = ctx.src.pf_ifaces.get(key)
    if iface is None:
        return ""
    network = _iface_network(iface.ipaddr, iface.subnet)
    return str(network) if network else ""


# --------------------------------------------------------------------------- #
# Range translator (§12.1.11)
# --------------------------------------------------------------------------- #


@register
class DhcpRange(Translator):
    section = "dhcp_range"
    title = "Dnsmasq DHCP ranges"
    tier = 1
    order = 110
    base_policy = "none"
    container = None

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for subnet in _enabled_subnets(src):
            key = subnet.tag
            item_id = f"dhcp:{key}"
            iface = src.pf_ifaces.get(key)
            descr = iface.descr if iface else key
            label = f"DHCP range on {descr or key}"

            if key not in ifmap.included_keys():
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        subnet,
                        Status.UNSUPPORTED,
                        reason=f"interface {key} not migrated",
                    )
                )
                continue
            if not _has_static_ipv4(src, key):
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        subnet,
                        Status.UNSUPPORTED,
                        reason="Dnsmasq needs a static interface address",
                    )
                )
                continue

            warnings = _option_warnings(subnet)
            items.append(
                Item(
                    item_id,
                    self.section,
                    label,
                    subnet,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    deps=[f"iface:{key}"],
                    data={"interface": key, "descr": descr},
                )
            )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        subnet = item.source
        if subnet is None:
            return []
        key = item.data["interface"]
        placements: list[Placement] = [
            # Replace the base's default range for this interface (§12.1.11): a
            # fresh 26.7 base ships a default LAN row, so the base row must go
            # before the migrated rows are appended.  This runs here (render)
            # rather than in finalize because the writer applies render before
            # finalize, so a finalize REMOVE would delete the rows it had just
            # appended; flagged in the milestone report.
            Placement(
                f"dnsmasq/dhcp_ranges/dhcp_ranges[interface='{key}']",
                Mode.REMOVE,
                item_id=item.id,
            )
        ]
        for element in self._range_elements(subnet, item, ctx):
            placements.append(
                Placement("dnsmasq/dhcp_ranges", Mode.APPEND, element=element, item_id=item.id)
            )
        placements.extend(
            _option_placements(item, ctx, subnet, interface=key, tag_uuid="", suffix="")
        )
        boot = _boot_placement(item, ctx, subnet, interface=key, tag_uuid="", suffix="")
        if boot is not None:
            placements.append(boot)
        return placements

    def _range_elements(
        self, subnet: etree._Element, item: Item, ctx: WriteContext
    ) -> list[etree._Element]:
        key = item.data["interface"]
        domain = pf_text(subnet, "domain")
        lease = pf_text(subnet, "defaultleasetime")
        main = subnet.find("range")
        pools = subnet.findall("pool")
        dynamic = _has_dynamic_range(subnet)
        # `static` names only a static-only subnet; Dnsmasq.php rejects it with a
        # non-empty end_addr, so `denyunknown` never sets it (§12.1.11).
        mode = "" if dynamic else "static"
        description = f"pf2opn: {item.data['descr']}".strip()

        rows: list[tuple[str, str, str, str]] = []  # suffix, start, end, domain
        if main is not None:
            rows.append(("r0", pf_text(main, "from"), pf_text(main, "to"), domain))
        elif not dynamic:
            iface = ctx.src.pf_ifaces.get(key)
            network = _iface_network(iface.ipaddr, iface.subnet) if iface else None
            rows.append(("r0", str(network.network_address) if network else "", "", domain))
        for pool in pools:
            pool_range = pool.find("range")
            if pool_range is None:
                continue
            rows.append(
                (f"r{len(rows)}", pf_text(pool_range, "from"), pf_text(pool_range, "to"), "")
            )

        elements: list[etree._Element] = []
        for suffix, start, end, row_domain in rows:
            el = E("dhcp_ranges", uuid=ctx.uuid(item.id, suffix))
            sub(el, "interface", key)
            sub(el, "start_addr", start)
            sub(el, "end_addr", end)
            sub(el, "subnet_mask", "")
            if mode:
                sub(el, "mode", mode)
            if lease:
                sub(el, "lease_time", lease)
            if row_domain:
                sub(el, "domain_type", "interface")
                sub(el, "domain", row_domain)
            sub(el, "description", description)
            elements.append(el)
        return elements

    def finalize(self, selected: list[Item], ctx: WriteContext) -> list[Placement]:
        keys = [item.data["interface"] for item in selected]
        domains: list[str] = []
        for item in selected:
            domain = pf_text(item.source, "domain")
            if domain and domain not in domains:
                domains.append(domain)

        placements: list[Placement] = [
            Placement("dnsmasq/enable", Mode.SET, text="1"),
            Placement("dnsmasq/dhcp/fqdn", Mode.SET, text="1"),
            Placement("dnsmasq/interface", Mode.SET, text=_interface_list(keys, ctx)),
        ]

        if _unbound_enabled(ctx):
            unbound_port = _unbound_port(ctx)
            base_port = (ctx.src.base.findtext("dnsmasq/port") or "").strip()
            if not base_port or base_port == unbound_port:
                placements.append(Placement("dnsmasq/port", Mode.SET, text=_DNSMASQ_DNS_PORT))
                dnsmasq_port = _DNSMASQ_DNS_PORT
            else:
                dnsmasq_port = base_port
            for index, domain in enumerate(domains):
                if _forward_exists(ctx.src.base, domain):
                    continue
                placements.append(
                    Placement(
                        "OPNsense/unboundplus/dots",
                        Mode.APPEND,
                        element=_forward_element(selected[0], ctx, domain, dnsmasq_port, index),
                        item_id=selected[0].id,
                    )
                )
            for item in selected:
                network = _item_network(item, ctx)
                if network:
                    followup = f"add an in-addr.arpa reverse forward for {network} to Dnsmasq"
                    if followup not in item.followups:
                        item.followups.append(followup)
        else:
            placements.append(Placement("dnsmasq/port", Mode.SET, text="53"))

        if _kea_enabled(ctx.src.base):
            for item in selected:
                message = "dnsmasq will not start while Kea holds the DHCP ports"
                if message not in item.warnings:
                    item.warnings.append(message)
                    if item.status == Status.OK:
                        item.status = Status.PARTIAL
        return placements


def _interface_list(keys: list[str], ctx: WriteContext) -> str:
    base_existing = [
        part.strip()
        for part in (ctx.src.base.findtext("dnsmasq/interface") or "").split(",")
        if part.strip()
    ]
    merged = list(keys)
    for key in base_existing:
        if key not in merged:
            merged.append(key)
    return ",".join(merged)


def _forward_element(
    item: Item, ctx: WriteContext, domain: str, port: str, index: int
) -> etree._Element:
    dot = E("dot", uuid=ctx.uuid(item.id, f"fwd{index}"))
    sub(dot, "enabled", "1")
    sub(dot, "type", "forward")
    sub(dot, "domain", domain)
    sub(dot, "server", "127.0.0.1")
    sub(dot, "port", port)
    sub(dot, "forward_tcp_upstream", "0")
    sub(dot, "forward_first", "0")
    sub(dot, "description", "pf2opn: local DHCP domain via Dnsmasq")
    return dot


# --------------------------------------------------------------------------- #
# Static reservation translator (§12.1.11)
# --------------------------------------------------------------------------- #


@register
class DhcpStatic(Translator):
    section = "dhcp_static"
    title = "Dnsmasq DHCP reservations"
    tier = 1
    order = 111
    base_policy = "none"
    container = None

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        seen: set[str] = set()
        for subnet in _subnets(src):
            key = subnet.tag
            for staticmap in subnet.findall("staticmap"):
                mac = pf_text(staticmap, "mac").lower()
                item_id = f"dhcpres:{key}:{mac}"
                if item_id in seen:
                    continue
                seen.add(item_id)
                hostname = pf_text(staticmap, "hostname")
                label = f"DHCP reservation {hostname or mac}"
                deps = [f"dhcp:{key}"]

                ipaddr = pf_text(staticmap, "ipaddr")
                if not ipaddr:
                    items.append(
                        Item(
                            item_id,
                            self.section,
                            label,
                            staticmap,
                            Status.UNSUPPORTED,
                            reason="Dnsmasq reservation needs an IP address",
                            deps=deps,
                        )
                    )
                    continue
                if not _in_subnet(src, key, ipaddr):
                    items.append(
                        Item(
                            item_id,
                            self.section,
                            label,
                            staticmap,
                            Status.UNSUPPORTED,
                            reason="reservation IP is outside the interface subnet",
                            deps=deps,
                        )
                    )
                    continue

                warnings = _option_warnings(staticmap)
                tag = (
                    f"pf2opn_{key}_{re.sub(r'[^0-9a-zA-Z]', '_', mac)}"
                    if _per_host_options(staticmap)
                    else ""
                )
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        staticmap,
                        Status.PARTIAL if warnings else Status.OK,
                        warnings=warnings,
                        deps=deps,
                        data={
                            "key": key,
                            "mac": mac,
                            "ipaddr": ipaddr,
                            "hostname": hostname,
                            "tag": tag,
                        },
                    )
                )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        staticmap = item.source
        if staticmap is None:
            return []
        tag = item.data["tag"]
        # `set_tag`/`tag` are ModelRelationFields into dhcp_tags: they hold the
        # row uuid, not the tag text (§12.1.11).
        tag_uuid = ctx.uuid(item.id, "tag") if tag else ""
        placements: list[Placement] = []

        if tag:
            tag_row = E("dhcp_tags", uuid=tag_uuid)
            sub(tag_row, "tag", tag)
            placements.append(
                Placement("dnsmasq/dhcp_tags", Mode.APPEND, element=tag_row, item_id=item.id)
            )

        host = E("hosts", uuid=ctx.uuid(item.id))
        sub(host, "hwaddr", item.data["mac"])
        sub(host, "client_id", pf_text(staticmap, "cid"))
        sub(host, "ip", item.data["ipaddr"])
        sub(host, "host", item.data["hostname"])
        domain = pf_text(staticmap, "domain")
        if domain:
            sub(host, "domain", domain)
        if tag_uuid:
            sub(host, "set_tag", tag_uuid)
        sub(host, "descr", pf_desc(staticmap))
        sub(host, "comments", "")
        sub(host, "local", "0")
        sub(host, "cnames", "")
        sub(host, "lease_time", "")
        sub(host, "ignore", "0")
        sub(host, "aliases", "")
        placements.append(
            Placement("dnsmasq/hosts", Mode.APPEND, element=host, item_id=item.id)
        )

        if tag_uuid:
            placements.extend(
                _option_placements(
                    item, ctx, staticmap, interface="", tag_uuid=tag_uuid, suffix="h"
                )
            )
            boot = _boot_placement(
                item, ctx, staticmap, interface="", tag_uuid=tag_uuid, suffix="h"
            )
            if boot is not None:
                placements.append(boot)
        return placements


def _in_subnet(src: SourceContext, key: str, ipaddr: str) -> bool:
    iface = src.pf_ifaces.get(key)
    if iface is None:
        return True
    network = _iface_network(iface.ipaddr, iface.subnet)
    if network is None:
        return True
    try:
        return ipaddress.ip_address(ipaddr) in network
    except ValueError:
        return False
