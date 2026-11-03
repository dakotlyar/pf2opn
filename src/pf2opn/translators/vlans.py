"""VLANs (design §12.1.3). base_policy ``merge`` by ``vlanif``."""

from __future__ import annotations

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, pf_desc, pf_text, sub


@register
class VlansTranslator(Translator):
    section = "vlans"
    title = "VLANs"
    tier = 1
    order = 45
    base_policy = "merge"
    container = "vlans"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for vlan in src.pf.findall("vlans/vlan"):
            vlanif = pf_text(vlan, "vlanif")
            if not vlanif:
                continue
            parent = pf_text(vlan, "if")
            label = f"VLAN {pf_text(vlan, 'tag')} on {parent} ({vlanif})"
            if not ifmap.nic_map.get(parent):
                items.append(
                    Item(
                        f"vlan:{vlanif}",
                        self.section,
                        label,
                        vlan,
                        Status.UNSUPPORTED,
                        reason=f"parent NIC {parent} not mapped",
                    )
                )
            elif vlanif not in ifmap.vlan_names:
                items.append(
                    Item(
                        f"vlan:{vlanif}",
                        self.section,
                        label,
                        vlan,
                        Status.UNSUPPORTED,
                        reason="VLAN device name not assigned",
                    )
                )
            else:
                items.append(Item(f"vlan:{vlanif}", self.section, label, vlan, Status.OK))
        return items

    def identity(self, item: Item) -> str | None:
        return pf_text(item.source, "vlanif") or None

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        vlan = item.source
        if vlan is None:
            return []
        vlanif = pf_text(vlan, "vlanif")

        root = E("vlan", uuid=ctx.uuid(item.id))
        sub(root, "if", ctx.plan.ifmap.nic_map.get(pf_text(vlan, "if"), ""))
        sub(root, "tag", pf_text(vlan, "tag"))
        sub(root, "pcp", pf_text(vlan, "pcp") or "0")
        sub(root, "descr", pf_desc(vlan))
        sub(root, "vlanif", ctx.plan.ifmap.vlan_names.get(vlanif, ""))
        sub(root, "proto", "")

        return [Placement("vlans", Mode.APPEND, element=root, item_id=item.id)]
