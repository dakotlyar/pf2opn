"""Tests for the users/groups translators (design §12.1.15, M9)."""

from __future__ import annotations

import types

from lxml import etree

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == section}


# -- discovery --------------------------------------------------------------- #


def test_system_user_is_not_a_user_item(trust_users_plan) -> None:
    _src, plan = trust_users_plan
    users = _items(plan, "user")
    assert set(users) == {"user:alice"}


def test_user_uid_collision_reassigned(trust_users_plan) -> None:
    _src, plan = trust_users_plan
    alice = _items(plan, "user")["user:alice"]
    assert alice.status == Status.PARTIAL
    assert alice.data["uid"] == 2000
    assert any("uid 0" in warning for warning in alice.warnings)


def test_non_numeric_uid_is_allocated_without_a_collision_warning() -> None:
    """An absent/non-numeric uid is not a "uid N already used" collision (§12.1.15)."""
    pf = etree.fromstring(
        "<pfsense><system><user><name>eve</name><scope>user</scope>"
        "<bcrypt-hash>$2y$10$eveeveeveeveeveeveeveeveeveeveeveeveeveeveeveeveeveev</bcrypt-hash>"
        "<uid>not-a-number</uid></user></system></pfsense>"
    )
    base = etree.fromstring(
        "<opnsense><system><nextuid>2000</nextuid>"
        "<user><name>root</name><scope>system</scope><uid>0</uid></user></system></opnsense>"
    )
    src = types.SimpleNamespace(pf=pf, base=base)
    item = registry.get("user").discover(src, None)[0]
    assert item.data["uid"] == 2000
    assert not any("already used" in warning for warning in item.warnings)


def test_user_privileges_filtered(trust_users_plan) -> None:
    _src, plan = trust_users_plan
    alice = _items(plan, "user")["user:alice"]
    assert alice.data["priv"] == "page-all,user-shell-access"
    assert any("webcfg-shell" in warning for warning in alice.warnings)


def test_sha512_only_user_is_unsupported() -> None:
    pf = etree.fromstring(
        "<pfsense><system><user><name>bob</name><scope>user</scope>"
        "<sha512-hash>$6$abcdefghijklmnop</sha512-hash></user></system></pfsense>"
    )
    src = types.SimpleNamespace(pf=pf, base=etree.fromstring("<opnsense><system/></opnsense>"))
    items = registry.get("user").discover(src, None)
    assert items[0].status == Status.UNSUPPORTED
    assert "unsupported hash type" in items[0].reason


def test_groups_discovered(trust_users_plan) -> None:
    _src, plan = trust_users_plan
    groups = _items(plan, "group")
    assert set(groups) == {"group:admins", "group:operators"}
    assert groups["group:operators"].data["members"] == ["alice"]


def test_user_scope_admins_group_has_a_distinct_id() -> None:
    """A user group named ``admins`` is not the system-admins merge (§12.1.15)."""
    pf = etree.fromstring(
        "<pfsense><system>"
        "<user><name>carol</name><scope>user</scope>"
        "<bcrypt-hash>$2y$10$carolcarolcarolcarolcarolcarolcarolcarolcarolcarol</bcrypt-hash>"
        "</user>"
        "<group><name>admins</name><scope>user</scope><gid>2005</gid><member>carol</member></group>"
        "</system></pfsense>"
    )
    base = etree.fromstring(
        "<opnsense><system><nextgid>2000</nextgid>"
        "<user><name>root</name><scope>system</scope><uid>0</uid></user>"
        "<group><name>admins</name><scope>system</scope><gid>1999</gid><member>0</member></group>"
        "</system></opnsense>"
    )
    src = types.SimpleNamespace(pf=pf, base=base)
    items = registry.get("group").discover(src, None)
    assert [item.id for item in items] == ["group:user:admins"]
    # the identity is the group name, so the merge can replace a same-named base group
    assert registry.get("group").identity(items[0]) == "admins"


