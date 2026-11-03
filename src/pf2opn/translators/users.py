"""Users and groups (design §12.1.15).

``user``   ``pf:system/user[scope=user][]``  -> ``system/user[]``
``group``  ``pf:system/group[scope!=system]`` -> ``system/group[]``
plus a special ``group:admins`` that merges extra members into the base admins.

Both are ``merge`` by name under ``system``; ``identity`` only matches its own
element tag so a user and a group that share a name cannot replace each other.
"""

from __future__ import annotations

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, pf_desc, pf_list, pf_text, sub

#: Privileges OPNsense carries over; anything else is dropped with a warning.
KNOWN_PRIVS = frozenset({"page-all", "user-shell-access"})


def _base_uids(base: etree._Element) -> set[int]:
    uids: set[int] = set()
    for uid in base.findall("system/user/uid"):
        text = (uid.text or "").strip()
        if text.isdigit():
            uids.add(int(text))
    return uids


def _base_gids(base: etree._Element) -> set[int]:
    gids: set[int] = set()
    for gid in base.findall("system/group/gid"):
        text = (gid.text or "").strip()
        if text.isdigit():
            gids.add(int(text))
    return gids


def _next_free(raw: str, allocated: set[int], floor: int) -> tuple[int, bool]:
    """Return ``(id, collided)``: keep *raw* or allocate above *floor*.

    *collided* is true only for a real collision: a *numeric* *raw* that is
    already in use. An absent/non-numeric id is allocated silently, so the
    caller must not warn "N already used" for one.
    """
    if raw.isdigit() and int(raw) not in allocated:
        value = int(raw)
        allocated.add(value)
        return value, False
    candidate = max([0, *allocated, floor])
    while candidate in allocated:
        candidate += 1
    allocated.add(candidate)
    return candidate, raw.isdigit()


def _split_privs(user: etree._Element) -> tuple[str, list[str]]:
    kept: list[str] = []
    dropped: list[str] = []
    for priv in pf_list(user, "priv"):
        if priv in KNOWN_PRIVS:
            if priv not in kept:
                kept.append(priv)
        elif priv not in dropped:
            dropped.append(priv)
    return ",".join(kept), dropped


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


