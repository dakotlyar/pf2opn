"""Static routes (design §12.1.5). base_policy ``merge`` by network."""

from __future__ import annotations

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, opn_bool, pf_desc, pf_text, sub


@register
class RoutesTranslator(Translator):
    section = "routes"
    title = "Static routes"
    tier = 1
    order = 65
    base_policy = "merge"
    container = "staticroutes"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for route in src.pf.findall("staticroutes/route"):
            network = pf_text(route, "network")
            gateway = pf_text(route, "gateway")
            deps = [f"gw:{gateway}"] if gateway else []
            items.append(
                Item(
                    f"route:{network}",
                    self.section,
                    f"Route {network} via {gateway}" if gateway else f"Route {network}",
                    route,
                    Status.OK,
                    deps=deps,
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        return pf_text(item.source, "network") or None

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        route = item.source
        if route is None:
            return []
        root = E("route", uuid=ctx.uuid(item.id))
        sub(root, "network", pf_text(route, "network"))
        sub(root, "gateway", pf_text(route, "gateway"))
        sub(root, "descr", pf_desc(route))
        sub(root, "enabled", opn_bool(route.find("disabled") is None))
        return [Placement("staticroutes", Mode.APPEND, element=root, item_id=item.id)]
