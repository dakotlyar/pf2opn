"""Shared path checks (design §9).

One source of truth for "does this look like a pfSense/OPNsense config" and
"is this output path safe to write". ``cli.py`` runs these on flag values and
the TUI Load screen runs the same functions on values the user typed, so the
two cannot diverge.
"""

from __future__ import annotations

import os
from pathlib import Path

from lxml import etree

from . import xmlio


def parse_pf_root(path: Path | str) -> etree._Element:
    """Parse *path* read-only and return its root, requiring ``<pfsense>``.

    Raises the same errors ``SourceContext.load`` has always raised: an
    :class:`OSError`/:class:`~lxml.etree.XMLSyntaxError` from parsing, or a
    :class:`ValueError` when the root element is not ``<pfsense>``.
    """
    path = Path(path)
    root = xmlio.parse_readonly(path).getroot()
    if root.tag != "pfsense":
        raise ValueError(f"{path}: root element is <{root.tag}>, expected <pfsense>")
    return root


def parse_opnsense_root(path: Path | str) -> etree._Element:
    """Parse *path* read-only and return its root, requiring ``<opnsense>``."""
    path = Path(path)
    root = xmlio.parse_readonly(path).getroot()
    if root.tag != "opnsense":
        raise ValueError(f"{path}: root element is <{root.tag}>, expected <opnsense>")
    return root


def file_error(path: Path | str) -> str | None:
    """The cheap filesystem check: exists, is a file, is readable.

    Returns a message, or ``None`` when the path passes. This is the part of
    source/base validation that is safe to run on every keystroke; the parse
    and root check is :func:`source_error`/:func:`base_error`.
    """
    path = Path(path)
    if not path.exists():
        return f"no such file: {path}"
    if path.is_dir():
        return f"is a directory: {path}"
    if not os.access(path, os.R_OK):
        return f"permission denied: {path}"
    return None


def _config_error(path: Path | str, parse) -> str | None:
    if not path:
        return "required"
    error = file_error(path)
    if error is not None:
        return error
    try:
        parse(path)
    except (OSError, ValueError, etree.XMLSyntaxError) as exc:
        return str(exc)
    return None


def source_error(path: Path | str) -> str | None:
    """Return a message when *path* is not a readable ``<pfsense>`` config, else ``None``."""
    return _config_error(path, parse_pf_root)


def base_error(path: Path | str) -> str | None:
    """Return a message when *path* is not a readable ``<opnsense>`` config, else ``None``."""
    return _config_error(path, parse_opnsense_root)


def same_file(a: Path | str, b: Path | str) -> bool:
    """True when *a* and *b* resolve to the same filesystem entry."""
    pa, pb = Path(a), Path(b)
    try:
        if pa.exists() and pb.exists() and os.path.samefile(pa, pb):
            return True
    except OSError:  # pragma: no cover - platform dependent
        pass
    return pa.resolve() == pb.resolve()


def check_output(
    out: Path | str,
    source: Path | str | None = None,
    base: Path | str | None = None,
    force: bool = False,
) -> str | None:
    """Validate an output path (design §9).

    Returns a message when writing *out* must be refused — it already exists
    and *force* is not set, or it is the same file as *source*/*base* — else
    ``None``. Omitted (``None``) source/base paths are skipped.
    """
    out_path = Path(out)
    if not force and out_path.exists():
        return f"{out_path} exists; pass --force to overwrite"
    for other in (source, base):
        if other is None:
            continue
        if same_file(out_path, other):
            return f"refusing to write the output onto {other}"
    return None


__all__ = [
    "parse_pf_root",
    "parse_opnsense_root",
    "file_error",
    "source_error",
    "base_error",
    "same_file",
    "check_output",
]
