"""Tests for the Markdown report (design §14)."""

from __future__ import annotations

from pathlib import Path

from pf2opn.report import render_report
from pf2opn.validate import validate
from pf2opn.writer import build_output, write_output


def _report(tmp_path: Path, minimal_plan) -> str:
    src, plan = minimal_plan
    out = build_output(plan, src)
    out_path = tmp_path / "out.xml"
    write_output(out, out_path)
    return render_report(plan, validate(out, plan), src, out_path)


def test_report_has_all_sections(tmp_path: Path, minimal_plan) -> None:
    text = _report(tmp_path, minimal_plan)
    for heading in (
        "# pf2opn migration report",
        "## Interface map",
        "## Summary",
        "## Migrated",
        "## Skipped by user",
        "## Not translatable",
        "## Plugins to install first",
        "## Manual follow-up checklist",
        "## Validation findings",
    ):
        assert heading in text


def test_report_has_fixed_followups(tmp_path: Path, minimal_plan) -> None:
    text = _report(tmp_path, minimal_plan)
    assert "Import via System" in text
    assert "- [ ]" in text


def test_report_has_no_secret_values(tmp_path: Path, minimal_plan) -> None:
    text = _report(tmp_path, minimal_plan)
    assert "$2y$" not in text
    assert "BASEBASE" not in text


def test_report_lists_migrated_system_facets(tmp_path: Path, minimal_plan) -> None:
    text = _report(tmp_path, minimal_plan)
    assert "Hostname: pfbox.example.test" in text
    assert "Timezone: Etc/UTC" in text


def _validation_section(text: str) -> str:
    section = text.split("## Validation findings", 1)[1]
    return section.split("\n## ", 1)[0]


def test_validation_findings_include_sanitized_description(tmp_path: Path, minimal_plan) -> None:
    """§13/§14: the sanitizing warning surfaces in the report's findings section."""
    src, plan = minimal_plan
    plan.ifmap.targets["lan"].descr = "My LAN"
    out = build_output(plan, src)
    out_path = tmp_path / "out.xml"
    write_output(out, out_path)
    text = render_report(plan, validate(out, plan), src, out_path)
    assert "sanitized" in _validation_section(text)
    assert "lan" in _validation_section(text)