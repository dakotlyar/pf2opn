"""The Load screen: collect and validate the three paths (design §15)."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Input, Static

from ... import paths
from ...context import SourceContext
from ...model import Status
from ...registry import all_translators
from ...versions import MODEL_VERSIONS
from . import AppScreen
from .ifmap import IfaceMapScreen

_FIELDS = ("source", "base", "output")


def _section_counts(plan) -> list[tuple[str, int, int, int]]:
    counts: list[tuple[str, int, int, int]] = []
    for translator in all_translators():
        items = [item for item in plan.items if item.section == translator.section]
        if not items:
            continue
        ok = sum(1 for item in items if item.status == Status.OK)
        partial = sum(1 for item in items if item.status == Status.PARTIAL)
        unsupported = sum(1 for item in items if item.status == Status.UNSUPPORTED)
        counts.append((translator.title, ok, partial, unsupported))
    return counts


def base_version_warnings(src) -> list[str]:
    """The §10.3 base-model version mismatches worth flagging up front."""
    warnings: list[str] = []
    for path, expected in MODEL_VERSIONS.items():
        matches = src.base.xpath(path)
        if not matches:
            continue
        actual = matches[0].get("version")
        if actual is not None and actual != expected:
            warnings.append(
                f"base model {path} is version {actual}, pf2opn was written for {expected}"
            )
    return warnings


class LoadScreen(AppScreen):
    """Collect the three paths, validate them, then continue to the interface map."""

    BINDINGS = [("enter", "continue_", "Continue")]

    #: Seconds to wait after the last keystroke before re-parsing (§15).
    PARSE_DEBOUNCE = 0.4

    def compose(self) -> ComposeResult:
        source, base, output = self._initial_values()
        with VerticalScroll(id="load-body"):
            with Vertical(id="load-fields"):
                yield Static("pfSense source", classes="load-label")
                yield Input(value=source, placeholder="config.xml to read (read-only)", id="load-source")
                yield Static("", id="load-source-error", classes="load-error")
                yield Static("OPNsense base", classes="load-label")
                yield Input(
                    value=base,
                    placeholder="base config.xml exported from the target",
                    id="load-base",
                )
                yield Static("", id="load-base-error", classes="load-error")
                yield Static("Output", classes="load-label")
                yield Input(value=output, placeholder="config.xml to write", id="load-output")
                yield Static("", id="load-output-error", classes="load-error")
            yield Static(id="load-summary")
        yield Footer()

    # ------------------------------------------------------------------ #
    # Initial state
    # ------------------------------------------------------------------ #

    def _initial_values(self) -> tuple[str, str, str]:
        app = self.pfapp
        if app.src is not None:
            source, base = str(app.src.source_path), str(app.src.base_path)
        else:
            source = getattr(app.args, "source", None) or ""
            base = getattr(app.args, "base", None) or ""
        if app.output_path is not None:
            output = str(app.output_path)
        else:
            output = str(getattr(app.args, "output", None) or "")
        return source, base, output

    def on_mount(self) -> None:
        self._errors: dict[str, str | None] = {field: None for field in _FIELDS}
        self._valid: dict[str, bool] = {field: False for field in _FIELDS}
        self._parse_timer = None
        self._validate()
        self.query_one("#load-source", Input).focus()

    # ------------------------------------------------------------------ #
    # Validation (§15): cheap checks on every change, parse debounced
    # ------------------------------------------------------------------ #

    def _text(self, field: str) -> str:
        return self.query_one(f"#load-{field}", Input).value.strip()

    def _output_error(self, output: str, source: str, base: str) -> str | None:
        if not output:
            return "required"
        return paths.check_output(output, source or None, base or None, self.pfapp.force)

    def _validate(self) -> None:
        source, base, output = (self._text(field) for field in _FIELDS)
        src = self.pfapp.src

        s_err = "required" if not source else paths.file_error(source)
        b_err = "required" if not base else paths.file_error(base)
        o_err = self._output_error(output, source, base)

        source_matches = src is not None and Path(source) == Path(src.source_path)
        base_matches = src is not None and Path(base) == Path(src.base_path)
        if source_matches:
            s_err = None
        if base_matches:
            b_err = None

        self._errors = {"source": s_err, "base": b_err, "output": o_err}
        self._valid = {
            "source": s_err is None and source_matches,
            "base": b_err is None and base_matches,
            "output": o_err is None,
        }
        for field, message in self._errors.items():
            self.query_one(f"#load-{field}-error", Static).update(message or "")

        # The value collected here is what the run uses (§9): keep the app's
        # output path (and hence the report default) in step with the field.
        if src is not None and output and o_err is None:
            self.pfapp.set_output(output)

        if source_matches and base_matches:
            self._cancel_parse()
            self._update_summary()
            return

        self._query_summary().update("")
        self._cancel_parse()
        if s_err is not None or b_err is not None:
            return

        # Cheap checks pass but these exact paths have not parsed yet.
        self._parse_timer = self.set_timer(self.PARSE_DEBOUNCE, self._debounced_parse)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._validate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter is handled by the Input's own binding; route it to continue.
        self.action_continue_()

    def _cancel_parse(self) -> None:
        if self._parse_timer is not None:
            self._parse_timer.stop()
            self._parse_timer = None

    def _debounced_parse(self) -> None:
        self._parse_timer = None
        self._load_context(self._text("source"), self._text("base"))

    @work(thread=True, exclusive=True, group="load")
    def _load_context(self, source: str, base: str) -> None:
        s_err = paths.source_error(source)
        b_err = paths.base_error(base)
        if s_err is not None or b_err is not None:
            self.app.call_from_thread(self._parse_failed, source, base, s_err, b_err)
            return
        src = SourceContext.load(source, base)
        ifmap, plan = self.pfapp.make_plan(src)
        self.app.call_from_thread(self._context_loaded, source, base, src, ifmap, plan)

    def _parse_failed(self, source: str, base: str, s_err: str | None, b_err: str | None) -> None:
        if not self._same_text(source, base):
            return
        self._errors["source"] = s_err
        self._errors["base"] = b_err
        self._valid["source"] = False
        self._valid["base"] = False
        self.query_one("#load-source-error", Static).update(s_err or "")
        self.query_one("#load-base-error", Static).update(b_err or "")
        self._query_summary().update("")

    def _context_loaded(self, source: str, base: str, src, ifmap, plan) -> None:
        if not self._same_text(source, base):
            return
        self.pfapp.apply_loaded(src, ifmap, plan, self._text("output"))
        self._validate()

    def _same_text(self, source: str, base: str) -> bool:
        return Path(self._text("source")) == Path(source) and Path(self._text("base")) == Path(base)

    # ------------------------------------------------------------------ #
    # Continue / summary
    # ------------------------------------------------------------------ #

    def action_continue_(self) -> None:
        if self.app.screen is not self:
            return
        if self.pfapp.src is None or not all(self._valid.values()):
            for field in _FIELDS:
                if not self._valid.get(field):
                    self.query_one(f"#load-{field}", Input).focus()
                    break
            return
        self.app.push_screen(IfaceMapScreen())

    def _query_summary(self) -> Static:
        return self.query_one("#load-summary", Static)

    def _update_summary(self) -> None:
        self._query_summary().update(self._summary())

    def _summary(self) -> str:
        src = self.pfapp.src
        plan = self.pfapp.plan
        lines = [
            "[b]pf2opn[/b] — review the migration before writing anything.",
            "",
            f"Source: {src.source_path}",
            f"  pfSense version: {src.pf_version or '(unknown)'}",
            f"  hostname: {src.pf_hostname or '(unknown)'}",
            "",
            f"Base: {src.base_path}",
            f"  OPNsense hostname: {src.base_hostname or '(unknown)'}",
            "",
            "Sections found:",
        ]
        for title, ok, partial, unsupported in _section_counts(plan):
            lines.append(
                f"  {title}: {ok + partial + unsupported} item(s) "
                f"({ok} ok, {partial} partial, {unsupported} unsupported)"
            )
        problems = base_version_warnings(src)
        if problems:
            lines.append("")
            lines.append("Base version warnings:")
            lines.extend(f"  - {warning}" for warning in problems)
        if self.pfapp.selection_sha_mismatch:
            lines.append("")
            lines.append("Selection warning:")
            lines.append(
                "  - the loaded selection's source_sha256 does not match the source file"
            )
        lines.append("")
        lines.append("Press [b]enter[/b] to continue to the interface map.")
        return "\n".join(lines)
