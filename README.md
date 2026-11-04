# pf2opn

Neither pfSense nor OPNsense will import the other's `config.xml`, which makes moving
from one to the other a tedious hand-transcription job. This is the tool I wrote to do
that translation instead.

Point it at a pfSense `config.xml` and it walks everything in there, shows you how each
piece maps (or explains why it can't), lets you pick what actually carries over, and
writes a new OPNsense `config.xml` plus a report of what it did. It targets OPNsense
26.7.x and the pfSense CE 2.7/2.8 config format.

It only ever reads the pfSense file — that's opened read-only and never touched — and it
doesn't talk to a live firewall at all: no API, no SSH. All you get back is a config file
to look over and import yourself.

The full design, including why each section is mapped the way it is and what's
deliberately left out, is in [`docs/DESIGN.md`](docs/DESIGN.md).

## Installing

A virtualenv is the usual way:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Or build it into a single binary and skip Python entirely:

```sh
.venv/bin/pip install -e '.[build]'
bash scripts/build-binary.sh        # or: .venv/bin/pyinstaller pf2opn.spec
```

That leaves a ~23 MB file at `dist/pf2opn` you can copy anywhere — it doesn't care about
the current directory or the checkout. It's a Linux build; there's no macOS or Windows
one.

## Running it

With no arguments at all it opens the TUI:

```sh
pf2opn                 # or ./dist/pf2opn if you built the binary
```

If you'd rather pass things in up front, or use it non-interactively, there are three
subcommands:

```
pf2opn inspect SOURCE [--base BASE] [--json]
pf2opn migrate SOURCE -o OUT [--base BASE] [--report REPORT]
               (--select-all-ok | --selection FILE) [--map-nic PF=OPN ...]
               [--keep-base-rules] [--force]
pf2opn tui     [SOURCE] [--base BASE] [-o OUT] [--report REPORT]
               [--search-path DIR ...] [--selection FILE] [--force]
```

A quick word on the flags, since there are a few:

- `SOURCE` — the pfSense config to read, opened read-only.
- `--base` — the OPNsense config to merge into. Omit it and the built-in one is used.
- `-o` / `--output` — where the new config goes (written `0600`).
- `--report` — where the report goes; defaults to the output path with `.report.md` in
  place of `.xml`.
- `--map-nic PF=OPN` — map a pfSense NIC to an OPNsense one; repeatable.
- `--keep-base-rules` — keep the base's two default LAN allow rules instead of replacing
  them when you're carrying firewall rules over.
- `--search-path DIR` — add a directory to the TUI's config search; repeatable.
- `--force` — overwrite an existing output.

`inspect` just lists what it found — one line per item, grouped by section, with a status
and a warning count. `--json` gives the same thing as a list of
`{id, section, label, status, reason, warnings, deps}`. Neither form prints any of the
source XML.

`migrate` is the headless path and needs one of `--select-all-ok` (take everything that's
selected by default) or `--selection FILE` (a selection you saved from the TUI). The
output is written `0600`, and it refuses to overwrite an existing file unless you pass
`--force`, or to write onto your source or base config by accident.

`tui` is the interactive one, described below. Everything after `SOURCE` is optional
there: run `pf2opn tui` on its own and the first screen asks for the rest.

Exit codes are simple: `0` wrote something, `1` found validation errors and wrote
nothing, `2` is a usage or input problem (missing/unmapped NIC, a bad selection id, XML
that won't parse, refusing to overwrite, that sort of thing).

### The TUI

```sh
pf2opn tui            # or just `pf2opn`
```

The screens go left to right, `Esc` or `b` steps back, and `Ctrl+Q` quits from anywhere.
(`q` also quits from most screens, but on the Load screen it just types a `q` into the
search box — use `Ctrl+Q` there.)

**Load.** A search box for the pfSense config and one for the OPNsense base, plus a
plain path for the output. The search boxes look in the current directory, `~/Downloads`,
`~/Desktop`, `~`, and the folder of anything you've already told it about (add more with
`--search-path`); they scan once and filter as you type. Type part of a name, arrow up
and down the list, `Enter` to pick, `Esc` to clear, `r` to rescan. A full path you paste
in works too — it doesn't have to come from the list. Leaving the base blank is fine.

Each field is checked inline, and once the source and output are valid (and the base is
either blank or valid) `Enter` moves on. You get a short summary of what it's about to
work on before the next screen.

**Interface map.** A table of the interfaces it found with the device each one uses.
VLAN and WireGuard rows show the device they'll get and can't be edited; the physical
ones you assign yourself — `space` to include or not, `Enter` to pick a NIC, `c` to carry
on once everything that's included has a device.

**Select.** The main event: a tree of everything it found on the left, and a detail pane
on the right showing the source XML, a preview of what it'll become, and any notes.
Each row is prefixed with a checkbox — `[x]` for selected, `[ ]` for not — and anything
worth flagging gets a marker at the end: nothing for a clean item, `⚠` for partial, `✖`
for untranslatable. Section headers use the same idea with a third state, so `[x]` is all
of it, `[-]` is some of it, `[ ]` is none.

Selection is clever about dependencies: pick a firewall rule and it quietly pulls in the
aliases and interfaces that rule needs. Deselect something other selections rely on and
it asks first.

The keys that matter:

- `space` toggles the highlighted item or section
- `a` / `n` select all / none in the current section, `A` selects all defaults everywhere
- `f` cycles the view: everything → selected only → problems only
- `/` searches labels, `e` / `E` expand or collapse everything
- `i` goes back to the interface map, `r` moves on to review

**Review.** It builds the output and runs validation in the background, then shows you
the summary, any errors, and any warnings. `w` writes the files (it's disabled while
there are errors), `s` saves the selection so you can replay it later with
`migrate --selection`, `b` goes back.

**Done.** The paths it wrote, the counts, and which plugins the target will need.

### Saving a selection

If you want a repeatable run — say, to re-do the migration after changing something — the
TUI can save the selection as JSON:

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

Feed it back with `migrate --selection`. If the source file has changed since you saved
it, pf2opn warns but carries on; an id it doesn't recognise is an error.

## About the base config

The output isn't built from nothing. It's a copy of a base OPNsense `config.xml` with
your chosen pfSense items merged into it, because that's the only way to keep things like
the real NIC names, the root password, and the model `version` attributes, which OPNsense
needs to not re-run its own migrations on import.

The best base is a fresh export from the target box — install OPNsense, assign WAN/LAN at
the console, then grab the config from **System ▸ Configuration ▸ Backups**. That way the
NIC names and everything else line up with the hardware you're actually moving to.

You don't *have* to supply one, though. If you leave it out, pf2opn uses a built-in
factory base (the stock OPNsense 26.7 config, wizard prompt stripped, with `igc0`/`igc1`
as placeholder NICs). It works, and the report will tell you loud and clear that you used
it — including that those `igc0`/`igc1` names are placeholders you need to confirm, and
that the root password is still OPNsense's default `opnsense`.

One thing that stays consistent either way: pfSense's interface keys (`wan`, `lan`,
`optN`) are kept as-is, and only the underlying *devices* get remapped (`mlxen1` →
`igc1`, `mlxen1.10` → `vlan03`, `tun_wg0` → `wg0`). OPNsense uses the same key scheme, so
re-keying would mean rewriting every rule, NAT entry, DHCP range, gateway and DNS
override that references them, for no real benefit.