@register
class UserTranslator(Translator):
    section = "user"
    title = "Users"
    tier = 1
    order = 20
    base_policy = "merge"
    container = "system"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        allocated = _base_uids(src.base) | {0}
        nextuid = int(pf_text(src.base, "system/nextuid") or 0)
        items: list[Item] = []
        for user in src.pf.findall("system/user"):
            if pf_text(user, "scope") != "user":
                continue
            name = pf_text(user, "name")
            label = f"User {name}"

            bcrypt = pf_text(user, "bcrypt-hash")
            sha512 = pf_text(user, "sha512-hash")
            if not bcrypt and sha512:
                items.append(
                    Item(
                        f"user:{name}",
                        self.section,
                        label,
                        user,
                        Status.UNSUPPORTED,
                        reason="unsupported hash type; reset password after import",
                    )
                )
                continue

            warnings: list[str] = []
            priv, dropped = _split_privs(user)
            if dropped:
                warnings.append("privileges not migrated: " + ", ".join(dropped))
            if pf_text(user, "ipsecpsk"):
                warnings.append("IPsec pre-shared key not migrated")
            if any((cert.text or "").strip() for cert in user.findall("cert")):
                warnings.append("user certificate not migrated")

            uid, collided = _next_free(pf_text(user, "uid"), allocated, nextuid)
            if collided:
                warnings.append(
                    f"uid {pf_text(user, 'uid')} already used in the base; assigned {uid}"
                )

            items.append(
                Item(
                    f"user:{name}",
                    self.section,
                    label,
                    user,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    data={"uid": uid, "password": bcrypt, "priv": priv},
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        if item.source is None or item.source.tag != "user":
            return None
        return pf_text(item.source, "name") or None

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        user = item.source
        if user is None:
            return []
        root = E("user", uuid=ctx.uuid(item.id))
        sub(root, "name", pf_text(user, "name"))
        sub(root, "descr", pf_desc(user))
        sub(root, "scope", "user")
        sub(root, "disabled", "1" if user.find("disabled") is not None else "0")
        expires = pf_text(user, "expires")
        if expires:
            sub(root, "expires", expires)
        if item.data["password"]:
            sub(root, "password", item.data["password"])
        authorizedkeys = pf_text(user, "authorizedkeys")
        if authorizedkeys:
            sub(root, "authorizedkeys", authorizedkeys)
        sub(root, "uid", str(item.data["uid"]))
        if item.data["priv"]:
            sub(root, "priv", item.data["priv"])
        return [Placement("system", Mode.APPEND, element=root, item_id=item.id)]

    def finalize(self, selected: list[Item], ctx: WriteContext) -> list[Placement]:
        base = ctx.src.base
        floor = max([0, *_base_uids(base), *(item.data["uid"] for item in selected)])
        nextuid = max(int(pf_text(base, "system/nextuid") or 0), floor + 1)
        return [Placement("system/nextuid", Mode.SET, text=str(nextuid))]


# --------------------------------------------------------------------------- #
# Groups
# --------------------------------------------------------------------------- #


def _user_names(pf: etree._Element) -> set[str]:
    return {
        pf_text(user, "name")
        for user in pf.findall("system/user")
        if pf_text(user, "scope") == "user"
    }


def _group_id(name: str, scope: str) -> str:
    """A group item id, distinct from the special system-admins merge.

    The system ``admins`` merge uses the id ``group:admins`` (§12.1.15); a
    *user-scope* group of the same name is a different group and must not be
    misclassified as that merge, so its id is scope-qualified.
    """
    if name == "admins" and scope != "system":
        return "group:user:admins"
    return f"group:{name}"


@register
class GroupTranslator(Translator):
    section = "group"
    title = "Groups"
    tier = 1
    order = 21
    base_policy = "merge"
    container = "system"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        pf_users = _user_names(src.pf)
        allocated_gids = _base_gids(src.base) | {0}
        nextgid = int(pf_text(src.base, "system/nextgid") or 0)
        items: list[Item] = []
        for group in src.pf.findall("system/group"):
            name = pf_text(group, "name")
            scope = pf_text(group, "scope")
            if scope == "system":
                if name != "admins":
                    continue
                extra = [member for member in pf_list(group, "member") if member in pf_users]
                if not extra:
                    continue
                items.append(
                    Item(
                        "group:admins",
                        self.section,
                        "Group admins",
                        group,
                        Status.OK,
                        data={"members": extra},
                    )
                )
                continue

            warnings: list[str] = []
            priv, dropped = _split_privs(group)
            if dropped:
                warnings.append("privileges not migrated: " + ", ".join(dropped))
            gid, gid_collided = _next_free(pf_text(group, "gid"), allocated_gids, nextgid)
            if gid_collided:
                warnings.append(
                    f"gid {pf_text(group, 'gid')} already used in the base; assigned {gid}"
                )
            items.append(
                Item(
                    _group_id(name, scope),
                    self.section,
                    f"Group {name}",
                    group,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    data={
                        "members": pf_list(group, "member"),
                        "priv": priv,
                        "gid": gid,
                    },
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        # ``group:admins`` only adds members to the base admins group, so it must
        # not trigger the ``merge`` removal of that base entry.
        if item.id == "group:admins":
            return None
        if item.source is None or item.source.tag != "group":
            return None
        return pf_text(item.source, "name") or None

    def _member_uids(self, names: list[str], ctx: WriteContext) -> list[str]:
        uids: list[str] = []
        for name in names:
            user = ctx.plan.by_id.get(f"user:{name}")
            if user is None or user.status == Status.UNSUPPORTED or not ctx.selected(user.id):
                continue
            uid = user.data.get("uid")
            if uid is not None and str(uid) not in uids:
                uids.append(str(uid))
        return uids

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        group = item.source
        if group is None:
            return []
        if item.id == "group:admins":
            return self._render_admins(item, ctx)

        root = E("group", uuid=ctx.uuid(item.id))
        sub(root, "name", pf_text(group, "name"))
        sub(root, "description", pf_desc(group, "description"))
        sub(root, "scope", "user")
        sub(root, "gid", str(item.data["gid"]))
        uids = self._member_uids(item.data["members"], ctx)
        sub(root, "member", ",".join(uids) if uids else "0")
        if item.data["priv"]:
            sub(root, "priv", item.data["priv"])
        return [Placement("system", Mode.APPEND, element=root, item_id=item.id)]

    def _render_admins(self, item: Item, ctx: WriteContext) -> list[Placement]:
        admins = ctx.src.base.find("system/group[name='admins']")
        if admins is None:
            return []
        existing = [
            part.strip()
            for part in (admins.findtext("member") or "").split(",")
            if part.strip()
        ]
        for uid in self._member_uids(item.data["members"], ctx):
            if uid not in existing:
                existing.append(uid)
        return [
            Placement(
                "system/group[name='admins']/member",
                Mode.SET,
                text=",".join(existing) if existing else "0",
                item_id=item.id,
            )
        ]

    def finalize(self, selected: list[Item], ctx: WriteContext) -> list[Placement]:
        base = ctx.src.base
        gids = [
            int(item.data["gid"])
            for item in selected
            if item.id != "group:admins" and item.data.get("gid") is not None
        ]
        floor = max([0, *_base_gids(base), *gids])
        nextgid = max(int(pf_text(base, "system/nextgid") or 0), floor + 1)
        return [Placement("system/nextgid", Mode.SET, text=str(nextgid))]
