"""Tests for validation checks 1-2 (design §13)."""

from __future__ import annotations

from lxml import etree

from pf2opn.validate import validate
from pf2opn.writer import build_output


def _el(xml: str) -> etree._Element:
    return etree.fromstring(xml)


def _errors(findings):
    return [finding for finding in findings if finding.level == "error"]


def test_valid_output_has_no_errors(minimal_plan) -> None:
    src, plan = minimal_plan
    out = build_output(plan, src)
    assert _errors(validate(out, plan)) == []


def test_wrong_root_element_is_an_error(minimal_plan) -> None:
    _src, plan = minimal_plan
    findings = validate(_el("<notopnsense/>"), plan)
    assert any(finding.level == "error" and "opnsense" in finding.message for finding in findings)


def test_duplicate_uuid_is_an_error(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el('<opnsense><a uuid="abc"/><b uuid="abc"/></opnsense>')
    findings = _errors(validate(out, plan))
    assert len(findings) == 1
    assert "duplicate uuid abc" in findings[0].message


def test_unique_uuids_pass(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el('<opnsense><a uuid="one"/><b uuid="two"/></opnsense>')
    assert _errors(validate(out, plan)) == []


def test_version_mismatch_is_a_warning(minimal_plan) -> None:
    _src, plan = minimal_plan
    out = _el(
        '<opnsense><OPNsense><Firewall><Filter version="1.0.0"/></Firewall></OPNsense></opnsense>'
    )
    findings = validate(out, plan)
    warnings = [finding for finding in findings if finding.level == "warning"]
    assert any("1.0.0" in warning.message and "1.0.6" in warning.message for warning in warnings)
    assert _errors(findings) == []