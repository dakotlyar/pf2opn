# pf2opn

`pf2opn` reads a pfSense `config.xml` (read-only), shows every migratable unit as
an **item** with a status, and lets you choose what carries over. It then merges
the selected items into a **base OPNsense config** exported from the target box
and writes a new OPNsense `config.xml` plus a Markdown migration report.

It targets **OPNsense 26.7.x** and the pfSense CE 2.7.x/2.8.x config format. It
does **not** talk to a live firewall: there is no API or SSH access. Validation is
structural and referential only — it does not re-implement OPNsense's PHP model
validation, so the output must be import-tested (see below).

The authoritative design is [`docs/DESIGN.md`](docs/DESIGN.md). This README only
describes how to run what exists today.

## Why `--base` is required

`--base` is a fresh OPNsense `config.xml` exported from the **target appliance**
(WAN/LAN assigned at the console first). The output is a deep copy of that base
with the selected pfSense items merged in — it is never built from scratch.

That is deliberate:

* Building from scratch (or from the bundled `config.xml.sample`) would lose the
  target's real NIC names, root password, model `version` attributes, and
  firmware/plugin state.
* A missing or wrong model `version` attribute makes OPNsense re-run its own model
  migrations on import (for example `MFP1_0_3` deletes `nat/npt`), which fails
  silently.

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

## CLI usage

```
pf2opn inspect SOURCE --base BASE [--json]
pf2opn migrate SOURCE --base BASE -o OUT [--report REPORT]
               (--select-all-ok | --selection FILE) [--map-nic PF=OPN ...]
               [--keep-base-rules] [--force]
pf2opn tui     [SOURCE] [--base BASE] [-o OUT] [--report REPORT] [--selection FILE]
               [--force]
```

For `tui`, `SOURCE`, `--base` and `-o` are **optional**: run `pf2opn tui` on its own and the
Load screen collects them (any you pass pre-fill the fields). `migrate`/`inspect` still require them.

* `SOURCE` — the pfSense `config.xml` to read. It is opened read-only and is
  **never** modified.
* `--base BASE` — the base OPNsense `config.xml` (required, see above).
* `-o OUT` / `--output OUT` — the new OPNsense `config.xml` to write (mode `0600`).
* `--report REPORT` — report path; defaults to `OUT` with the suffix replaced by
  `.report.md`.
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
pf2opn tui [SOURCE] [--base BASE] [-o OUT]
```

Or just `pf2opn tui` with no arguments — the Load screen asks for the three paths.
`--report` and `--selection` stay flags (the report defaults to `OUT` with its
suffix replaced by `.report.md`).

Screens run left to right; `Esc` or `b` goes back one screen, and `q` quits from
anywhere (with a confirmation if the selection changed and nothing was written).

1. **Load** — editable fields for the pfSense config, the OPNsense base, and the
   output path (pre-filled from any flags). Each is validated inline; `enter`
   continues once all three are valid. Then a summary: source path, pfSense
   version/hostname, section counts, base path/hostname, and any base
   version-mismatch warnings.
2. **Interface map** — a table of `Incl | Key | Description | pfSense device |
   Kind | OPNsense device`. VLAN/WireGuard rows show their derived device
   (read-only); physical rows are editable.
   * `space` toggle Include (not allowed for `unsupported` kinds)
   * `enter` open the NIC picker for a physical row (a list of the base's NICs plus
     free text); applies to every row using that pfSense NIC
   * `c` continue (runs the mapping validation; errors are shown and you stay)
3. **Select** — the item tree on the left, a **Source / Preview / Notes** detail
   pane on the right. Item labels are `[x] ✔ label` (OK), `[ ] ⚠ label` (PARTIAL)
   and `    ✖ label` (UNSUPPORTED). The Source and Preview tabs show redacted XML
   (see the security model).
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
