"""OPNsense model version attributes (design §10.3).

Path (relative to ``<opnsense>``) -> ``version`` attribute as shipped in
OPNsense 26.7.8. ``+`` mounts (``ca``, ``cert``, ``system/user``,
``system/group``, ``nat/rule``) carry no version attribute and are absent here.
"""

from __future__ import annotations

MODEL_VERSIONS: dict[str, str] = {
    "OPNsense/Firewall/Alias": "1.0.1",
    "OPNsense/Firewall/Filter": "1.0.6",
    "OPNsense/Gateways": "1.0.0",
    "dnsmasq": "1.0.9",
    "OPNsense/unboundplus": "1.0.15",
    "OPNsense/wireguard/server": "1.0.1",
    "OPNsense/wireguard/client": "1.0.0",
    "OPNsense/wireguard/general": "0.0.1",
    "OPNsense/Syslog": "1.0.2",
    "OPNsense/DynDNS": "1.5.1",
    "vlans": "1.0.0",
    "virtualip": "1.0.1",
    "staticroutes": "1.0.1",
}
