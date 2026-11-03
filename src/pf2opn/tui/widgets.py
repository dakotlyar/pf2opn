"""Shared TUI widgets: the item tree and the detail pane (design §15)."""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable

from lxml import etree
from rich.text import Text
from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Static, TabbedContent, TabPane, TextArea, Tree

from ..context import SourceContext, WriteContext
from ..model import Item, Plan, Status
from ..registry import all_translators, get
from ..secrets import redacted_xml

#: The trailing status marker per §15 item label (OK is unmarked).
STATUS_MARKER: dict[Status, str] = {
    Status.OK: "",
    Status.PARTIAL: " \u26a0",  # ⚠
    Status.UNSUPPORTED: " \u2716",  # ✖
}

#: Style per §15 item status, applied to the label + marker (not the bracket).
STATUS_STYLE: dict[Status, str] = {
    Status.OK: "green",
    Status.PARTIAL: "yellow",
    Status.UNSUPPORTED: "red dim",
}

_NOT_TRANSLATABLE = "Not translatable"


def is_selectable(item: Item) -> bool:
    """True when this item's selection counts (UNSUPPORTED items never count)."""
    return item.status != Status.UNSUPPORTED


def is_toggleable(item: Item) -> bool:
    """True when space may change this item's selection on the select screen."""
    return is_selectable(item) and not item.readonly_selection


def item_label(item: Item, selected: bool) -> str:
    """The §15 item label: selection bracket, then label, then status marker.

    The leading bracket is selection only (``[x]`` selected, ``[ ]`` not; never
    ``[-]``). Status is a trailing glyph present only on non-OK items, so it
    can't be read as the checkbox. Every row is bracketed, including
    UNSUPPORTED ones, so the column aligns.
    """
    check = "[x]" if selected and is_selectable(item) else "[ ]"
    return f"{check} {item.label}{STATUS_MARKER[item.status]}"


def item_text(item: Item, selected: bool) -> Text:
    """The tree renderable for *item*: the plain §15 label plus its status colour.

    The selection bracket is left unstyled; the status colour marks the label
    and its trailing marker only, so the glyph — not the colour — says selected.
    """
    label = item_label(item, selected)
    text = Text(label[:3], style="")
    text.append(label[3:], style=STATUS_STYLE[item.status])
    return text


def section_label(title: str, items: list[Item], selected: set[str]) -> str:
    """The §15 section label: ``[x]/[-]/[ ] title (chosen/total)``.

    Both the bracket and the counts are computed over the section's *selectable*
    items, so the bracket is ``[x]`` exactly when ``chosen == total``.
    """
    selectable = [i for i in items if is_selectable(i)]
    chosen = sum(1 for i in selectable if i.id in selected)
    total = len(selectable)
    prefix = "[ ]" if total == 0 or chosen == 0 else "[x]" if chosen == total else "[-]"
    return f"{prefix} {title} ({chosen}/{total})"


class ItemTree(Tree):
    """A :class:`~textual.widgets.Tree` whose ``space`` posts a toggle request.

    Textual's stock ``Tree`` expands the highlighted node on space; §15 needs
    space to toggle the item instead, so the action is repurposed into a message
    the screen handles (expansion stays on the arrow keys).
    """

    class ToggleRequested(Message):
        """Posted when space is pressed on the highlighted node."""

        def __init__(self, node) -> None:
            self.node = node
            super().__init__()

    def action_toggle_node(self) -> None:  # noqa: D102 - Textual action hook
        node = self.cursor_node
        if node is not None:
            self.post_message(self.ToggleRequested(node))

    def build(
        self, plan: Plan, predicate: Callable[[Item], bool] | None = None
    ) -> None:
        """Fill the tree from *plan*, keeping only items matching *predicate*."""
        predicate = predicate or (lambda _item: True)
        self.root.remove_children()
        first = None
        tail: list[Item] = []

        for translator in all_translators():
            items = [item for item in plan.items if item.section == translator.section]
            if not items:
                continue
            if translator.tier == 0:
                tail.extend(item for item in items if predicate(item))
                continue
            shown = [item for item in items if predicate(item)]
            if not shown:
                continue
            node = self.root.add(
                section_label(translator.title, items, plan.selected),
                data={"kind": "section", "section": translator.section},
                expand=True,
            )
            for item in shown:
                node.add_leaf(
                    item_text(item, item.id in plan.selected),
                    data={"kind": "item", "id": item.id},
                )
            first = first or node

        if tail:
            node = self.root.add(
                _NOT_TRANSLATABLE, data={"kind": "group"}, expand=True
            )
            for item in tail:
                node.add_leaf(
                    item_text(item, item.id in plan.selected),
                    data={"kind": "item", "id": item.id},
                )
            first = first or node

        if first is not None:
            self.move_cursor(first)


