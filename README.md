# pf2opn

`pf2opn` reads a pfSense `config.xml` (read-only), shows every migratable unit as
an **item** with a status, and lets you choose what carries over. It then merges
the selected items into a **base OPNsense config** — one exported from the target
box, or pf2opn's built-in factory base — and writes a new OPNsense `config.xml`
plus a Markdown migration report.

It targets **OPNsense 26.7.x** and the pfSense CE 2.7.x/2.8.x config format. It
does **not** talk to a live firewall: there is no API or SSH access. Validation is
structural and referential only — it does not re-implement OPNsense's PHP model
validation, so the output must be import-tested (see below).

The authoritative design is [`docs/DESIGN.md`](docs/DESIGN.md). This README only
describes how to run what exists today.

## Choosing a base (`--base` is optional)

`--base` is a fresh OPNsense `config.xml` exported from the **target appliance**
(WAN/LAN assigned at the console first). The output is a deep copy of that base
with the selected pfSense items merged in — it is never built from scratch.

**It is optional.** When `--base` is omitted — for `inspect`, `migrate` and `tui`
alike — pf2opn merges into a **built-in factory base** shipped as package data
(`src/pf2opn/data/base_default.xml`): the genuine OPNsense 26.7 factory config,
with the initial-wizard trigger stripped, `wan.if=igc0` / `lan.if=igc1`, and the
26.7.8 model `version` attributes baked in. The output is produced exactly the
same way; the report header, the validation findings and the follow-up checklist
state that the built-in base was used, and the checklist tells you to confirm the
interface devices and change the factory root password (the documented default is
`opnsense`).

A real export is still the **recommended** path, because it carries the target's
real NIC names, root password, model `version` attributes, and firmware/plugin
state. The built-in base's `igc0`/`igc1` are placeholders chosen as the common
current OPNsense driver — they are **not** the target's NICs, and must be
confirmed (on the console, or on the interface-map screen).

A missing or wrong model `version` attribute makes OPNsense re-run its own model
migrations on import (for example `MFP1_0_3` deletes `nat/npt`), which fails
silently; the built-in base ships the correct 26.7.8 attributes so a default-base
output does not re-migrate either.

pfSense logical interface keys (`wan`, `lan`, `optN`) are kept as-is; only the
*devices* are remapped (`mlxen1` → `igc1`, `mlxen1.10` → `vlan03`, `tun_wg0` →
`wg0`), because OPNsense uses the same key scheme and re-keying would rewrite every
rule, NAT, DHCP, gateway and resolver reference for no benefit.

## Installing

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

This installs the `pf2opn` console script (and the `dev` extra: `pytest`,
`pytest-asyncio`).

## Standalone binary

`pf2opn` can be frozen into a single self-contained **Linux** executable, so it
runs without a Python venv. The build uses PyInstaller via the `build` extra:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[build]'
.venv/bin/pyinstaller pf2opn.spec        # or: bash scripts/build-binary.sh
```

This writes `./dist/pf2opn` — a single file of roughly **23 MB**. Copy it
anywhere; it does not depend on the current directory, the venv, or the checkout:

```sh
./dist/pf2opn                       # opens the TUI (same as `pf2opn tui`)
./dist/pf2opn migrate SOURCE -o OUT --select-all-ok --map-nic PF=OPN
```

The bundled factory base (`base_default.xml`) and the TUI stylesheet
(`app.tcss`), plus Textual's and Rich's own assets, are compiled into the
binary, so `--base` can still be omitted. It is a Linux build only — there is
no macOS or Windows target.

## CLI usage

```
pf2opn inspect SOURCE [--base BASE] [--json]
pf2opn migrate SOURCE [-o OUT] [--base BASE] [--report REPORT]
               (--select-all-ok | --selection FILE) [--map-nic PF=OPN ...]
               [--keep-base-rules] [--force]
pf2opn tui     [SOURCE] [--base BASE] [-o OUT] [--report REPORT] [--selection FILE]
               [--search-path DIR ...] [--force]
