"""Trust: certificate authorities and certificates (design §12.1.14).

``ca``    ``pf:ca[]``   -> root-level ``ca[]``   (``/ca+`` mount)
``cert``  ``pf:cert[]`` -> root-level ``cert[]`` (``/cert+`` mount)

Both are ``merge`` by ``refid``. The ``+`` mounts repeat at the *root* of
``<opnsense>`` (there is no container element), so the merge container is ``.``
and the placement path is empty.
"""

from __future__ import annotations

import base64
import binascii
import re

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, pf_desc, pf_text, sub

_BEGIN = "-----BEGIN CERTIFICATE-----"
_END = "-----END CERTIFICATE-----"

#: OPNsense 26.7.8 stores one certificate per entry; a chain is trimmed.
_CHAIN_WARNING = "chain trimmed to leaf (OPNsense 26.7.8 single-cert rule)"


def _decode_base64(text: str) -> str | None:
    """Base64-decode *text* (whitespace ignored); ``None`` when it is not base64."""
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", text), validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw.decode("utf-8", "replace")


def _pem_blocks(pem: str) -> list[str]:
    blocks: list[str] = []
    index = 0
    while True:
        start = pem.find(_BEGIN, index)
        if start == -1:
            break
        end = pem.find(_END, start)
        if end == -1:
            break
        end += len(_END)
        blocks.append(pem[start:end])
        index = end
    return blocks


def check_crt(crt: str) -> tuple[str | None, str | None, str | None]:
    """Validate a pfSense ``crt`` value.

    Returns ``(output_value, warning, reason)``. pfSense stores ``crt`` as
    base64 of a PEM bundle, and OPNsense 26.7.8 keeps one certificate per entry,
    so a bundle with several ``BEGIN CERTIFICATE`` blocks is trimmed to the leaf.
    """
    if not crt:
        return None, None, "certificate data is empty"
    decoded = _decode_base64(crt)
    if decoded is None:
        return None, None, "certificate data could not be decoded"
    blocks = _pem_blocks(decoded)
    if not blocks:
        return None, None, "no PEM certificate found in certificate data"
    if len(blocks) > 1:
        trimmed = base64.b64encode(blocks[0].encode("utf-8")).decode("ascii")
        return trimmed, _CHAIN_WARNING, None
    return crt, None, None


@register
class CaTranslator(Translator):
    section = "ca"
    title = "Certificate authorities"
    tier = 1
    order = 30
    base_policy = "merge"
    container = "."

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for ca in src.pf.findall("ca"):
            refid = pf_text(ca, "refid")
            label = f"CA {pf_desc(ca) or refid}"
            crt, warning, reason = check_crt(pf_text(ca, "crt"))
            if reason is not None:
                items.append(
                    Item(f"ca:{refid}", self.section, label, ca, Status.UNSUPPORTED, reason=reason)
                )
                continue
            caref = pf_text(ca, "caref")
            deleted = {f"ca:{caref}"} if caref else set()
            items.append(
                Item(
                    f"ca:{refid}",
                    self.section,
                    label,
                    ca,
                    Status.PARTIAL if warning else Status.OK,
                    warnings=[warning] if warning else [],
                    deps=sorted(deleted),
                    data={"crt": crt, "caref": caref},
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        if item.source is None or item.source.tag != "ca":
            return None
        return pf_text(item.source, "refid") or None

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        ca = item.source
        if ca is None:
            return []
        root = E("ca", uuid=ctx.uuid(item.id))
        sub(root, "refid", pf_text(ca, "refid"))
        sub(root, "descr", pf_desc(ca))
        sub(root, "crt", item.data["crt"])
        prv = pf_text(ca, "prv")
        if prv:
            sub(root, "prv", prv)
        sub(root, "serial", pf_text(ca, "serial") or "0")
        if item.data["caref"]:
            sub(root, "caref", item.data["caref"])
        return [Placement("", Mode.APPEND, element=root, item_id=item.id)]


@register
class CertTranslator(Translator):
    section = "cert"
    title = "Certificates"
    tier = 1
    order = 31
    base_policy = "merge"
    container = "."

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for cert in src.pf.findall("cert"):
            refid = pf_text(cert, "refid")
            descr = pf_desc(cert)
            label = f"Certificate {descr or refid}"
            crt, warning, reason = check_crt(pf_text(cert, "crt"))
            if reason is not None:
                items.append(
                    Item(
                        f"cert:{refid}", self.section, label, cert, Status.UNSUPPORTED, reason=reason
                    )
                )
                continue
            caref = pf_text(cert, "caref")
            warnings = [warning] if warning else []
            default_selected = "webConfigurator default" not in descr
            items.append(
                Item(
                    f"cert:{refid}",
                    self.section,
                    label,
                    cert,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    deps=[f"ca:{caref}"] if caref else [],
                    default_selected=default_selected,
                    data={"crt": crt, "caref": caref},
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        if item.source is None or item.source.tag != "cert":
            return None
        return pf_text(item.source, "refid") or None

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        cert = item.source
        if cert is None:
            return []
        root = E("cert", uuid=ctx.uuid(item.id))
        sub(root, "refid", pf_text(cert, "refid"))
        sub(root, "descr", pf_desc(cert))
        if item.data["caref"]:
            sub(root, "caref", item.data["caref"])
        sub(root, "crt", item.data["crt"])
        prv = pf_text(cert, "prv")
        if prv:
            sub(root, "prv", prv)
        csr = pf_text(cert, "csr")
        if csr:
            sub(root, "csr", csr)
        return [Placement("", Mode.APPEND, element=root, item_id=item.id)]
