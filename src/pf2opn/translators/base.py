"""Translator ABC and the shared helper functions (design §7.2, §7.3).

Translators are pure: ``render``/``finalize`` return :class:`Placement` objects
and never touch the output tree. Only ``writer.py`` mutates it (D11).
"""

from __future__ import annotations

import html
import ipaddress
import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement

#: Built-in interface keywords that are not items and are always valid (§8).
BUILTIN_IFACE_KEYWORDS = frozenset({"lo0"})


class Translator(ABC):
    section: ClassVar[str]  # unique key, matches Item.section
    title: ClassVar[str]  # tree heading
    tier: ClassVar[int]  # 1 or 2 (0 = classifier/unsupported only)
    order: ClassVar[int]  # display + render order
    base_policy: ClassVar[str]  # "merge" | "replace" | "none"
    container: ClassVar[str | None]  # output container path for base_policy

    @abstractmethod
    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        """Find the items this translator can migrate.

        Must never raise on odd input: problems become UNSUPPORTED items.
        """
        raise NotImplementedError

    def identity(self, item: Item) -> str | None:
        """The §10.2 identity key of *item*, or ``None``.

        The writer uses it for the ``merge`` base policy: a base element that
        shares this key is removed before the item is rendered. Sections that do
        not use ``merge`` leave the default ``None``.
        """
        return None

    def effective_base_policy(self, ctx: WriteContext) -> str:
        """The §10.2 base policy in force for *ctx*.

        Defaults to the class's :attr:`base_policy`; translators whose policy
        depends on a run option (filter ``--keep-base-rules``) override it.
        """
        return self.base_policy

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        """Produce placements for one selected, non-UNSUPPORTED item."""
        return []

    def finalize(self, selected: list[Item], ctx: WriteContext) -> list[Placement]:
        """Produce section-level placements once every item has rendered."""
        return []


# --------------------------------------------------------------------------- #
# §7.3 helpers
# --------------------------------------------------------------------------- #


def pf_text(el: etree._Element | None, path: str, default: str = "") -> str:
    """``findtext`` + ``.strip()``; a missing element or text yields *default*."""
    if el is None:
        return default
    value = el.findtext(path)
    if value is None:
        return default
    return value.strip()


def pf_desc(el: etree._Element | None, path: str = "descr") -> str:
    """A pfSense description: entity-unescaped, newline-flattened, <=255 chars."""
    value = pf_text(el, path)
    if not value:
        return ""
    for _ in range(2):
        new = html.unescape(value)
        if new == value:
            break
        value = new
    value = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return value[:255]


def pf_flag(el: etree._Element | None, path: str) -> bool:
    """True when the element exists and its text is not a negative token."""
    if el is None:
        return False
    sub = el.find(path)
    if sub is None:
        return False
    text = (sub.text or "").strip().lower()
    return text not in {"no", "off", "0", "false"}


def pf_list(el: etree._Element | None, path: str) -> list[str]:
    """Every matching element's stripped, non-empty text, in document order."""
    if el is None:
        return []
    out: list[str] = []
    for sub in el.findall(path):
        text = (sub.text or "").strip()
        if text:
            out.append(text)
    return out


def opn_bool(value: bool) -> str:
    """OPNsense boolean text: ``"1"`` or ``"0"``."""
    return "1" if value else "0"


def E(tag: str, text: str | None = None, **attrs: Any) -> etree._Element:
    """Create an element, optionally with text and attributes."""
    el = etree.Element(tag)
    if text is not None:
        el.text = text
    for key, value in attrs.items():
        el.set(key, str(value))
    return el


def sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    """Append a child element; empty/None text yields ``<tag/>``."""
    child = etree.SubElement(parent, tag)
    if text:
        child.text = str(text)
    return child


