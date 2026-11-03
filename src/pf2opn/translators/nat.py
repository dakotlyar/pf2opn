"""NAT translators (design §12.1.8-10).

``nat_pf``   port forwards  -> ``/nat/rule`` (DNat model)
``nat_out``  outbound NAT   -> ``nat/outbound/mode`` + MVC ``snatrules``
``nat_1to1`` 1:1 NAT        -> MVC ``onetoone``

The outbound and 1:1 mappings copy ``list_legacy_outbound_nat.php`` and
``Firewall/Migrations/MFP1_0_4.php`` respectively.
"""

from __future__ import annotations

import copy
import ipaddress

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import (
    E,
    Translator,
    fix_port,
    map_net,
    net_deps,
    opn_bool,
    pf_desc,
    pf_endpoint,
    pf_text,
    sub,
)
from .filter import rule_item_id


# --------------------------------------------------------------------------- #
# Port forwards (§12.1.8)
# --------------------------------------------------------------------------- #


@register
class NatPortForward(Translator):
    section = "nat_pf"
    title = "Port forwards"
    tier = 1
    order = 100
    base_policy = "merge"
    container = "nat"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        # associated-rule-id -> the filter rule item it produced (§12.1.7 / D8).
        associated: dict[str, str] = {}
        for index, rule in enumerate(src.pf.findall("filter/rule")):
            assoc = pf_text(rule, "associated-rule-id")
            if assoc and assoc != "pass":
                associated[assoc] = rule_item_id(rule, index)

        items: list[Item] = []
        for index, rule in enumerate(src.pf.findall("nat/rule")):
            assoc_id = pf_text(rule, "associated-rule-id")
            item_id = f"pf:{assoc_id or index}"
            descr = pf_desc(rule)
            label = descr or f"Port forward #{index}"

            interface, reason = _map_interfaces(pf_text(rule, "interface"), ifmap)
            if reason is not None:
                items.append(
                    Item(item_id, self.section, label, rule, Status.UNSUPPORTED, reason=reason)
                )
                continue

            source_net, _, source_port = pf_endpoint(rule.find("source"))
            destination_net, _, destination_port = pf_endpoint(rule.find("destination"))
            _, error = map_net(source_net, ifmap)
            if error is None:
                _, error = map_net(destination_net, ifmap)
            if error is not None:
                items.append(
                    Item(item_id, self.section, label, rule, Status.UNSUPPORTED, reason=error)
                )
                continue

            warnings: list[str] = []
            if assoc_id == "pass":
                warnings.append(
                    "pfSense \u2018pass\u2019 association: add an explicit pass rule manually"
                )
            reflection, reflection_warning = _port_forward_reflection(pf_text(rule, "natreflection"))
            if reflection_warning:
                warnings.append(reflection_warning)

            deps: set[str] = set()
            for token in (part.strip() for part in interface.split(",")):
                if token:
                    deps.add(f"iface:{token}")
            deps.update(net_deps(source_net, src))
            deps.update(net_deps(destination_net, src))
            target = pf_text(rule, "target")
            if target in src.alias_names:
                deps.add(f"alias:{target}")
            for port in (source_port, destination_port, pf_text(rule, "local-port")):
                if port in src.alias_names:
                    deps.add(f"alias:{port}")
            if assoc_id and assoc_id in associated:
                deps.add(associated[assoc_id])

            items.append(
                Item(
                    item_id,
                    self.section,
                    label,
                    rule,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    deps=sorted(deps),
                    data={
                        "sequence": 10 * (index + 1),
                        "interface": interface,
                        "natreflection": reflection,
                    },
                )
            )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        rule = item.source
        if rule is None:
            return []
        data = item.data

        root = E("rule", uuid=ctx.uuid(item.id))
        sub(root, "sequence", str(data["sequence"]))
        if rule.find("disabled") is not None:
            sub(root, "disabled", "1")
        if rule.find("nordr") is not None:
            sub(root, "nordr", "1")
        sub(root, "interface", data["interface"])
        sub(root, "ipprotocol", pf_text(rule, "ipprotocol") or "inet")
        sub(root, "protocol", pf_text(rule, "protocol"))
        for field in ("source", "destination"):
            element = rule.find(field)
            if element is not None:
                root.append(_copy_endpoint(element, ctx.plan.ifmap))
        sub(root, "target", pf_text(rule, "target"))
        sub(root, "local-port", pf_text(rule, "local-port"))
        if data["natreflection"]:
            sub(root, "natreflection", data["natreflection"])
        sub(root, "descr", pf_desc(rule))
        for tag in ("tag", "tagged"):
            value = pf_text(rule, tag)
            if value:
                sub(root, tag, value)
        if rule.find("log") is not None:
            sub(root, "log", "1")
        if rule.find("nosync") is not None:
            sub(root, "nosync", "1")
        return [Placement("nat", Mode.APPEND, element=root, item_id=item.id)]


