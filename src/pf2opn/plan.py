"""Plan construction (design §4, §6).

``build_plan`` is pure and cheap: it discovers items through the registry,
resolves dependencies, and seeds the default selection. Run it again whenever
the interface map changes.
"""

from __future__ import annotations

import re

from . import translators  # noqa: F401  (import side effect: registers translators)
from .context import SourceContext
from .deps import resolve
from .model import IfaceMap, IfaceTarget, Item, Plan
from .registry import all_translators
from .selection import default_ids

#: Device prefixes that v1 cannot migrate (design §12.1.2 / §12.2).
_UNSUPPORTED_PREFIXES = (
    "ovpn",
    "ppp",
    "pppoe",
    "l2tp",
    "lagg",
    "bridge",
    "gif",
    "gre",
    "tailscale",
    "enc",
    "lo",
)

_VLAN_NUMBER = re.compile(r"^vlan0(\d+)$")


def classify(device: str, src: SourceContext) -> str:
    """Classify a pfSense device: physical / vlan / wireguard / unsupported."""
    vlanifs = {(v.findtext("vlanif") or "").strip() for v in src.pf.findall("vlans/vlan")}
    if device in vlanifs:
        return "vlan"
    if device.startswith("tun_wg"):
        return "wireguard"
    if device.startswith(_UNSUPPORTED_PREFIXES):
        return "unsupported"
    return "physical"


def _existing_vlan_numbers(base) -> int:
    highest = 0
    for vlanif in base.findall("vlans/vlan/vlanif"):
        match = _VLAN_NUMBER.match((vlanif.text or "").strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


def build_default_ifmap(src: SourceContext, nic_map: dict[str, str] | None = None) -> IfaceMap:
    """Build a default :class:`IfaceMap` from the source and an optional NIC map.

    Filled in fully by ``ifmap.py`` in milestone M3; M1/M2 only need enough to
    construct a plan for the ``system`` translator.
    """
    nic = dict(nic_map or {})

    vlan_names: dict[str, str] = {}
    next_number = _existing_vlan_numbers(src.base)
    for vlan in src.pf.findall("vlans/vlan"):
        vlanif = (vlan.findtext("vlanif") or "").strip()
        if not vlanif:
            continue
        next_number += 1
        vlan_names[vlanif] = f"vlan0{next_number}"

    wg_names: dict[str, str] = {}
    for tunnel in src.pf.findall("installedpackages/wireguard/tunnels/item"):
        name = (tunnel.findtext("name") or "").strip()
        match = re.fullmatch(r"tun_wg(\d+)", name)
        if match:
            wg_names[name] = f"wg{match.group(1)}"

    targets: dict[str, IfaceTarget] = {}
    for key, pf_iface in src.pf_ifaces.items():
        kind = classify(pf_iface.device, src)
        if kind == "physical":
            opn_device = nic.get(pf_iface.device)
        elif kind == "vlan":
            opn_device = vlan_names.get(pf_iface.device)
        elif kind == "wireguard":
            opn_device = wg_names.get(pf_iface.device)
        else:
            opn_device = None
        targets[key] = IfaceTarget(
            pf_key=key,
            descr=pf_iface.descr,
            pf_device=pf_iface.device,
            kind=kind,
            opn_device=opn_device,
            include=kind != "unsupported",
        )

    return IfaceMap(targets=targets, nic_map=nic, vlan_names=vlan_names, wg_names=wg_names)


def _dedupe_check(items: list[Item]) -> None:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"duplicate item id produced by a translator: {item.id}")
        seen.add(item.id)


def build_plan(src: SourceContext, ifmap: IfaceMap) -> Plan:
    """Discover every item, resolve dependencies, and seed the default selection."""
    items: list[Item] = []
    for translator in all_translators():
        items.extend(translator.discover(src, ifmap))

    _dedupe_check(items)
    by_id = {item.id: item for item in items}
    resolve(items)

    plan = Plan(
        items=items,
        by_id=by_id,
        ifmap=ifmap,
        selected=set(),
        options={"keep_base_rules": False},
    )
    plan.selected = default_ids(plan)
    return plan