def test_system_and_user_admins_do_not_collide() -> None:
    """Both the system-admins merge and a user group named admins coexist."""
    pf = etree.fromstring(
        "<pfsense><system>"
        "<user><name>dave</name><scope>user</scope>"
        "<bcrypt-hash>$2y$10$davedavedavedavedavedavedavedavedavedavedavedaveda</bcrypt-hash>"
        "</user>"
        "<group><name>admins</name><scope>system</scope><gid>1999</gid>"
        "<member>admin</member><member>dave</member></group>"
        "<group><name>admins</name><scope>user</scope><gid>2007</gid><member>dave</member></group>"
        "</system></pfsense>"
    )
    base = etree.fromstring(
        "<opnsense><system><nextgid>2000</nextgid>"
        "<user><name>root</name><scope>system</scope><uid>0</uid></user>"
        "<group><name>admins</name><scope>system</scope><gid>1999</gid><member>0</member></group>"
        "</system></opnsense>"
    )
    src = types.SimpleNamespace(pf=pf, base=base)
    items = registry.get("group").discover(src, None)
    ids = [item.id for item in items]
    assert ids == ["group:admins", "group:user:admins"]
    assert len(set(ids)) == len(ids)


def test_gid_collision_reassigned() -> None:
    """A pfSense gid already used in the base is reallocated (§12.1.15)."""
    pf = etree.fromstring(
        "<pfsense><system>"
        "<user><name>bob</name><scope>user</scope>"
        "<bcrypt-hash>$2y$10$bobbobbobbobbobbobbobbobbobbobbobbobbobbobbobbobbobbo</bcrypt-hash>"
        "</user>"
        "<group><name>ops</name><scope>user</scope><gid>1999</gid><member>bob</member></group>"
        "</system></pfsense>"
    )
    base = etree.fromstring(
        "<opnsense><system><nextgid>2000</nextgid>"
        "<user><name>root</name><scope>system</scope><uid>0</uid></user>"
        "<group><name>admins</name><scope>system</scope><gid>1999</gid><member>0</member></group>"
        "</system></opnsense>"
    )
    src = types.SimpleNamespace(pf=pf, base=base)
    item = registry.get("group").discover(src, None)[0]
    assert item.data["gid"] == 2000
    assert any("gid 1999 already used in the base; assigned 2000" == w for w in item.warnings)


# -- rendering --------------------------------------------------------------- #


def test_user_render(trust_users_plan) -> None:
    src, plan = trust_users_plan
    root = registry.get("user").render(plan.by_id["user:alice"], WriteContext(src, plan))[0].element
    assert root.findtext("name") == "alice"
    assert root.findtext("scope") == "user"
    assert root.findtext("disabled") == "0"
    assert root.findtext("uid") == "2000"
    assert root.findtext("priv") == "page-all,user-shell-access"
    assert root.findtext("password").startswith("$2y$")
    assert root.findtext("authorizedkeys")


def test_group_render_member_uids(trust_users_plan) -> None:
    src, plan = trust_users_plan
    ctx = WriteContext(src, plan)
    root = registry.get("group").render(plan.by_id["group:operators"], ctx)[0].element
    assert root.findtext("name") == "operators"
    assert root.findtext("scope") == "user"
    assert root.findtext("gid") == "2001"
    assert root.findtext("member") == "2000"  # alice's reassigned uid


def test_admins_group_merges_member(trust_users_plan) -> None:
    src, plan = trust_users_plan
    placement = registry.get("group").render(
        plan.by_id["group:admins"], WriteContext(src, plan)
    )[0]
    assert placement.mode == Mode.SET
    assert placement.path == "system/group[name='admins']/member"
    assert placement.text == "0,2000"  # base root uid 0 plus alice


def test_finalize_advances_nextuid_and_nextgid(trust_users_plan) -> None:
    src, plan = trust_users_plan
    ctx = WriteContext(src, plan)
    users = [item for item in plan.items if item.section == "user" and item.id in plan.selected]
    groups = [item for item in plan.items if item.section == "group" and item.id in plan.selected]
    assert registry.get("user").finalize(users, ctx)[0].text == "2001"
    assert registry.get("group").finalize(groups, ctx)[0].text == "2002"


def test_root_password_only_applies_when_selected(trust_users_plan) -> None:
    src, plan = trust_users_plan
    system = registry.get("system")
    # not selected by default
    assert "system:root_password" not in plan.selected
    plan.selected = {"system:root_password"}
    placements = system.render(plan.by_id["system:root_password"], WriteContext(src, plan))
    assert placements[0].path == "system/user[name='root']/password"


# -- golden ------------------------------------------------------------------ #


def test_user_golden(trust_users_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = trust_users_plan
    placements = section_placements(src, plan, "user")
    golden_checker("user/trust_users.xml", wrap_placements(placements))


def test_group_golden(trust_users_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = trust_users_plan
    placements = section_placements(src, plan, "group")
    golden_checker("group/trust_users.xml", wrap_placements(placements))