def _map_interfaces(raw: str, ifmap: IfaceMap) -> tuple[str, str | None]:
    value = (raw or "").strip()
    if not value:
        return "", None
    mapped: list[str] = []
    for token in (part.strip() for part in value.split(",")):
        if not token:
            continue
        if token not in ifmap.included_keys():
            return "", f"interface {token} not migrated"
        mapped.append(token)
    return ",".join(mapped), None


def _copy_endpoint(element: etree._Element, ifmap: IfaceMap) -> etree._Element:
    """Rebuild a ``<source>``/``<destination>`` for the DNat model (§12.1.8).

    ``DNat.xml`` has no ``any`` child, so pfSense's stray ``<any/>`` is never
    copied: an any endpoint becomes the ``network`` keyword ``any`` instead. The
    ``network`` keyword is remapped with :func:`map_net`; ``address``/``not``/
    ``port`` are copied verbatim.
    """
    node = E(element.tag)
    if element.find("any") is not None:
        sub(node, "network", "any")
    elif element.find("network") is not None:
        value = pf_text(element, "network")
        mapped, _error = map_net(value, ifmap)
        sub(node, "network", mapped if mapped is not None else value)
    elif element.find("address") is not None:
        sub(node, "address", pf_text(element, "address"))
    else:
        sub(node, "network", "any")
    for child in element:
        if isinstance(child.tag, str) and child.tag not in {"any", "network", "address"}:
            node.append(copy.deepcopy(child))
    return node


def _port_forward_reflection(value: str) -> tuple[str, str | None]:
    if not value:
        return "", None
    if value == "disable":
        return "disable", None
    if value in {"enable", "pfsync", "pfsync+enable"}:
        return "purenat", (
            "NAT+proxy/pfsync reflection has no equivalent, using pure NAT"
        )
    return "", f"NAT reflection mode {value!r} has no equivalent, dropped"


# --------------------------------------------------------------------------- #
# Outbound NAT (§12.1.9)
# --------------------------------------------------------------------------- #


