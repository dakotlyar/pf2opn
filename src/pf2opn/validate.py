"""Structural validation of the output (design §13).

M2 implements checks 1 (well-formed round trip) and 2 (unique uuids). Later
milestones add the referential checks; the warning about a base model version
mismatch (§10.3) is emitted here because it needs the output tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

from .model import Plan
from .versions import MODEL_VERSIONS

_VLAN_DEVICE = re.compile(r"^vlan0\d+$")
_WG_DEVICE = re.compile(r"^wg(\d+)$")


@dataclass
class Finding:
    level: str  # "error" | "warning"
    message: str
    path: str | None = None


def _path_of(el: etree._Element) -> str:
    parts: list[str] = []
    node: etree._Element | None = el
    while node is not None and isinstance(node.tag, str):
        parts.append(node.tag)
        node = node.getparent()
    return "/".join(reversed(parts))


def validate(out: etree._Element, plan: Plan) -> list[Finding]:
    """Return every finding for *out*; ``error`` findings block writing."""
    findings: list[Finding] = []
    findings.extend(_check_well_formed(out))
    findings.extend(_check_unique_uuids(out))
    findings.extend(_check_interfaces(out))
    findings.extend(_check_vlan_parents(out, plan))
    findings.extend(_check_versions(out))
    return findings


def _interface_elements(out: etree._Element) -> list[etree._Element]:
    return [
        iface
        for iface in out.findall("interfaces/*")
        if isinstance(iface.tag, str)
    ]


def _check_interfaces(out: etree._Element) -> list[Finding]:
    """§13 check 3: interface devices present, unique, VLANs/WG defined."""
    findings: list[Finding] = []
    vlanifs = {(vlan.findtext("vlanif") or "").strip() for vlan in out.findall("vlans/vlan")}
    instances = {
        (server.findtext("instance") or "").strip()
        for server in out.findall("OPNsense/wireguard/server/servers/server")
    }
    seen: set[str] = set()
    for iface in _interface_elements(out):
        value = (iface.findtext("if") or "").strip()
        if not value:
            findings.append(
                Finding("error", f"interface {iface.tag} has no device", _path_of(iface))
            )
            continue
        if value in seen:
            findings.append(
                Finding(
                    "error",
                    f"device {value} is used by more than one interface",
                    _path_of(iface),
                )
            )
            continue
        seen.add(value)
        wg = _WG_DEVICE.match(value)
        if _VLAN_DEVICE.match(value) and value not in vlanifs:
            findings.append(
                Finding(
                    "error",
                    f"interface {iface.tag} uses {value}, which is not a defined VLAN",
                    _path_of(iface),
                )
            )
        elif wg and wg.group(1) not in instances:
            findings.append(
                Finding(
                    "error",
                    f"interface {iface.tag} uses {value}, which is not a WireGuard server",
                    _path_of(iface),
                )
            )
    return findings


def _check_vlan_parents(out: etree._Element, plan: Plan) -> list[Finding]:
    """§13 check 4: every VLAN parent is an assigned interface or a target NIC."""
    findings: list[Finding] = []
    assigned = {(iface.findtext("if") or "").strip() for iface in _interface_elements(out)}
    target_nics = set(plan.ifmap.nic_map.values())
    target_nics |= set(plan.ifmap.vlan_names.values())
    target_nics |= set(plan.ifmap.wg_names.values())
    target_nics.discard("")
    for vlan in out.findall("vlans/vlan"):
        parent = (vlan.findtext("if") or "").strip()
        if parent and parent not in assigned and parent not in target_nics:
            findings.append(
                Finding(
                    "error",
                    f"VLAN {vlan.findtext('vlanif')} parent {parent} is not assigned",
                    _path_of(vlan),
                )
            )
    return findings


def _check_well_formed(out: etree._Element) -> list[Finding]:
    try:
        data = etree.tostring(out)
        reparsed = etree.fromstring(data)
    except etree.XMLSyntaxError as exc:
        return [Finding("error", f"output is not well-formed XML: {exc}")]
    if reparsed.tag != "opnsense":
        return [Finding("error", f"root element is <{reparsed.tag}>, expected <opnsense>")]
    return []


def _check_unique_uuids(out: etree._Element) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for el in out.iter():
        if not isinstance(el.tag, str):
            continue
        uuid = el.get("uuid")
        if uuid is None:
            continue
        if uuid in seen:
            findings.append(Finding("error", f"duplicate uuid {uuid}", _path_of(el)))
        else:
            seen.add(uuid)
    return findings


def _check_versions(out: etree._Element) -> list[Finding]:
    findings: list[Finding] = []
    for path, expected in MODEL_VERSIONS.items():
        matches = out.xpath(path)
        if not matches:
            continue
        actual = matches[0].get("version")
        if actual is not None and actual != expected:
            findings.append(
                Finding(
                    "warning",
                    f"base model {path} is version {actual}, pf2opn was written for {expected}",
                    path,
                )
            )
    return findings