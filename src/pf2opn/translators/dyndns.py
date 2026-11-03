"""Dynamic DNS (design §12.1.18). base_policy ``merge``; needs ``os-ddclient``."""

from __future__ import annotations

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, pf_desc, pf_text, sub

#: pfSense provider -> OPNsense os-ddclient ``service`` (§12.1.18).
SERVICE_MAP: dict[str, str] = {
    "cloudflare": "cloudflare",
    "cloudflare-v6": "cloudflare",
    "dyndns": "dyndns2",
    "dyndns-static": "dyndns2",
    "dyndns-custom": "dyndns2",
    "custom": "custom",
    "duckdns": "duckdns",
    "noip": "noip",
    "namecheap": "namecheap",
    "he-net": "he-net",
    "route53": "route53",
}

#: ``host`` values that mean "the current WAN address", i.e. the apex (§19 item 1).
_APEX_HOSTS = {"", "@", "dynamic"}


@register
class DyndnsTranslator(Translator):
    section = "dyndns"
    title = "Dynamic DNS"
    tier = 1
    order = 130
    base_policy = "merge"
    container = "OPNsense/DynDNS/accounts"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for index, entry in enumerate(src.pf.findall("dyndnses/dyndns")):
            item_id = f"ddns:{index}"
            descr = pf_desc(entry)
            label = f"Dynamic DNS {descr or ('#' + str(index))}"

            service = pf_text(entry, "service")
            mapped = SERVICE_MAP.get(service)
            if mapped is None:
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        entry,
                        Status.UNSUPPORTED,
                        reason=f"provider {service!r} not mapped",
                    )
                )
                continue

            iface = pf_text(entry, "requestif") or pf_text(entry, "interface")
            if iface and iface in ifmap.targets and iface not in ifmap.included_keys():
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        entry,
                        Status.UNSUPPORTED,
                        reason=f"interface {iface} not migrated",
                    )
                )
                continue

            warnings: list[str] = []
            if service == "cloudflare-v6":
                warnings.append("cloudflare-v6 maps to the cloudflare (IPv4) service")
            if service == "custom":
                warnings.append("custom update URL; verify the server and response handling")

            host = pf_text(entry, "host")
            domain = pf_text(entry, "domainname")
            hostname = domain if host in _APEX_HOSTS else f"{host}.{domain}"
            deps = [f"iface:{iface}"] if iface in ifmap.targets and iface else []

            items.append(
                Item(
                    item_id,
                    self.section,
                    label,
                    entry,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    deps=deps,
                    requires_plugin="os-ddclient",
                    followups=[
                        "install os-ddclient before importing, or the section stays inert "
                        "until installed"
                    ],
                    data={
                        "service": mapped,
                        "hostname": hostname,
                        "zone": domain,
                        "interface": iface,
                        "server": pf_text(entry, "updateurl") if service == "custom" else "",
                    },
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        if item.source is None:
            return None
        host = pf_text(item.source, "host")
        domain = pf_text(item.source, "domainname")
        if not host and not domain:
            return None
        return f"{host}|{domain}|{pf_text(item.source, 'service')}"

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        entry = item.source
        if entry is None:
            return []
        data = item.data
        root = E("account", uuid=ctx.uuid(item.id))
        sub(root, "enabled", "1" if entry.find("enable") is not None else "0")
        sub(root, "service", data["service"])
        if data["server"]:
            sub(root, "server", data["server"])
        sub(root, "protocol", "")
        sub(root, "username", pf_text(entry, "username"))
        sub(root, "password", pf_text(entry, "password"))
        sub(root, "hostnames", data["hostname"])
        sub(root, "zone", data["zone"])
        sub(root, "checkip", "if")
        sub(root, "interface", data["interface"])
        sub(root, "ttl", pf_text(entry, "ttl") or "300")
        sub(root, "force_ssl", "1")
        sub(root, "wildcard", "1" if entry.find("wildcard") is not None else "0")
        sub(root, "description", pf_desc(entry))
        return [
            Placement("OPNsense/DynDNS/accounts", Mode.APPEND, element=root, item_id=item.id)
        ]

    def finalize(self, selected: list[Item], ctx: WriteContext) -> list[Placement]:
        return [
            Placement("OPNsense/DynDNS/general/enabled", Mode.SET, text="1"),
            Placement("OPNsense/DynDNS/general/backend", Mode.SET, text="opnsense"),
        ]
