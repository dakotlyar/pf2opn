"""Apply placements to a copy of the base config (design §10)."""

from __future__ import annotations

import copy
import os
import time
from pathlib import Path

from lxml import etree

from .context import SourceContext, WriteContext
from .model import Mode, Placement, Plan, Status
from .registry import all_translators
from .versions import MODEL_VERSIONS


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #


def _step(node: etree._Element, segment: str, create: bool) -> etree._Element | None:
    if "[" in segment:  # an XPath step such as ``user[name='root']``
        found = node.xpath(segment)
        return found[0] if found else None
    found = node.find(segment)
    if found is not None:
        return found
    if create:
        return etree.SubElement(node, segment)
    return None


def _resolve(
    root: etree._Element, path: str, create: bool = True
) -> tuple[etree._Element | None, str]:
    """Return ``(parent, last_segment)`` for *path* relative to *root*."""
    segments = [seg for seg in path.split("/") if seg]
    node = root
    for segment in segments[:-1]:
        node = _step(node, segment, create)
        if node is None:
            return None, segments[-1]
    return node, segments[-1]


def _ensure(root: etree._Element, path: str) -> etree._Element | None:
    """Resolve *path*, creating every missing plain segment."""
    node = root
    for segment in [seg for seg in path.split("/") if seg]:
        node = _step(node, segment, create=True)
        if node is None:
            return None
    return node


def apply(out: etree._Element, placements: list[Placement]) -> None:
    """Apply *placements* to the output tree in order."""
    for placement in placements:
        if placement.mode == Mode.APPEND:
            parent = _ensure(out, placement.path)
            if parent is not None and placement.element is not None:
                parent.append(placement.element)
        elif placement.mode == Mode.SET:
            _apply_set(out, placement)
        elif placement.mode == Mode.REPLACE:
            _apply_replace(out, placement)
        elif placement.mode == Mode.REMOVE:
            for el in out.xpath(placement.path):
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)


def _apply_set(out: etree._Element, placement: Placement) -> None:
    parent, last = _resolve(out, placement.path, create=True)
    if parent is None:
        return
    if "[" in last:
        found = parent.xpath(last)
        if found:
            found[0].text = placement.text
        return
    child = parent.find(last)
    if child is None:
        child = etree.SubElement(parent, last)
    child.text = placement.text


def _apply_replace(out: etree._Element, placement: Placement) -> None:
    parent, last = _resolve(out, placement.path, create=True)
    if parent is None:
        return
    if "[" in last:
        existing = parent.xpath(last)
    else:
        existing = parent.findall(last)
    if existing:
        index = list(parent).index(existing[0])
        for el in existing:
            parent.remove(el)
    else:
        index = len(parent)
    if placement.element is not None:
        parent.insert(index, placement.element)
    elif placement.text is not None:
        new = etree.Element(last.split("[", 1)[0])
        new.text = placement.text
        parent.insert(index, new)


# --------------------------------------------------------------------------- #
# Base policies (§10.2)
# --------------------------------------------------------------------------- #


def apply_base_policy(out: etree._Element, translator, selected, ctx: WriteContext) -> None:
    policy = translator.base_policy
    if policy == "none":
        return
    if policy == "replace":
        container = translator.container
        if not container:
            return
        matches = out.xpath(container)
        if matches:
            for child in list(matches[0]):
                matches[0].remove(child)
        return
    if policy == "merge":
        # The base's own entries are kept; removing a base entry that shares an
        # item's identity key needs a per-translator identity key, which the
        # first "merge" translator introduces in milestone M3 (§10.2). No M2
        # translator uses "merge".
        return
    raise ValueError(f"unknown base_policy {policy!r} on section {translator.section!r}")


# --------------------------------------------------------------------------- #
# Versions and revision (§10.3, §10.4)
# --------------------------------------------------------------------------- #


def stamp_versions(out: etree._Element) -> None:
    for path, version in MODEL_VERSIONS.items():
        matches = out.xpath(path)
        if not matches:
            continue
        element = matches[0]
        if element.get("version") is None:
            element.set("version", version)


def stamp_revision(out: etree._Element, src: SourceContext) -> None:
    revision = out.find("revision")
    if revision is None:
        revision = etree.SubElement(out, "revision")
    for child in list(revision):
        revision.remove(child)
    username = etree.SubElement(revision, "username")
    username.text = "pf2opn"
    timestamp = etree.SubElement(revision, "time")
    timestamp.text = str(int(time.time()))
    description = etree.SubElement(revision, "description")
    description.text = f"pf2opn: migrated from pfSense {src.pf_hostname}"


# --------------------------------------------------------------------------- #
# Assembly and writing
# --------------------------------------------------------------------------- #


def build_output(plan: Plan, src: SourceContext) -> etree._Element:
    """Deep-copy the base and apply every selected item's placements."""
    out = copy.deepcopy(src.base)
    ctx = WriteContext(src, plan)
    for translator in all_translators():
        selected = [
            item
            for item in plan.items
            if item.section == translator.section
            and item.id in plan.selected
            and item.status != Status.UNSUPPORTED
        ]
        if not selected:
            continue
        apply_base_policy(out, translator, selected, ctx)
        for item in selected:
            apply(out, translator.render(item, ctx))
        apply(out, translator.finalize(selected, ctx))
    stamp_versions(out)
    stamp_revision(out, src)
    return out


def write_output(out: etree._Element, path: Path | str) -> None:
    """Serialize *out* to *path*, atomically and mode 0600."""
    path = Path(path)
    etree.indent(out, space="  ")
    data = etree.tostring(out, xml_declaration=True, encoding="UTF-8")

    tmp = Path(str(path) + ".tmp")
    if tmp.exists():
        tmp.unlink()
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)