"""Entry point for the PyInstaller-built standalone ``pf2opn`` executable.

PyInstaller analyses this file as the top-level ``__main__`` module; a normal
absolute import keeps the package (and its relative imports) intact, unlike
freezing ``src/pf2opn/__main__.py`` directly.
"""

from pf2opn.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
