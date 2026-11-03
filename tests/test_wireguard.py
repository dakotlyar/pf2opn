"""Tests for the WireGuard translators (design §12.1.17, M10)."""

from __future__ import annotations

import types
from pathlib import Path

from lxml import etree

from pf2opn import registry
from pf2opn.context import SourceContext, WriteContext
from pf2opn.ifmap import default_ifmap
from pf2opn.model import Mode, Status
from pf2opn.plan import build_plan
from pf2opn.validate import validate
from pf2opn.writer import build_output, write_output

_PFSENSE = Path(__file__).resolve().parent / "fixtures" / "pfsense"
_NIC_MAP = {"em0": "igc0", "em1": "igc1"}


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == section}


def _plan_with_base(base: Path):
    src = SourceContext.load(_PFSENSE / "wireguard.xml", base)
    return src, build_plan(src, default_ifmap(src, dict(_NIC_MAP)))


def _run(source: Path, base: Path, out: Path) -> etree._Element:
    src = SourceContext.load(source, base)
    plan = build_plan(src, default_ifmap(src, dict(_NIC_MAP)))
    result = build_output(plan, src)
    write_output(result, out)
    return result


def _clients(out: etree._Element) -> list[etree._Element]:
    return out.findall("OPNsense/wireguard/client/clients/client")


def _servers(out: etree._Element) -> list[etree._Element]:
    return out.findall("OPNsense/wireguard/server/servers/server")


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


def test_disableroutes_warning_excludes_the_default_route(wireguard_plan) -> None:
    """A default route is not a "remote subnet" in the warning (§12.1.17)."""
    _src, plan = wireguard_plan
    warnings = _items(plan, "wg_tunnel")["wgtun:tun_wg1"].warnings
    route_warning = next(w for w in warnings if "remote subnets" in w)
    assert "0.0.0.0/0" not in route_warning
    assert "192.168.8.0/24" in route_warning


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


# -- a base that already holds WireGuard servers/clients (AP-5 review) ------- #


def test_peer_reimport_is_idempotent(opnsense_wg_base: Path, tmp_path: Path) -> None:
    """A base client sharing the peer's name is merge-replaced, not duplicated."""
    src_path = _PFSENSE / "wireguard.xml"
    first = tmp_path / "first.xml"
    out1 = _run(src_path, opnsense_wg_base, first)
    assert [c.findtext("name") for c in _clients(out1)].count("GL.iNet_Slate_Router") == 1

    second = tmp_path / "second.xml"
    out2 = _run(src_path, first, second)
    names = [c.findtext("name") for c in _clients(out2)]
    assert names.count("GL.iNet_Slate_Router") == 1
    assert names.count("Leftover_Client") == 1
    assert sorted(names) == [
        "GL.iNet_Slate_Router",
        "Leftover_Client",
        "Site_B",
        "Site_C",
    ]


def test_colliding_base_tunnel_survives(opnsense_wg_base: Path) -> None:
    src, plan = _plan_with_base(opnsense_wg_base)
    out = build_output(plan, src)
    by_name = {server.findtext("name"): server.findtext("instance") for server in _servers(out)}
    # The unrelated base tunnels are kept; the pf tunnel that would have used
    # instance 1 is reassigned to the next free instance.
    assert by_name["Legacy Tunnels"] == "1"
    assert by_name["Leftover"] == "4"
    assert by_name["ProtonVPN"] == "0"
    assert by_name["Smartass_Wireguard"] == "5"
    instances = [server.findtext("instance") for server in _servers(out)]
    assert len(instances) == len(set(instances))
    assert (
        "instance 1 already used in the base; assigned 5"
        in plan.by_id["wgtun:tun_wg1"].warnings
    )


def test_ifmap_target_matches_reassigned_instance(opnsense_wg_base: Path) -> None:
    src, plan = _plan_with_base(opnsense_wg_base)
    out = build_output(plan, src)
    assert plan.ifmap.wg_names["tun_wg1"] == "wg5"
    assert plan.ifmap.targets["opt2"].opn_device == "wg5"
    assert out.findtext("interfaces/opt2/if") == "wg5"
    server = out.find("OPNsense/wireguard/server/servers/server[instance='5']")
    assert server is not None
    assert server.findtext("name") == "Smartass_Wireguard"


def test_base_wg_output_validates(opnsense_wg_base: Path) -> None:
    src, plan = _plan_with_base(opnsense_wg_base)
    out = build_output(plan, src)
    assert [f for f in validate(out, plan) if f.level == "error"] == []
