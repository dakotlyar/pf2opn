"""Unit tests pinning the §15 Select-screen label templates (design §17 M12).

These are the exact templates from §15: the leading bracket is selection only,
status is a trailing marker on non-OK items, and sections count their
*selectable* items (UNSUPPORTED excluded). The label strings are plain text.
"""

from __future__ import annotations

from pf2opn.model import Item, Status
from pf2opn.tui.widgets import (
    is_selectable,
    is_toggleable,
    item_label,
    item_text,
    section_label,
)


def _item(
    item_id: str,
    *,
    status: Status = Status.OK,
    label: str = "label",
    section: str = "s",
    readonly_selection: bool = False,
) -> Item:
    return Item(
        id=item_id,
        section=section,
        label=label,
        source=None,
        status=status,
        readonly_selection=readonly_selection,
    )


def test_item_label_ok_selected_and_unselected() -> None:
    item = _item("a", status=Status.OK, label="eth0")
    assert item_label(item, True) == "[x] eth0"
    assert item_label(item, False) == "[ ] eth0"


def test_item_label_partial_selected_and_unselected() -> None:
    item = _item("a", status=Status.PARTIAL, label="eth0")
    assert item_label(item, True) == "[x] eth0 \u26a0"
    assert item_label(item, False) == "[ ] eth0 \u26a0"


def test_item_label_unsupported_never_selectable() -> None:
    item = _item("a", status=Status.UNSUPPORTED, label="eth0")
    assert item_label(item, False) == "[ ] eth0 \u2716"
    # even if the id somehow appears in the selection, it never shows [x]
    assert item_label(item, True) == "[ ] eth0 \u2716"


def test_item_label_uses_label_verbatim() -> None:
    item = _item("a", status=Status.OK, label="Rule 1003 (Web)")
    assert item_label(item, True) == "[x] Rule 1003 (Web)"


def test_section_label_all_selected_is_x() -> None:
    items = [_item("a"), _item("b"), _item("c")]
    assert section_label("Interfaces", items, {"a", "b", "c"}) == "[x] Interfaces (3/3)"


def test_section_label_partial_is_dash() -> None:
    items = [_item("a"), _item("b"), _item("c")]
    assert section_label("Interfaces", items, {"a", "b"}) == "[-] Interfaces (2/3)"


def test_section_label_none_selected_is_empty() -> None:
    items = [_item("a"), _item("b"), _item("c")]
    assert section_label("Interfaces", items, set()) == "[ ] Interfaces (0/3)"


def test_section_label_all_untranslatable_is_zero_of_zero() -> None:
    items = [
        _item("a", status=Status.UNSUPPORTED),
        _item("b", status=Status.UNSUPPORTED),
    ]
    assert section_label("Cron", items, set()) == "[ ] Cron (0/0)"


def test_section_label_excludes_unsupported_from_counts() -> None:
    items = [
        _item("a", status=Status.OK),
        _item("b", status=Status.PARTIAL),
        _item("c", status=Status.UNSUPPORTED),
    ]
    selected = {"a", "b", "c"}
    # c can never be selected, so selecting the two selectable items is [x]
    assert section_label("Mixed", items, selected) == "[x] Mixed (2/2)"
    assert section_label("Mixed", items, {"a", "c"}) == "[-] Mixed (1/2)"


def test_section_label_counts_readonly_interface_items() -> None:
    # Read-only interface items are selectable (toggled on the map), so a
    # section with 2 of 3 included reads [-] (2/3) — the old bug read [ ] (2/3).
    items = [
        _item("iface:lan", section="interfaces", readonly_selection=True),
        _item("iface:wan", section="interfaces", readonly_selection=True),
        _item("iface:opt1", section="interfaces", readonly_selection=True),
    ]
    assert (
        section_label("Interfaces", items, {"iface:lan", "iface:wan"})
        == "[-] Interfaces (2/3)"
    )
    assert (
        section_label("Interfaces", items, {"iface:lan", "iface:wan", "iface:opt1"})
        == "[x] Interfaces (3/3)"
    )


def test_item_text_keeps_plain_label_and_colours_only_the_body() -> None:
    # The renderable the tree actually shows: plain text identical to
    # item_label, with the status style on the label + marker and not the
    # selection bracket (which starts at index 0 and is never styled).
    item = _item("a", status=Status.PARTIAL, label="eth0")
    text = item_text(item, True)
    assert str(text) == item_label(item, True) == "[x] eth0 \u26a0"
    assert text.plain[:3] == "[x]"
    assert text.spans[0].start == 3  # colour starts after the bracket
    assert text.spans[0].style == "yellow"  # body carries the status colour
    assert all(span.start >= 3 for span in text.spans)  # bracket never styled


def test_selectable_vs_toggleable() -> None:
    readonly = _item("a", readonly_selection=True)
    assert is_selectable(readonly) is True
    assert is_toggleable(readonly) is False

    unsupported = _item("b", status=Status.UNSUPPORTED)
    assert is_selectable(unsupported) is False
    assert is_toggleable(unsupported) is False

    plain = _item("c")
    assert is_selectable(plain) is True
    assert is_toggleable(plain) is True
