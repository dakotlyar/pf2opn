# pf2opn

Migrate a pfSense `config.xml` onto an OPNsense 26.7.x box by merging selected
fragments into a base OPNsense config exported from the target appliance.

The authoritative design is [`docs/DESIGN.md`](docs/DESIGN.md). This README only
describes how to run what exists today.

## Status

Built in milestones (see §17 of the design). Implemented so far:

| Milestone | Scope | State |
|---|---|---|
| M0 | Scaffold: `pyproject.toml`, `xmlio.py`, `secrets.py`, CLI skeleton | done |
| M1 | Model, contexts, registry, deps, selection, plan, `system` translator, `inspect` | done |
| M2 | `writer`, `versions`, core `validate` (checks 1–2), `report`, `migrate` | done |

Everything after M2 (interface mapping, the remaining translators, the TUI) is
not implemented yet.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

## Usage (M2)

```sh
# List every migratable unit found in a pfSense config.
pf2opn inspect local/config-pfsense.xml --base local/base-opnsense.xml

# Same, as machine-readable JSON (never prints source XML).
pf2opn inspect local/config-pfsense.xml --base local/base-opnsense.xml --json

# Merge the default-selected items into the base and write a new OPNsense config.
pf2opn migrate local/config-pfsense.xml --base local/base-opnsense.xml \
    -o out/config.xml --select-all-ok --map-nic em0=igc0 --map-nic em1=igc1
```

The output is written mode `0600` (it holds secrets). The Markdown report next to
it never contains secret *values*; secret fields are referenced only by name.

Never commit a real config. `config-*.xml`, `*.real.xml` and `/local/` are ignored.
