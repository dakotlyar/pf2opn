"""mDNS reflector / Avahi (design §12.1.20).

Source ``pf:installedpackages/avahi/config`` is one settings block -> one item
``avahi:config`` rendering into ``//OPNsense/MDNSRepeater`` (plugin
``os-mdns-repeater``, model version 1.0.1). ``base_policy`` is ``none``: the
target is a single settings mount with no base equivalent to merge.
"""

from __future__ import annotations

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import Translator, opn_bool, pf_text

#: Fields with no OPNsense equivalent: a truthy (by text) value earns a PARTIAL
#: warning; an empty element — as in the reference config — is off and silent.
_NO_EQUIVALENT = (
    ("disable_ipv4", "pfSense disabled IPv4 reflection; os-mdns-repeater reflects IPv4"),
    ("disable_ipv6", "pfSense disabled IPv6 reflection; os-mdns-repeater reflects IPv4"),
    ("override_hostname", "hostname override has no OPNsense equivalent"),
    ("override_domain", "domain override has no OPNsense equivalent"),
    ("publishing", "publishing settings have no OPNsense equivalent"),
    ("filtering", "filtering settings have no OPNsense equivalent"),
)

_MIN_INTERFACES = 2
_MAX_INTERFACES = 5


def _nonempty(el: etree._Element | None) -> bool:
    """True when *el* has non-whitespace text, or a child that is itself non-empty.

    §12.4's non-empty rule: a present-but-empty ``<config/>`` does not raise an item.
    """
    if el is None:
        return False
    if (el.text or "").strip():
        return True
    return any(_nonempty(child) for child in el if isinstance(child.tag, str))


def _truthy(value: str) -> bool:
    """A pfSense flag is on unless its text is empty or an explicit negative."""
    return value.strip().lower() not in {"", "no", "off", "0", "false"}


def _split_keys(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


@register
class AvahiTranslator(Translator):
    section = "avahi"
    title = "mDNS reflector (Avahi)"
    tier = 1
    order = 160
    base_policy = "none"
    container = None

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        config = src.pf.find("installedpackages/avahi/config")
        if not _nonempty(config):
            return []

        enabled = _truthy(pf_text(config, "enable"))
        tokens = _split_keys(pf_text(config, "interfaces"))
        included = ifmap.included_keys()
        kept = [key for key in tokens if key in included]
        dropped = [key for key in tokens if key not in included]

        action = pf_text(config, "action")
        carp = pf_text(config, "carpstatusvid")
        enablecarp = carp not in {"", "none"}

        warnings: list[str] = []
        if enabled:
            for field, message in _NO_EQUIVALENT:
                if _truthy(pf_text(config, field)):
                    warnings.append(message)
            if not _truthy(pf_text(config, "reflection")):
                warnings.append(
                    "os-mdns-repeater always reflects; reflection cannot be off "
                    "while the service is enabled"
                )
            if enablecarp:
                warnings.append(
                    "pfSense gated the service on one CARP VIP; OPNsense's "
                    "enablecarp gates on any CARP master"
                )
            warnings.extend(
                f"interface {key} dropped (not migrated)" for key in dropped
            )
            if len(kept) == 1:
                warnings.append(
                    f"os-mdns-repeater needs at least {_MIN_INTERFACES} listen interfaces"
                )
            elif len(kept) > _MAX_INTERFACES:
                warnings.append(
                    f"os-mdns-repeater supports at most {_MAX_INTERFACES} listen interfaces"
                )

        status = Status.OK
        reason = ""
        if enabled and action == "deny":
            status = Status.UNSUPPORTED
            reason = (
                "pfSense used an action=deny list; OPNsense has no deny-list — "
                "set Listen Interfaces manually"
            )
        elif enabled and not tokens:
            status = Status.UNSUPPORTED
            reason = (
                "pfSense reflects on all interfaces; os-mdns-repeater needs an "
                "explicit list — set Listen Interfaces manually"
            )
        elif enabled and not kept:
            status = Status.UNSUPPORTED
            reason = "no migrated interfaces to reflect"
        elif warnings:
            status = Status.PARTIAL

        return [
            Item(
                "avahi:config",
                self.section,
                self.title,
                config,
                status,
                reason=reason,
                warnings=warnings,
                default_selected=enabled,
                requires_plugin="os-mdns-repeater",
                data={
                    "enabled": enabled,
                    "enablecarp": enablecarp,
                    "interfaces": ",".join(kept),
                },
            )
        ]

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        if item.status == Status.UNSUPPORTED:
            return []
        data = item.data
        return [
            Placement(
                "OPNsense/MDNSRepeater/enabled",
                Mode.SET,
                text=opn_bool(data["enabled"]),
                item_id=item.id,
            ),
            Placement(
                "OPNsense/MDNSRepeater/enablecarp",
                Mode.SET,
                text=opn_bool(data["enablecarp"]),
                item_id=item.id,
            ),
            Placement(
                "OPNsense/MDNSRepeater/interfaces",
                Mode.SET,
                text=data["interfaces"],
                item_id=item.id,
            ),
        ]
