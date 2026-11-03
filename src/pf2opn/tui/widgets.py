"""Shared TUI widgets: the item tree and the detail pane (design §15)."""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable

from lxml import etree
from textual.message import Message
from textual.widgets import Static, TabbedContent, TabPane, TextArea, Tree

from ..context import SourceContext, WriteContext
from ..model import Item, Plan, Status
from ..registry import all_translators, get
from ..secrets import redacted_xml

#: The status glyph per §15 item label.
STATUS_GLYPH: dict[Status, str] = {
    Status.OK: "\u2714",  # ✔
    Status.PARTIAL: "\u26a0",  # ⚠
    Status.UNSUPPORTED: "\u2716",  # ✖
}

_NOT_TRANSLATABLE = "Not translatable"


def is_toggleable(item: Item) -> bool:
    """True when space may change this item's selection on the select screen."""
    return item.status != Status.UNSUPPORTED and not item.readonly_selection


def item_label(item: Item, selected: bool) -> str:
    """The §15 tree label: ``[x] ✔ label`` / ``[ ] ⚠ label`` / ``    ✖ label``."""
    glyph = STATUS_GLYPH[item.status]
    if item.status == Status.UNSUPPORTED:
        return f"    {glyph} {item.label}"
    return f"[{'x' if selected else ' '}] {glyph} {item.label}"


def section_label(title: str, items: list[Item], selected: set[str]) -> str:
    """The §15 section label: ``[x]/[-]/[ ] title (selected/total)``."""
    selectable = [item for item in items if is_toggleable(item)]
    chosen = sum(1 for item in selectable if item.id in selected)
    if not selectable or chosen == 0:
        prefix = "[ ]"
    elif chosen == len(selectable):
        prefix = "[x]"
    else:
        prefix = "[-]"
    total_selected = sum(1 for item in items if item.id in selected)
    return f"{prefix} {title} ({total_selected}/{len(items)})"


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
                    item_label(item, item.id in plan.selected),
                    data={"kind": "item", "id": item.id},
                )
            first = first or node

        if tail:
            node = self.root.add(
                _NOT_TRANSLATABLE, data={"kind": "group"}, expand=True
            )
            for item in tail:
                node.add_leaf(
                    item_label(item, item.id in plan.selected),
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
            node.text = placement.text
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
        self.titles = [self.render_str(title) for title in ("Source", "Preview", "Notes")]
        # compose_add_child is TabbedContent's hook for collecting its panes; the
        # default compose() turns each into a tab pane when the widget mounts.
        self.compose_add_child(
            TabPane(
                "Source",
                TextArea(id="source", read_only=True, language="xml"),
                id="tab-source",
            )
        )
        self.compose_add_child(
            TabPane(
                "Preview",
                TextArea(id="preview", read_only=True, language="xml"),
                id="tab-preview",
            )
        )
        self.compose_add_child(TabPane("Notes", Static(id="notes"), id="tab-notes"))

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
