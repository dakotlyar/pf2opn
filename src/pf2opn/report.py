"""Markdown migration report (design §14).

Built only from labels, reasons, warnings and follow-ups plus counts — never
from secret field values (§11 rule 3).
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from . import __version__
from .context import SourceContext
from .model import Plan, Status
from .registry import all_translators
from .translators.unsupported import ignored_sections
from .validate import Finding

_FIXED_FOLLOWUPS = [
    "Import via System \u25b8 Configuration \u25b8 Backups \u25b8 Restore (whole config), then reboot.",
    "Verify interface assignments on the console before connecting WAN.",
    "SSH host keys and web GUI certificate fingerprints will differ.",
    "Dynamic DHCP leases are not migrated; clients will re-request.",
]

#: §14: only when the built-in base was used (§5.1 / D2).
_DEFAULT_BASE_FOLLOWUP = (
    "Output was merged into pf2opn's built-in factory base: interface devices "
    "(`igc0`/`igc1`) and system settings are defaults \u2014 confirm interface "
    "assignments, and change the root password (the factory default is `opnsense`) "
    "before connecting."
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _title_map() -> dict[str, str]:
    return {translator.section: translator.title for translator in all_translators()}


def render_report(
    plan: Plan,
    findings: list[Finding],
    src: SourceContext,
    out_path: Path | str,
) -> str:
    titles = _title_map()
    out_path = Path(out_path)
    lines: list[str] = []

    # 1. Header
    lines.append("# pf2opn migration report")
    lines.append("")
    lines.append(f"- pf2opn version: {__version__}")
    lines.append(f"- date: {date.today().isoformat()}")
    lines.append(f"- source: `{src.source_path}`")
    lines.append(f"- source hostname: {src.pf_hostname or '(unknown)'}")
    lines.append(f"- pfSense version: {src.pf_version or '(unknown)'}")
    if src.base_is_default:
        base_origin = "built-in default (pf2opn factory base, 26.7)"
    else:
        base_origin = f"`{src.base_path}`"
    lines.append(f"- base: {base_origin}")
    lines.append(f"- base hostname: {src.base_hostname or '(unknown)'}")
    lines.append(f"- output: `{out_path}`")
    lines.append(f"- source sha256: `{src.source_sha256}`")
    lines.append(f"- output sha256: `{_sha256(out_path) if out_path.exists() else ''}`")
    lines.append("")

    # 2. Interface map
    lines.append("## Interface map")
    lines.append("")
    lines.append("| pf key | description | pfSense device | OPNsense device | included |")
    lines.append("|---|---|---|---|---|")
    for key, target in plan.ifmap.targets.items():
        device = target.opn_device or "(unmapped)"
        lines.append(
            f"| {key} | {target.descr} | {target.pf_device} | {device} "
            f"| {'yes' if target.include else 'no'} |"
        )
    lines.append("")

    # 3. Summary per section
    order, counts = _section_counts(plan)
    lines.append("## Summary")
    lines.append("")
    lines.append("| section | migrated | skipped by user | not translatable |")
    lines.append("|---|---|---|---|")
    for section in order:
        migrated, skipped, unsupported = counts[section]
        lines.append(
            f"| {titles.get(section, section)} | {migrated} | {skipped} | {unsupported} |"
        )
    lines.append("")

    # 4. Migrated — emitted objects only; settings/classifier notes go to 4b (§14)
    lines.append("## Migrated")
    lines.append("")
    for section in order:
        items = _items_for(plan, section, Status.UNSUPPORTED, invert=True)
        selected = [
            item for item in items if item.id in plan.selected and item.emits
        ]
        if not selected:
            continue
        lines.append(f"### {titles.get(section, section)}")
        for item in selected:
            lines.append(f"- {item.label}")
            for warning in item.warnings:
                lines.append(f"  - {warning}")
        lines.append("")

    # 4b. Settings & notes — selected items that emit no config object (§14)
    lines.append("## Settings & notes")
    lines.append("")
    any_notes = False
    for section in order:
        items = _items_for(plan, section, Status.UNSUPPORTED, invert=True)
        notes = [item for item in items if not item.emits and item.id in plan.selected]
        if not notes:
            continue
        any_notes = True
        lines.append(f"### {titles.get(section, section)}")
        for item in notes:
            lines.append(f"- {item.label}")
            for warning in item.warnings:
                lines.append(f"  - {warning}")
        lines.append("")
    if not any_notes:
        lines.append("- (none)")
        lines.append("")

    # 5. Skipped by user
    lines.append("## Skipped by user")
    lines.append("")
    for section in order:
        items = _items_for(plan, section, Status.UNSUPPORTED, invert=True)
        skipped = [item for item in items if item.id not in plan.selected]
        if not skipped:
            continue
        lines.append(f"### {titles.get(section, section)}")
        for item in skipped:
            lines.append(f"- {item.label}")
        lines.append("")

    # 6. Not translatable
    lines.append("## Not translatable")
    lines.append("")
    any_unsupported = False
    for item in plan.items:
        if item.status == Status.UNSUPPORTED:
            any_unsupported = True
            lines.append(f"- {item.label} \u2014 {item.reason}")
    if not any_unsupported:
        lines.append("- (none)")
    lines.append("")

    # 6b. Not applicable (non-empty sections with no policy, §12.4)
    lines.append("## Not applicable")
    lines.append("")
    ignored = ignored_sections(src.pf)
    if ignored:
        for label, reason in ignored:
            lines.append(f"- {label} \u2014 {reason}")
    else:
        lines.append("- (none)")
    lines.append("")

    # 7. Plugins to install first
    plugins = sorted(
        {
            item.requires_plugin
            for item in plan.items
            if item.id in plan.selected and item.requires_plugin
        }
    )
    lines.append("## Plugins to install first")
    lines.append("")
    if plugins:
        for plugin in plugins:
            lines.append(f"- {plugin}")
    else:
        lines.append("- (none)")
    lines.append("")

    # 8. Manual follow-up checklist
    lines.append("## Manual follow-up checklist")
    lines.append("")
    followups: list[str] = []
    for item in plan.items:
        for followup in item.followups:
            if followup not in followups:
                followups.append(followup)
    for fixed in _FIXED_FOLLOWUPS:
        if fixed not in followups:
            followups.append(fixed)
    if src.base_is_default and _DEFAULT_BASE_FOLLOWUP not in followups:
        followups.append(_DEFAULT_BASE_FOLLOWUP)
    for followup in followups:
        lines.append(f"- [ ] {followup}")
    lines.append("")

    # 9. Validation findings (warnings)
    lines.append("## Validation findings")
    lines.append("")
    warnings = [finding for finding in findings if finding.level == "warning"]
    if warnings:
        for finding in warnings:
            location = f" (`{finding.path}`)" if finding.path else ""
            lines.append(f"- {finding.message}{location}")
    else:
        lines.append("- (none)")
    lines.append("")

    return "\n".join(lines)


def _items_for(plan: Plan, section: str, status: Status, invert: bool = False):
    return [
        item
        for item in plan.items
        if item.section == section and ((item.status != status) if invert else (item.status == status))
    ]


def _section_counts(plan: Plan) -> tuple[list[str], dict]:
    order: list[str] = []
    counts: dict[str, list[int]] = {}
    for item in plan.items:
        if item.section not in counts:
            counts[item.section] = [0, 0, 0]
            order.append(item.section)
        if item.status == Status.UNSUPPORTED:
            counts[item.section][2] += 1
        elif not item.emits:
            # §14: non-emitting items are neither migrated nor skipped-by-user;
            # they are listed under "Settings & notes".
            continue
        elif item.id in plan.selected:
            counts[item.section][0] += 1
        else:
            counts[item.section][1] += 1
    return order, counts