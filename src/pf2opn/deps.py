"""Dependency resolution (design §8)."""

from __future__ import annotations

from .model import Item, Plan, Status


def resolve(items: list[Item]) -> list[Item]:
    """Propagate UNSUPPORTED through ``Item.deps`` until nothing changes.

    An item that depends on a missing or UNSUPPORTED id becomes UNSUPPORTED with
    reason ``depends on <dep label> which cannot be migrated``.
    """
    by_id = {item.id: item for item in items}
    changed = True
    while changed:
        changed = False
        for item in items:
            if item.status == Status.UNSUPPORTED:
                continue
            for dep in item.deps:
                target = by_id.get(dep)
                if target is None or target.status == Status.UNSUPPORTED:
                    label = target.label if target is not None else dep
                    item.status = Status.UNSUPPORTED
                    item.reason = f"depends on {label} which cannot be migrated"
                    item.default_selected = False
                    changed = True
                    break
    return items


def closure(ids: set[str] | list[str], plan: Plan) -> set[str]:
    """The given ids plus every transitive dependency."""
    result: set[str] = set()
    stack = list(ids)
    while stack:
        item_id = stack.pop()
        if item_id in result:
            continue
        result.add(item_id)
        item = plan.by_id.get(item_id)
        if item is not None:
            stack.extend(item.deps)
    return result


def dependents(item_id: str, plan: Plan) -> set[str]:
    """Selected items that transitively depend on *item_id*."""
    return {
        other for other in plan.selected if other != item_id and item_id in closure({other}, plan)
    }
