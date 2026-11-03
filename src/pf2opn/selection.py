"""Selection JSON load/save and selection semantics (design §9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .deps import closure
from .model import Plan, Selection, Status

SELECTION_VERSION = 1


# --------------------------------------------------------------------------- #
# (De)serialisation
# --------------------------------------------------------------------------- #


def to_dict(sel: Selection) -> dict[str, Any]:
    data: dict[str, Any] = {"pf2opn_selection": SELECTION_VERSION}
    if sel.source_sha256:
        data["source_sha256"] = sel.source_sha256
    data["nic_map"] = dict(sel.nic_map)
    data["interfaces"] = {key: dict(spec) for key, spec in sel.interfaces.items()}
    if sel.selected is not None:
        data["selected"] = list(sel.selected)
    data["deselected"] = list(sel.deselected)
    data["options"] = dict(sel.options)
    return data


def to_json(sel: Selection) -> str:
    return json.dumps(to_dict(sel), indent=2, sort_keys=True) + "\n"


def from_dict(data: dict[str, Any]) -> Selection:
    if data.get("pf2opn_selection") != SELECTION_VERSION:
        raise ValueError(
            f"unsupported selection version {data.get('pf2opn_selection')!r}, "
            f"expected {SELECTION_VERSION}"
        )
    selected = data.get("selected")
    return Selection(
        nic_map=dict(data.get("nic_map", {})),
        interfaces={k: dict(v) for k, v in data.get("interfaces", {}).items()},
        selected=list(selected) if selected is not None else None,
        deselected=list(data.get("deselected", [])),
        options=dict(data.get("options", {})),
        source_sha256=str(data.get("source_sha256", "")),
    )


def from_json(text: str) -> Selection:
    return from_dict(json.loads(text))


def load_selection(path: Path | str) -> Selection:
    return from_json(Path(path).read_text(encoding="utf-8"))


def save_selection(path: Path | str, sel: Selection) -> None:
    Path(path).write_text(to_json(sel), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Selection semantics
# --------------------------------------------------------------------------- #


def default_ids(plan: Plan) -> set[str]:
    """Every default-selected, migratable item, with interfaces forced by inclusion."""
    ids = {
        item.id
        for item in plan.items
        if item.default_selected and item.status != Status.UNSUPPORTED
    }
    _apply_interfaces(plan, Selection(), ids)
    return ids


def select_all_ok(plan: Plan) -> set[str]:
    """``--select-all-ok``: every default-selected item."""
    return default_ids(plan)


def _apply_interfaces(plan: Plan, sel: Selection, ids: set[str]) -> None:
    for key, target in plan.ifmap.targets.items():
        item_id = f"iface:{key}"
        if item_id not in plan.by_id:
            continue
        include = target.include
        spec = sel.interfaces.get(key)
        if spec is not None:
            include = bool(spec.get("include", True))
        if include and plan.by_id[item_id].status != Status.UNSUPPORTED:
            ids.add(item_id)
        else:
            ids.discard(item_id)


def validate_ids(plan: Plan, sel: Selection) -> list[str]:
    """Unknown ids referenced by *sel* (empty list when all are known)."""
    valid = set(plan.by_id)
    referenced = list(sel.selected or []) + list(sel.deselected)
    return sorted({item_id for item_id in referenced if item_id not in valid})


def compute_selection(plan: Plan, sel: Selection) -> set[str]:
    """Resolve *sel* against *plan* into the set of selected, migratable ids.

    Raises :class:`ValueError` for unknown ids, and for a deselected id that
    selected items still depend on (§8).
    """
    unknown = validate_ids(plan, sel)
    if unknown:
        raise ValueError("unknown item id(s): " + ", ".join(unknown))

    if sel.selected is not None:
        selected = set(sel.selected)
        deselected: set[str] = set()  # ignored on the explicit path (§9)
    else:
        selected = {
            item.id
            for item in plan.items
            if item.default_selected and item.status != Status.UNSUPPORTED
        }
        deselected = set(sel.deselected)
        selected -= deselected

    _apply_interfaces(plan, sel, selected)

    # Selecting an item also selects everything it needs.
    selected = closure(selected, plan)

    # A dependency closure can pull a deselected id back in; that is the conflict
    # the design makes an error (a selected item still needs it).
    conflicts = sorted(deselected & selected)
    if conflicts:
        raise ValueError(
            "deselected id(s) still required by selected items: " + ", ".join(conflicts)
        )

    return {item_id for item_id in selected if plan.by_id[item_id].status != Status.UNSUPPORTED}
