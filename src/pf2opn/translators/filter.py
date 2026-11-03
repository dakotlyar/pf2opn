"""Filter rules (design §12.1.7). base_policy ``replace`` unless ``--keep-base-rules``.

The mapping copies OPNsense's own converter ``list_legacy_rules.php`` (26.7.8):
field names, the deliberate ``tcpflags_any`` divergence, the ``<enabled>on</enabled>``
quirk (only ``<disabled>`` counts) and the exact port handling all follow it.
"""

from __future__ import annotations

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import (
    E,
    Translator,
    map_net,
    net_deps,
    opn_bool,
    pf_desc,
    pf_endpoint,
    pf_text,
    sub,
)

#: ``pf:icmp6-type`` name -> numeric code, copied from ``list_legacy_rules.php``.
_ICMP6_TYPES = {
    "unreach": "1",
    "toobig": "2",
    "timex": "3",
    "paramprob": "4",
    "echoreq": "128",
    "echorep": "129",
    "listqry": "130",
    "listenrep": "131",
    "listendone": "132",
    "routersol": "133",
    "routeradv": "134",
    "neighbrsol": "135",
    "neighbradv": "136",
    "redir": "137",
    "routrrenum": "138",
    "niqry": "139",
    "nirep": "140",
    "mtraceresp": "200",
    "mtrace": "201",
}

#: Virtual interface tokens copied through unchanged (OPNsense knows them too).
_VPN_IFACES = frozenset({"openvpn", "enc0"})

#: pfSense virtual interfaces with no OPNsense equivalent.
_UNSUPPORTED_IFACES = frozenset({"l2tp", "pppoe"})

#: Presence -> ``1``/``0`` (model order).
_PRESENCE_BOOLS = ("disablereplyto", "log", "allowopts", "nosync", "nopfsync")

#: Copied verbatim when non-empty (before ``tcpflags_any``).
_COPY_IF_SET = (
    "statetimeout",
    "max-src-nodes",
    "max-src-states",
    "max-src-conn",
    "max",
    "max-src-conn-rate",
    "max-src-conn-rates",
    "overload",
    "adaptivestart",
    "adaptiveend",
    "prio",
    "set-prio",
    "set-prio-low",
    "tag",
    "tagged",
    "tcpflags1",
    "tcpflags2",
)

#: Traffic-shaper fields whose presence is warned about (§12.1.7).
_SHAPER_FIELDS = ("dnpipe", "pdnpipe", "defaultqueue", "ackqueue")


def rule_item_id(rule: etree._Element, index: int) -> str:
    """The stable item id for a ``pf:filter/rule`` (§6.1)."""
    tracker = pf_text(rule, "tracker")
    return f"rule:{tracker}" if tracker else f"rule#{index}"


