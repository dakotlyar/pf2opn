"""Command-line entry point (design §9)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from lxml import etree

from . import __version__, paths, secrets
from .context import SourceContext
from .ifmap import default_ifmap, validate_ifmap
from .model import Plan, Selection
from .plan import build_plan
from .registry import all_translators
from .report import render_report
from .selection import compute_selection, load_selection
from .validate import validate
from .writer import build_output, write_output

_PROG = "pf2opn"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="Migrate a pfSense config.xml onto OPNsense.",
    )
    parser.add_argument("--version", action="version", version=f"{_PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    inspect = sub.add_parser("inspect", help="list migratable items in a pfSense config")
    inspect.add_argument("source", metavar="SOURCE", help="pfSense config.xml to read (read-only)")
    inspect.add_argument(
        "--base",
        metavar="BASE",
        help="base OPNsense config.xml (default: pf2opn's built-in factory base)",
    )
    inspect.add_argument("--json", action="store_true", help="emit JSON instead of text")

    migrate = sub.add_parser("migrate", help="write a migrated OPNsense config.xml")
    migrate.add_argument("source", metavar="SOURCE", help="pfSense config.xml to read (read-only)")
    migrate.add_argument(
        "--base",
        metavar="BASE",
        help="base OPNsense config.xml (default: pf2opn's built-in factory base)",
    )
    migrate.add_argument("-o", "--output", required=True, metavar="OUT", help="output config.xml")
    migrate.add_argument(
        "--report",
        metavar="REPORT",
        help="report path (default: OUT with its suffix replaced by .report.md, "
        "e.g. out.xml -> out.report.md)",
    )
    selection = migrate.add_mutually_exclusive_group(required=True)
    selection.add_argument("--select-all-ok", action="store_true", help="select every default item")
    selection.add_argument("--selection", metavar="FILE", help="selection JSON to load")
    migrate.add_argument(
        "--map-nic",
        action="append",
        default=[],
        metavar="PF=OPN",
        help="map a pfSense NIC to an OPNsense NIC (repeatable)",
    )
    migrate.add_argument("--keep-base-rules", action="store_true", help="keep the base filter rules")
    migrate.add_argument("--force", action="store_true", help="overwrite an existing OUT")

    tui = sub.add_parser("tui", help="interactive selection and migration")
    tui.add_argument(
        "source",
        metavar="SOURCE",
        nargs="?",
        help="pfSense config.xml to read (read-only); collected on the Load screen if omitted",
    )
    tui.add_argument(
        "--base",
        metavar="BASE",
        help="base OPNsense config.xml (default: pf2opn's built-in factory base); "
        "collected on the Load screen if omitted",
    )
    tui.add_argument(
        "-o",
        "--output",
        metavar="OUT",
        help="output config.xml; collected on the Load screen if omitted",
    )
    tui.add_argument(
        "--report",
        metavar="REPORT",
        help="report path (default: OUT with its suffix replaced by .report.md, "
        "e.g. out.xml -> out.report.md)",
    )
    tui.add_argument("--selection", metavar="FILE", help="selection JSON to load/save")
    tui.add_argument(
        "--search-path",
        action="append",
        default=[],
        metavar="DIR",
        help="add a directory to the Load screen's config search roots (repeatable)",
    )
    tui.add_argument("--force", action="store_true", help="overwrite an existing OUT")

    return parser


class UsageError(Exception):
    """An input/usage problem; ``main`` maps this to exit code 2."""


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #


def _load_source(source: str, base: str | None = None) -> SourceContext:
    try:
        return SourceContext.load(source, base)
    except (OSError, ValueError, etree.XMLSyntaxError) as exc:
        raise UsageError(str(exc)) from exc


def _load_plan(source: str, base: str | None = None) -> tuple[SourceContext, Plan]:
    src = _load_source(source, base)
    ifmap = default_ifmap(src)
    return src, build_plan(src, ifmap)


def _item_json(item) -> dict:
    return {
        "id": item.id,
        "section": item.section,
        "label": item.label,
        "status": item.status.value,
        "reason": item.reason,
        "warnings": list(item.warnings),
        "deps": list(item.deps),
    }


def cmd_inspect(args: argparse.Namespace) -> int:
    _src, plan = _load_plan(args.source, args.base)
    if args.json:
        print(json.dumps([_item_json(item) for item in plan.items], indent=2))
        return 0

    titles = {translator.section: translator.title for translator in all_translators()}
    current_section = None
    for item in plan.items:
        if item.section != current_section:
            current_section = item.section
            print(f"== {titles.get(item.section, item.section)} ==")
        suffix = f"  [{len(item.warnings)} warnings]" if item.warnings else ""
        print(f"{item.status.value.upper():<11} {item.id:<28} {item.label}{suffix}")
    return 0


# --------------------------------------------------------------------------- #
# migrate / tui
# --------------------------------------------------------------------------- #


def _parse_nic_pairs(pairs: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise UsageError(f"invalid --map-nic {pair!r}, expected PF=OPN")
        pf_nic, opn_nic = (part.strip() for part in pair.split("=", 1))
        if not pf_nic or not opn_nic:
            raise UsageError(f"invalid --map-nic {pair!r}, expected PF=OPN")
        mapping[pf_nic] = opn_nic
    return mapping


def _report_path(output: str, report: str | None) -> Path:
    if report:
        return Path(report)
    out = Path(output)
    return out.with_name(out.stem + ".report.md")


def _ensure_report_writable(report_path: Path) -> None:
    """Raise :class:`UsageError` unless *report_path* can be written.

    The report is rendered from the written output, so it is written *after* the
    config; checking it first means an unwritable report path fails (exit 2)
    before the config is touched, instead of leaving a written config behind.
    """
    try:
        if report_path.exists():
            if report_path.is_dir():
                raise IsADirectoryError(f"is a directory: {report_path}")
            if not os.access(report_path, os.W_OK):
                raise PermissionError(f"permission denied: {report_path}")
            return
        parent = report_path.parent
        if not parent.exists():
            raise FileNotFoundError(f"no such directory: {parent}")
        probe = parent / f".pf2opn-report-{os.getpid()}.tmp"
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        probe.unlink()
    except OSError as exc:
        raise UsageError(f"cannot write report {report_path}: {exc}") from exc


def cmd_migrate(args: argparse.Namespace) -> int:
    src = _load_source(args.source, args.base)

    if args.selection:
        try:
            sel = load_selection(args.selection)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise UsageError(str(exc)) from exc
    else:
        sel = Selection()

    nic_map = dict(sel.nic_map)
    nic_map.update(_parse_nic_pairs(args.map_nic))

    ifmap = default_ifmap(src, nic_map)
    for key, spec in sel.interfaces.items():
        target = ifmap.targets.get(key)
        if target is not None:
            target.include = bool(spec.get("include", True))

    plan = build_plan(src, ifmap)
    plan.options["keep_base_rules"] = args.keep_base_rules
    try:
        plan.selected = compute_selection(plan, sel)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc

    mapping_errors = validate_ifmap(ifmap)
    if mapping_errors:
        raise UsageError("; ".join(mapping_errors))

    if sel.source_sha256 and sel.source_sha256 != src.source_sha256:
        print(
            "pf2opn: warning: selection source_sha256 does not match the source file",
            file=sys.stderr,
        )

    out_path = Path(args.output)
    overwrite = paths.check_output(out_path, args.source, args.base, args.force)
    if overwrite:
        raise UsageError(overwrite)
    if not out_path.parent.exists():
        raise UsageError(f"cannot write {out_path}: no such directory: {out_path.parent}")

    out = build_output(plan, src)
    findings = validate(out, plan)
    errors = [finding for finding in findings if finding.level == "error"]
    if errors:
        for finding in errors:
            print(f"pf2opn: error: {finding.message}", file=sys.stderr)
        return 1

    # Validate the report path before writing the config: a report failure must
    # not leave a written config behind (README exit-1 = "nothing written").
    report_path = _report_path(args.output, args.report)
    _ensure_report_writable(report_path)

    try:
        write_output(out, out_path)
    except OSError as exc:
        raise UsageError(f"cannot write {out_path}: {exc}") from exc
    report_path.write_text(render_report(plan, findings, src, out_path), encoding="utf-8")
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    source = getattr(args, "source", None)
    base = getattr(args, "base", None)
    output = getattr(args, "output", None)

    # §9: where a path was passed as a flag, the CLI is authoritative and
    # validates it before starting the TUI (exit 2, no TUI). Omitted paths are
    # collected — and validated — on the Load screen instead.
    if source is not None:
        error = paths.source_error(source)
        if error:
            raise UsageError(error)
    if base is not None:
        error = paths.base_error(base)
        if error:
            raise UsageError(error)
    if output is not None:
        error = paths.check_output(output, source, base, args.force)
        if error:
            raise UsageError(error)

    # Preload the context when a source is known; --base is optional (§9), so an
    # omitted base means the Load screen starts loaded against the built-in base.
    src = SourceContext.load(source, base) if source is not None else None

    from .tui import run

    try:
        return run(args, src)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise UsageError(str(exc)) from exc


_HANDLERS = {
    "inspect": cmd_inspect,
    "migrate": cmd_migrate,
    "tui": cmd_tui,
}


def main(argv: Sequence[str] | None = None) -> int:
    # §11 rule 5: the redaction filter must be attached before any file is parsed.
    secrets.install_logging_filter()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    handler = _HANDLERS[args.command]
    try:
        return handler(args)
    except UsageError as exc:
        print(f"pf2opn: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())