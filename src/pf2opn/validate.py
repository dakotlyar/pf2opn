"""Structural validation of the output (design §13).

M2 implements checks 1 (well-formed round trip) and 2 (unique uuids). Later
milestones add the referential checks; the warning about a base model version
mismatch (§10.3) is emitted here because it needs the output tree.
"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from .model import Plan
from .versions import MODEL_VERSIONS


@dataclass
class Finding:
    level: str  # "error" | "warning"
    message: str
    path: str | None = None


def _path_of(el: etree._Element) -> str:
    parts: list[str] = []
    node: etree._Element | None = el
    while node is not None and isinstance(node.tag, str):
        parts.append(node.tag)
        node = node.getparent()
    return "/".join(reversed(parts))


def validate(out: etree._Element, plan: Plan) -> list[Finding]:
    """Return every finding for *out*; ``error`` findings block writing."""
    findings: list[Finding] = []
    findings.extend(_check_well_formed(out))
    findings.extend(_check_unique_uuids(out))
    findings.extend(_check_versions(out))
    return findings


def _check_well_formed(out: etree._Element) -> list[Finding]:
    try:
        data = etree.tostring(out)
        reparsed = etree.fromstring(data)
    except etree.XMLSyntaxError as exc:
        return [Finding("error", f"output is not well-formed XML: {exc}")]
    if reparsed.tag != "opnsense":
        return [Finding("error", f"root element is <{reparsed.tag}>, expected <opnsense>")]
    return []


def _check_unique_uuids(out: etree._Element) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for el in out.iter():
        if not isinstance(el.tag, str):
            continue
        uuid = el.get("uuid")
        if uuid is None:
            continue
        if uuid in seen:
            findings.append(Finding("error", f"duplicate uuid {uuid}", _path_of(el)))
        else:
            seen.add(uuid)
    return findings


def _check_versions(out: etree._Element) -> list[Finding]:
    findings: list[Finding] = []
    for path, expected in MODEL_VERSIONS.items():
        matches = out.xpath(path)
        if not matches:
            continue
        actual = matches[0].get("version")
        if actual is not None and actual != expected:
            findings.append(
                Finding(
                    "warning",
                    f"base model {path} is version {actual}, pf2opn was written for {expected}",
                    path,
                )
            )
    return findings