@register
class FilterTranslator(Translator):
    section = "filter"
    title = "Firewall rules"
    tier = 1
    order = 90
    base_policy = "replace"
    container = "OPNsense/Firewall/Filter/rules"

    def effective_base_policy(self, ctx: WriteContext) -> str:
        """``--keep-base-rules`` keeps the base's MVC rules and appends ours."""
        if ctx.plan.options.get("keep_base_rules"):
            return "none"
        return "replace"

    # -- discovery ---------------------------------------------------------- #

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        rules = src.pf.findall("filter/rule")
        floating = [i for i, rule in enumerate(rules) if rule.find("floating") is not None]
        rest = [i for i, rule in enumerate(rules) if rule.find("floating") is None]
        # pfSense evaluates floating rules first, then interface rules (§12.1.7).
        sequence = {index: 10 * (n + 1) for n, index in enumerate(floating + rest)}

        items = [
            self._discover_rule(rule, index, sequence[index], src, ifmap)
            for index, rule in enumerate(rules)
        ]
        separators = src.pf.findall("filter/separator")
        if separators:
            note = (
                f"{len(separators)} pfSense separator block(s) dropped "
                "(MVC rules have no separators)"
            )
            items.append(
                Item(
                    "filter:separator",
                    self.section,
                    "Firewall rule separators",
                    None,
                    Status.PARTIAL,
                    warnings=[note],
                )
            )
        return items

    def _discover_rule(
        self,
        rule: etree._Element,
        index: int,
        sequence: int,
        src: SourceContext,
        ifmap: IfaceMap,
    ) -> Item:
        item_id = rule_item_id(rule, index)
        descr = pf_desc(rule)
        label = descr or f"Rule #{index}"

        interface, reason, tokens, is_any = _parse_interfaces(pf_text(rule, "interface"), ifmap)
        if reason is not None:
            return Item(item_id, self.section, label, rule, Status.UNSUPPORTED, reason=reason)

        source_net, source_not, source_port = pf_endpoint(rule.find("source"))
        destination_net, destination_not, destination_port = pf_endpoint(rule.find("destination"))
        source_mapped, error = map_net(source_net, ifmap)
        if error is not None:
            return Item(item_id, self.section, label, rule, Status.UNSUPPORTED, reason=error)
        destination_mapped, error = map_net(destination_net, ifmap)
        if error is not None:
            return Item(item_id, self.section, label, rule, Status.UNSUPPORTED, reason=error)

        quick = "1" if rule.find("quick") is not None else (
            "0" if rule.find("floating") is not None else "1"
        )

        warnings: list[str] = []
        state_policy = pf_text(rule, "state-policy")
        if state_policy and state_policy not in {"if-bound", "floating"}:
            warnings.append(f"state policy {state_policy!r} has no OPNsense equivalent, dropped")
            state_policy = ""

        if rule.find("associated-rule-id") is not None:
            warnings.append(
                "was the auto rule of a NAT port forward; now a standalone rule"
            )
        if rule.find("floating") is not None and len(tokens) == 1 and quick == "0":
            warnings.append("non-quick floating rule becomes an interface rule; check ordering")
        if any(token.lower() in _VPN_IFACES for token in tokens):
            warnings.append("references a VPN that isn't migrated")
        if pf_text(rule, "sched"):
            warnings.append("schedule not migrated")
        if pf_text(rule, "os"):
            warnings.append("OS fingerprint filter dropped")
        if any(pf_text(rule, field) for field in _SHAPER_FIELDS):
            warnings.append("traffic shaper not migrated")
        if any(
            pf_text(rule, child.tag)
            for child in rule
            if isinstance(child.tag, str) and child.tag.startswith("vlanprio")
        ):
            warnings.append("VLAN priority matching dropped")

        default_selected = not (
            descr.endswith(" auto rule")
            and (source_net.startswith("pfB_") or destination_net.startswith("pfB_"))
        )

        deps: set[str] = set()
        for token in tokens:
            if token in ifmap.targets:
                deps.add(f"iface:{token}")
        deps.update(net_deps(source_net, src))
        deps.update(net_deps(destination_net, src))
        for port in (source_port, destination_port):
            if port in src.alias_names:
                deps.add(f"alias:{port}")
        gateway = pf_text(rule, "gateway")
        if gateway:
            deps.add(f"gw:{gateway}")

        return Item(
            item_id,
            self.section,
            label,
            rule,
            Status.PARTIAL if warnings else Status.OK,
            warnings=warnings,
            deps=sorted(deps),
            default_selected=default_selected,
            data={
                "sequence": sequence,
                "quick": quick,
                "interface": interface,
                "any_interface": is_any,
                "state_policy": state_policy,
                "icmp6type": _icmp6types(rule),
                "source": (source_mapped, source_not, source_port),
                "destination": (destination_mapped, destination_not, destination_port),
            },
        )

    # -- rendering ---------------------------------------------------------- #

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        rule = item.source
        if rule is None:
            return []
        data = item.data

        interface = data["interface"]
        if data["any_interface"] and ctx.plan.options.get("expand_any_interface"):
            interface = ",".join(
                key for key, target in ctx.plan.ifmap.targets.items() if target.include
            )

        root = E("rule", uuid=ctx.uuid(item.id))
        sub(root, "enabled", opn_bool(rule.find("disabled") is None))
        sub(root, "statetype", _statetype(rule))
        sub(root, "state-policy", data["state_policy"])
        sub(root, "sequence", str(data["sequence"]))
        sub(root, "action", pf_text(rule, "type") or "pass")
        sub(root, "quick", data["quick"])
        sub(root, "interfacenot", opn_bool(rule.find("interfacenot") is not None))
        sub(root, "interface", interface)
        sub(root, "direction", pf_text(rule, "direction") or "in")
        sub(root, "ipprotocol", pf_text(rule, "ipprotocol") or "inet")
        sub(root, "protocol", (pf_text(rule, "protocol") or "").upper() or "any")
        icmptype = pf_text(rule, "icmptype")
        sub(root, "icmptype", "" if icmptype == "any" else icmptype)
        sub(root, "icmp6type", data["icmp6type"])

        source_net, source_not, source_port = data["source"]
        sub(root, "source_net", source_net)
        sub(root, "source_not", opn_bool(source_not))
        sub(root, "source_port", source_port)
        destination_net, destination_not, destination_port = data["destination"]
        sub(root, "destination_net", destination_net)
        sub(root, "destination_not", opn_bool(destination_not))
        sub(root, "destination_port", destination_port)

        sub(root, "gateway", pf_text(rule, "gateway"))
        sub(root, "replyto", pf_text(rule, "reply-to"))
        for tag in _PRESENCE_BOOLS:
            sub(root, tag, opn_bool(rule.find(tag) is not None))
        for tag in _COPY_IF_SET:
            value = pf_text(rule, tag)
            if value:
                sub(root, tag, value)
        sub(root, "tcpflags_any", opn_bool(rule.find("tcpflags_any") is not None))
        category = pf_text(rule, "category")
        if category:
            sub(root, "categories", category)
        tos = pf_text(rule, "tos")
        if tos:
            sub(root, "tos", tos)
        sub(root, "description", pf_desc(rule))

        return [
            Placement(
                "OPNsense/Firewall/Filter/rules",
                Mode.APPEND,
                element=root,
                item_id=item.id,
            )
        ]


