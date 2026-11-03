"""The Select screen: the item tree, detail pane and dependency cascade (§15, §8)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Static

from ...deps import closure, dependents
from ...model import Item, Status
from ..widgets import DetailPane, ItemTree, is_toggleable
from . import AppScreen

_FILTER_CYCLE = {"all": "selected", "selected": "problems", "problems": "all"}

_HELP = "\n".join(
    [
        "Select screen keys",
        "",
        "  space  toggle item / section",
        "  a      select all OK/PARTIAL in the section",
        "  n      select none in the section",
        "  A      select all default items",
        "  f      cycle filter: all / selected / problems",
        "  /      search by label (Esc clears)",
        "  e / E  expand / collapse all",
        "  i      back to the interface map",
        "  r      review and write",
        "  ?      this help",
        "  b      back one screen",
        "  q      quit",
    ]
)


class ConfirmDeselectModal(ModalScreen[bool]):
    """Ask before dropping items that selected items still depend on (§8)."""

    BINDINGS = [("y", "yes", "Yes"), ("n", "no", "No"), ("escape", "no", "Cancel")]

    def __init__(self, items: list[Item]) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        body = "\n".join(f"  - {item.label}" for item in self.items)
        with Vertical(id="confirm-deselect"):
            yield Static(
                f"Also deselect {len(self.items)} item(s) that depend on this one?",
                id="confirm-deselect-title",
            )
            yield Static(body, id="confirm-deselect-list")
            with Horizontal():
                yield Button("Deselect all", variant="warning", id="deselect-yes")
                yield Button("Cancel", id="deselect-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "deselect-yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class HelpModal(ModalScreen[None]):
    """The key list shown by ``?``."""

    BINDINGS = [("escape", "close", "Close"), ("question_mark", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-modal"):
            yield Static(_HELP, id="help-text")
            yield Button("Close", id="help-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class SelectScreen(AppScreen):
    """Choose which migratable items carry over."""

    BINDINGS = [
        ("a", "select_section", "All in section"),
        ("n", "none_section", "None in section"),
        ("A", "select_all_default", "All defaults"),
        ("f", "cycle_filter", "Filter"),
        ("slash", "search", "Search"),
        ("e", "expand_all", "Expand"),
        ("E", "collapse_all", "Collapse"),
        ("i", "goto_ifmap", "Interfaces"),
        ("r", "review", "Review"),
        ("question_mark", "help", "Help"),
        ("b", "back", "Back"),
        ("escape", "escape", "Back"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="select-body"):
            yield ItemTree("Migration", id="items")
            yield DetailPane()
        yield Input(placeholder="search by label\u2026", id="select-search")
        yield Footer()

    def on_mount(self) -> None:
        self.filter_mode = "all"
        self.search = ""
        tree = self.query_one(ItemTree)
        tree.show_root = False
        self._refresh()
        tree.focus()

    # ------------------------------------------------------------------ #
    # Tree building / highlighting
    # ------------------------------------------------------------------ #

    @property
    def _tree(self) -> ItemTree:
        return self.query_one(ItemTree)

    @property
    def _detail(self) -> DetailPane:
        return self.query_one(DetailPane)

    def _predicate(self):
        mode = self.filter_mode
        search = self.search.lower()

        def keep(item: Item) -> bool:
            if search and search not in item.label.lower():
                return False
            if mode == "selected":
                return item.id in self.pfapp.plan.selected
            if mode == "problems":
                return item.status in (Status.PARTIAL, Status.UNSUPPORTED)
            return True

        return keep

    def _current_key(self) -> tuple[str, str] | None:
        node = self._tree.cursor_node
        data = (node.data if node is not None else None) or {}
        kind = data.get("kind")
        if kind == "item":
            return ("item", data["id"])
        if kind == "section":
            return ("section", data["section"])
        return None

    def _find(self, key: tuple[str, str]):
        kind, value = key

        def match(node) -> bool:
            data = node.data or {}
            return data.get("kind") == kind and (
                data.get("id") == value or data.get("section") == value
            )

        def walk(node):
            if match(node):
                return node
            for child in node.children:
                found = walk(child)
                if found is not None:
                    return found
            return None

        return walk(self._tree.root)

    def _refresh(self) -> None:
        remembered = self._current_key()
        self._tree.build(self.pfapp.plan, self._predicate())
        if remembered is not None:
            node = self._find(remembered)
            if node is not None:
                self._tree.move_cursor(node)
        self._show(self._tree.cursor_node)

    def _show(self, node) -> None:
        data = (node.data if node is not None else None) or {}
        item = None
        if data.get("kind") == "item":
            item = self.pfapp.plan.by_id.get(data["id"])
        self._detail.show_item(item, self.pfapp.plan, self.pfapp.src)

    def on_tree_node_highlighted(self, event) -> None:
        self._show(event.node)

    # ------------------------------------------------------------------ #
    # Selection
    # ------------------------------------------------------------------ #

    def _label(self, item_id: str) -> str:
        item = self.pfapp.plan.by_id.get(item_id)
        return item.label if item is not None else item_id

    def _select(self, ids) -> None:
        plan = self.pfapp.plan
        needed = closure(set(ids), plan)
        extra = {
            item_id
            for item_id in needed
            if item_id not in plan.selected
            and plan.by_id[item_id].status != Status.UNSUPPORTED
        }
        if not extra:
            return
        plan.selected |= extra
        self.pfapp.mark_changed()
        added_deps = sorted(extra - set(ids))
        if added_deps:
            labels = ", ".join(self._label(item_id) for item_id in added_deps)
            self.notify(f"Also selected {len(added_deps)} dependency(ies): {labels}")
        self._refresh()

    def _deselect(self, items: list[Item]) -> None:
        plan = self.pfapp.plan
        ids = {item.id for item in items}
        dependants: set[str] = set()
        for item in items:
            dependants |= dependents(item.id, plan)
        dependants -= ids
        dependants &= plan.selected
        if dependants:
            modal = ConfirmDeselectModal(
                [plan.by_id[item_id] for item_id in sorted(dependants)]
            )
            self.app.push_screen(
                modal,
                lambda ok: self._apply_deselect(ids | dependants) if ok else None,
            )
            return
        self._apply_deselect(ids)

    def _apply_deselect(self, ids: set[str]) -> None:
        self.pfapp.plan.selected -= ids
        self.pfapp.mark_changed()
        self._refresh()

    def _toggle_item(self, item: Item) -> None:
        plan = self.pfapp.plan
        if item.status == Status.UNSUPPORTED:
            self.notify(f"{item.label} cannot be migrated.", severity="warning")
            return
        if item.readonly_selection:
            self.notify(
                "Interfaces are included on the interface map (press i).",
                severity="warning",
            )
            return
        if item.id in plan.selected:
            self._deselect([item])
        else:
            self._select([item.id])

    def _toggle_section(self, section: str) -> None:
        plan = self.pfapp.plan
        selectable = [
            item
            for item in plan.items
            if item.section == section and is_toggleable(item)
        ]
        if not selectable:
            self.notify("This section has nothing selectable.", severity="warning")
            return
        if all(item.id in plan.selected for item in selectable):
            self._deselect(selectable)
        else:
            self._select([item.id for item in selectable])

    def on_item_tree_toggle_requested(self, event: ItemTree.ToggleRequested) -> None:
        data = event.node.data or {}
        kind = data.get("kind")
        if kind == "item":
            item = self.pfapp.plan.by_id.get(data["id"])
            if item is not None:
                self._toggle_item(item)
        elif kind == "section":
            self._toggle_section(data["section"])
        else:
            self.notify("This group holds only items that cannot be migrated.")

    def _section_of_cursor(self) -> str | None:
        node = self._tree.cursor_node
        while node is not None:
            data = node.data or {}
            if data.get("kind") == "section":
                return data["section"]
            node = node.parent
        return None

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def action_select_section(self) -> None:
        section = self._section_of_cursor()
        if section is None:
            return
        ids = [
            item.id
            for item in self.pfapp.plan.items
            if item.section == section and is_toggleable(item)
        ]
        self._select(ids)

    def action_none_section(self) -> None:
        section = self._section_of_cursor()
        if section is None:
            return
        items = [
            item
            for item in self.pfapp.plan.items
            if item.section == section
            and is_toggleable(item)
            and item.id in self.pfapp.plan.selected
        ]
        self._deselect(items)

    def action_select_all_default(self) -> None:
        ids = [
            item.id
            for item in self.pfapp.plan.items
            if item.default_selected and item.status != Status.UNSUPPORTED
        ]
        self._select(ids)

    def action_cycle_filter(self) -> None:
        self.filter_mode = _FILTER_CYCLE[self.filter_mode]
        self.notify(f"Filter: {self.filter_mode}")
        self._refresh()

    def action_search(self) -> None:
        self.query_one("#select-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "select-search":
            return
        self.search = event.value
        self._refresh()

    def action_expand_all(self) -> None:
        self._tree.root.expand_all()

    def action_collapse_all(self) -> None:
        self._tree.root.collapse_all()

    def action_goto_ifmap(self) -> None:
        self.action_back()

    def action_review(self) -> None:
        from .review import ReviewScreen

        self.app.push_screen(ReviewScreen())

    def action_help(self) -> None:
        self.app.push_screen(HelpModal())

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_escape(self) -> None:
        search = self.query_one("#select-search", Input)
        if search.has_focus or search.value:
            search.value = ""
            self.search = ""
            self._refresh()
            self._tree.focus()
            return
        self.action_back()