def _preview_text(item: Item, plan: Plan, src: SourceContext) -> str:
    """The redacted XML of ``render()`` placements for *item* (§15 Preview tab)."""
    if item.status == Status.UNSUPPORTED:
        reason = item.reason or "not translatable"
        return f"(not translatable)\n{reason}"
    # Render on a copy: some translators adjust warnings/status during render,
    # and a preview must not mutate the live item.
    probe = dataclasses.replace(
        item,
        warnings=list(item.warnings),
        deps=list(item.deps),
        followups=list(item.followups),
    )
    ctx = WriteContext(src, plan)
    try:
        placements = get(item.section).render(probe, ctx)
    except Exception as exc:  # noqa: BLE001 - a preview must never crash the TUI
        return f"(preview unavailable: {exc})"
    root = etree.Element("preview")
    for placement in placements:
        node = etree.SubElement(root, "placement")
        node.set("path", placement.path)
        node.set("mode", placement.mode.value)
        if placement.element is not None:
            node.append(copy.deepcopy(placement.element))
        elif placement.text is not None:
            # Put the text in an element named after the placement's leaf so
            # redact_element() can see a secret tag (e.g. ``.../password``); a
            # bare ``<placement>`` element would leak the value (§11 rule 2).
            leaf = placement.path.rstrip("/").split("/")[-1].split("[", 1)[0]
            child = etree.SubElement(node, leaf or "value")
            child.text = placement.text
    return redacted_xml(root)


def _notes_text(item: Item, plan: Plan) -> str:
    lines = [f"Status: {item.status.value.upper()}"]
    if item.reason:
        lines.append(f"Reason: {item.reason}")
    if item.warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in item.warnings)
    if item.deps:
        lines.append("Depends on:")
        for dep in item.deps:
            target = plan.by_id.get(dep)
            label = target.label if target is not None else dep
            state = "selected" if dep in plan.selected else "not selected"
            lines.append(f"  - {label} [{state}]")
    if item.requires_plugin:
        lines.append(f"Plugin: {item.requires_plugin}")
    if item.followups:
        lines.append("Follow-ups:")
        lines.extend(f"  - {followup}" for followup in item.followups)
    return "\n".join(lines)


class DetailPane(TabbedContent):
    """The Source / Preview / Notes pane (design §15).

    Source and Preview always serialize through :func:`redacted_xml`, so a secret
    value can never reach the screen (§11 rule 2).
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(id="detail", **kwargs)

    def compose(self) -> ComposeResult:
        yield TabPane(
            "Source",
            TextArea(id="source", read_only=True, language="xml"),
            id="tab-source",
        )
        yield TabPane(
            "Preview",
            TextArea(id="preview", read_only=True, language="xml"),
            id="tab-preview",
        )
        yield TabPane("Notes", Static(id="notes"), id="tab-notes")

    def show_item(self, item: Item | None, plan: Plan, src: SourceContext) -> None:
        """Display *item* (or a placeholder when *item* is ``None``)."""
        source = self.query_one("#source", TextArea)
        preview = self.query_one("#preview", TextArea)
        notes = self.query_one("#notes", Static)
        if item is None:
            source.load_text("")
            preview.load_text("")
            notes.update("Select an item to see its details.")
            return
        if item.source is not None:
            source.load_text(redacted_xml(item.source))
        else:
            source.load_text("(no source element)")
        preview.load_text(_preview_text(item, plan, src))
        notes.update(_notes_text(item, plan))