def fix_port(p: str, *, colon_to_dash: bool = False) -> str:
    """Normalise a pfSense port value.

    ``any-X`` becomes ``1-X`` and ``X-any`` becomes ``X-65535`` (matching
    ``list_legacy_rules.php``). The ``:`` -> ``-`` rewrite applies only to
    rule/NAT ports, so callers opt in with ``colon_to_dash=True``.
    """
    value = (p or "").strip()
    if not value:
        return ""
    if colon_to_dash and ":" in value:
        value = value.replace(":", "-")
    if value.startswith("any-"):
        value = "1-" + value[len("any-") :]
    if value.endswith("-any"):
        value = value[: -len("-any")] + "-65535"
    return value


def sanitize_ifdescr(s: str) -> str:
    """Interface description: anything outside ``[A-Za-z0-9_]`` becomes ``_``."""
    return re.sub(r"[^A-Za-z0-9_]", "_", s or "")[:255]


def sanitize_name(s: str, maxlen: int = 64) -> str:
    """WireGuard/ACL-style name: anything outside ``[0-9A-Za-z._-]`` becomes ``_``."""
    return re.sub(r"[^0-9A-Za-z._-]", "_", s or "")[:maxlen]


def _is_ip_or_cidr(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return True


def _as_ifmap(obj: Any) -> IfaceMap:
    return obj.plan.ifmap if hasattr(obj, "plan") else obj


def _iface_keyword_base(value: str, ifmap: IfaceMap) -> str | None:
    """Return the interface key if *value* is ``<key>`` or ``<key>ip``, else None."""
    if value in ifmap.targets:
        return value
    if value.endswith("ip") and value[:-2] in ifmap.targets:
        return value[:-2]
    return None


def map_net(net: str, ctx_or_ifmap: Any) -> tuple[str | None, str | None]:
    """Map a pfSense network token to its OPNsense value.

    Handles ``any``, ``(self)``, IP/CIDR, ``<key>``/``<key>ip`` interface keywords
    and alias names. Returns ``(value, None)`` or ``(None, error)``.
    """
    ifmap = _as_ifmap(ctx_or_ifmap)
    value = (net or "").strip()
    if not value or value == "any":
        return ("any", None)
    if value == "(self)":
        return ("(self)", None)

    base = _iface_keyword_base(value, ifmap)
    if base is not None:
        if base in ifmap.included_keys():
            return (value, None)
        return (None, f"interface {base} not migrated")

    if value in BUILTIN_IFACE_KEYWORDS or (
        value.endswith("ip") and value[:-2] in BUILTIN_IFACE_KEYWORDS
    ):
        return (value, None)

    # An IP/CIDR, an alias name, or an unknown token passes through unchanged.
    return (value, None)


def net_deps(net: str, src: Any) -> list[str]:
    """Item ids a network token depends on: ``alias:<n>`` or ``iface:<k>``."""
    context = getattr(src, "src", src)
    value = (net or "").strip()
    if not value or value in {"any", "(self)"}:
        return []
    if value in context.alias_names:
        return [f"alias:{value}"]
    base = value[:-2] if (value.endswith("ip") and value[:-2] in context.pf_ifaces) else value
    if base in context.pf_ifaces:
        return [f"iface:{base}"]
    return []


def pf_endpoint(el: etree._Element | None) -> tuple[str, bool, str]:
    """Read a ``<source>``/``<destination>`` element -> ``(net, not, port)``."""
    if el is None:
        return ("any", False, "")
    if el.find("any") is not None:
        net = "any"
    elif el.find("network") is not None:
        net = (el.findtext("network") or "").strip()
    elif el.find("address") is not None:
        net = (el.findtext("address") or "").strip()
    else:
        net = "any"
    not_ = el.find("not") is not None
    port = fix_port(el.findtext("port") or "")
    return (net, not_, port)


__all__ = [
    "Translator",
    "pf_text",
    "pf_desc",
    "pf_flag",
    "pf_list",
    "opn_bool",
    "E",
    "sub",
    "fix_port",
    "sanitize_ifdescr",
    "sanitize_name",
    "map_net",
    "net_deps",
    "pf_endpoint",
    "Mode",
    "Placement",
    "BUILTIN_IFACE_KEYWORDS",
]
