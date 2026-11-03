"""Interface mapping: device classification and the default IfaceMap (§12.2)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from lxml import etree

from .context import SourceContext
from .model import IfaceMap, IfaceTarget

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

#: A VLAN device name: ``vlan0`` / ``qinq0`` plus digits/dots, max 15 chars (§2).
_VLAN_NAME = re.compile(r"^(?:vlan|qinq)0[0-9.]{1,10}$")

#: Existing ``vlan0N`` names whose integer suffix drives the next generated one.
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


def _is_physical_nic(device: str) -> bool:
    if not device:
        return False
    if _VLAN_NAME.match(device) or device.startswith("wg"):
        return False
    return not device.startswith(_UNSUPPORTED_PREFIXES)


def _existing_vlan_numbers(base: etree._Element) -> int:
    highest = 0
    for vlanif in base.findall("vlans/vlan/vlanif"):
        match = _VLAN_NUMBER.match((vlanif.text or "").strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


def _base_nic_suggestion(src: SourceContext, key: str) -> str:
    """The base's ``interfaces/<key>/if`` when it is a physical NIC, else ""."""
    device = (src.base.findtext(f"interfaces/{key}/if") or "").strip()
    return device if device and _is_physical_nic(device) else ""


def default_ifmap(src: SourceContext, nic_map: dict[str, str] | None = None) -> IfaceMap:
    """Build the default :class:`IfaceMap` (design §12.2).

    Physical pf NICs default to the base's NIC for the same interface key (so
    ``pf wan=mlxen0`` with ``base wan=igc0`` suggests ``mlxen0 -> igc0``), or
    to ``""`` when there is no suggestion. *nic_map* entries override that.
    """
    overrides = {pf_nic: opn_nic for pf_nic, opn_nic in (nic_map or {}).items() if opn_nic}

    nic: dict[str, str] = {}
    for key, pf_iface in src.pf_ifaces.items():
        if classify(pf_iface.device, src) != "physical":
            continue
        nic.setdefault(pf_iface.device, "")
        suggestion = _base_nic_suggestion(src, key)
        if suggestion:
            nic[pf_iface.device] = suggestion
    nic.update(overrides)

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
            opn_device = nic.get(pf_iface.device) or None
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


def target_nics(src: SourceContext, extra: Iterable[str] = ()) -> list[str]:
    """NIC choices for the picker: the base's physical NICs plus *extra*.

    *extra* carries the targets given via ``--map-nic`` (the design's §12.2
    signature has no argument for them, so they are passed in by the caller).
    """
    nics = {
        (device.text or "").strip()
        for device in src.base.findall("interfaces/*/if")
        if _is_physical_nic((device.text or "").strip())
    }
    nics.update(value for value in extra if value)
    return sorted(nics)


def validate_ifmap(m: IfaceMap) -> list[str]:
    """Return the mapping errors that block writing (design §12.2)."""
    errors: list[str] = []
    for key, target in m.targets.items():
        if target.kind == "physical" and target.include and not target.opn_device:
            errors.append(f"interface {key}: NIC {target.pf_device} is not mapped")

    by_device: dict[str, list[str]] = {}
    for key, target in m.targets.items():
        if target.include and target.opn_device:
            by_device.setdefault(target.opn_device, []).append(key)
    for device, keys in by_device.items():
        if len(keys) > 1:
            errors.append(f"interfaces {', '.join(keys)} all map to {device}")

    by_nic: dict[str, list[str]] = {}
    for pf_nic, opn_nic in m.nic_map.items():
        if opn_nic:
            by_nic.setdefault(opn_nic, []).append(pf_nic)
    for opn_nic, pf_nics in by_nic.items():
        if len(pf_nics) > 1:
            errors.append(f"NICs {', '.join(pf_nics)} all map to {opn_nic}")

    return errors
