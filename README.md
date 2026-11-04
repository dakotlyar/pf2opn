# pf2opn

pf2opn translates a pfSense `config.xml` into an OPNsense one. It reads the pfSense
configuration, presents every migratable item with a status, lets you select what should
carry over, and merges the selection into a base OPNsense config — writing a new
`config.xml` plus a Markdown report of what it did.

The need for it is simple: neither pfSense nor OPNsense imports the other's full
configuration, so the move is normally done by hand. pf2opn targets OPNsense 26.7.x and
the pfSense CE 2.7/2.8 config format.

The pfSense file is opened read-only and is never modified. The tool does not connect to a
live firewall — there is no API or SSH access — so it only ever produces a config file to
review and import.

The full design, including how each section is mapped and what is deliberately left out,
is in [`docs/DESIGN.md`](docs/DESIGN.md).

## Installing

A virtualenv is the usual approach:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Or build a standalone binary, which does not need Python at runtime:

```sh
.venv/bin/pip install -e '.[build]'
bash scripts/build-binary.sh        # or: .venv/bin/pyinstaller pf2opn.spec
```

This produces a single ~23 MB file at `dist/pf2opn` that can be copied anywhere; it does
not depend on the current directory or the checkout. It is a Linux build; there is no
macOS or Windows target.

## Running it

With no arguments, the interactive TUI opens:

```sh
pf2opn                 # or ./dist/pf2opn if you built the binary
```

There are also three subcommands, for passing options up front or for non-interactive use:

```
pf2opn inspect SOURCE [--base BASE] [--json]
pf2opn migrate SOURCE -o OUT [--base BASE] [--report REPORT]
               (--select-all-ok | --selection FILE) [--map-nic PF=OPN ...]
               [--keep-base-rules] [--force]
pf2opn tui     [SOURCE] [--base BASE] [-o OUT] [--report REPORT]
               [--search-path DIR ...] [--selection FILE] [--force]
```

The flags:

- `SOURCE` — the pfSense config to read, opened read-only.
- `--base` — the OPNsense config to merge into. Omit it and the built-in base is used.
- `-o` / `--output` — where the new config is written (mode `0600`).
- `--report` — where the report is written; defaults to the output path with `.report.md`
  in place of `.xml`.
- `--map-nic PF=OPN` — map a pfSense NIC to an OPNsense one; repeatable.
- `--keep-base-rules` — keep the base's two default LAN allow rules instead of replacing
  them when firewall rules are carried over.
- `--search-path DIR` — add a directory to the TUI's config search; repeatable.
- `--force` — overwrite an existing output.

`inspect` lists what was found, one line per item, grouped by section, with a status and a
warning count. `--json` gives the same data as a list of
`{id, section, label, status, reason, warnings, deps}`. Neither form prints any source XML.

`migrate` is the non-interactive path and requires exactly one selection source:
`--select-all-ok` (everything selected by default) or `--selection FILE` (a selection saved
from the TUI). The output is written `0600`. It refuses to overwrite an existing file
unless `--force` is given, and refuses to write onto the source or base config.

`tui` is the interactive interface, described below. Everything after `SOURCE` is optional
there: running `pf2opn tui` on its own lets the first screen collect the rest.

Exit codes: `0` wrote output, `1` found validation errors and wrote nothing, `2` is a usage
or input error (a missing or unmapped NIC, a bad selection id, XML that does not parse,
refusing to overwrite, and so on).

### The TUI

```sh
pf2opn tui            # or just `pf2opn`
```

The screens run left to right; `Esc` or `b` steps back, and `Ctrl+Q` quits from anywhere.
(`q` quits from most screens, but on the Load screen it is treated as input to the search
box — use `Ctrl+Q` there.)

**Load.** A search box for the pfSense config and one for the OPNsense base, plus a plain
path field for the output. The search boxes look in the current directory, `~/Downloads`,
`~/Desktop`, `~`, and the directory of any path already supplied; add more with
`--search-path`. They scan once and filter as you type: type part of a name, use the arrow
keys, `Enter` to pick, `Esc` to clear, `r` to rescan. A full path can also be typed or
pasted directly. Leaving the base blank is valid.

Each field is validated inline, and once the source and output are valid — and the base is
either blank or valid — `Enter` continues. A short summary of the source and base is shown
before the next screen.

**Interface map.** A table of the interfaces found and the device each uses. VLAN and
WireGuard rows show the device they will get and cannot be edited; the physical rows are
assigned here. `space` toggles inclusion, `Enter` picks a NIC, and `c` continues once every
included interface has a device.

**Select.** A tree of everything found on the left and a detail pane on the right, showing
the source XML, a preview of the translated output, and any notes. Each row is prefixed
with a checkbox: `[x]` selected, `[ ]` not selected. Items that need attention carry a
trailing marker — none for a clean item, `⚠` for partial, `✖` for untranslatable. Section
headers use the same scheme with a third state: `[x]` for all, `[-]` for some, `[ ]` for
none.

