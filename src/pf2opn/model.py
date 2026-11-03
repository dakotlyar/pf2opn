"""Core data model (design §6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from lxml import etree


class Status(str, Enum):
    OK = "ok"  # translates fully
    PARTIAL = "partial"  # translates; warnings explain what is lost or changed
    UNSUPPORTED = "unsupported"  # cannot be written; `reason` says why + alternative


@dataclass
class Item:
    id: str
    section: str
    label: str
    source: etree._Element | None
    status: Status
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)
    default_selected: bool = True
    followups: list[str] = field(default_factory=list)
    requires_plugin: str | None = None
    readonly_selection: bool = False
    data: dict = field(default_factory=dict)


class Mode(str, Enum):
    APPEND = "append"  # append `element` as last child of container at `path`
    SET = "set"  # set text of leaf at `path` (create path if missing)
    REPLACE = "replace"  # replace element at `path` (create if missing) with `element`
    REMOVE = "remove"  # remove all elements matching `path` (an XPath relative to root)


@dataclass
class Placement:
    path: str
    mode: Mode
    element: etree._Element | None = None
    text: str | None = None
    item_id: str | None = None


@dataclass
class IfaceTarget:
    pf_key: str  # "opt2" (also the output key, D3)
    descr: str  # pfSense descr (unescaped)
    pf_device: str  # "mlxen1.10"
    kind: str  # "physical" | "vlan" | "wireguard" | "unsupported"
    opn_device: str | None  # "vlan02"; None = unmapped
    include: bool = True


@dataclass
class IfaceMap:
    targets: dict[str, IfaceTarget]  # by pf_key, ordered as in pfSense
    nic_map: dict[str, str]  # physical pf NIC -> target NIC ("mlxen1" -> "igc1")
    vlan_names: dict[str, str]  # pf vlanif -> opn vlanif ("mlxen1.10" -> "vlan02")
    wg_names: dict[str, str]  # pf tun -> opn device ("tun_wg0" -> "wg0")

    def device_for(self, pf_device: str) -> str | None:
        """Resolve a pfSense device name to its OPNsense device, or None."""
        if not pf_device:
            return None
        if pf_device in self.vlan_names:
            return self.vlan_names[pf_device]
        if pf_device in self.wg_names:
            return self.wg_names[pf_device]
        return self.nic_map.get(pf_device)

    def included_keys(self) -> set[str]:
        return {key for key, target in self.targets.items() if target.include}


@dataclass
class Plan:
    items: list[Item]  # in registry order, then discovery order
    by_id: dict[str, Item]
    ifmap: IfaceMap
    selected: set[str]
    options: dict  # {"keep_base_rules": False}


@dataclass
class Selection:
    """A saved selection (design §9: selection JSON, version 1)."""

    nic_map: dict[str, str] = field(default_factory=dict)
    interfaces: dict[str, dict] = field(default_factory=dict)
    selected: list[str] | None = None
    deselected: list[str] = field(default_factory=list)
    options: dict = field(default_factory=dict)
    source_sha256: str = ""
