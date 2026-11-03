"""Virtual IPs (design §12.1.12). base_policy ``merge`` by interface+subnet."""

from __future__ import annotations

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, opn_bool, pf_desc, pf_text, sub

_MODES = {"ipalias", "carp", "proxyarp"}


@register
class VipsTranslator(Translator):
    section = "vips"
    title = "Virtual IPs"
    tier = 1
    order = 70
    base_policy = "merge"
    container = "virtualip"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for vip in src.pf.findall("virtualip/vip"):
            interface = pf_text(vip, "interface")
            subnet = pf_text(vip, "subnet")
            mode = pf_text(vip, "mode")
            label = f"Virtual IP {interface} {subnet}"
            if mode not in _MODES:
                items.append(
                    Item(f"vip:{interface}:{subnet}", self.section, label, vip,
                         Status.UNSUPPORTED, reason=f"VIP mode {mode!r} is not migrated")
                )
                continue

            if interface != "lo0" and interface not in ifmap.included_keys():
                items.append(
                    Item(f"vip:{interface}:{subnet}", self.section, label, vip,
                         Status.UNSUPPORTED, reason=f"interface {interface} not migrated")
                )
                continue

            warnings: list[str] = []
            default_selected = True
            if "DNSBL" in pf_desc(vip):
                warnings.append("pfBlockerNG DNSBL VIP; not needed on OPNsense")
                default_selected = False
            if mode == "carp":
                warnings.append("CARP needs HA setup on both nodes")
            deps = [] if interface == "lo0" else [f"iface:{interface}"]
            items.append(
                Item(
                    f"vip:{interface}:{subnet}",
                    self.section,
                    label,
                    vip,
                    Status.PARTIAL if mode == "carp" else Status.OK,
                    warnings=warnings,
                    deps=deps,
                    default_selected=default_selected,
                    data={"interface": interface, "mode": mode},
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        if item.source is None:
            return None
        interface = pf_text(item.source, "interface")
        subnet = pf_text(item.source, "subnet")
        return f"{interface}:{subnet}" if interface or subnet else None

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        vip = item.source
        if vip is None:
            return []
        mode = item.data["mode"]
        root = E("vip", uuid=ctx.uuid(item.id))
        sub(root, "mode", mode)
        sub(root, "interface", item.data["interface"] or "")
        sub(root, "subnet", pf_text(vip, "subnet"))
        sub(root, "subnet_bits", pf_text(vip, "subnet_bits"))
        sub(root, "descr", pf_desc(vip))
        sub(root, "noexpand", opn_bool(vip.find("noexpand") is not None))
        sub(root, "nobind", "0")
        if mode == "carp":
            sub(root, "vhid", pf_text(vip, "vhid"))
            sub(root, "advbase", pf_text(vip, "advbase"))
            sub(root, "advskew", pf_text(vip, "advskew"))
            sub(root, "password", pf_text(vip, "password"))
            sub(root, "nosync", "0")
        return [Placement("virtualip", Mode.APPEND, element=root, item_id=item.id)]
