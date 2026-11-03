"""Firewall aliases (design §12.1.6). base_policy ``merge`` by name."""

from __future__ import annotations

import re

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, pf_desc, pf_list, pf_text, sub

#: ``AliasNameField.php`` (OPNsense 26.7.8) name regex.
ALIAS_NAME_RE = re.compile(
    r"^([a-zA-Z]|(([_a-zA-Z][a-zA-Z0-9]|[a-zA-Z][_a-zA-Z0-9])[_a-zA-Z0-9]{0,29}))$"
)

#: Internally reserved pf keywords, copied verbatim from ``AliasNameField.php``.
RESERVED_PF_WORDS = frozenset(
    {
        "af-to", "all", "allow-opts", "allow-related", "altq", "anchor", "antispoof",
        "any", "bandwidth", "binat", "binat-anchor", "binat-to", "bitmask", "block",
        "block-policy", "bridge-to", "buckets", "cbq", "code", "codelq", "debug",
        "divert-reply", "divert-to", "dnpipe", "dnqueue", "drop", "dup-to",
        "endpoint-independent", "ether", "fail-policy", "fairq", "fastroute", "file",
        "fingerprints", "flags", "floating", "flush", "for", "fragment", "from",
        "global", "group", "hfsc", "hogs", "hostid", "icmp-type", "icmp6-type",
        "if-bound", "in", "include", "inet", "inet6", "interval", "keep",
        "keepcounters", "l3", "label", "limit", "linkshare", "load", "log",
        "loginterface", "map-e-portset", "match", "matches", "max", "max-mss",
        "max-pkt-rate", "max-pkt-size", "max-src-conn", "max-src-conn-rate",
        "max-src-nodes", "max-src-states", "min-ttl", "modulate", "nat", "nat-anchor",
        "nat-to", "no", "no-df", "no-route", "no-sync", "on", "optimization", "os",
        "out", "overload", "pass", "pflow", "port", "prefer-ipv6-nexthop", "prio",
        "priority", "priq", "probability", "proto", "qlimit", "queue", "quick",
        "random", "random-id", "rdr", "rdr-anchor", "rdr-to", "realtime", "reassemble",
        "received-on", "reply-to", "require-order", "return", "return-icmp",
        "return-icmp6", "return-rst", "ridentifier", "round-robin", "route",
        "route-to", "rtable", "rule", "ruleset-optimization", "scrub", "set",
        "set-tos", "skip", "sloppy", "source-hash", "source-track", "state",
        "state-defaults", "state-policy", "static-port", "sticky-address", "syncookies",
        "synproxy", "table", "tag", "tagged", "target", "tbrsize", "timeout", "to",
        "tos", "ttl", "upperlimit", "urpf-failed", "user",
    }
)

#: Reserved service names from the design (§12.1.6).
COMMON_SERVICES = frozenset(
    {
        "http", "https", "ssh", "domain", "ntp", "smtp", "ftp", "telnet", "snmp",
        "imap", "pop3", "ldap", "tftp", "syslog", "bootps", "bootpc", "isakmp", "nat-t",
    }
)

#: OPNsense built-in alias names an alias must not collide with (§12.1.6).
_BUILTIN_NAMES = frozenset({"bogons", "bogonsv6", "virusprot", "sshlockout"})

_TYPE_MAP = {
    "host": "host",
    "network": "network",
    "port": "port",
    "url": "url",
    "urltable": "urltable",
}
_UNSUPPORTED_TYPES = {"url_ports", "urltable_ports"}


@register
class AliasesTranslator(Translator):
    section = "aliases"
    title = "Aliases"
    tier = 1
    order = 80
    base_policy = "merge"
    container = "OPNsense/Firewall/Alias/aliases"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for alias in src.pf.findall("aliases/alias"):
            name = pf_text(alias, "name")
            label = f"Alias {name}"
            reason = self._name_problem(name)
            if reason is not None:
                items.append(
                    Item(f"alias:{name}", self.section, label, alias, Status.UNSUPPORTED,
                         reason=reason)
                )
                continue

            alias_type = pf_text(alias, "type")
            if alias_type in _UNSUPPORTED_TYPES:
                items.append(
                    Item(f"alias:{name}", self.section, label, alias, Status.UNSUPPORTED,
                         reason="URL port lists not supported by OPNsense aliases")
                )
                continue
            if alias_type not in _TYPE_MAP:
                items.append(
                    Item(f"alias:{name}", self.section, label, alias, Status.UNSUPPORTED,
                         reason=f"alias type {alias_type!r} not supported")
                )
                continue

            warnings: list[str] = []
            if self._detail_dropped(alias):
                warnings.append("per-entry descriptions dropped")
            content = self._content(alias, alias_type)
            deps = sorted({f"alias:{entry}" for entry in content if entry in src.alias_names})
            items.append(
                Item(
                    f"alias:{name}",
                    self.section,
                    label,
                    alias,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    deps=deps,
                    data={"type": alias_type, "content": content},
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        return pf_text(item.source, "name") or None

    # -- discovery helpers -------------------------------------------------- #

    @staticmethod
    def _name_problem(name: str) -> str | None:
        if name.startswith("pfB_"):
            return (
                "managed by pfBlockerNG; recreate as a GeoIP alias (Firewall \u25b8 Aliases, "
                "needs a MaxMind key) or use Unbound blocklists"
            )
        if name in _BUILTIN_NAMES or name.startswith("__"):
            return "name collides with OPNsense built-in alias"
        if not ALIAS_NAME_RE.match(name):
            return (
                "name invalid on OPNsense: must start with a letter or single underscore, "
                "be less than 32 characters and only consist of alphanumerics or underscores"
            )
        if name in RESERVED_PF_WORDS:
            return f"name invalid on OPNsense: reserved keyword {name!r}"
        if name in COMMON_SERVICES:
            return f"name invalid on OPNsense: reserved service name {name!r}"
        return None

    @staticmethod
    def _detail_dropped(alias) -> bool:
        detail = pf_text(alias, "detail")
        if not detail:
            return False
        for entry in detail.split("||"):
            entry = entry.strip()
            if entry and not entry.startswith("Entry added"):
                return True
        return False

    @staticmethod
    def _content(alias, alias_type: str) -> list[str]:
        if alias_type in {"url", "urltable"}:
            return pf_list(alias, "url") + pf_list(alias, "aliasurl")
        return pf_text(alias, "address").split()

    # -- rendering ---------------------------------------------------------- #

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        alias = item.source
        if alias is None:
            return []
        alias_type = item.data["type"]
        content = item.data["content"]

        root = E("alias", uuid=ctx.uuid(item.id))
        sub(root, "enabled", "1")
        sub(root, "name", pf_text(alias, "name"))
        sub(root, "type", _TYPE_MAP[alias_type])
        sub(root, "content", "\n".join(content) if content else "")
        if alias_type == "urltable":
            updatefreq = pf_text(alias, "updatefreq")
            if updatefreq:
                sub(root, "updatefreq", updatefreq)
        sub(root, "description", pf_desc(alias))
        sub(root, "proto", "")
        sub(root, "counters", "0")
        return [
            Placement(
                "OPNsense/Firewall/Alias/aliases", Mode.APPEND, element=root, item_id=item.id
            )
        ]
