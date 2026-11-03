"""WireGuard translators (design §12.1.17).

``wg_tunnel``  ``pf:installedpackages/wireguard/tunnels/item[]`` -> servers
``wg_peer``    ``pf:installedpackages/wireguard/peers/item[]``   -> clients

The one lossy mapping is the peer's ``tunneladdress``: pfSense ``allowedips``
describe what is *behind* the peer, not the address it holds on the tunnel, so
the heuristic in §12.1.17 (OK / PARTIAL / UNSUPPORTED) is applied verbatim.
"""

from __future__ import annotations

import ipaddress
import re

from lxml import etree

from ..context import SourceContext, WriteContext
from ..model import IfaceMap, Item, Mode, Placement, Status
from ..registry import register
from .base import E, Translator, pf_desc, pf_text, sanitize_name, sub

_DEFAULT_ROUTES = frozenset({"0.0.0.0/0", "::/0"})
_TUN_NAME = re.compile(r"^tun_wg(\d+)$")


def _row_cidrs(parent: etree._Element, path: str) -> list[str]:
    """Parse ``<path>/row`` ``address``+``mask`` into CIDR strings (§12.1.17).

    Shared by a tunnel's ``addresses`` and a peer's ``allowedips`` — pfSense
    stores both as the same ``<row><address>…</address><mask>…</mask></row>``
    shape, never as text. A row with an empty ``mask`` takes the host mask for
    its address family (``/32`` IPv4, ``/128`` IPv6); a bare address is never
    emitted.
    """
    cidrs: list[str] = []
    for row in parent.findall(path):
        address = pf_text(row, "address")
        if not address:
            continue
        mask = pf_text(row, "mask")
        if not mask:
            try:
                mask = "128" if ipaddress.ip_address(address).version == 6 else "32"
            except ValueError:
                mask = "32"
        cidrs.append(f"{address}/{mask}")
    return cidrs


def _is_host(value: str) -> bool:
    if "/" not in value:
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False
    try:
        net = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return net.prefixlen == net.max_prefixlen


def _in_net(value: str, net) -> bool:
    try:
        return ipaddress.ip_network(value, strict=False).subnet_of(net)
    except (ValueError, TypeError):
        return False


def _server_net(tunnel_address: str):
    first = (tunnel_address or "").split(",")[0].strip()
    if not first:
        return None
    try:
        return ipaddress.ip_network(first, strict=False)
    except ValueError:
        return None


def _assigned_ifkey(name: str, src: SourceContext) -> str | None:
    for key, iface in src.pf_ifaces.items():
        if iface.device == name:
            return key
    return None


def _tunnel_address(tunnel: etree._Element, src: SourceContext) -> str:
    """The tunnel's own address: its ``addresses`` rows, else its interface."""
    parts = _row_cidrs(tunnel, "addresses/row")
    if parts:
        return ",".join(parts)

    name = pf_text(tunnel, "name")
    ifkey = _assigned_ifkey(name, src)
    if ifkey is None:
        return ""
    iface = src.pf_ifaces[ifkey]
    if not iface.ipaddr:
        return ""
    return f"{iface.ipaddr}/{iface.subnet}" if iface.subnet else iface.ipaddr


def _unique(base: str, used: set[str]) -> str:
    name = base
    suffix = 2
    while name in used:
        name = f"{base}_{suffix}"
        suffix += 1
    used.add(name)
    return name


def _base_server_instances(base: etree._Element) -> tuple[set[int], dict[str, int]]:
    """Base WireGuard ``(instances in use, instance of each named server)``.

    A base server whose ``name`` equals a tunnel's output name is a prior pf2opn
    output of that same tunnel: its instance is reused so a re-run is a no-op
    instead of allocating a fresh instance each time (§12.1.17).
    """
    instances: set[int] = set()
    by_name: dict[str, int] = {}
    for server in base.findall("OPNsense/wireguard/server/servers/server"):
        text = (server.findtext("instance") or "").strip()
        if not text.isdigit():
            continue
        value = int(text)
        instances.add(value)
        name = (server.findtext("name") or "").strip()
        if name:
            by_name.setdefault(name, value)
    return instances, by_name


# --------------------------------------------------------------------------- #
# Tunnels
# --------------------------------------------------------------------------- #


