# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone ``pf2opn`` executable.

Linux, one-file. Build with ``pyinstaller pf2opn.spec`` (or
``bash scripts/build-binary.sh``); the result is ``dist/pf2opn``.

The frozen app reads its package data with :func:`importlib.resources`, so the
bundle must carry not only pf2opn's own base config and Textual stylesheet but
also Textual's and Rich's assets, or the TUI fails to start under the bootloader.
"""

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = []
binaries = []
hiddenimports = []

# pf2opn's own package data: src/pf2opn/data/*.xml (the bundled factory base)
# and src/pf2opn/tui/app.tcss (the Textual stylesheet).
datas += collect_data_files("pf2opn")

# Textual's and Rich's own assets (stylesheets, widget templates, …). Without
# these, importlib.resources lookups inside the frozen app come up empty.
for dist in ("textual", "rich"):
    dist_datas, dist_binaries, dist_hiddenimports = collect_all(dist)
    datas += dist_datas
    binaries += dist_binaries
    hiddenimports += dist_hiddenimports

a = Analysis(
    ["scripts/pf2opn_entry.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Dev-only test tooling must never be dragged into the binary.
    excludes=["pytest", "pytest_asyncio", "_pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pf2opn",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
