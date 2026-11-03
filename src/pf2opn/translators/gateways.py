"""Routing gateways (design §12.1.4). base_policy ``merge`` by name."""

from __future__ import annotations

import re

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, opn_bool, pf_desc, pf_text, sub

_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,32}$")

#: Copied verbatim when non-empty (the monitoring thresholds, §12.1.4).
_COPIED_TAGS = (
    "latencylow",
    "latencyhigh",
    "losslow",
    "losshigh",
    "interval",
    "time_period",
    "loss_interval",
    "data_length",
)


@register
class GatewaysTranslator(Translator):
    section = "gateways"
    title = "Gateways"
    tier = 1
    order = 60
    base_policy = "merge"
    container = "OPNsense/Gateways"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        default4 = pf_text(src.pf, "gateways/defaultgw4")
        default6 = pf_text(src.pf, "gateways/defaultgw6")
        items: list[Item] = []
        for gw in src.pf.findall("gateways/gateway_item"):
            name = pf_text(gw, "name")
            interface = pf_text(gw, "interface")
            label = f"Gateway {name} ({interface})"
            if not _NAME_RE.match(name):
                items.append(
                    Item(
                        f"gw:{name}",
                        self.section,
                        label,
                        gw,
                        Status.UNSUPPORTED,
                        reason=f"gateway name {name!r} is invalid on OPNsense",
                    )
                )
                continue
            if interface not in ifmap.included_keys():
                items.append(
                    Item(
                        f"gw:{name}",
                        self.section,
                        label,
                        gw,
                        Status.UNSUPPORTED,
                        reason=f"interface {interface} not migrated",
                    )
                )
                continue

            warnings: list[str] = []
            if gw.find("action_disable") is not None:
                warnings.append("\u2018disable gateway monitoring action\u2019 has no direct equivalent")
            items.append(
                Item(
                    f"gw:{name}",
                    self.section,
                    label,
                    gw,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    deps=[f"iface:{interface}"],
                    data={"defaultgw": name in (default4, default6)},
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        return pf_text(item.source, "name") or None

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        gw = item.source
        if gw is None:
            return []
        root = E("gateway_item", uuid=ctx.uuid(item.id))
        sub(root, "name", pf_text(gw, "name"))
        sub(root, "descr", pf_desc(gw))
        sub(root, "interface", pf_text(gw, "interface"))
        sub(root, "ipprotocol", pf_text(gw, "ipprotocol") or "inet")
        gateway = pf_text(gw, "gateway")
        sub(root, "gateway", "" if gateway == "dynamic" else gateway)
        sub(root, "monitor", pf_text(gw, "monitor"))
        sub(root, "monitor_disable", opn_bool(gw.find("monitor_disable") is not None))
        sub(root, "disabled", opn_bool(gw.find("disabled") is not None))
        sub(root, "fargw", opn_bool(gw.find("nonlocalgateway") is not None))
        sub(root, "force_down", opn_bool(gw.find("force_down") is not None))
        sub(root, "monitor_killstates", opn_bool(bool(pf_text(gw, "gw_down_kill_states"))))
        sub(root, "weight", str(self._weight(gw)))
        sub(root, "priority", "255")
        for tag in _COPIED_TAGS:
            value = pf_text(gw, tag)
            if value:
                sub(root, tag, value)
        sub(root, "defaultgw", opn_bool(bool(item.data.get("defaultgw"))))
        return [Placement("OPNsense/Gateways", Mode.APPEND, element=root, item_id=item.id)]

    @staticmethod
    def _weight(gw) -> int:
        value = pf_text(gw, "weight")
        if not value:
            return 1
        try:
            return max(1, min(10, int(value)))
        except ValueError:
            return 1