@register
class WgTunnelTranslator(Translator):
    section = "wg_tunnel"
    title = "WireGuard tunnels"
    tier = 1
    order = 50
    base_policy = "merge"
    container = "OPNsense/wireguard/server/servers"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        tunnels = src.pf.findall("installedpackages/wireguard/tunnels/item")
        if not tunnels:
            return []
        peers = src.pf.findall("installedpackages/wireguard/peers/item")

        base_instances, base_instance_by_name = _base_server_instances(src.base)

        items: list[Item] = []
        #: Names assigned within this migration only — never seeded from the base,
        #: so a base name collision is the entry being replaced, not a rename.
        used_names: set[str] = set()
        #: Instances assigned within this migration (guards an intra-migration clash).
        assigned_instances: set[int] = set()
        for index, tunnel in enumerate(tunnels):
            name = pf_text(tunnel, "name")
            descr = pf_desc(tunnel)
            label = f"WireGuard tunnel {descr or name}"

            warnings: list[str] = []
            followups: list[str] = []
            match = _TUN_NAME.match(name)
            n = int(match.group(1)) if match else 0
            out_name = _unique(sanitize_name(descr or name) or f"wg{n}", used_names)

            reused = base_instance_by_name.get(out_name)
            if reused is not None:
                # A prior pf2opn output of this tunnel: reuse its instance (§12.1.17).
                instance = reused
            else:
                used = base_instances | assigned_instances
                if n not in used:
                    instance = n
                else:
                    instance = max(used | {n}) + 1
                    if n in base_instances:
                        warnings.append(f"instance {n} already used in the base; assigned {instance}")
                    else:
                        warnings.append(
                            f"instance {n} already used by another tunnel in this migration; "
                            f"assigned {instance}"
                        )
            assigned_instances.add(instance)

            if ifmap is not None:
                device = f"wg{instance}"
                ifmap.wg_names[name] = device
                # Keep the already-built interface target in sync (the report and
                # TUI read ``opn_device``, not ``wg_names``).
                for target in ifmap.targets.values():
                    if target.kind == "wireguard" and target.pf_device == name:
                        target.opn_device = device

            tunnel_address = _tunnel_address(tunnel, src)
            own_peers = [peer for peer in peers if pf_text(peer, "tun") == name]
            peer_ids = [
                f"wgpeer:{i}"
                for i, peer in enumerate(peers)
                if pf_text(peer, "tun") == name
            ]
            disableroutes = any(
                entry in _DEFAULT_ROUTES
                for peer in own_peers
                for entry in _row_cidrs(peer, "allowedips/row")
            )

            ifkey = _assigned_ifkey(name, src)
            if ifkey is None:
                followups.append(f"unassigned tunnel {name}: assign it to an interface to use it")

            gateway = ""
            if disableroutes:
                net = _server_net(tunnel_address)
                remote: list[str] = []
                for peer in own_peers:
                    for entry in _row_cidrs(peer, "allowedips/row"):
                        if entry in _DEFAULT_ROUTES:
                            continue  # a default route is not a "remote subnet"
                        if not (_is_host(entry) and net is not None and _in_net(entry, net)):
                            if entry not in remote:
                                remote.append(entry)
                warnings.append(
                    "routes for peer allowed IPs disabled (a peer has a default route); "
                    "add static routes for remote subnets: " + ", ".join(remote)
                )
                if ifkey is not None:
                    gateway = self._gateway_for(ifkey, src)

            items.append(
                Item(
                    f"wgtun:{name}",
                    self.section,
                    label,
                    tunnel,
                    Status.PARTIAL if warnings else Status.OK,
                    warnings=warnings,
                    followups=followups,
                    deps=[f"iface:{ifkey}"] if ifkey else [],
                    data={
                        "instance": instance,
                        "name": out_name,
                        "tunneladdress": tunnel_address,
                        "disableroutes": disableroutes,
                        "gateway": gateway,
                        "peer_ids": peer_ids,
                        "index": index,
                    },
                )
            )
        return items

    @staticmethod
    def _gateway_for(ifkey: str, src: SourceContext) -> str:
        for gateway in src.pf.findall("gateways/gateway_item"):
            if pf_text(gateway, "interface") == ifkey:
                return pf_text(gateway, "gateway")
        return ""

    def identity(self, item: Item) -> str | None:
        """Tunnel identity key: the *output* instance (§12.1.17).

        A base ``server`` identifies by its own ``instance``; a source item by the
        instance it will be written with, so the merge can only remove a base
        server this run actually replaces.
        """
        if item.source is None:
            return None
        if item.source.tag == "server":
            return pf_text(item.source, "instance") or None
        instance = item.data.get("instance")
        return str(instance) if instance is not None else None

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        tunnel = item.source
        if tunnel is None:
            return []
        root = E("server", uuid=ctx.uuid(item.id))
        sub(root, "enabled", "1" if pf_text(tunnel, "enabled") == "yes" else "0")
        sub(root, "name", item.data["name"])
        sub(root, "instance", str(item.data["instance"]))
        sub(root, "pubkey", pf_text(tunnel, "publickey"))
        sub(root, "privkey", pf_text(tunnel, "privatekey"))
        sub(root, "port", pf_text(tunnel, "listenport"))
        sub(root, "mtu", pf_text(tunnel, "mtu"))
        sub(root, "dns", "")
        sub(root, "tunneladdress", item.data["tunneladdress"])
        sub(root, "disableroutes", "1" if item.data["disableroutes"] else "0")
        sub(root, "gateway", item.data["gateway"])
        peers = ",".join(
            ctx.uuid(peer_id) for peer_id in item.data["peer_ids"] if ctx.selected(peer_id)
        )
        sub(root, "peers", peers)
        sub(root, "carp_depend_on", "")
        sub(root, "debug", "0")
        sub(root, "endpoint", "")
        sub(root, "peer_dns", "0")
        return [
            Placement(
                "OPNsense/wireguard/server/servers",
                Mode.APPEND,
                element=root,
                item_id=item.id,
            )
        ]

    def finalize(self, selected: list[Item], ctx: WriteContext) -> list[Placement]:
        return [Placement("OPNsense/wireguard/general/enabled", Mode.SET, text="1")]


