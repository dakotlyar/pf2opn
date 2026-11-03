"""Plan construction (design §4, §6).

``build_plan`` is pure and cheap: it discovers items through the registry,
resolves dependencies, and seeds the default selection. Run it again whenever
the interface map changes.
"""

from __future__ import annotations

from . import translators  # noqa: F401  (import side effect: registers translators)
from .context import SourceContext
from .deps import resolve
from .model import IfaceMap, Item, Plan
from .registry import all_translators
from .selection import default_ids


def _dedupe_check(items: list[Item]) -> None:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"duplicate item id produced by a translator: {item.id}")
        seen.add(item.id)


def build_plan(src: SourceContext, ifmap: IfaceMap) -> Plan:
    """Discover every item, resolve dependencies, and seed the default selection."""
    items: list[Item] = []
    for translator in all_translators():
        items.extend(translator.discover(src, ifmap))

    _dedupe_check(items)
    by_id = {item.id: item for item in items}
    resolve(items)

    plan = Plan(
        items=items,
        by_id=by_id,
        ifmap=ifmap,
        selected=set(),
        options={"keep_base_rules": False, "base_is_default": src.base_is_default},
    )
    plan.selected = default_ids(plan)
    return plan
