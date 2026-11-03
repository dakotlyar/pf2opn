"""Source and write contexts (design §7.4)."""

from __future__ import annotations

import hashlib
import html
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from . import paths
from .model import Plan

#: Namespace UUID for deterministic item UUIDs (design D7 / §7.4).
PF2OPN_NS = uuid.UUID("6f2c1d2e-8a4b-4c1e-9a43-7d0c5e0f2a11")


def _unescape(text: str) -> str:
    """Unescape HTML entities until stable (max two passes), like §7.3 pf_desc."""
    for _ in range(2):
        new = html.unescape(text)
        if new == text:
            break
        text = new
    return text


@dataclass
class PfIface:
    key: str
    descr: str
    device: str
    enabled: bool
    ipaddr: str
    subnet: str
    ipaddrv6: str
    subnetv6: str
    gateway: str
    el: etree._Element


@dataclass
class SourceContext:
    pf: etree._Element  # <pfsense> root
    base: etree._Element  # <opnsense> root of --base (read-only here)
    pf_ifaces: dict[str, PfIface]  # ordered
    alias_names: set[str]
    gateway_names: set[str]
    pf_version: str
    source_path: Path
    base_path: Path
    source_sha256: str
    pf_hostname: str = ""
    base_hostname: str = ""

    @classmethod
    def load(cls, src_path: Path | str, base_path: Path | str) -> "SourceContext":
        src_path = Path(src_path)
        base_path = Path(base_path)
        pf = paths.parse_pf_root(src_path)
        base = paths.parse_opnsense_root(base_path)

        pf_ifaces = _read_ifaces(pf)
        alias_names = {
            (a.findtext("name") or "").strip()
            for a in pf.findall("aliases/alias")
            if (a.findtext("name") or "").strip()
        }
        gateway_names = {
            (g.findtext("name") or "").strip()
            for g in pf.findall("gateways/gateway_item")
            if (g.findtext("name") or "").strip()
        }
        digest = hashlib.sha256(src_path.read_bytes()).hexdigest()
        return cls(
            pf=pf,
            base=base,
            pf_ifaces=pf_ifaces,
            alias_names=alias_names,
            gateway_names=gateway_names,
            pf_version=(pf.findtext("version") or "").strip(),
            source_path=src_path,
            base_path=base_path,
            source_sha256=digest,
            pf_hostname=(pf.findtext("system/hostname") or "").strip(),
            base_hostname=(base.findtext("system/hostname") or "").strip(),
        )


def _read_ifaces(pf: etree._Element) -> dict[str, PfIface]:
    ifaces: dict[str, PfIface] = {}
    container = pf.find("interfaces")
    if container is None:
        return ifaces
    for el in container:
        if not isinstance(el.tag, str):  # comments / PIs
            continue
        key = el.tag
        ifaces[key] = PfIface(
            key=key,
            descr=_unescape((el.findtext("descr") or "").strip()),
            device=(el.findtext("if") or "").strip(),
            enabled=el.find("enable") is not None,
            ipaddr=(el.findtext("ipaddr") or "").strip(),
            subnet=(el.findtext("subnet") or "").strip(),
            ipaddrv6=(el.findtext("ipaddrv6") or "").strip(),
            subnetv6=(el.findtext("subnetv6") or "").strip(),
            gateway=(el.findtext("gateway") or "").strip(),
            el=el,
        )
    return ifaces


@dataclass
class WriteContext:
    src: SourceContext
    plan: Plan

    def uuid(self, item_id: str, suffix: str = "") -> str:
        """Deterministic uuid5 for an item (design D7)."""
        return str(uuid.uuid5(PF2OPN_NS, f"{item_id}/{suffix}"))

    def selected(self, item_id: str) -> bool:
        return item_id in self.plan.selected

    def map_iface(self, pf_key: str) -> str | None:
        """Return *pf_key* if it is included in the output, else None."""
        return pf_key if pf_key in self.plan.ifmap.included_keys() else None

    def device(self, pf_device: str) -> str | None:
        return self.plan.ifmap.device_for(pf_device)