@register
class NatOutbound(Translator):
    section = "nat_out"
    title = "Outbound NAT"
    tier = 1
    order = 101
    base_policy = "replace"
    container = "OPNsense/Firewall/Filter/snatrules"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        outbound = src.pf.find("nat/outbound")
        if outbound is None:
            return []
        mode = pf_text(outbound, "mode") or "automatic"
        items: list[Item] = [
            Item(
                "snat:mode",
                self.section,
                f"Outbound NAT mode: {mode}",
                outbound,
                Status.OK,
            )
        ]

        inactive = mode in {"automatic", "disabled"}
        for index, rule in enumerate(outbound.findall("rule")):
            item_id = f"snat#{index}"
            descr = pf_desc(rule)
            label = descr or f"Outbound NAT #{index}"

            interface = pf_text(rule, "interface") or "lan"
            if interface not in ifmap.included_keys():
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        rule,
                        Status.UNSUPPORTED,
                        reason=f"interface {interface} not migrated",
                    )
                )
                continue

            source_net, source_not, _ = pf_endpoint(rule.find("source"))
            destination_net, destination_not, _ = pf_endpoint(rule.find("destination"))
            source_mapped, error = map_net(source_net, ifmap)
            if error is None:
                destination_mapped, error = map_net(destination_net, ifmap)
            if error is not None:
                items.append(
                    Item(item_id, self.section, label, rule, Status.UNSUPPORTED, reason=error)
                )
                continue

            target, target_error = _outbound_target(rule, interface, ifmap)
            if target_error is not None:
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        rule,
                        Status.UNSUPPORTED,
                        reason=target_error,
                    )
                )
                continue

            warnings: list[str] = []
            if inactive:
                warnings.append(f"rules are inactive in mode {mode}")
            if pf_text(rule, "poolopts"):
                warnings.append(
                    "outbound NAT pool options not migrated; re-select the pool option in the GUI"
                )
            if pf_text(rule, "category"):
                warnings.append(
                    "outbound NAT categories not migrated; recreate them under "
                    "Firewall \u25b8 Rules \u25b8 Categories if needed"
                )

            deps: set[str] = {f"iface:{interface}", "snat:mode"}
            deps.update(net_deps(source_net, src))
            deps.update(net_deps(destination_net, src))
            if target in src.alias_names:
                deps.add(f"alias:{target}")
            for port in (
                pf_text(rule, "sourceport"),
                pf_text(rule, "dstport"),
                pf_text(rule, "natport"),
            ):
                if port in src.alias_names:
                    deps.add(f"alias:{port}")

            items.append(
                Item(
                    item_id,
                    self.section,
                    label,
                    rule,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    deps=sorted(deps),
                    data={
                        "sequence": 10 * (index + 1),
                        "interface": interface,
                        "source": (source_mapped, source_not),
                        "destination": (destination_mapped, destination_not),
                        "target": target,
                    },
                )
            )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        if item.id == "snat:mode":
            return []
        rule = item.source
        if rule is None:
            return []
        data = item.data

        root = E("rule", uuid=ctx.uuid(item.id))
        sub(root, "enabled", opn_bool(rule.find("disabled") is None))
        sub(root, "nonat", opn_bool(rule.find("nonat") is not None))
        sub(root, "nosync", opn_bool(rule.find("nosync") is not None))
        sub(root, "sequence", str(data["sequence"]))
        sub(root, "interface", data["interface"])
        sub(root, "ipprotocol", pf_text(rule, "ipprotocol") or "inet")
        sub(root, "protocol", (pf_text(rule, "protocol") or "").upper() or "any")
        source_net, source_not = data["source"]
        sub(root, "source_net", source_net)
        sub(root, "source_not", opn_bool(source_not))
        sub(root, "source_port", fix_port(pf_text(rule, "sourceport"), colon_to_dash=True))
        destination_net, destination_not = data["destination"]
        sub(root, "destination_net", destination_net)
        sub(root, "destination_not", opn_bool(destination_not))
        sub(root, "destination_port", fix_port(pf_text(rule, "dstport"), colon_to_dash=True))
        sub(root, "target", data["target"])
        sub(root, "target_port", fix_port(pf_text(rule, "natport"), colon_to_dash=True))
        # pfSense poolopts strings are not OPNsense's opaque p1..p6 keys (§12.1.9).
        sub(root, "poolopts", "")
        sub(root, "poolopts_sourcehashkey", pf_text(rule, "source_hash_key"))
        sub(root, "staticnatport", opn_bool(rule.find("staticnatport") is not None))
        sub(root, "log", opn_bool(rule.find("log") is not None))
        for tag in ("tag", "tagged"):
            value = pf_text(rule, tag)
            if value:
                sub(root, tag, value)
        sub(root, "description", pf_desc(rule))
        return [
            Placement(
                "OPNsense/Firewall/Filter/snatrules",
                Mode.APPEND,
                element=root,
                item_id=item.id,
            )
        ]

    def finalize(self, selected: list[Item], ctx: WriteContext) -> list[Placement]:
        mode = pf_text(ctx.src.pf.find("nat/outbound"), "mode") or "automatic"
        return [Placement("nat/outbound/mode", Mode.SET, text=mode)]


def _outbound_target(rule: etree._Element, interface: str, ifmap: IfaceMap) -> tuple[str, str | None]:
    target = pf_text(rule, "target")
    if target == "other-subnet":
        targetip = pf_text(rule, "targetip")
        mask = pf_text(rule, "targetip_subnet")
        if targetip == "":
            target = f"{interface}ip"
        elif mask and mask not in {"0", "32", "128"}:
            return f"{targetip}/{mask}", None
        else:
            return targetip, None
    if target == "openvpnip":
        return "", "OpenVPN not migrated"
    if target == "":
        return "", None
    if target.endswith("ip") and target[:-2] in ifmap.targets:
        key = target[:-2]
        if key == interface:
            return "", None
        if key in ifmap.included_keys():
            return target, None
        return "", f"interface {key} not migrated"
    return target, None


