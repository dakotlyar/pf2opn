"""Unsupported catalogue (design §12.4).

Every entry is one UNSUPPORTED item, emitted only when the section/package is
present and non-empty. Sections that are non-empty but carry no policy at all are
not items: :func:`ignored_sections` returns them for the report's "not
applicable" list.
"""

from __future__ import annotations

from collections.abc import Callable

from lxml import etree

from ..context import SourceContext
from ..model import IfaceMap, Item, Status
from ..registry import register
from .base import Translator, pf_text


def _nonempty(el: etree._Element | None) -> bool:
    """True when *el* has non-whitespace text, or a child that is itself non-empty.

    §12.4: a present-but-empty child does not count — ``<pfblockerng/>`` and
    ``<snmpd><syslocation/></snmpd>`` are not non-empty and must not raise an item.
    """
    if el is None:
        return False
    if (el.text or "").strip():
        return True
    return any(_nonempty(child) for child in el if isinstance(child.tag, str))


def _packages(pf: etree._Element, prefix: str) -> bool:
    container = pf.find("installedpackages")
    if container is None:
        return False
    return any(
        isinstance(child.tag, str) and child.tag.startswith(prefix) and _nonempty(child)
        for child in container
    )


def _l2tp_on(pf: etree._Element) -> bool:
    l2tp = pf.find("l2tp")
    return l2tp is not None and pf_text(l2tp, "mode") not in {"", "off"}


def _snmpd(pf: etree._Element) -> bool:
    snmpd = pf.find("snmpd")
    if snmpd is None:
        return False
    # §12.4: the child must carry content — an empty <syslocation/> does not count.
    return any(
        _nonempty(snmpd.find(tag))
        for tag in ("syslocation", "syscontact", "rocommunity", "enable")
    )


def _shaper(pf: etree._Element) -> bool:
    return bool(pf.findall("shaper/queue")) or bool(pf.findall("dnshaper/queue"))


def _ipsec(pf: etree._Element) -> bool:
    return (
        any(_nonempty(phase1) for phase1 in pf.findall("ipsec/phase1"))
        or _nonempty(pf.find("ipsec/mobilekey"))
        or _nonempty(pf.find("ipsec/client"))
    )


def _openvpn(pf: etree._Element) -> bool:
    return _nonempty(pf.find("openvpn/openvpn-server")) or _nonempty(
        pf.find("openvpn/openvpn-client")
    )


def _shellcmd(pf: etree._Element) -> bool:
    return (
        _packages(pf, "shellcmdsettings")
        or _nonempty(pf.find("system/earlyshellcmd"))
        or _nonempty(pf.find("system/shellcmd"))
    )


