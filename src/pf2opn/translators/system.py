"""System facets (design §12.1.1). base_policy ``none``."""

from __future__ import annotations

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, opn_bool, pf_list, pf_text

#: pf flags copied straight through to the same OPNsense tag (present -> 1).
_OFFLOAD_TAGS = (
    "disablechecksumoffloading",
    "disablesegmentationoffloading",
    "disablelargereceiveoffloading",
    "ipv6allow",
)


@register
class SystemTranslator(Translator):
    section = "system"
    title = "System"
    tier = 1
    order = 10
    base_policy = "none"
    container = None

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        system = src.pf.find("system")
        if system is None:
            return []
        found = [
            self._hostname(system),
            self._timezone(system),
            self._ntp(system),
            self._dns(system),
            self._offload(system),
            self._ssh(system),
            self._console(system),
            self._webgui(system),
            self._root_password(system),
        ]
        return [item for item in found if item is not None]

    # -- discovery ---------------------------------------------------------- #

    def _hostname(self, system: etree._Element) -> Item | None:
        hostname = pf_text(system, "hostname")
        domain = pf_text(system, "domain")
        if not hostname and not domain:
            return None
        label = "Hostname: " + ".".join(part for part in (hostname, domain) if part)
        return Item("system:hostname", self.section, label, system, Status.OK)

    def _timezone(self, system: etree._Element) -> Item | None:
        value = pf_text(system, "timezone")
        if not value:
            return None
        return Item("system:timezone", self.section, f"Timezone: {value}", system, Status.OK)

    def _ntp(self, system: etree._Element) -> Item | None:
        value = pf_text(system, "timeservers")
        if not value:
            return None
        servers = value.split()
        uses_pfsense_pool = any("pfsense.pool.ntp.org" in server for server in servers)
        warnings: list[str] = []
        status = Status.OK
        if uses_pfsense_pool:
            status = Status.PARTIAL
            warnings.append("pfSense pool servers; consider opnsense.pool.ntp.org")
        return Item(
            "system:ntp", self.section, f"NTP servers: {value}", system, status, warnings=warnings
        )

    def _dns(self, system: etree._Element) -> Item | None:
        servers = pf_list(system, "dnsserver")
        deps: list[str] = []
        for n in range(1, 9):
            gateway = pf_text(system, f"dns{n}gw")
            if gateway and gateway != "none":
                deps.append(f"gw:{gateway}")
        has_override = system.find("dnsallowoverride") is not None
        if not servers and not has_override and not deps:
            return None
        label = "DNS servers" + (": " + ", ".join(servers) if servers else "")
        return Item("system:dns", self.section, label, system, Status.OK, deps=deps)

    def _offload(self, system: etree._Element) -> Item | None:
        # Always discovered so the base's flags are synced too (absent -> REMOVE).
        return Item("system:offload", self.section, "Offload settings", system, Status.OK)

    def _ssh(self, system: etree._Element) -> Item | None:
        ssh = system.find("ssh")
        if ssh is None:
            return None
        enabled = pf_text(ssh, "enable")
        label = f"SSH: {enabled or 'disabled'}"
        return Item(
            "system:ssh",
            self.section,
            label,
            ssh,
            Status.PARTIAL,
            warnings=[
                "OPNsense denies root login and password auth by default; "
                "check System \u25b8 Settings \u25b8 Administration"
            ],
            followups=["SSH host keys change; clients will warn"],
        )

    def _console(self, system: etree._Element) -> Item | None:
        primary = pf_text(system, "primaryconsole")
        speed = pf_text(system, "serialspeed")
        if not primary and not speed:
            return None
        label = f"Console: {primary or 'video'}"
        return Item("system:console", self.section, label, system, Status.OK)

    def _webgui(self, system: etree._Element) -> Item | None:
        webgui = system.find("webgui")
        if webgui is None:
            return None
        certref = pf_text(webgui, "ssl-certref")
        deps = [f"cert:{certref}"] if certref else []
        return Item(
            "system:webgui",
            self.section,
            "WebGUI protocol/port/certificate",
            webgui,
            Status.PARTIAL,
            warnings=["theme/CSS and dashboard settings not migrated"],
            deps=deps,
            default_selected=False,
        )

    def _root_password(self, system: etree._Element) -> Item | None:
        admin = system.find("user[name='admin']")
        if admin is None or not pf_text(admin, "bcrypt-hash"):
            return None
        return Item(
            "system:root_password",
            self.section,
            "Root password (from pfSense admin)",
            admin,
            Status.PARTIAL,
            warnings=["replaces OPNsense root password with pfSense admin password"],
            default_selected=False,
        )

    # -- rendering ---------------------------------------------------------- #

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        system = item.source
        if system is None:
            return []
        handler = {
            "system:hostname": self._render_hostname,
            "system:timezone": self._render_timezone,
            "system:ntp": self._render_ntp,
            "system:dns": self._render_dns,
            "system:offload": self._render_offload,
            "system:ssh": self._render_ssh,
            "system:console": self._render_console,
            "system:webgui": self._render_webgui,
            "system:root_password": self._render_root_password,
        }.get(item.id)
        if handler is None:
            return []
        return handler(item, ctx)

    def _set(self, path: str, value: str, item: Item) -> Placement:
        return Placement(path, Mode.SET, text=value, item_id=item.id)

    def _render_hostname(self, item: Item, ctx: WriteContext) -> list[Placement]:
        placements: list[Placement] = []
        hostname = pf_text(item.source, "hostname")
        domain = pf_text(item.source, "domain")
        if hostname:
            placements.append(self._set("system/hostname", hostname, item))
        if domain:
            placements.append(self._set("system/domain", domain, item))
        return placements

    def _render_timezone(self, item: Item, ctx: WriteContext) -> list[Placement]:
        value = pf_text(item.source, "timezone")
        return [self._set("system/timezone", value, item)] if value else []

    def _render_ntp(self, item: Item, ctx: WriteContext) -> list[Placement]:
        value = pf_text(item.source, "timeservers")
        if not value:
            return []
        servers = value.split()
        placements = [self._set("system/timeservers", value, item)]
        pool = " ".join(server for server in servers if "pool" in server)
        placements.append(
            Placement("ntpd/ispool", Mode.REPLACE, element=E("ispool", pool), item_id=item.id)
        )
        prefer = ctx.src.base.find("ntpd/prefer")
        if prefer is not None:
            prefer_value = (prefer.text or "").strip()
            if prefer_value and prefer_value not in servers:
                placements.append(Placement("ntpd/prefer", Mode.REMOVE, item_id=item.id))
        return placements

    def _render_dns(self, item: Item, ctx: WriteContext) -> list[Placement]:
        system = item.source
        placements: list[Placement] = [Placement("system/dnsserver", Mode.REMOVE, item_id=item.id)]
        for server in pf_list(system, "dnsserver"):
            placements.append(
                Placement("system", Mode.APPEND, element=E("dnsserver", server), item_id=item.id)
            )
        for n in range(1, 9):
            gateway = pf_text(system, f"dns{n}gw")
            if gateway and gateway != "none":
                placements.append(self._set(f"system/dns{n}gw", gateway, item))
        has_override = system.find("dnsallowoverride") is not None
        placements.append(self._set("system/dnsallowoverride", opn_bool(has_override), item))
        return placements

    def _render_offload(self, item: Item, ctx: WriteContext) -> list[Placement]:
        placements: list[Placement] = []
        for tag in _OFFLOAD_TAGS:
            if item.source.find(tag) is not None:
                placements.append(self._set(f"system/{tag}", "1", item))
            else:
                placements.append(Placement(f"system/{tag}", Mode.REMOVE, item_id=item.id))
        return placements

    def _render_ssh(self, item: Item, ctx: WriteContext) -> list[Placement]:
        ssh = item.source.find("ssh")
        placements: list[Placement] = []
        enabled = pf_text(ssh, "enable")
        if enabled:
            placements.append(self._set("system/ssh/enabled", enabled, item))
        port = pf_text(ssh, "port")
        if port:
            placements.append(self._set("system/ssh/port", port, item))
        return placements

    def _render_console(self, item: Item, ctx: WriteContext) -> list[Placement]:
        placements: list[Placement] = []
        primary = pf_text(item.source, "primaryconsole")
        if primary:
            placements.append(self._set("system/primaryconsole", primary, item))
        speed = pf_text(item.source, "serialspeed")
        if speed:
            placements.append(self._set("system/serialspeed", speed, item))
        return placements

    def _render_webgui(self, item: Item, ctx: WriteContext) -> list[Placement]:
        webgui = item.source
        placements: list[Placement] = []
        for tag in ("protocol", "port", "ssl-certref"):
            value = pf_text(webgui, tag)
            if value:
                placements.append(self._set(f"system/webgui/{tag}", value, item))
        return placements

    def _render_root_password(self, item: Item, ctx: WriteContext) -> list[Placement]:
        value = pf_text(item.source, "bcrypt-hash")
        if not value:
            return []
        return [
            Placement(
                "system/user[name='root']/password", Mode.SET, text=value, item_id=item.id
            )
        ]
