"""Tests for selection JSON and semantics (design §9)."""

from __future__ import annotations

import json

import pytest

from pf2opn.model import IfaceMap, Item, Plan, Selection, Status
from pf2opn.selection import (
    compute_selection,
    default_ids,
    from_json,
    select_all_ok,
    to_dict,
    to_json,
    validate_ids,
)


def _item(item_id: str, status: Status = Status.OK, deps: list[str] | None = None,
          default: bool = True) -> Item:
    return Item(
        id=item_id,
        section="t",
        label=item_id,
        source=None,
        status=status,
        deps=list(deps or []),
        default_selected=default,
    )


def _plan(items: list[Item]) -> Plan:
    by_id = {item.id: item for item in items}
    return Plan(
        items=items,
        by_id=by_id,
        ifmap=IfaceMap(targets={}, nic_map={}, vlan_names={}, wg_names={}),
        selected=set(),
        options={},
    )


def test_selection_json_round_trip() -> None:
    sel = Selection(
        nic_map={"em0": "igc0"},
        interfaces={"lan": {"include": True}, "opt1": {"include": False}},
        selected=["alias:RFC1918", "rule:1"],
        deselected=["rule:2"],
        options={"keep_base_rules": True},
        source_sha256="deadbeef",
    )
    restored = from_json(to_json(sel))
    assert restored == sel


def test_to_dict_omits_absent_selected() -> None:
    data = to_dict(Selection())
    assert "selected" not in data
    assert data["pf2opn_selection"] == 1


def test_from_json_rejects_unknown_version() -> None:
    with pytest.raises(ValueError):
        from_json(json.dumps({"pf2opn_selection": 99}))


def test_default_ids_excludes_unsupported_and_opted_out(minimal_plan) -> None:
    _src, plan = minimal_plan
    ids = default_ids(plan)
    assert "system:hostname" in ids
    assert "system:dns" in ids
    # webgui and root_password are default_selected=False
    assert "system:webgui" not in ids
    assert "system:root_password" not in ids


def test_select_all_ok_equals_default_ids(minimal_plan) -> None:
    _src, plan = minimal_plan
    assert select_all_ok(plan) == default_ids(plan)


def test_compute_selection_default_path(minimal_plan) -> None:
    _src, plan = minimal_plan
    selected = compute_selection(plan, Selection())
    assert selected == default_ids(plan)


def test_compute_selection_explicit_closes_over_deps() -> None:
    a = _item("a", deps=["b"])
    b = _item("b", deps=["c"])
    c = _item("c")
    plan = _plan([a, b, c])
    selected = compute_selection(plan, Selection(selected=["a"]))
    assert selected == {"a", "b", "c"}


def test_compute_selection_explicit_ignores_defaults() -> None:
    a = _item("a")
    b = _item("b")
    plan = _plan([a, b])
    assert compute_selection(plan, Selection(selected=["a"])) == {"a"}


def test_compute_selection_default_minus_deselected() -> None:
    a = _item("a")
    b = _item("b")
    plan = _plan([a, b])
    assert compute_selection(plan, Selection(deselected=["b"])) == {"a"}


def test_compute_selection_rejects_unknown_ids(minimal_plan) -> None:
    _src, plan = minimal_plan
    with pytest.raises(ValueError, match="unknown item id"):
        compute_selection(plan, Selection(selected=["does:not:exist"]))


def test_compute_selection_rejects_deselected_dependency() -> None:
    a = _item("a", deps=["b"])
    b = _item("b")
    plan = _plan([a, b])
    with pytest.raises(ValueError, match="still required"):
        compute_selection(plan, Selection(deselected=["b"]))


def test_validate_ids(minimal_plan) -> None:
    _src, plan = minimal_plan
    assert validate_ids(plan, Selection(selected=["system:hostname"])) == []
    assert validate_ids(plan, Selection(selected=["nope"])) == ["nope"]