#: ``(item suffix, label, detector, reason)`` per §12.4.
_CATALOGUE: tuple[tuple[str, str, Callable[[etree._Element], bool], str], ...] = (
    ("pfblockerng", "pfBlockerNG", lambda pf: _packages(pf, "pfblockerng"),
     "pfBlockerNG has no OPNsense port. Use Unbound DNS blocklists (Services \u25b8 Unbound \u25b8 "
     "Blocklist) and GeoIP aliases"),
    ("snort", "Snort", lambda pf: _packages(pf, "snortglobal"),
     "use the built-in Suricata IDS/IPS (Services \u25b8 Intrusion Detection); rules and policies "
     "must be rebuilt"),
    ("suricata", "Suricata", lambda pf: _packages(pf, "suricata"),
     "OPNsense Suricata uses a different config model; reconfigure"),
    ("captiveportal", "Captive portal",
     lambda pf: any(_nonempty(child) for child in pf.findall("captiveportal/*")),
     "OPNsense captive portal uses a different zone/template model; rebuild"),
    ("ntopng", "ntopng", lambda pf: _packages(pf, "ntopng"),
     "install os-ntopng and reconfigure"),
    ("bandwidthd", "BandwidthD", lambda pf: _packages(pf, "bandwidthd"),
     "use Reporting \u25b8 Insight (NetFlow)"),
    ("traffictotals", "Traffic Totals", lambda pf: _packages(pf, "traffictotals"),
     "use Reporting \u25b8 Insight"),
    ("miniupnpd", "miniupnpd", lambda pf: _packages(pf, "miniupnpd"),
     "install os-upnp and reconfigure (Tier 2 candidate)"),
    ("telegraf", "Telegraf", lambda pf: _packages(pf, "telegraf"),
     "install os-telegraf and reconfigure"),
    ("acme", "ACME", lambda pf: _packages(pf, "acme"),
     "install os-acme-client; re-create accounts and certificates (account keys not migrated)"),
    ("pimd", "PIMD", lambda pf: _packages(pf, "pimd"),
     "multicast routing: os-frr (PIM) or os-igmp-proxy"),
    ("udpbroadcastrelay", "UDP Broadcast Relay", lambda pf: _packages(pf, "udpbroadcastrelay"),
     "install os-udpbroadcastrelay (Tier 2 candidate)"),
    ("freeradius", "FreeRADIUS", lambda pf: _packages(pf, "freeradius"),
     "install os-freeradius and reconfigure; client secrets not migrated"),
    ("frr", "FRR", lambda pf: _packages(pf, "frr"),
     "install os-frr 2.x and rebuild BGP config"),
    ("tailscale", "Tailscale", lambda pf: _packages(pf, "tailscale"),
     "install os-tailscale and re-authenticate"),
    ("shellcmd", "Shell commands", _shellcmd, "use /usr/local/etc/rc.syshook.d/ scripts"),
    ("servicewatchdog", "Service Watchdog", lambda pf: _packages(pf, "servicewatchdog"),
     "use Monit"),
    ("openvpn_export", "OpenVPN export", lambda pf: _packages(pf, "vpn_openvpn_export"),
     "built into OPNsense OpenVPN"),
    ("l2tp", "L2TP server", _l2tp_on, "no L2TP server on OPNsense; use WireGuard or OpenVPN"),
    ("ipsec", "IPsec", _ipsec, "IPsec (mobile clients / tunnels) not migrated in v1 (Tier 2)"),
    ("openvpn", "OpenVPN", _openvpn, "OpenVPN not migrated in v1 (Tier 2)"),
    ("shaper", "Traffic shaper", _shaper, "traffic shaper models differ; rebuild"),
    ("snmpd", "SNMP", _snmpd, "install os-net-snmp (Tier 2); community string not shown"),
    ("dhcpdv6", "DHCPv6 / router advertisements", lambda pf: bool(pf.findall("dhcpdv6/*/enable")),
     "Tier 2: Dnsmasq DHCPv6 / router advertisements"),
    ("sysctl", "System tunables", lambda pf: bool(pf.findall("sysctl/item")),
     "Tier 2: review tunables manually (System \u25b8 Settings \u25b8 Tunables)"),
    ("ifgroups", "Interface groups", lambda pf: bool(pf.findall("ifgroups/ifgroupentry")),
     "Tier 2; package-created groups (WireGuard/Tailscale) aren't needed"),
    ("sshdata", "SSH host keys", lambda pf: _nonempty(pf.find("sshdata")),
     "SSH host keys not migrated; OPNsense generates new ones"),
)

#: Non-empty sections with no policy; listed once under "not applicable" (§12.4).
_IGNORED: tuple[tuple[str, str], ...] = (
    ("ovpnserver", "OpenVPN-wizard leftovers; the openvpn section is empty, so there is no live "
                   "VPN to migrate"),
    ("diag", "diagnostic history, not configuration"),
    ("rrd", "RRD graph data, not migrated"),
    ("widgets", "web GUI dashboard layout, not migrated"),
    ("traffic_graphs", "traffic graph settings, not migrated"),
    ("revision", "config revision metadata, regenerated on import"),
    ("lastchange", "config change metadata, not migrated"),
    ("dhcpbackend", "DHCP backend selector; Dnsmasq is used instead"),
    ("proxyarp", "proxy ARP entries, not migrated"),
    ("wol", "Wake-on-LAN entries, not migrated"),
    ("dnshaper", "traffic shaper leftovers; queues are reported separately"),
    ("igmpproxy", "IGMP proxy settings, not migrated"),
    ("ppps", "PPP interface settings, not migrated"),
)


def ignored_sections(pf: etree._Element) -> list[tuple[str, str]]:
    """The §12.4 non-empty-but-ignored sections, as ``(label, reason)`` pairs."""
    out: list[tuple[str, str]] = []
    for tag, reason in _IGNORED:
        if _nonempty(pf.find(tag)):
            if tag == "dnshaper" and pf.findall("dnshaper/queue"):
                continue  # already reported as the traffic shaper
            out.append((tag, reason))
    installed = pf.find("installedpackages")
    if installed is not None:
        for tag in ("package", "menu", "service"):
            if _nonempty(installed.find(tag)):
                out.append((f"installedpackages/{tag}", "package bookkeeping, not policy"))
    return out


@register
class UnsupportedTranslator(Translator):
    section = "unsupported"
    title = "Not translatable"
    tier = 0
    order = 900
    base_policy = "none"
    container = None

    def discover(self, src: SourceContext, ifmap: IfaceMap) -> list[Item]:
        items: list[Item] = []
        for suffix, label, detector, reason in _CATALOGUE:
            if detector(src.pf):
                items.append(
                    Item(
                        f"unsup:{suffix}",
                        self.section,
                        label,
                        None,
                        Status.UNSUPPORTED,
                        reason=reason,
                    )
                )
        return items
