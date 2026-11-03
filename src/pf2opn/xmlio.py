"""Safe XML reading and serialization (design §5, §11).

All parsing goes through :func:`parse_readonly`, which uses an lxml parser with
entities, the external DTD and network access disabled, so an XML document can
never cause a file read, a network fetch, or a billion-laughs expansion.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from . import secrets

# Parser switches mandated by design §17 (M0). ``resolve_entities=False`` keeps
# ``&foo;`` as an entity reference instead of expanding it, ``load_dtd=False``
# ignores an external DTD and ``no_network=True`` blocks any fetch.
_SAFE_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=True,
    remove_blank_text=True,
)


def parse_readonly(path: Path | str) -> etree._ElementTree:
    """Parse an XML document defensively and register any secret values found.

    Returns an :class:`lxml.etree._ElementTree`; raises
    :class:`lxml.etree.XMLSyntaxError` on malformed input.
    """
    tree = etree.parse(str(path), _SAFE_PARSER)
    _register_secrets(tree)
    return tree


def _register_secrets(tree: etree._ElementTree) -> None:
    for el in tree.iter():
        tag = el.tag
        if isinstance(tag, str) and secrets.is_secret(tag) and el.text:
            secrets.register_secret(el.text.strip())


def to_string(el: etree._Element) -> str:
    """Serialize *el* to a pretty-printed unicode string."""
    return etree.tostring(el, pretty_print=True, encoding="unicode")