# --------------------------------------------------------------------------- #
# 1:1 NAT (§12.1.10)
# --------------------------------------------------------------------------- #


@register
class NatOneToOne(Translator):
    section = "nat_1to1"
    title = "1:1 NAT"
    tier = 1
    order = 102
    base_policy = "replace"
    container = "OPNsense/Firewall/Filter/onetoone"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for index, rule in enumerate(src.pf.findall("nat/onetoone")):
            item_id = f"binat#{index}"
            descr = pf_desc(rule)
            label = descr or f"1:1 NAT #{index}"

            source = _mfp_net(rule, "source")
            destination = _mfp_net(rule, "destination")
            external = pf_text(rule, "external")
            if source and external and "/" not in external and "/" in source:
                external = f"{external}/{source.split('/', 1)[1]}"

            if not source or not destination or not external:
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        rule,
                        Status.UNSUPPORTED,
                        reason="incomplete 1:1 rule",
                    )
                )
                continue

            reflection, reflection_warning = _one_to_one_reflection(
                pf_text(rule, "natreflection")
            )
            warnings = [reflection_warning] if reflection_warning else []
            items.append(
                Item(
                    item_id,
                    self.section,
                    label,
                    rule,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    data={
                        "sequence": 10 * (index + 1),
                        "source": source,
                        "destination": destination,
                        "external": external,
                        "natreflection": reflection,
                    },
                )
            )
        return items

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        rule = item.source
        if rule is None:
            return []
        data = item.data

        root = E("rule", uuid=ctx.uuid(item.id))
        sub(root, "enabled", opn_bool(rule.find("disabled") is None))
        sub(root, "log", opn_bool(rule.find("log") is not None))
        sub(root, "sequence", str(data["sequence"]))
        sub(root, "interface", pf_text(rule, "interface"))
        sub(root, "type", pf_text(rule, "type") or "binat")
        sub(root, "source_net", data["source"])
        sub(root, "source_not", opn_bool(rule.find("source/not") is not None))
        sub(root, "destination_net", data["destination"])
        sub(root, "destination_not", opn_bool(rule.find("destination/not") is not None))
        sub(root, "external", data["external"])
        if data["natreflection"]:
            sub(root, "natreflection", data["natreflection"])
        sub(root, "description", pf_desc(rule))
        return [
            Placement(
                "OPNsense/Firewall/Filter/onetoone",
                Mode.APPEND,
                element=root,
                item_id=item.id,
            )
        ]


def _one_to_one_reflection(value: str) -> tuple[str, str | None]:
    """Map pfSense 1:1 ``natreflection`` to the OPNsense onetoone set (§12.1.10).

    ``onetoone/rule/natreflection`` is ``default``/``enable``/``disable`` only (no
    ``purenat``), so the pfsync mechanisms map to ``enable`` with a warning.
    """
    if value in {"", "default"}:
        return "", None
    if value in {"enable", "disable"}:
        return value, None
    if value in {"pfsync", "pfsync+enable"}:
        return "enable", (
            "pfSense pfsync NAT reflection has no OPNsense equivalent; "
            "using proxy NAT reflection"
        )
    return "", f"NAT reflection mode {value!r} has no equivalent, dropped"


def _mfp_net(rule: etree._Element, field: str) -> str | None:
    """The ``MFP1_0_4.php`` source/destination normalisation."""
    element = rule.find(field)
    if element is None:
        return None
    if element.find("any") is not None:
        return "any"
    address = pf_text(element, "address")
    if address:
        if _is_network(address):
            return address
        if _is_address(address):
            return f"{address}/{128 if ':' in address else 32}"
        return address
    network = pf_text(element, "network")
    if network:
        return network
    return None


def _is_network(value: str) -> bool:
    if "/" not in value:
        return False
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return True


def _is_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
