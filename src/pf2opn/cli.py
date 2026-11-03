"""Command-line entry point (design §9)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from lxml import etree

from . import __version__, secrets
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
    inspect.add_argument("--base", required=True, metavar="BASE", help="base OPNsense config.xml")
    inspect.add_argument("--json", action="store_true", help="emit JSON instead of text")

    migrate = sub.add_parser("migrate", help="write a migrated OPNsense config.xml")
    migrate.add_argument("source", metavar="SOURCE", help="pfSense config.xml to read (read-only)")
    migrate.add_argument("--base", required=True, metavar="BASE", help="base OPNsense config.xml")
    migrate.add_argument("-o", "--output", required=True, metavar="OUT", help="output config.xml")
    migrate.add_argument("--report", metavar="REPORT", help="report path (default: OUT.report.md)")
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
    tui.add_argument("source", metavar="SOURCE", help="pfSense config.xml to read (read-only)")
    tui.add_argument("--base", required=True, metavar="BASE", help="base OPNsense config.xml")
    tui.add_argument("-o", "--output", required=True, metavar="OUT", help="output config.xml")
    tui.add_argument("--report", metavar="REPORT", help="report path (default: OUT.report.md)")
    tui.add_argument("--selection", metavar="FILE", help="selection JSON to load/save")
    tui.add_argument("--force", action="store_true", help="overwrite an existing OUT")

    return parser


class UsageError(Exception):
    """An input/usage problem; ``main`` maps this to exit code 2."""


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #


def _load_source(source: str, base: str) -> SourceContext:
    try:
        return SourceContext.load(source, base)
    except (OSError, ValueError, etree.XMLSyntaxError) as exc:
        raise UsageError(str(exc)) from exc


def _load_plan(source: str, base: str) -> tuple[SourceContext, Plan]:
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


def _same_file(a: Path | str, b: Path | str) -> bool:
    pa, pb = Path(a), Path(b)
    try:
        if pa.exists() and pb.exists() and os.path.samefile(pa, pb):
            return True
    except OSError:  # pragma: no cover - platform dependent
        pass
    return pa.resolve() == pb.resolve()


def _report_path(output: str, report: str | None) -> Path:
    if report:
        return Path(report)
    out = Path(output)
    return out.with_name(out.stem + ".report.md")


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
    if out_path.exists() and not args.force:
        raise UsageError(f"{out_path} exists; pass --force to overwrite")
    for other in (args.source, args.base):
        if _same_file(out_path, other):
            raise UsageError(f"refusing to write the output onto {other}")

    out = build_output(plan, src)
    findings = validate(out, plan)
    errors = [finding for finding in findings if finding.level == "error"]
    if errors:
        for finding in errors:
            print(f"pf2opn: error: {finding.message}", file=sys.stderr)
        return 1

    write_output(out, out_path)
    report_path = _report_path(args.output, args.report)
    report_path.write_text(render_report(plan, findings, src, out_path), encoding="utf-8")
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    src = _load_source(args.source, args.base)

    out_path = Path(args.output)
    for other in (args.source, args.base):
        if _same_file(out_path, other):
            raise UsageError(f"refusing to write the output onto {other}")

    from .tui import run

    try:
        return run(src, args)
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