```

Running `pf2opn` with **no subcommand launches the TUI** (identical to
`pf2opn tui`); `--help`/`-h` still prints the help text.

`--base` is **optional** for all three subcommands (see above). For `inspect` and
`migrate`, `SOURCE` (and `-o` for `migrate`) are still required. For `tui`,
`SOURCE`, `--base` and `-o` are all optional: run `pf2opn tui` on its own and the
Load screen collects them (any you pass pre-fill the fields).

* `SOURCE` — the pfSense `config.xml` to read. It is opened read-only and is
  **never** modified.
* `--base BASE` — the base OPNsense `config.xml`; when omitted, pf2opn's built-in
  factory base is used (see above).
* `-o OUT` / `--output OUT` — the new OPNsense `config.xml` to write (mode `0600`).
* `--report REPORT` — report path; defaults to `OUT` with the suffix replaced by
  `.report.md`.
* `--search-path DIR` — add a directory to the TUI Load screen's config search
  roots; repeatable. The roots always include the current directory,
  `~/Downloads`, `~/Desktop`, `~`, and the parent of any path already known.
* `--map-nic PF=OPN` — map a pfSense NIC to an OPNsense NIC; repeatable.
* `--keep-base-rules` — keep the base's two default LAN allow rules instead of
  replacing them when filter rules are selected.
* `--force` — overwrite an existing `OUT`.

`inspect` prints one line per item, grouped by section:

```
STATUS      id                            label  [warnings count]
```

`inspect --json` prints a JSON list of
`{id, section, label, status, reason, warnings, deps}`. **Neither form ever prints
source XML.**

`migrate` is the non-interactive path. It needs exactly one selection source:

* `--select-all-ok` — all default-selected items (OK and PARTIAL, minus items a
  translator defaulted off).
* `--selection FILE` — a selection JSON saved by the TUI (see below).

### Exit codes

| code | meaning |
|---|---|
| 0 | written |
| 1 | validation errors (nothing written) |
| 2 | usage/input error (unmapped NIC, bad selection id, unparseable XML, refusing to overwrite, writing onto `SOURCE`/`BASE`) |

### Selection JSON

The TUI can save the selection so a repeat run is reproducible through
`migrate --selection`:

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

If the saved `source_sha256` no longer matches, pf2opn prints a warning (not an
error). Unknown ids in `selected`/`deselected` are an error (exit 2).

## TUI usage

```
pf2opn tui [SOURCE] [--base BASE] [-o OUT] [--search-path DIR ...]
```

Or just `pf2opn tui` with no arguments — the Load screen asks for the three paths.
(`pf2opn` with no subcommand at all does the same.)
`--report` and `--selection` stay flags (the report defaults to `OUT` with its
suffix replaced by `.report.md`).

Screens run left to right; `Esc` or `b` goes back one screen, and `q` quits from
anywhere (with a confirmation if the selection changed and nothing was written).

1. **Load** — a search field for the pfSense config and one for the OPNsense base
   (leave the base blank to use the built-in factory base), plus a plain output
   path field. Flags pre-fill the fields. The source and base fields search for
   `*.xml` files (current directory, `~/Downloads`, `~/Desktop`, `~`, the parent
   of any known path, and each `--search-path`), scanned once and filtered as you
   type. Key bindings:
   * `↑` / `↓` move the candidate list; `Enter` on an entry **picks** it (fills
     the input, stays put); `Enter` in the input **continues**
   * `Tab` / `Shift+Tab` move between fields
   * `Esc` clears the focused field's query; `r` rescans the roots
   * a typed or pasted full path is used verbatim — it does not have to be in the
     list

   Each field is validated inline (the base may be empty); `Enter` continues once
   the source and output are valid and the base is either empty or valid. Then a
   summary: source path, pfSense version/hostname, section counts, base path (or
   "built-in default") / hostname, and any base version-mismatch warnings.
2. **Interface map** — a table of `Incl | Key | Description | pfSense device |
   Kind | OPNsense device`. VLAN/WireGuard rows show their derived device
   (read-only); physical rows are editable.
   * `space` toggle Include (not allowed for `unsupported` kinds)
   * `enter` open the NIC picker for a physical row (a list of the base's NICs plus
     free text); applies to every row using that pfSense NIC
   * `c` continue (runs the mapping validation; errors are shown and you stay)
3. **Select** — the item tree on the left, a **Source / Preview / Notes** detail
   pane on the right. Each row starts with a selection bracket — `[x]` selected,
   `[ ]` not — followed by the label and, for non-OK items only, a trailing
   status marker: `[x] label` (OK), `[ ] label ⚠` (PARTIAL), `[ ] label ✖`
   (UNSUPPORTED). The bracket is selection only (never status), and every row —
   including untranslatable ones — is bracketed so the column aligns. Section
   headers are `[x]`/`[-]`/`[ ] title (n/total)`, counting only selectable
   items (UNSUPPORTED items are excluded from both numbers). Colours: OK green,
   PARTIAL yellow, UNSUPPORTED red/dim, applied to the label and its marker.
   The Source and Preview tabs show redacted XML (see the security model).
   * `space` toggle item / section (dependency rules apply — see below)
   * `a` select all OK/PARTIAL in the section, `n` select none in the section
   * `A` select all default items globally
   * `f` cycle filter: all → selected → problems (PARTIAL + UNSUPPORTED)
   * `/` search by label (`Esc` clears)
   * `e` / `E` expand / collapse all
   * `i` back to the interface map, `r` review and write, `?` help
4. **Review** — `build_output` and validation run in a worker; the summary table,
   errors (red) and warnings (yellow) are shown, plus the output/report paths.
   * `w` write (disabled while there are validation errors; confirms if `OUT`
     already exists and `--force` is set)
   * `s` save the selection JSON (default `OUT.selection.json`)
   * `b` back
5. **Done** — the written paths, counts, plugin list, and the report location.
   `q` quits.

**Dependencies.** Selecting an item closes over the items it needs (an alias, an
interface, a gateway, a NAT rule's associated filter rule). Deselecting an item
that selected items depend on asks first; the CLI with an explicit `selected` list
closes over deps automatically, and a `deselected` id that still has selected
dependents is an error (exit 2).

## Security model

* **The input is never modified.** `SOURCE` (and `BASE`) are opened read-only.
* **Secrets are redacted from everything shown or logged.** The TUI Source and
  Preview tabs, the Markdown report, and log records never contain the value of a
  `SECRET_TAGS` field (passwords/hashes, private keys, PSKs, API keys, community
  strings, certificate material, …). Secret fields are referred to by name only;
  logging installs a redacting filter *before* any file is parsed.
* **The output config naturally contains secrets** (it is a firewall config), so it
  is written atomically with mode **`0600`**.
* The Markdown report is built only from item labels, reasons, warnings,
  follow-ups and counts — it contains no XML and no secret values. It is written
  with the process's normal file mode (`0644` under the default umask), not the
  config's `0600`; that is deliberate, so a reviewer or diff tool can read it
  without the config's permissions.

## Import-test before you trust it

pf2opn's validation is **structural and referential only**. It checks
well-formedness, unique UUIDs, interface/VLAN/WireGuard definitions, and that
filter/NAT/DHCP/alias/gateway/unbound references resolve — it does **not**
re-implement OPNsense's PHP model validation.

**Always import the output into a throwaway OPNsense 26.7 VM first**, before
touching the real box. Restore the whole config
(**System ▸ Configuration ▸ Backups ▸ Restore**), reboot, and confirm the
Firewall ▸ Rules, NAT, **Dnsmasq DHCP**, Unbound and WireGuard pages load without
validation errors. Also verify interface assignments on the console before
connecting WAN.

## Development

```sh
.venv/bin/python -m pytest -q
```

Golden tests per translator live under `tests/golden/`; `pytest --update-golden`
rewrites them (always review the diff). All fixtures are synthetic: **never commit
a real config.** `config-*.xml`, `*.real.xml`, `*.report.md` and `/local/` are
git-ignored.

The one committed config is `src/pf2opn/data/base_default.xml`, the bundled
factory base. It is not a user's config: it is OPNsense's own public factory
default (the password is the documented `opnsense`), with the wizard trigger
stripped and placeholder NICs replaced, and is shipped as package data.