Selection handles dependencies: selecting a firewall rule also selects the aliases and
interfaces it needs. Deselecting something that selected items depend on prompts for
confirmation first.

Keys:

- `space` toggles the highlighted item or section
- `a` / `n` select all / none in the current section; `A` selects all defaults
- `f` cycles the view: everything → selected → problems
- `/` searches labels; `e` / `E` expand or collapse all
- `i` returns to the interface map; `r` continues to review

**Review.** The output is built and validated in the background, then the summary, errors
and warnings are shown. `w` writes the files (disabled while there are errors), `s` saves
the selection for a later `migrate --selection`, and `b` goes back.

**Done.** The paths written, the counts, and any plugins the target will need.

### Saving a selection

To make a run repeatable — for example, to redo a migration after a change — the TUI can
save the selection as JSON:

```json
{
  "pf2opn_selection": 1,
  "source_sha256": "hex…",
  "nic_map": {"mlxen0": "igc0", "mlxen1": "igc1"},
  "interfaces": {"opt7": {"include": true}, "opt10": {"include": false}},
  "selected": ["alias:RFC1918", "rule:1714398622"],
  "deselected": [],
  "options": {"keep_base_rules": false}
}
```

Replay it with `migrate --selection`. If the source file has changed since it was saved,
pf2opn warns but proceeds; an unrecognised id is an error.

## About the base config

The output is not built from nothing. It is a copy of a base OPNsense `config.xml` with the
selected pfSense items merged in, which is the only way to preserve details such as the
real NIC names, the root password, and the model `version` attributes. OPNsense uses those
version attributes to decide whether to re-run its own migrations on import.

The best base is a fresh export from the target appliance: install OPNsense, assign WAN and
LAN at the console, then download the config from **System ▸ Configuration ▸ Backups**.
That way the NIC names and other details match the hardware being moved to.

Supplying a base is not required, however. If none is given, pf2opn uses a built-in factory
base — the stock OPNsense 26.7 configuration with the wizard prompt removed and `igc0` /
`igc1` as placeholder NICs. The report states explicitly that the built-in base was used,
that those NIC names are placeholders to confirm, and that the root password is still
OPNsense's default `opnsense`.

Either way, pfSense's interface keys (`wan`, `lan`, `optN`) are preserved and only the
underlying *devices* are remapped (`mlxen1` → `igc1`, `mlxen1.10` → `vlan03`, `tun_wg0` →
`wg0`). OPNsense uses the same key scheme, so re-keying would mean rewriting every rule,
NAT entry, DHCP range, gateway and DNS override that refers to them, with no benefit.

## Security

The pfSense config contains password hashes, private keys and pre-shared keys, so several
choices are deliberate:

- The input is opened read-only and is never written to. The same applies to the base, if
  one is supplied.
- Secrets are kept out of anything displayed or logged. The TUI source and preview panes,
  the Markdown report, and log records never contain the value of a password, hash, private
  key, PSK, community string or certificate field; such fields are referred to by name
  only. A redacting filter is installed before any file is parsed.
- The output config does contain secrets, so it is written atomically with mode `0600`.
- The report is written with the normal file mode (`0644` by default), not `0600`, so that
  it can be read without the config's permissions. It contains no XML and no secret values —
  only labels, counts and notes.

## Test the output before relying on it

Validation is structural and referential: it checks that the XML is well-formed, UUIDs are
unique, the interface, VLAN and WireGuard links resolve, and that rules, NAT, DHCP, aliases
and DNS references point at things that exist. It does not re-implement OPNsense's own PHP
model validation, so a clean pf2opn run does not guarantee OPNsense will accept the config.

Before applying the output to the real gateway, import it into a throwaway OPNsense 26.7
VM: restore the whole config (**System ▸ Configuration ▸ Backups ▸ Restore**), reboot, and
confirm the Firewall, NAT, Dnsmasq DHCP, Unbound and WireGuard pages load without
validation errors. Check the interface assignments on the console as well, before
connecting WAN. This step has caught two real defects that the automated tests did not.

## Working on it

```sh
.venv/bin/python -m pytest -q
```

The per-translator tests are golden files under `tests/golden/`; `pytest --update-golden`
rewrites them, so review that diff. All fixtures are synthetic — do not commit a real
config (`config-*.xml`, `*.real.xml`, `*.report.md` and `/local/` are gitignored for this
reason).

The one committed config is `src/pf2opn/data/base_default.xml`: the tool's built-in base. It
is not a private export but OPNsense's own public factory default, with the wizard prompt
removed and the placeholder NICs tidied up, shipped as package data.
