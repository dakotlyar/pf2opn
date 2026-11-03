"""Network interfaces (design §12.1.2). base_policy ``merge`` by interface key."""

from __future__ import annotations

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, pf_desc, pf_text, sanitize_ifdescr, sub

#: Tags copied straight through when non-empty.
_COPIED_TAGS = (
    "spoofmac",
    "mtu",
    "mss",
    "media",
    "mediaopt",
    "dhcphostname",
    "alias-address",
    "alias-subnet",
    "dhcprejectfrom",
)

#: ``track6`` tags copied when non-empty.
_TRACK6_TAGS = ("track6-interface", "track6-prefix-id")

#: Presence flags written as ``1``.
_FLAG_TAGS = ("blockpriv", "blockbogons")

#: Recognised tags; anything else with text is reported once as dropped.
_KNOWN_TAGS = frozenset(
    {"if", "descr", "enable", "ipaddr", "subnet", "ipaddrv6", "subnetv6", "gateway", "gatewayv6",
     *_COPIED_TAGS, *_TRACK6_TAGS, *_FLAG_TAGS}
)


def _is_adv_dhcp(tag: str) -> bool:
    return tag.startswith("adv_dhcp_") or tag.startswith("adv_dhcp6_")


def _unknown_with_text(child: etree._Element) -> bool:
    tag = child.tag
    if not isinstance(tag, str):
        return False
    if tag in _KNOWN_TAGS or _is_adv_dhcp(tag):
        return False
    return bool((child.text or "").strip())


@register
class InterfacesTranslator(Translator):
    section = "interfaces"
    title = "Interfaces"
    tier = 1
    order = 40
    base_policy = "merge"
    container = "interfaces"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for key, pf_iface in src.pf_ifaces.items():
            target = ifmap.targets.get(key)
            kind = target.kind if target is not None else "physical"
            label = f"Interface {key}: {pf_iface.descr or pf_iface.device}"
            if kind == "unsupported":
                items.append(
                    Item(
                        f"iface:{key}",
                        self.section,
                        label,
                        pf_iface.el,
                        Status.UNSUPPORTED,
                        reason=f"device type {pf_iface.device} is not migrated in v1",
                    )
                )
                continue
            if kind == "physical" and not (target and target.opn_device):
                items.append(
                    Item(
                        f"iface:{key}",
                        self.section,
                        label,
                        pf_iface.el,
                        Status.UNSUPPORTED,
                        reason="NIC not mapped",
                    )
                )
                continue

            deps: list[str] = []
            if kind == "vlan":
                deps.append(f"vlan:{pf_iface.device}")
            elif kind == "wireguard":
                deps.append(f"wgtun:{pf_iface.device}")

            warnings = self._warnings(pf_iface.el)
            items.append(
                Item(
                    f"iface:{key}",
                    self.section,
                    label,
                    pf_iface.el,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    deps=deps,
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        if item.source is None or not isinstance(item.source.tag, str):
            return None
        return item.source.tag

    # -- discovery helpers -------------------------------------------------- #

    @staticmethod
    def _warnings(el: etree._Element) -> list[str]:
        warnings: list[str] = []
        descr = pf_desc(el)
        sanitized = sanitize_ifdescr(descr)
        if descr and sanitized != descr:
            warnings.append(f'interface description "{descr}" contains characters OPNsense rejects')
        if pf_text(el, "ipaddrv6") == "track6":
            warnings.append("consider OPNsense 'Identity association' mode")
        if any((child.text or "").strip() for child in el if _is_adv_dhcp(child.tag)):
            warnings.append("advanced DHCP client options dropped")
        unknown = sorted({child.tag for child in el if _unknown_with_text(child)})
        if unknown:
            warnings.append("unknown interface options dropped: " + ", ".join(unknown))
        return warnings

    # -- rendering ---------------------------------------------------------- #

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        el = item.source
        if el is None:
            return []
        key = item.id.split(":", 1)[1]
        target = ctx.plan.ifmap.targets.get(key)
        kind = target.kind if target is not None else "physical"

        root = E(key)
        sub(root, "if", ctx.device(pf_text(el, "if")) or "")
        descr = sanitize_ifdescr(pf_desc(el))
        if descr:
            sub(root, "descr", descr)
        if el.find("enable") is not None:
            sub(root, "enable", "1")

        if kind != "wireguard":
            self._copy_when_present(root, el, "ipaddr")
            self._copy_when_present(root, el, "subnet")
        self._copy_when_present(root, el, "ipaddrv6")
        self._copy_when_present(root, el, "subnetv6")
        for tag in _TRACK6_TAGS:
            self._copy_when_nonempty(root, el, tag)
        self._copy_when_nonempty(root, el, "gateway")
        self._copy_when_nonempty(root, el, "gatewayv6")
        for tag in _FLAG_TAGS:
            if el.find(tag) is not None:
                sub(root, tag, "1")
        for tag in _COPIED_TAGS:
            self._copy_when_nonempty(root, el, tag)

        return [Placement(f"interfaces/{key}", Mode.REPLACE, element=root, item_id=item.id)]

    @staticmethod
    def _copy_when_present(root: etree._Element, el: etree._Element, tag: str) -> None:
        if el.find(tag) is not None:
            sub(root, tag, (el.findtext(tag) or "").strip())

    @staticmethod
    def _copy_when_nonempty(root: etree._Element, el: etree._Element, tag: str) -> None:
        value = pf_text(el, tag)
        if value:
            sub(root, tag, value)
