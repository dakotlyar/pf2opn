"""Command-line entry point (design §9)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from lxml import etree

from . import __version__
from .context import SourceContext
from .plan import build_default_ifmap, build_plan
from .model import Plan
from .registry import all_translators

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

    tui = sub.add_parser("tui", help="interactive selection (not implemented until M11)")
    tui.add_argument("source", metavar="SOURCE", help="pfSense config.xml to read (read-only)")
    tui.add_argument("--base", required=True, metavar="BASE", help="base OPNsense config.xml")
    tui.add_argument("-o", "--output", required=True, metavar="OUT", help="output config.xml")
    tui.add_argument("--report", metavar="REPORT", help="report path (default: OUT.report.md)")
    tui.add_argument("--selection", metavar="FILE", help="selection JSON to load/save")

    return parser


class UsageError(Exception):
    """An input/usage problem; ``main`` maps this to exit code 2."""


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #


def _load_plan(source: str, base: str) -> tuple[SourceContext, Plan]:
    try:
        src = SourceContext.load(source, base)
    except (OSError, ValueError, etree.XMLSyntaxError) as exc:
        raise UsageError(str(exc)) from exc
    ifmap = build_default_ifmap(src)
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


def cmd_migrate(args: argparse.Namespace) -> int:
    raise NotImplementedError("migrate is implemented in milestone M2")


def cmd_tui(args: argparse.Namespace) -> int:
    print("pf2opn: the interactive TUI is not implemented yet (milestone M11)", file=sys.stderr)
    return 2


_HANDLERS = {
    "inspect": cmd_inspect,
    "migrate": cmd_migrate,
    "tui": cmd_tui,
}


def main(argv: Sequence[str] | None = None) -> int:
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
    except NotImplementedError as exc:
        print(f"pf2opn: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())