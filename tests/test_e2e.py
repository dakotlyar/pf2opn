"""End-to-end CLI tests (design §16.3)."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path

from lxml import etree

from pf2opn import cli
from pf2opn.context import SourceContext
from pf2opn.ifmap import default_ifmap
from pf2opn.plan import build_plan
from pf2opn.validate import validate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_e2e_migrate_minimal(tmp_path: Path, minimal_path: Path, opnsense_base: Path) -> None:
    out = tmp_path / "out.xml"
    before = _sha256(minimal_path)

    code = cli.main(
        [
            "migrate",
            str(minimal_path),
            "--base",
            str(opnsense_base),
            "-o",
            str(out),
            "--select-all-ok",
            "--map-nic",
            "em0=igc0",
            "--map-nic",
            "em1=igc1",
        ]
    )

    assert code == 0
    assert out.exists()
    assert stat.S_IMODE(out.stat().st_mode) == 0o600

    reparsed = etree.fromstring(out.read_bytes())
    assert reparsed.tag == "opnsense"
    assert reparsed.findtext("system/hostname") == "pfbox"

    # the source file is unchanged
    assert _sha256(minimal_path) == before

    # the report exists next to the output
    assert (tmp_path / "out.report.md").exists()

    # validate() finds no errors on the written output
    src = SourceContext.load(minimal_path, opnsense_base)
    plan = build_plan(src, default_ifmap(src))
    assert [f for f in validate(reparsed, plan) if f.level == "error"] == []


def test_unwritable_output_exits_2(
    tmp_path: Path, minimal_path: Path, opnsense_base: Path, capsys
) -> None:
    """A missing output directory is a clean usage error, not a traceback (§9)."""
    out = tmp_path / "missing" / "out.xml"
    code = cli.main(
        [
            "migrate",
            str(minimal_path),
            "--base",
            str(opnsense_base),
            "-o",
            str(out),
            "--select-all-ok",
        ]
    )
    assert code == 2
    assert not out.exists()
    assert "cannot write" in capsys.readouterr().err


def test_unwritable_report_leaves_nothing(
    tmp_path: Path, minimal_path: Path, opnsense_base: Path, capsys
) -> None:
    """An unwritable report path fails before the config is written (§9)."""
    out = tmp_path / "out.xml"
    report = tmp_path / "missing" / "report.md"
    code = cli.main(
        [
            "migrate",
            str(minimal_path),
            "--base",
            str(opnsense_base),
            "-o",
            str(out),
            "--select-all-ok",
            "--report",
            str(report),
        ]
    )
    assert code == 2
    assert not out.exists()
    assert not report.exists()
    assert not (tmp_path / "out.report.md").exists()
    assert "cannot write report" in capsys.readouterr().err


def test_e2e_refuses_to_overwrite(tmp_path: Path, minimal_path: Path, opnsense_base: Path) -> None:
    out = tmp_path / "out.xml"
    out.write_text("<existing/>", encoding="utf-8")
    code = cli.main(
        [
            "migrate",
            str(minimal_path),
            "--base",
            str(opnsense_base),
            "-o",
            str(out),
            "--select-all-ok",
        ]
    )
    assert code == 2
    assert out.read_text(encoding="utf-8") == "<existing/>"


def test_e2e_force_overwrites(tmp_path: Path, minimal_path: Path, opnsense_base: Path) -> None:
    out = tmp_path / "out.xml"
    out.write_text("<existing/>", encoding="utf-8")
    code = cli.main(
        [
            "migrate",
            str(minimal_path),
            "--base",
            str(opnsense_base),
            "-o",
            str(out),
            "--select-all-ok",
            "--force",
        ]
    )
    assert code == 0
    assert etree.fromstring(out.read_bytes()).tag == "opnsense"


def test_e2e_refuses_output_onto_input(
    minimal_path: Path, opnsense_base: Path
) -> None:
    code = cli.main(
        [
            "migrate",
            str(minimal_path),
            "--base",
            str(opnsense_base),
            "-o",
            str(minimal_path),
            "--select-all-ok",
            "--force",
        ]
    )
    assert code == 2


def test_e2e_refuses_output_onto_base(tmp_path: Path, minimal_path: Path, opnsense_base: Path) -> None:
    code = cli.main(
        [
            "migrate",
            str(minimal_path),
            "--base",
            str(opnsense_base),
            "-o",
            str(opnsense_base),
            "--select-all-ok",
            "--force",
        ]
    )
    assert code == 2


def test_e2e_migrate_without_base_uses_default(tmp_path: Path, minimal_path: Path) -> None:
    """§16.3: no ``--base`` merges into the built-in base and the report says so."""
    out = tmp_path / "out.xml"
    code = cli.main(
        [
            "migrate",
            str(minimal_path),
            "-o",
            str(out),
            "--select-all-ok",
            "--map-nic",
            "em0=igc0",
            "--map-nic",
            "em1=igc1",
        ]
    )
    assert code == 0
    reparsed = etree.fromstring(out.read_bytes())
    assert reparsed.tag == "opnsense"
    assert reparsed.findtext("system/hostname") == "pfbox"

    report = (tmp_path / "out.report.md").read_text(encoding="utf-8")
    assert "built-in default (pf2opn factory base, 26.7)" in report
    assert "merged into pf2opn's built-in factory base" in report

    src = SourceContext.load(minimal_path)
    plan = build_plan(src, default_ifmap(src, {"em0": "igc0", "em1": "igc1"}))
    findings = validate(reparsed, plan)
    assert [f for f in findings if f.level == "error"] == []
    assert any(
        f.level == "warning" and "built-in factory base" in f.message for f in findings
    )


def test_e2e_inspect_without_base(minimal_path: Path, capsys) -> None:
    """§16.3: ``inspect`` works with no ``--base``."""
    code = cli.main(["inspect", str(minimal_path), "--json"])
    assert code == 0
    assert "system:hostname" in capsys.readouterr().out


def test_base_flag_is_optional_for_inspect_and_migrate() -> None:
    """§9: ``--base`` is optional (one rule for inspect/migrate/tui)."""
    parser = cli.build_parser()
    args = parser.parse_args(["inspect", "cfg.xml"])
    assert args.base is None
    args = parser.parse_args(["migrate", "cfg.xml", "-o", "out.xml", "--select-all-ok"])
    assert args.base is None
    args = parser.parse_args(["tui"])
    assert args.base is None and args.search_path == []