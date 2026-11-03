"""Tests for the WireGuard translators (design §12.1.17, M10)."""

from __future__ import annotations

import types

from lxml import etree

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status
from pf2opn.validate import validate
from pf2opn.writer import build_output


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == section}


# -- discovery --------------------------------------------------------------- #


def test_tunnel_statuses(wireguard_plan) -> None:
    _src, plan = wireguard_plan
    tunnels = _items(plan, "wg_tunnel")
    assert tunnels["wgtun:tun_wg0"].status == Status.OK
    server = tunnels["wgtun:tun_wg1"]
    assert server.status == Status.PARTIAL
    assert server.data["disableroutes"] is True
    assert server.data["gateway"] == "10.0.5.254"
    assert any("default route" in warning for warning in server.warnings)


def test_tunnel_address_from_interface(wireguard_plan) -> None:
    _src, plan = wireguard_plan
    # tun_wg1 has no addresses of its own; opt2 carries the subnet.
    assert _items(plan, "wg_tunnel")["wgtun:tun_wg1"].data["tunneladdress"] == "10.0.5.1/24"
    assert _items(plan, "wg_tunnel")["wgtun:tun_wg0"].data["tunneladdress"] == "10.2.0.2/32"


def test_peer_statuses(wireguard_plan) -> None:
    _src, plan = wireguard_plan
    peers = _items(plan, "wg_peer")
    assert peers["wgpeer:0"].status == Status.PARTIAL  # host + default route
    assert peers["wgpeer:0"].data["tunneladdress"] == "10.0.5.21/32"
    assert peers["wgpeer:0"].deps == ["wgtun:tun_wg1"]
    assert peers["wgpeer:1"].status == Status.PARTIAL  # host + remote subnet
    assert peers["wgpeer:2"].status == Status.PARTIAL  # no host address in the tunnel net
    assert peers["wgpeer:2"].data["tunneladdress"] == "172.16.5.0/24"
    assert peers["wgpeer:3"].status == Status.UNSUPPORTED
    assert peers["wgpeer:3"].reason == "peer not attached to a tunnel"


def test_peer_with_host_address_is_ok() -> None:
    pf = etree.fromstring(
        "<pfsense><installedpackages><wireguard>"
        "<tunnels><item><name>tun_wg5</name><descr>t</descr><enabled>yes</enabled>"
        "<addresses><row><address>10.0.9.1</address><mask>24</mask></row></addresses>"
        "</item></tunnels>"
        "<peers><item><tun>tun_wg5</tun><descr>Host only</descr><enabled>yes</enabled>"
        "<allowedips>10.0.9.5/32</allowedips></item></peers>"
        "</wireguard></installedpackages></pfsense>"
    )
    src = types.SimpleNamespace(pf=pf, base=etree.fromstring("<opnsense/>"))
    items = registry.get("wg_peer").discover(src, None)
    assert items[0].status == Status.OK
    assert items[0].data["tunneladdress"] == "10.0.9.5/32"


# -- rendering --------------------------------------------------------------- #


def test_tunnel_render(wireguard_plan) -> None:
    src, plan = wireguard_plan
    ctx = WriteContext(src, plan)
    root = registry.get("wg_tunnel").render(plan.by_id["wgtun:tun_wg1"], ctx)[0].element
    assert root.tag == "server"
    assert root.findtext("instance") == "1"
    assert root.findtext("name") == "Smartass_Wireguard"
    assert root.findtext("tunneladdress") == "10.0.5.1/24"
    assert root.findtext("disableroutes") == "1"
    assert root.findtext("gateway") == "10.0.5.254"
    peers = root.findtext("peers").split(",")
    assert len(peers) == 3


def test_tunnel_finalize_enables_general(wireguard_plan) -> None:
    src, plan = wireguard_plan
    ctx = WriteContext(src, plan)
    selected = [
        item for item in plan.items if item.section == "wg_tunnel" and item.id in plan.selected
    ]
    placement = registry.get("wg_tunnel").finalize(selected, ctx)[0]
    assert placement.path == "OPNsense/wireguard/general/enabled"
    assert placement.text == "1"


def test_peer_render(wireguard_plan) -> None:
    src, plan = wireguard_plan
    root = registry.get("wg_peer").render(plan.by_id["wgpeer:0"], WriteContext(src, plan))[0].element
    assert root.tag == "client"
    assert root.findtext("name") == "GL.iNet_Slate_Router"
    assert root.findtext("psk")
    assert root.findtext("keepalive") == "25"
    assert root.findtext("servers")  # the tunnel's server uuid
    assert root.findtext("serveraddress") == ""
    assert root.findtext("serverport") == ""


def test_wireguard_interface_has_no_ipaddr(wireguard_plan) -> None:
    src, plan = wireguard_plan
    out = build_output(plan, src)
    opt2 = out.find("interfaces/opt2")
    assert opt2.findtext("if") == "wg1"
    assert opt2.find("ipaddr") is None
    # the server took the address instead
    server = out.find("OPNsense/wireguard/server/servers/server[instance='1']")
    assert server.findtext("tunneladdress") == "10.0.5.1/24"


def test_wireguard_output_validates(wireguard_plan) -> None:
    src, plan = wireguard_plan
    out = build_output(plan, src)
    assert [f for f in validate(out, plan) if f.level == "error"] == []


def test_validate_detects_duplicate_instance(wireguard_plan) -> None:
    _src, plan = wireguard_plan
    out = etree.fromstring(
        "<opnsense><OPNsense><wireguard><server><servers>"
        "<server><instance>1</instance><name>a</name></server>"
        "<server><instance>1</instance><name>b</name></server>"
        "</servers></server></wireguard></OPNsense></opnsense>"
    )
    errors = [f.message for f in validate(out, plan) if f.level == "error"]
    assert any("duplicate WireGuard instance" in message for message in errors)


# -- golden ------------------------------------------------------------------ #


def test_wg_tunnel_golden(wireguard_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = wireguard_plan
    placements = section_placements(src, plan, "wg_tunnel")
    golden_checker("wg_tunnel/wireguard.xml", wrap_placements(placements))


def test_wg_peer_golden(wireguard_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = wireguard_plan
    placements = section_placements(src, plan, "wg_peer")
    golden_checker("wg_peer/wireguard.xml", wrap_placements(placements))
