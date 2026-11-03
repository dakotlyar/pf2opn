"""Remote syslog destinations (design §12.1.19). base_policy ``merge``."""

from __future__ import annotations

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, pf_text, sub

_REMOTES = (("remoteserver", 1), ("remoteserver2", 2), ("remoteserver3", 3))

#: pfSense-only local log toggles that have no OPNsense equivalent.
_LOCAL_TOGGLES = (
    "filterdescriptions",
    "logall",
    "logargv",
    "logauth",
    "logbridge",
    "logdhcp",
    "logfilter",
    "logntp",
    "logportalauth",
    "logresolver",
    "logsystem",
    "logvpn",
    "logwireless",
    "rawfilter",
)


@register
class SyslogTranslator(Translator):
    section = "syslog"
    title = "Remote logging"
    tier = 1
    order = 140
    base_policy = "merge"
    container = "OPNsense/Syslog/destinations"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        syslog = src.pf.find("syslog")
        if syslog is None:
            return []
        items: list[Item] = []
        enabled = syslog.find("enable") is not None
        for tag, number in _REMOTES:
            value = pf_text(syslog, tag)
            if not enabled or not value:
                continue
            items.append(
                Item(
                    f"syslog:remote{number}",
                    self.section,
                    f"Remote syslog {value}",
                    syslog,
                    Status.OK,
                    data={"value": value},
                )
            )

        if any(syslog.find(tag) is not None for tag in _LOCAL_TOGGLES):
            items.append(
                Item(
                    "syslog:local",
                    self.section,
                    "Local log settings",
                    syslog,
                    Status.UNSUPPORTED,
                    reason="local log settings differ; review System \u25b8 Settings \u25b8 Logging",
                )
            )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        syslog = item.source
        if syslog is None or item.id == "syslog:local":
            return []
        value = item.data["value"]
        hostname, _, port = value.partition(":")
        root = E("destination", uuid=ctx.uuid(item.id))
        sub(root, "enabled", "1")
        sub(root, "transport", "udp6" if pf_text(syslog, "ipproto") == "ipv6" else "udp4")
        sub(root, "hostname", hostname)
        sub(root, "port", port or "514")
        sub(root, "level", "")
        sub(root, "facility", "")
        sub(root, "program", "")
        sub(root, "rfc5424", "0")
        sub(root, "description", "pf2opn remote syslog")
        return [
            Placement("OPNsense/Syslog/destinations", Mode.APPEND, element=root, item_id=item.id)
        ]
