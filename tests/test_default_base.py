"""The bundled factory base and the default-base path (design §5.1, §16.1)."""

from __future__ import annotations

from pf2opn import paths, secrets, xmlio


def test_default_base_bytes_parse_with_opnsense_root() -> None:
    root = paths.load_default_base_root()
    assert root.tag == "opnsense"


def test_default_base_has_no_wizard_or_placeholder_nics() -> None:
    root = paths.load_default_base_root()
    assert root.find("trigger_initial_wizard") is None
    assert "mismatch" not in (root.findtext("interfaces/wan/if") or "")
    assert "mismatch" not in (root.findtext("interfaces/lan/if") or "")
    assert root.findtext("interfaces/wan/if") == "igc0"
    assert root.findtext("interfaces/lan/if") == "igc1"
    serialized = xmlio.to_string(root)
    assert "mismatch" not in serialized


def test_default_base_keeps_factory_sections() -> None:
    root = paths.load_default_base_root()
    # the root user, the two default LAN allow rules, automatic outbound NAT,
    # and the two sibling dnsmasq ranges (design §5.1).
    assert root.findtext("system/user[name='root']/name") == "root"
    assert len(root.findall("OPNsense/Firewall/Filter/rules/rule")) == 2
    assert root.findtext("nat/outbound/mode") == "automatic"
    assert root.findtext("dnsmasq/enable") == "1"
    assert len(root.findall("dnsmasq/dhcp_ranges")) == 2


def test_default_base_carries_26_7_8_model_versions() -> None:
    root = paths.load_default_base_root()
    assert root.find("OPNsense/Firewall/Filter").get("version") == "1.0.6"
    assert root.find("dnsmasq").get("version") == "1.0.9"


def test_default_base_only_secret_is_the_public_factory_password() -> None:
    root = paths.load_default_base_root()
    secret_tags = [
        el.tag
        for el in root.iter()
        if isinstance(el.tag, str) and secrets.is_secret(el.tag) and (el.text or "").strip()
    ]
    assert secret_tags == ["password"]
    # the documented factory default password hash (password is `opnsense`)
    assert (root.findtext("system/user[name='root']/password") or "").startswith("$2y$")


def test_parse_bytes_requires_the_root() -> None:
    data = b"<opnsense><a/></opnsense>"
    assert xmlio.parse_bytes(data, root="opnsense").tag == "opnsense"
    try:
        xmlio.parse_bytes(data, root="pfsense")
    except ValueError as exc:
        assert "expected <pfsense>" in str(exc)
    else:  # pragma: no cover - the wrong root must raise
        raise AssertionError("parse_bytes accepted the wrong root")


def test_parse_bytes_returns_a_mutable_element() -> None:
    root = xmlio.parse_bytes(b"<opnsense><interfaces><wan><if>x</if></wan></interfaces></opnsense>")
    root.find("interfaces/wan/if").text = "igc0"
    assert root.findtext("interfaces/wan/if") == "igc0"


def test_default_base_output_is_independent_of_the_package_data() -> None:
    """Each load returns a fresh tree; mutating one must not affect the next."""
    first = paths.load_default_base_root()
    first.find("interfaces/wan/if").text = "changed"
    second = paths.load_default_base_root()
    assert second.findtext("interfaces/wan/if") == "igc0"
