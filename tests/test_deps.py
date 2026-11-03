"""Tests for dependency resolution (design §8)."""

from __future__ import annotations

from pf2opn.deps import closure, dependents, resolve
from pf2opn.model import IfaceMap, Item, Plan, Status


def _item(item_id: str, status: Status = Status.OK, deps: list[str] | None = None) -> Item:
    return Item(
        id=item_id,
        section="t",
        label=f"label {item_id}",
        source=None,
        status=status,
        deps=list(deps or []),
    )


def _plan(items: list[Item], selected: set[str] | None = None) -> Plan:
    by_id = {item.id: item for item in items}
    return Plan(
        items=items,
        by_id=by_id,
        ifmap=IfaceMap(targets={}, nic_map={}, vlan_names={}, wg_names={}),
        selected=set(selected or set()),
        options={},
    )


def test_resolve_propagates_unsupported() -> None:
    a = _item("a", deps=["b"])
    b = _item("b", Status.UNSUPPORTED)
    resolve([a, b])
    assert a.status == Status.UNSUPPORTED
    assert a.reason == "depends on label b which cannot be migrated"
    assert a.default_selected is False


def test_resolve_missing_dep_uses_id_as_label() -> None:
    a = _item("a", deps=["ghost"])
    resolve([a])
    assert a.status == Status.UNSUPPORTED
    assert "ghost" in a.reason


def test_resolve_is_transitive() -> None:
    a = _item("a", deps=["b"])
    b = _item("b", deps=["c"])
    c = _item("c", Status.UNSUPPORTED)
    resolve([a, b, c])
    assert a.status == Status.UNSUPPORTED
    assert b.status == Status.UNSUPPORTED
    assert c.status == Status.UNSUPPORTED


def test_resolve_leaves_satisfied_items_alone() -> None:
    a = _item("a", deps=["b"])
    b = _item("b", Status.OK)
    resolve([a, b])
    assert a.status == Status.OK
    assert a.reason == ""


def test_closure() -> None:
    a = _item("a", deps=["b"])
    b = _item("b", deps=["c"])
    c = _item("c")
    plan = _plan([a, b, c])
    assert closure({"a"}, plan) == {"a", "b", "c"}
    assert closure({"c"}, plan) == {"c"}
    assert closure(set(), plan) == set()


def test_closure_ignores_missing_ids() -> None:
    a = _item("a", deps=["ghost"])
    plan = _plan([a])
    assert closure({"a"}, plan) == {"a", "ghost"}


def test_dependents() -> None:
    a = _item("a", deps=["b"])
    b = _item("b", deps=["c"])
    c = _item("c")
    other = _item("other")
    plan = _plan([a, b, c, other], selected={"a", "b", "other"})
    assert dependents("c", plan) == {"a", "b"}
    assert dependents("b", plan) == {"a"}
    assert dependents("a", plan) == set()
    assert dependents("other", plan) == set()


def test_dependents_only_counts_selected() -> None:
    a = _item("a", deps=["b"])
    b = _item("b")
    plan = _plan([a, b], selected={"b"})
    assert dependents("b", plan) == set()
