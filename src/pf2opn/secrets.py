"""Secret-handling policy (design §11).

The labels, reasons, warnings and follow-ups produced by translators must never
contain the *value* of a secret field; secret fields are referenced only by name
("private key", "PSK"). The output config naturally holds secrets and is written
mode 0600. Anything displayed (TUI, report, logs) goes through this module.
"""

from __future__ import annotations

import copy
import logging
from lxml import etree

# Element local-names whose text is a secret (or, for ``crt``, merely huge and
# pointless to display). Kept verbatim from design §11.
SECRET_TAGS = frozenset(
    {
        "bcrypt-hash",
        "sha512-hash",
        "md5-hash",
        "nt-hash",
        "password",
        "passwordenc",
        "prv",
        "privatekey",
        "privkey",
        "presharedkey",
        "psk",
        "pre-shared-key",
        "ipsecpsk",
        "authorizedkeys",
        "otp_seed",
        "apikeys",
        "key",
        "secret",
        "shared_key",
        "tls",
        "auth_pass",
        "rocommunity",
        "ddnsdomainkey",
        "ddns_domain_key_secret",
        "custom_options",
        "sshkeyfile",
        "data",
        "crt",
    }
)

# Known secret *values* discovered while parsing, masked out of log records.
_known_values: set[str] = set()

_REDACTED = "«redacted»"


def is_secret(tag: str) -> bool:
    """True if an element with this local-name holds a secret value."""
    return etree.QName(tag).localname in SECRET_TAGS


def register_secret(value: str) -> None:
    """Register a secret value so :class:`RedactingFilter` can mask it in logs."""
    if value and len(value) > 8:
        _known_values.add(value)


def known_secret_values() -> frozenset[str]:
    """The secrets discovered so far (used by tests)."""
    return frozenset(_known_values)


def redact_element(el: etree._Element) -> etree._Element:
    """Deep-copy *el*, replacing the text of every secret element with a marker.

    The copy is independent, so it can be shown/serialized without leaking.
    """
    clone = copy.deepcopy(el)
    for sub in clone.iter():
        if not isinstance(sub.tag, str):  # comments / PIs
            continue
        if is_secret(sub.tag) and sub.text:
            sub.text = f"«redacted {len(sub.text)} chars»"
    return clone


def redacted_xml(el: etree._Element) -> str:
    """Pretty-printed string of *el* with every secret value redacted."""
    return etree.tostring(redact_element(el), pretty_print=True, encoding="unicode")


class RedactingFilter(logging.Filter):
    """Logging filter that masks registered secret values in records."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        if not _known_values:
            return True
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive, never expected
            return True
        masked = message
        for value in _known_values:
            if value in masked:
                masked = masked.replace(value, _REDACTED)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def install_logging_filter(logger: logging.Logger | None = None) -> None:
    """Attach a :class:`RedactingFilter` to *logger* (the root logger by default)."""
    target = logger if logger is not None else logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in target.filters):
        target.addFilter(RedactingFilter())