## Security

The pfSense config holds password hashes, private keys and pre-shared keys, so a few
things are deliberate:

- The input is opened read-only. It is never written to. Nor is the base, if you pass one.
- Secrets are kept out of anything you see or that gets logged. The TUI's source and
  preview panes, the Markdown report, and log records never contain the value of a
  password/hash/private-key/PSK/community-string/cert field — those are referred to by
  name only. A redacting filter is installed before any file is parsed.
- The output config obviously does contain secrets, so it's written atomically with mode
  `0600`.
- The report is deliberately *not* `0600` — it's plain mode (`0644` by default) so you or
  a diff tool can read it without the config's permissions. It contains no XML and no
  secret values, just labels, counts and notes.

## Please test the output before you trust it

Validation here is structural and referential: it checks the XML is well-formed, the
UUIDs are unique, the interfaces/VLANs/WireGuard links resolve, and that rules, NAT, DHCP,
aliases and DNS references all point at things that exist. It does **not** re-implement
OPNsense's own PHP model validation, so "pf2opn says it's fine" is not the same as
"OPNsense will accept it".

So before this goes anywhere near the real gateway: import the output into a throwaway
OPNsense 26.7 VM, restore the whole config (**System ▸ Configuration ▸ Backups ▸
Restore**), reboot, and make sure the Firewall, NAT, Dnsmasq DHCP, Unbound and WireGuard
pages all load without complaints. Check the interface assignments on the console too,
before you plug in WAN. I found two real bugs this way that the tests never would have.

## Working on it

```sh
.venv/bin/python -m pytest -q
```

The per-translator tests are golden files under `tests/golden/`; `pytest --update-golden`
rewrites them, so always read that diff. Fixtures are all synthetic — **don't commit a
real config** (`config-*.xml`, `*.real.xml`, `*.report.md` and `/local/` are gitignored
for that reason).

The one config that is committed is `src/pf2opn/data/base_default.xml`. That's not anyone's
export — it's OPNsense's own public factory default, with the wizard prompt stripped and
the placeholder NICs tidied up, shipped so the tool has a base to fall back on.
