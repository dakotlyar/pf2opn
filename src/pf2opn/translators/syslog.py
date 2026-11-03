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


def split_host_port(value: str) -> tuple[str, str]:
    """Split a remote-server value into ``(hostname, port)`` by shape (§12.1.19).

    Splitting on the first ``:`` mangles an IPv6 literal (``::1`` becomes an empty
    hostname), so the rule is shape-based:
    ``[v6]`` / ``[v6]:port`` -> the brackets hold the hostname (dropped);
    a bare IPv6 literal (two or more colons, unbracketed) is the whole hostname
    with port ``514``; otherwise ``host:port`` / ``host``.
    """
    text = (value or "").strip()
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            hostname = text[1:end]
            rest = text[end + 1 :]
            port = rest[1:] if rest.startswith(":") else ""
            return hostname, port or "514"
    if text.count(":") >= 2:
        return text, "514"
    hostname, _, port = text.partition(":")
    return hostname, port or "514"


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
        hostname, port = split_host_port(item.data["value"])
        root = E("destination", uuid=ctx.uuid(item.id))
        sub(root, "enabled", "1")
        sub(root, "transport", "udp6" if pf_text(syslog, "ipproto") == "ipv6" else "udp4")
        sub(root, "hostname", hostname)
        sub(root, "port", port)
        sub(root, "level", "")
        sub(root, "facility", "")
        sub(root, "program", "")
        sub(root, "rfc5424", "0")
        sub(root, "description", "pf2opn remote syslog")
        return [
            Placement("OPNsense/Syslog/destinations", Mode.APPEND, element=root, item_id=item.id)
        ]