def _parse_interfaces(
    raw: str, ifmap: IfaceMap
) -> tuple[str, str | None, list[str], bool]:
    """Map a pfSense interface value -> ``(value, reason, tokens, is_any)``.

    ``any``/empty becomes the empty string (OPNsense shows it as "all"); the
    built-in groups pass through. Unknown or excluded keys are unsupported.
    """
    value = (raw or "").strip()
    if not value or value.lower() == "any":
        return "", None, [], True

    tokens = [token.strip() for token in value.split(",") if token.strip()]
    mapped: list[str] = []
    for token in tokens:
        low = token.lower()
        if low == "any":
            return "", None, tokens, True
        if low == "wireguard":
            mapped.append("wireguard")
        elif low in _VPN_IFACES:
            mapped.append(low)
        elif low in _UNSUPPORTED_IFACES:
            return "", "L2TP/PPPoE server not supported", tokens, False
        elif token in ifmap.targets:
            if token not in ifmap.included_keys():
                return "", f"interface {token} not migrated", tokens, False
            mapped.append(token)
        else:
            return "", f"interface {token} not migrated", tokens, False
    return ",".join(mapped), None, tokens, False


def _statetype(rule: etree._Element) -> str:
    value = pf_text(rule, "statetype")
    if not value:
        return "keep"
    return value.split()[0]


def _icmp6types(rule: etree._Element) -> str:
    raw = pf_text(rule, "icmp6-type")
    if not raw:
        return ""
    codes = [
        _ICMP6_TYPES[item]
        for item in (part.strip() for part in raw.split(","))
        if item in _ICMP6_TYPES
    ]
    return ",".join(codes)
