"""Tests for the trust translators (design §12.1.14, M9)."""

from __future__ import annotations

import base64
import types

from lxml import etree

from pf2opn import registry
from pf2opn.context import WriteContext
from pf2opn.model import Mode, Status
from pf2opn.translators.trust import _decode_base64, check_crt


def _items(plan, section):
    return {item.id: item for item in plan.items if item.section == section}


# -- discovery --------------------------------------------------------------- #


def test_ca_and_cert_discovered(trust_users_plan) -> None:
    _src, plan = trust_users_plan
    ca = _items(plan, "ca")["ca:ca1"]
    assert ca.status == Status.OK
    assert ca.default_selected is True

    certs = _items(plan, "cert")
    assert certs["cert:cert1"].status == Status.OK
    assert certs["cert:cert1"].deps == ["ca:ca1"]
    assert certs["cert:cert2"].status == Status.PARTIAL
    assert "chain trimmed" in certs["cert:cert2"].warnings[0]


def test_chain_trimmed_to_leaf(trust_users_plan) -> None:
    _src, plan = trust_users_plan
    item = _items(plan, "cert")["cert:cert2"]
    decoded = _decode_base64(item.data["crt"])
    assert decoded is not None
    assert decoded.count("-----BEGIN CERTIFICATE-----") == 1


def test_check_crt_rejects_non_base64() -> None:
    assert check_crt("not base64 !!!")[2] == "certificate data could not be decoded"


def test_check_crt_rejects_non_pem() -> None:
    payload = base64.b64encode(b"just some bytes").decode()
    assert check_crt(payload)[2] == "no PEM certificate found in certificate data"


def test_check_crt_rejects_empty() -> None:
    """An empty ``crt`` is unusable, not a valid empty certificate."""
    value, warning, reason = check_crt("")
    assert value is None
    assert warning is None
    assert reason == "certificate data is empty"


def test_check_crt_trims_and_reencodes() -> None:
    pem = (
        "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n"
        "-----BEGIN CERTIFICATE-----\nBBB\n-----END CERTIFICATE-----\n"
    )
    crt = base64.b64encode(pem.encode()).decode()
    value, warning, reason = check_crt(crt)
    assert reason is None
    assert warning is not None
    decoded = _decode_base64(value)
    assert decoded is not None
    assert decoded.count("-----BEGIN CERTIFICATE-----") == 1
    assert "AAA" in decoded
    assert "BBB" not in decoded


# -- rendering --------------------------------------------------------------- #


def test_ca_render(trust_users_plan) -> None:
    src, plan = trust_users_plan
    ctx = WriteContext(src, plan)
    placement = registry.get("ca").render(plan.by_id["ca:ca1"], ctx)[0]
    assert placement.mode == Mode.APPEND
    assert placement.path == ""
    root = placement.element
    assert root.tag == "ca"
    assert root.findtext("refid") == "ca1"
    assert root.findtext("descr") == "Test CA"
    assert root.findtext("serial") == "0"
    assert root.find("prv") is not None
    # dropped
    assert root.find("trust") is None
    assert root.find("randomserial") is None


def test_cert_render_drops_type(trust_users_plan) -> None:
    src, plan = trust_users_plan
    ctx = WriteContext(src, plan)
    root = registry.get("cert").render(plan.by_id["cert:cert1"], ctx)[0].element
    assert root.tag == "cert"
    assert root.findtext("caref") == "ca1"
    assert root.find("type") is None
    assert root.find("prv") is not None


def test_webconfigurator_cert_is_not_default_selected() -> None:
    pem = "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n"
    crt = base64.b64encode(pem.encode()).decode()
    pf = etree.fromstring(
        f"<pfsense><cert><refid>c9</refid><descr>webConfigurator default</descr>"
        f"<crt>{crt}</crt></cert></pfsense>"
    )
    src = types.SimpleNamespace(pf=pf)
    items = registry.get("cert").discover(src, None)
    assert items[0].default_selected is False


# -- golden ------------------------------------------------------------------ #


def test_ca_golden(trust_users_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = trust_users_plan
    placements = section_placements(src, plan, "ca")
    golden_checker("ca/trust_users.xml", wrap_placements(placements))


def test_cert_golden(trust_users_plan, golden_checker, section_placements, wrap_placements) -> None:
    src, plan = trust_users_plan
    placements = section_placements(src, plan, "cert")
    golden_checker("cert/trust_users.xml", wrap_placements(placements))