# --------------------------------------------------------------------------- #
# Peers
# --------------------------------------------------------------------------- #


@register
class WgPeerTranslator(Translator):
    section = "wg_peer"
    title = "WireGuard peers"
    tier = 1
    order = 51
    base_policy = "merge"
    container = "OPNsense/wireguard/client/clients"

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        peers = src.pf.findall("installedpackages/wireguard/peers/item")
        if not peers:
            return []
        tunnels = {
            pf_text(tunnel, "name"): tunnel
            for tunnel in src.pf.findall("installedpackages/wireguard/tunnels/item")
        }
        #: Peer names are uniquified within this migration only — never seeded
        #: from base client names, so a base name collision is a merge-replace
        #: (the identity is that name), not a ``_2`` rename (§12.1.17).
        used_names: set[str] = set()

        items: list[Item] = []
        for index, peer in enumerate(peers):
            item_id = f"wgpeer:{index}"
            tun = pf_text(peer, "tun")
            descr = pf_desc(peer)
            label = f"WireGuard peer {descr or tun or '#' + str(index)}"

            if tun not in tunnels:
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        peer,
                        Status.UNSUPPORTED,
                        reason="peer not attached to a tunnel",
                    )
                )
                continue

            allowed = _row_cidrs(peer, "allowedips/row")
            if not allowed:
                items.append(
                    Item(
                        item_id,
                        self.section,
                        label,
                        peer,
                        Status.UNSUPPORTED,
                        reason="peer has no allowed IPs",
                    )
                )
                continue

            tunnel_address = _tunnel_address(tunnels[tun], src)
            net = _server_net(tunnel_address)
            tunnel_ips = [
                entry for entry in allowed if _is_host(entry) and net is not None and _in_net(entry, net)
            ]
            remote_nets = [entry for entry in allowed if entry not in tunnel_ips]

            warnings: list[str] = []
            followups: list[str] = []
            if remote_nets:
                warnings.append(
                    "peer routes "
                    + ", ".join(remote_nets)
                    + "; add these as static routes or in the peer's Allowed IPs"
                )
                followups.append(
                    f"peer {descr or item_id}: add the remote subnets to OPNsense static routes"
                )

            if tunnel_ips:
                address = ",".join(tunnel_ips)
                status = Status.PARTIAL if remote_nets else Status.OK
            else:
                address = remote_nets[0]
                status = Status.PARTIAL
                warnings.append(
                    "peer has no host address inside the tunnel subnet; set its tunnel address "
                    "on the OPNsense side (site-to-site peer)"
                )
                followups.append(f"peer {descr or item_id}: set its tunnel address on OPNsense")

            items.append(
                Item(
                    item_id,
                    self.section,
                    label,
                    peer,
                    status,
                    warnings=warnings,
                    followups=followups,
                    deps=[f"wgtun:{tun}"],
                    data={
                        "tun": tun,
                        "name": _unique(
                            sanitize_name(descr) or f"peer{index}", used_names
                        ),
                        "tunneladdress": address,
                    },
                )
            )
        return items

    def identity(self, item: Item) -> str | None:
        """Client identity key: the rendered name (``sanitize_name(descr)``)."""
        if item.source is None:
            return None
        if item.source.tag == "client":
            return pf_text(item.source, "name") or None
        return item.data.get("name") or (sanitize_name(pf_desc(item.source)) or None)

    def render(self, item: Item, ctx: WriteContext) -> list[Placement]:
        peer = item.source
        if peer is None:
            return []
        root = E("client", uuid=ctx.uuid(item.id))
        sub(root, "enabled", "1" if pf_text(peer, "enabled") == "yes" else "0")
        sub(root, "name", item.data["name"])
        sub(root, "pubkey", pf_text(peer, "publickey"))
        sub(root, "psk", pf_text(peer, "presharedkey"))
        sub(root, "keepalive", pf_text(peer, "persistentkeepalive"))
        sub(root, "privkey", "")
        server_id = f"wgtun:{item.data['tun']}"
        sub(root, "servers", ctx.uuid(server_id) if ctx.selected(server_id) else "")
        sub(root, "tunneladdress", item.data["tunneladdress"])
        sub(root, "serveraddress", "")
        sub(root, "serverport", "")
        return [
            Placement(
                "OPNsense/wireguard/client/clients",
                Mode.APPEND,
                element=root,
                item_id=item.id,
            )
        ]
