# pf2opn — Design Spec

Status: **design, ready for implementation** (author: Sabine, 2026-11-03; OPNsense facts re-verified against
`opnsense/core` 26.7.8 the same day — see §19; Dave answered the seven open questions on 2026-11-03 and
they are folded in as a decisions record — see §18)
Target: **OPNsense 26.7.x** (latest stable tag `26.7.8`, released 2026-10-30; `27.1.a` is a dev tag)
Source: **pfSense config.xml** (`<pfsense><version>24.0</version>` — the pfSense CE 2.7.x/2.8.x config format)

This document is the single source of truth. The developer must not invent behaviour that
is not described here. If something is ambiguous, stop and ask; don't guess.

---

## 0. How to use this document (for the developer)

* Build strictly milestone by milestone (§17). Each milestone lists the sections you need to read.
* Each milestone should stay under ~400 lines of production code. Tests don't count toward that.
* Never commit a real config. All fixtures are synthetic (§16).
* Python ≥ 3.11 language features only (the system has 3.14, but keep it portable).
* Code style: type hints everywhere, `dataclasses`, no global mutable state except the
  translator registry, and no `print` in library code (only `cli.py` / TUI print).

---

## 1. Goals / non-goals

Goals
1. Read a pfSense `config.xml` (read-only), show every migratable unit as an **Item** with a
   status (OK / PARTIAL / UNSUPPORTED), and let the user pick what carries over.
2. Make **interface/NIC mapping** an explicit step.
3. Write a new OPNsense `config.xml` by **merging selected items into a base OPNsense
   config** exported from the target box.
4. Write a Markdown migration report (carried over / skipped / not translatable / manual steps)
   that contains **no secrets**.
5. Offer a non-interactive mode for tests and repeat runs.

Non-goals (v1)
* Talking to a live firewall (no API or SSH).
* Re-implementing OPNsense's PHP model validation. We do structural and referential checks only
  (§13). Final proof is importing the result into an OPNsense VM (M14).
* Migrating packages that have no core equivalent (§12.4).

---

## 2. Target platform facts (verified against `opnsense/core` tag `26.7.8`)

Sources I read (all under `https://github.com/opnsense/core/blob/26.7.8/`):

| Model file (`src/opnsense/mvc/app/models/OPNsense/…`) | mount (config path) | version |
|---|---|---|
| `Firewall/Alias.xml` | `//OPNsense/Firewall/Alias` | 1.0.1 |
| `Firewall/Filter.xml` | `//OPNsense/Firewall/Filter` (rules, snatrules, npt, onetoone) | 1.0.6 |
| `Firewall/DNat.xml` | `/nat/rule+` (port forwards, legacy path, MVC-validated) | 1.0.0 |
| `Firewall/Group.xml` | `/ifgroups` | 1.0.0 |
| `Routing/Gateways.xml` | `//OPNsense/Gateways` | 1.0.0 |
| `Routing/GatewayGroups.xml` | `/gateways/gateway_group+` | 1.0.0 |
| `Routes/Route.xml` | `//staticroutes` | 1.0.1 |
| `Interfaces/Vlan.xml` | `/vlans` | 1.0.0 |
| `Interfaces/Vip.xml` | `/virtualip` | 1.0.1 |
| `Interfaces/NetworkInterface.xml` | `:memory:` (a view over legacy `<interfaces>`) | – |
| `Dnsmasq/Dnsmasq.xml` | `/dnsmasq` | 1.0.9 |
| `Kea/KeaDhcpv4.xml` | `//OPNsense/Kea/dhcp4` | 1.0.7 |
| `Unbound/Unbound.xml` | `//OPNsense/unboundplus` | 1.0.15 |
| `Wireguard/Server.xml` / `Client.xml` / `General.xml` | `//OPNsense/wireguard/{server,client,general}` | 1.0.1 / 1.0.0 / 0.0.1 |
| `Trust/Ca.xml` / `Trust/Cert.xml` | `/ca+` / `/cert+` | 1.0.0 |
| `Auth/User.xml` / `Auth/Group.xml` | `/system/user+` / `/system/group+` | 1.0.0 |
| `Cron/Cron.xml` | `//OPNsense/cron` | 1.0.4 |
| `Syslog/Syslog.xml` | `//OPNsense/Syslog` | 1.0.2 |
| `Core/Tunables.xml` | `//sysctl` | 1.0.2 |
| plugins `dns/ddclient/…/DynDNS/DynDNS.xml` (branch `stable/26.7`) | `//OPNsense/DynDNS` | 1.5.1 |

> DHCPv4 is written to **Dnsmasq** (`Dnsmasq/Dnsmasq.xml`, mount `/dnsmasq`). The Kea row is kept only
> because Kea is the one plausible *future* backend (§12.3); it is not used (see §18 Q1 / D5).

Field transforms reference OPNsense's own converters:
* `src/opnsense/scripts/filter/list_legacy_rules.php` converts legacy filter rules to MVC rules.
  **Our filter mapping copies it.**
* `src/opnsense/scripts/filter/list_legacy_outbound_nat.php` converts legacy outbound NAT to
  source NAT. **Our outbound mapping copies it.**
* `Firewall/Migrations/MFP1_0_4.php` converts 1:1 NAT to MVC. **Our 1:1 mapping copies it.**
* `Routing/Migrations/M1_0_0.php` converts legacy gateways to MVC. **Our gateway mapping copies it.**
* `src/etc/config.xml.sample` shows the fresh-install defaults (dnsmasq DHCP on LAN, two
  default MVC LAN allow rules, `nat/outbound/mode=automatic`).

Release facts that drive the design (docs.opnsense.org `CE_26.1.html` and `CE_26.7.html`):
* 26.7: firewall rules **default to MVC** (`OPNsense/Firewall/Filter/rules`). The legacy rules
  pages moved to the `os-firewall-legacy` plugin. Legacy `<filter><rule>` still *works*, but
  it's the past.
* 26.7.5: the outbound NAT page moved to the legacy plugin. Source NAT (`snatrules`) is the
  replacement. `snatrules` only render when the mode is `hybrid` or `advanced`, and the mode is
  read from legacy `nat/outbound/mode` (`SNatModeField.php`).
* ISC DHCP has already been moved out of core into a plugin. The 26.1.11 notes call the
  firewall-legacy move "the same process that was employed with ISC-DHCP". Fresh installs use
  **Dnsmasq** for DHCP on LAN (`config.xml.sample`). **Kea** is the full-featured core DHCP server.
* 26.7.5: bcrypt password hashes still work (argon2id is the new default), so pfSense
  `bcrypt-hash` values can be reused.
* 26.7.8: "limit the certificate storage to a single certificate per entry".
* 26.7.6: interface settings now have an MVC/API, but the storage is still legacy
  `<interfaces><wan>…`. The DHCP-client "advanced/file based modes are gone".

Config-format facts (from the model PHP):
* MVC list items carry a `uuid="…"` attribute. Cross-references are by uuid (Kea reservation →
  subnet, WireGuard server → peers, Unbound alias → host).
* MVC multi-value fields are **comma-separated in one element** (`MemberField`, `PrivField`,
  `InterfaceField Multiple=Y`). Alias content is **newline-separated** (`AliasContentField`).
  Kea pools are newline-separated `from-to` (`KeaPoolsField`).
* MVC booleans are `1`/`0`. pfSense booleans are usually *presence* (`<enable/>`), sometimes
  `yes`/`on`.
* `DescriptionField`: 1–255 chars, no newlines. `IfDescField` (interface descr): `^[a-z_0-9]{0,255}$`i.
* Alias names (`AliasNameField`): `^([a-zA-Z]|(([_a-zA-Z][a-zA-Z0-9]|[a-zA-Z][_a-zA-Z0-9])[_a-zA-Z0-9]{0,29}))$`.
  They also can't be a reserved pf keyword or a service/protocol name.
* WireGuard server/client `name`: `^[0-9a-zA-Z._\-]{1,64}$`. The device name is `wg<instance>`.
* VLAN device names (`vlanif`) are validated in `Interfaces/Vlan.php` → `performValidation()`: the name must
  start with `vlan0` — or `qinq0` when the parent is itself a VLAN — and match `^{vlan|qinq}0([0-9.]){1,10}$`,
  max 15 characters (e.g. `vlan01`, `vlan02`, `vlan0.1.104`). We generate the next free `vlan0N`; the
  validator rejects anything else, so this must be exact. (The spec previously cited
  `VlanSettingsController.php`; the rule lives in the model, not the controller.)
* Kea/Unbound hostnames use `IsDNSName=Y`, so underscores are allowed. Dnsmasq's own `HostnameField`
  (`Dnsmasq/FieldTypes/HostnameField.php`) allows `_` too (regex `^(?![_-])[A-Za-z0-9_-]{1,63}$`), so
  DHCP reservation hostnames keep underscores rather than being sanitized.
* Filter rule address keywords (`NetworkAliasField`): `any`, `(self)`, `<ifkey>`,
  `<ifkey>ip`, alias names, IPs and CIDRs. This is the same vocabulary as pfSense.
* Built-in interface groups (`GroupField`): `openvpn`, `enc0`, `wireguard`.

### 2.1 Field-level facts verified from the models (2026-11-03)

These were read out of the 26.7.8 model XML during the design pass and are the reason several mappings
below are written the way they are. Do not "improve" them from memory:

* **OPNsense `ArrayField` placement (general rule — read this before writing any list).** A field
  declared `type="ArrayField"` (or a custom field extending it, e.g. `.\AliasField`) named **X** stores
  each row as an element *literally named X*, repeated as a direct child of the field's parent, each
  carrying a `uuid` — e.g. Dnsmasq's `dhcp_ranges`, `hosts`, `dhcp_tags`, `dhcp_options`, `dhcp_boot`
  are repeated `<dhcp_ranges uuid="…">…</dhcp_ranges>` / `<hosts uuid="…">…</hosts>` / … **siblings
  directly under `<dnsmasq>`** (confirmed against the stock base config and `helpers.toList('dnsmasq.dhcp_ranges')`).
  **There is no wrapper element named after the field**; the entry element *is* the field name. This is
  distinct from a **container**, which is a single wrapper element whose rows live under a differently
  named child: Firewall Alias's `<aliases>` is such a container, so `OPNsense/Firewall/Alias/aliases/alias[]`
  (container `aliases`, `ArrayField` `alias`) is correct there — but do not extrapolate that shape to a
  field that is *itself* an `ArrayField`. The earlier DHCP draft got this wrong and wrote a
  `dhcp_ranges`/`hosts` wrapper — see §12.1.11.
* Filter `state-policy` is an `OptionField` whose only values are `if-bound` and `floating` — it is **not**
  free text. pfSense stores it as `state-policy`; empty is the normal case.
* Source-NAT `snatrules/rule/poolopts` values are opaque keys `p1`…`p6`, **not** pfSense strings such as
  `round-robin`. Never copy pfSense `poolopts` verbatim (§12.1.9).
* 1:1 `onetoone/rule/natreflection` values are `default`/`enable`/`disable`. pfSense also emits `pfsync`
  variants that have no equivalent (§12.1.10).
* The DNat model **does** carry `pass` (`pass`/`rule`) and `associated-rule-id`; we deliberately set neither
  (D8), it is not a model limitation.
* Unbound exposes aliases twice: an inline `hosts/host/aliases` (AsList) *and* a top-level `aliases/alias`
  whose `host` is a `ModelRelationField` into `hosts/host`. We use the top-level relation (§12.1.13).
* WireGuard client `tunneladdress` is Required; `serveraddress`/`serverport` are optional; the server `peers`
  field is a `ModelRelationField` of client uuids. pfSense peers carry no endpoint (§12.1.17).
* `Kea/KeaDhcpv4`: `subnets/subnet4/subnet_id` is an `AutoNumberField`; `pools` is a newline `from-to` list
  (`KeaPoolsField`); `option_data/*` lists are comma-separated; `reservations/reservation/subnet` is a
  relation to `subnets`. Field names above are confirmed verbatim.
* `Dnsmasq/Dnsmasq` (v1.0.9): DHCP ranges are `dhcp_ranges` (one `ArrayField` row per range: `interface`
  `InterfaceField` with `AllowDynamic`, `start_addr` **Required**, optional `end_addr`, `mode` may be
  `static`, `lease_time`, and `domain` + `domain_type` ∈ {`interface`,`range`}). Reservations and static
  DNS host records share one table, `hosts` (`host`/`domain`/`ip` AsList in `ip`/`hwaddr` AsList/
  `client_id`/`set_tag`/`descr`). Reusable tags are `dhcp_tags` (`tag`, unique). DHCP options are
  `dhcp_options` (`type` ∈ {`set`,`match`}, numeric `option`/`option6`, `interface`, `tag`, `set_tag`,
  `value`, `force`); boot is `dhcp_boot` (`filename` Required, `servername`, `address`, `interface`,
  `tag`). There is **no per-interface enable flag** — see §12.1.11. The Kea facts above are kept for
  reference only; nothing in the current design writes them.
  * The `tag`/`set_tag` fields on `hosts`, `dhcp_options`, `dhcp_boot` and `dhcp_ranges` are
    `ModelRelationField`s into `dhcp_tags`: they hold the row's **uuid**, not the tag text (§12.1.11).
  * `Dnsmasq.php::performValidation()` rejects `mode=static` with a non-empty `end_addr` ("Static only
    accepts a starting address"), and requires a non-empty `end_addr` for a non-static IPv4 range. So
    `static` names only a static-only subnet (no `range`/`pool`), where `end_addr` is empty.
    `RangeAddressField` extends `NetworkField` with `NetMaskAllowed=N`, so a bare network address
    (`192.168.1.0`, no `/mask`) is accepted as `start_addr` (§12.1.11).
* `Firewall/Filter.xml`: `rules/rule/categories`, `snatrules/rule/categories`,
  `onetoone/rule/categories` and `rules/rule/overload` are all `ModelRelationField`s (uuid-keyed);
  `overload` relates to `Firewall/Alias`. `BaseListField::getValidators()` rejects any value not in the
  field's uuid option list. The legacy converters copy pfSense **names**, which is why §12.1.7/§12.1.9
  diverge from them.
* `Firewall/DNat.xml`: `source`/`destination` have `network`/`address` (volatile)/`port`/`not` and **no
  `any` child**; "any" is the `network` keyword `any` (or empty) (§12.1.8). On the `onetoone` rule,
  `natreflection` ∈ {`default` (empty), `enable`, `disable`} only — no `purenat` (§12.1.10).

---

## 3. Key decisions (and what I rejected)

| # | Decision | Rejected alternatives and why |
|---|---|---|
| D1 | **lxml** for all XML. | stdlib ElementTree has no `getparent()` (we need in-place replacement inside the base tree), weaker XPath, and no parser switches to hard-disable entities/DTD/network. lxml 6.x already has cp314 wheels. |
| D2 | **`--base` is required.** Output = deep copy of base plus the selected items. | Building from scratch or from the bundled `config.xml.sample` would lose the target's real NIC names, root password, model `version` attributes, and firmware/plugin state. A wrong or missing `version` attribute makes OPNsense re-run model migrations on import (for example `MFP1_0_3` deletes `nat/npt`). That fails silently. |
| D3 | **Keep pfSense logical interface keys** (`wan`, `lan`, `optN`) in the output and only remap *devices* (`mlxen1` → `igc1`, `mlxen1.10` → `vlan03`, `tun_wg0` → `wg0`). | Re-keying interfaces would mean rewriting every rule, NAT, DHCP, gateway and Unbound reference, with no user benefit. OPNsense uses the same key scheme. |
| D4 | **Firewall rules → MVC** `OPNsense/Firewall/Filter/rules/rule`. **Outbound NAT → MVC `snatrules`.** **1:1 → MVC `onetoone`.** **Port forwards → `/nat/rule`** (the DNat model lives there). | Writing legacy `<filter><rule>` / `nat/outbound/rule` would work in 26.7, but both are on the way out. The user would then have to run the in-GUI migration assistant anyway. |
| D5 | **DHCPv4 → Dnsmasq** (`/dnsmasq`, model 1.0.9). One `dhcp_ranges` row per pfSense range; static maps become `hosts` entries. Clear the base's default `dhcp_ranges` for each migrated interface first. | **Kea** was rejected as *this box's* backend (it was the earlier draft): it does not register leases into DNS, which breaks the pfSense `regdhcp`/`regdhcpstatic` behaviour Dave relies on. **Dnsmasq** is the fresh-install default, does DHCPv4/6 + RA, and registers both dynamic and static leases into its own DNS. ISC: plugin and deprecated. Kea remains the only plausible future backend (HA/scalability, §12.3) but is out of scope here. |
| D6 | Static DHCP maps are written as **Dnsmasq `hosts` entries** whose names Dnsmasq registers itself; the DHCP finalize emits **Unbound query-forwarding** for each local DHCP domain to Dnsmasq, so dynamic and static names both resolve through the box's resolver. The old **static-map → Unbound host-override synthesis** is **demoted to a fallback, default off**. | Dnsmasq *does* feed DNS, so the Kea-era synthesis ("nothing registers names") is now redundant and would duplicate records. The Unbound→Dnsmasq forward is the OPNsense-documented single-source mechanism. `dns_static_host` stays available but `default_selected=False` for setups that disable Dnsmasq DNS (§12.1.13). |
| D7 | **Deterministic UUIDs**: `uuid5(PF2OPN_NS, item_id + "/" + suffix)`. | uuid4 makes golden tests impossible and cross-references harder (a peer's uuid has to be known before the server renders). A collision with base uuid4 values is negligible. The validator checks uniqueness anyway. |
| D8 | Port-forward **associated filter rules are migrated as standalone MVC rules**. The DNat rule gets no `associated-rule-id`. | Re-creating OPNsense's "associated rule" linkage means generating legacy filter rules, which contradicts D4. pf semantics are the same. |
| D9 | When ≥1 filter rule is selected, the **base's MVC filter rules are replaced** (they're the two default LAN allow rules). Override with `--keep-base-rules`. | Merging gives duplicate "allow LAN" rules, because pfSense has its own. |
| D10 | **Textual `Tree`** with checkbox glyphs in labels, toggled by Space. | Textual has no checkbox-tree widget. `SelectionList` is flat and can't show sections. |
| D11 | Translators are **pure**: `render(item, ctx) -> list[Placement]`. Only `writer.py` mutates the output tree. | Translators that mutate the tree directly can't be previewed in the TUI or golden-tested in isolation. |
| D12 | pfSense cron jobs are **classified, never written**. OPNsense cron only runs configd actions (`ConfigdActionsField`). | Writing shell commands into `OPNsense/cron` produces invalid config. Every cron job in the real config is a pfSense/package internal job anyway. |
| D13 | **`tui` collects omitted paths on the Load screen**: `SOURCE`, `--base` and `-o/--output` are optional for `tui` only (still required for `inspect`/`migrate`). Provided flags pre-fill the Load-screen fields; the collected values are what the run uses. The three path checks live once, in `paths.py`, and are called by the CLI for flag values and by the Load screen for collected ones (§9, §15). `--report`/`--selection` stay optional flags and are **not** Load-screen fields. | A filesystem browser in the Load screen — Textual has no built-in file dialog and a custom one is out of scope (the ask says keep it small). Keeping `tui`'s paths required — contradicts the ask. Promoting `--report`/`--selection` to Load fields — they already have a default (report) or load/save behaviour (selection) and are not validity-gated, so fields would only add surface. |

---

## 4. Architecture

```
            ┌──────────────┐   read-only    ┌──────────────┐
 pfSense ──►│  xmlio.parse │──────────────► │ SourceContext│ (pf root, pf interfaces, base root)
 config.xml └──────────────┘                └──────┬───────┘
 base.xml ───────────────────────────────────────►│
                                                   ▼
                      IfaceMap (NIC mapping: TUI screen or --map-nic / JSON)
                                                   │
                                                   ▼
        registry: [Translator…]  ── discover(src, ifmap) ──►  list[Item]
                                                   │
                                     deps.resolve(items)   (propagate UNSUPPORTED, compute closures)
                                                   ▼
                                     Plan(items, ifmap, selection)
                         ┌─────────────────────────┼─────────────────────┐
                         ▼                         ▼                     ▼
                TUI (select/preview)      selection.json (save/load)    CLI non-interactive
                         └─────────────────────────┼─────────────────────┘
                                                   ▼
                  translators.render(item, WriteContext) ──► list[Placement]  (pure)
                  translators.finalize(selected, ctx)     ──► list[Placement]  (section settings)
                                                   ▼
                    writer.apply(base_copy, placements, policies) ──► output tree
                                                   ▼
                    validate.check(output tree, plan) ──► errors / warnings
                                                   ▼
                    write OUT (0600)  +  report.md (no secrets)
```

`build_plan(src: SourceContext, ifmap: IfaceMap) -> Plan` is pure and cheap. Run it again
whenever the mapping changes. Selection is kept by item id.

---

## 5. Project layout (exact)

```
pf2opn/
├── pyproject.toml
├── README.md
├── .gitignore
├── docs/DESIGN.md
├── src/pf2opn/
│   ├── __init__.py            # __version__ = "0.1.0"
│   ├── __main__.py            # from .cli import main; main()
│   ├── cli.py                 # argparse: inspect | migrate | tui
│   ├── xmlio.py               # safe parse, serialize, small element helpers
│   ├── secrets.py             # SECRET_TAGS, redact_element(), RedactingFilter
│   ├── model.py               # Status, Item, Placement, Plan, IfaceTarget, IfaceMap, Selection
│   ├── context.py             # SourceContext, WriteContext, PfIface, uuid helpers
│   ├── ifmap.py               # build default IfaceMap, device classification, vlan/wg naming
│   ├── registry.py            # @register, all_translators()
│   ├── deps.py                # resolve(), closure(), dependents()
│   ├── selection.py           # JSON load/save, defaults, select_all_ok
│   ├── plan.py                # build_plan()
│   ├── paths.py               # shared path checks: source/base parse+root, output same-file/overwrite (§9)
│   ├── writer.py              # apply placements to base copy, policies, versions, revision
│   ├── versions.py            # MODEL_VERSIONS (table in §10.3)
│   ├── validate.py            # §13
│   ├── report.py              # §14
│   ├── translators/
│   │   ├── __init__.py        # imports every module below (triggers registration)
│   │   ├── base.py            # Translator ABC + helper functions (§7.3)
│   │   ├── system.py
│   │   ├── interfaces.py
│   │   ├── vlans.py
│   │   ├── gateways.py
│   │   ├── routes.py
│   │   ├── aliases.py
│   │   ├── vips.py
│   │   ├── filter.py
│   │   ├── nat.py             # 3 translators: nat_pf, nat_out, nat_1to1
│   │   ├── dhcp_dnsmasq.py     # 2 translators: dhcp_range, dhcp_static
│   │   ├── unbound.py         # dns_general, dns_host, dns_static_host, dns_domain, dns_acl
│   │   ├── trust.py           # ca, cert
│   │   ├── users.py           # user, group
│   │   ├── wireguard.py       # wg_tunnel, wg_peer
│   │   ├── dyndns.py
│   │   ├── syslog.py
│   │   ├── cron.py            # classifier only, always UNSUPPORTED
│   │   └── unsupported.py     # catalogue of whole sections/packages (§12.4)
│   └── tui/
│       ├── __init__.py
│       ├── app.py             # Pf2OpnApp
│       ├── app.tcss
│       ├── widgets.py         # ItemTree, DetailPane
│       └── screens/
│           ├── __init__.py
│           ├── load.py
│           ├── ifmap.py
│           ├── select.py
│           ├── review.py
│           └── done.py
└── tests/
    ├── conftest.py            # fixtures loader, --update-golden option
    ├── fixtures/pfsense/*.xml # synthetic, one per topic (§16)
    ├── fixtures/opnsense/base_26_7.xml
    ├── golden/<translator>/<case>.xml
    └── test_*.py
```

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "pf2opn"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["lxml>=5.2", "textual>=3.0"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
pf2opn = "pf2opn.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"pf2opn.tui" = ["app.tcss"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Dev setup: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest`.

`.gitignore` (exists already, created with this doc):
```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
/local/
/out/
config-*.xml
*.real.xml
*.report.md
!tests/**
```
Real configs live in `local/` (ignored).

---

## 6. Core data model (`model.py`)

```python
from dataclasses import dataclass, field
from enum import Enum
from lxml import etree

class Status(str, Enum):
    OK = "ok"                  # translates fully
    PARTIAL = "partial"        # translates; warnings explain what is lost or changed
    UNSUPPORTED = "unsupported"  # cannot be written; `reason` says why + alternative

@dataclass
class Item:
    id: str                    # stable, see §6.1
    section: str               # translator key, e.g. "filter"
    label: str                 # one line, human readable, never contains secrets
    source: etree._Element | None   # pfSense element (read-only; never mutated)
    status: Status
    reason: str = ""           # required when UNSUPPORTED
    warnings: list[str] = field(default_factory=list)   # required non-empty when PARTIAL
    deps: list[str] = field(default_factory=list)       # item ids this item needs
    default_selected: bool = True                       # forced False when UNSUPPORTED
    followups: list[str] = field(default_factory=list)  # manual steps for the report
    requires_plugin: str | None = None                  # e.g. "os-ddclient"
    readonly_selection: bool = False                    # True for interfaces (toggled on ifmap screen)
    emits: bool = True                                  # False for note/settings/classifier items that
                                                        # produce no config object; excluded from the
                                                        # report's migrated counts (§14)
    data: dict = field(default_factory=dict)            # translator-private normalized values

class Mode(str, Enum):
    APPEND = "append"          # append `element` as last child of container at `path`
    SET = "set"                # set text of leaf at `path` (create path if missing)
    REPLACE = "replace"        # replace element at `path` (create if missing) with `element`
    REMOVE = "remove"          # remove all elements matching `path` (an XPath relative to root)

@dataclass
class Placement:
    path: str                  # slash path relative to <opnsense>, e.g. "OPNsense/Firewall/Alias/aliases"
    mode: Mode
    element: etree._Element | None = None
    text: str | None = None
    item_id: str | None = None # for error messages

@dataclass
class IfaceTarget:
    pf_key: str                # "opt2" (also the output key, D3)
    descr: str                 # pfSense descr (unescaped)
    pf_device: str             # "mlxen1.10"
    kind: str                  # "physical" | "vlan" | "wireguard" | "unsupported"
    opn_device: str | None     # "vlan02"; None = unmapped
    include: bool = True

@dataclass
class IfaceMap:
    targets: dict[str, IfaceTarget]          # by pf_key, ordered as in pfSense
    nic_map: dict[str, str]                  # physical pf NIC -> target NIC ("mlxen1" -> "igc1")
    vlan_names: dict[str, str]               # pf vlanif -> opn vlanif ("mlxen1.10" -> "vlan02")
    wg_names: dict[str, str]                 # pf tun -> opn device ("tun_wg0" -> "wg0")

    def device_for(self, pf_device: str) -> str | None: ...
    def included_keys(self) -> set[str]: ...

@dataclass
class Plan:
    items: list[Item]                        # in registry order, then discovery order
    by_id: dict[str, Item]
    ifmap: IfaceMap
    selected: set[str]
    options: dict                            # {"keep_base_rules": False}
```

### 6.1 Item id scheme (stable across runs, used in selection JSON)

| section | id format | example |
|---|---|---|
| system | `system:<facet>` | `system:hostname`, `system:dns`, `system:ssh`, `system:webgui`, `system:root_password`, `system:offload`, `system:console`, `system:timezone`, `system:ntp` |
| interfaces | `iface:<key>` | `iface:opt2` |
| vlans | `vlan:<pf vlanif>` | `vlan:mlxen1.10` |
| gateways | `gw:<name>` | `gw:WAN_DHCP` |
| routes | `route:<network>` | `route:10.9.0.0/16` |
| aliases | `alias:<name>` | `alias:RFC1918` |
| vips | `vip:<interface>:<subnet>` | `vip:lo0:192.168.2.1` |
| filter | `rule:<tracker>` (fallback `rule#<index>`) | `rule:1714398622` |
| nat_pf | `pf:<associated-rule-id or index>` | `pf:nat_5feb373550a854.47144861` |
| nat_out | `snat#<index>` | `snat#3` |
| nat_1to1 | `binat#<index>` | `binat#0` |
| dhcp_range | `dhcp:<ifkey>` | `dhcp:opt1` |
| dhcp_static | `dhcpres:<ifkey>:<mac>` | `dhcpres:opt1:8c:ce:4e:4b:9e:51` |
| dns_general | `dns:general` | |
| dns_host | `dnshost:<host>.<domain>` | `dnshost:alderaan.home.asmartass.me` |
| dns_static_host | `dnsstatic:<ifkey>:<mac>` | |
| dns_domain | `dnsdom:<domain>` | |
| dns_acl | `dnsacl:<aclid>` | |
| ca / cert | `ca:<refid>` / `cert:<refid>` | |
| user / group | `user:<name>` / `group:<name>` | |
| wg_tunnel | `wgtun:<pf tun name>` | `wgtun:tun_wg0` |
| wg_peer | `wgpeer:<index>` | `wgpeer:3` |
| dyndns | `ddns:<index>` | |
| syslog | `syslog:remote<N>` | |
| cron | `cron#<index>` | |
| unsupported | `unsup:<section or package>` | `unsup:pfblockerng`, `unsup:captiveportal` |

---

## 7. Translator API

### 7.1 `registry.py`
```python
_REGISTRY: list[type["Translator"]] = []
def register(cls): _REGISTRY.append(cls); return cls
def all_translators() -> list["Translator"]:
    return [c() for c in sorted(_REGISTRY, key=lambda c: c.order)]
def get(section: str) -> "Translator": ...
```

### 7.2 `translators/base.py`
```python
class Translator(ABC):
    section: ClassVar[str]        # unique key, matches Item.section
    title: ClassVar[str]          # tree heading, e.g. "Firewall rules"
    tier: ClassVar[int]           # 1 or 2 (0 = classifier/unsupported only)
    order: ClassVar[int]          # display + render order (see §12 table, column "order")
    base_policy: ClassVar[str]    # "merge" | "replace" | "none"  (§10.2)
    container: ClassVar[str | None]  # output container path for base_policy, e.g. "vlans"

    @abstractmethod
    def discover(self, src: "SourceContext", ifmap: IfaceMap) -> list[Item]: ...
    def render(self, item: Item, ctx: "WriteContext") -> list[Placement]: return []
    def finalize(self, selected: list[Item], ctx: "WriteContext") -> list[Placement]: return []
```
`discover` must never raise on odd input. Problems turn into UNSUPPORTED items with a
reason. `render` is only called for selected, non-UNSUPPORTED items.

### 7.3 Helper functions (`translators/base.py`), exact semantics

```python
def pf_text(el, path: str, default: str = "") -> str     # findtext, .strip(), None->default
def pf_desc(el, path: str = "descr") -> str              # html.unescape twice-safe (unescape until stable, max 2),
                                                         # replace \r\n/\n with " ", truncate to 255
def pf_flag(el, path: str) -> bool                       # element exists AND text.strip().lower() not in {"no","off","0","false"}
def pf_list(el, path: str) -> list[str]                  # all matching elements' stripped non-empty text
def opn_bool(b: bool) -> str                             # "1" / "0"
def E(tag: str, text: str | None = None, **attrs) -> etree._Element
def sub(parent, tag: str, text: str | None = None) -> etree._Element   # always creates (empty text => <tag/>)
def pf_endpoint(el) -> tuple[str, bool, str]             # for <source>/<destination>: (net, not, port)
    # net = "any" if <any> present; else <network> text; else <address> text; else "any"
    # not = <not> present; port = fix_port(<port> text)
def fix_port(p: str) -> str                              # "any-X"->"1-X", "X-any"->"X-65535"; ":" -> "-" ONLY for rule/NAT ports
def sanitize_ifdescr(s: str) -> str                      # re.sub(r"[^A-Za-z0-9_]", "_", s)[:255]
def sanitize_name(s: str, maxlen: int = 64) -> str       # re.sub(r"[^0-9A-Za-z._-]", "_", s)[:maxlen] (WG names, ACL names)
def map_net(net: str, ctx_or_ifmap) -> tuple[str | None, str | None]
    # returns (opn_value, error). Handles: any, (self), IP/CIDR, <key>, <key>ip, alias name.
    # <key>/<key>ip with key not included in ifmap -> (None, "interface <key> not migrated")
def net_deps(net: str, src) -> list[str]                 # "alias:<n>" if alias name, "iface:<k>" if iface keyword
```

### 7.4 Contexts (`context.py`)
```python
@dataclass
class PfIface:
    key: str; descr: str; device: str; enabled: bool
    ipaddr: str; subnet: str; ipaddrv6: str; subnetv6: str; gateway: str
    el: etree._Element

@dataclass
class SourceContext:
    pf: etree._Element                    # <pfsense> root
    base: etree._Element                  # <opnsense> root of --base (read-only here)
    pf_ifaces: dict[str, PfIface]         # ordered
    alias_names: set[str]                 # all pfSense alias names
    gateway_names: set[str]
    pf_version: str
    @classmethod
    def load(cls, src_path: Path, base_path: Path) -> "SourceContext": ...

@dataclass
class WriteContext:
    src: SourceContext
    plan: Plan
    def uuid(self, item_id: str, suffix: str = "") -> str     # str(uuid.uuid5(PF2OPN_NS, f"{item_id}/{suffix}"))
    def selected(self, item_id: str) -> bool
    def map_iface(self, pf_key: str) -> str | None            # key if included else None
    def device(self, pf_device: str) -> str | None
PF2OPN_NS = uuid.UUID("6f2c1d2e-8a4b-4c1e-9a43-7d0c5e0f2a11")
```

---

## 8. Dependencies (`deps.py`)

* `Item.deps` are item ids. Built-in targets aren't items and are never listed: `any`,
  `(self)`, IP literals, the groups `openvpn`, `enc0`, `wireguard`, and `lo0`.
* `resolve(items)`: repeat until nothing changes. If an item depends on an id that is missing
  or UNSUPPORTED, the item becomes UNSUPPORTED with reason
  `"depends on <dep label> which cannot be migrated"`. (Missing ids use the id itself as the label.)
* `closure(ids, plan) -> set[str]`: the ids plus all transitive deps.
* `dependents(id, plan) -> set[str]`: selected items that transitively depend on `id`.
* Selection rules (TUI and CLI):
  * Selecting X selects `closure({X})`. The TUI shows a notification listing what got added.
  * Deselecting X when `dependents(X)` is non-empty: the TUI shows a confirm modal ("Also
    deselect N items?" plus a list). Yes deselects all of them. No cancels. The CLI with an
    explicit `selected` list closes over deps automatically. An explicit `deselected` id that has
    selected dependents is an error (exit 2).
* Interfaces: `iface:<k>` is selected iff `ifmap.targets[k].include`. Only the ifmap screen
  changes this (`readonly_selection=True`).

---

## 9. CLI and selection file

```
pf2opn inspect SOURCE --base BASE [--json]
pf2opn migrate SOURCE --base BASE -o OUT [--report REPORT]
               (--select-all-ok | --selection FILE) [--map-nic PF=OPN ...]
               [--keep-base-rules] [--force]
pf2opn tui     [SOURCE] [--base BASE] [-o OUT] [--report REPORT]
               [--selection FILE] [--force]
```
* For `inspect` and `migrate`, `SOURCE`, `--base` (and `-o` for `migrate`) are **required**.
* For `tui` they are **optional**: any of `SOURCE`, `--base`, `-o` that is omitted is collected in
  the **Load** screen (§15). A provided flag **pre-fills** the matching Load field; the value the
  Load screen holds at continue time is what the run uses, so an edit in the TUI supersedes the
  flag and an untouched field keeps the flag's value.
* `REPORT` defaults to `OUT` with the suffix replaced by `.report.md`. For `tui` the default is
  derived from the **collected** output path (computed after the Load screen loads, not from
  `args.output`); an explicit `--report` always overrides it. `--report` and `--selection` are not
  Load-screen fields: the report path keeps that default, and `--selection` keeps today's load (on
  start) / save (Review screen `s`) behaviour.
* The tool refuses (exit 2) if OUT already exists and `--force` isn't given, or if OUT resolves
  to the same file as SOURCE or BASE (`os.path.samefile` when it exists, plus a resolved-path
  comparison). In `tui`, an output that already exists is **not** overwritten by a toggle —
  `--force` stays a flag and the Load screen reports "…exists; pass --force to overwrite".

**Where the path checks live (one source of truth).** `paths.py` owns every check above; `cli.py`
and the TUI both call it, so flag values and TUI-collected values cannot diverge:

* `parse_pf_root(path)` / `parse_opnsense_root(path)` — parse and require the root element
  `<pfsense>` / `<opnsense>` (the `ValueError`s `SourceContext.load` already raises move here), and
  `source_error(path)` / `base_error(path)` wrap them as "message or `None`" for form display.
  `SourceContext.load` calls the parse functions, so "parses with the right root" is defined once and
  `load` and the Load screen agree.
* `same_file(a, b)` and `check_output(out, source, base, force)` — the same-file rule and the
  refusals (`out == source`/`base`, skipping any path that is omitted/`None`, and `out exists`
  without `--force`). `cmd_migrate` and `cmd_tui` use them today under other names; the Load screen
  uses the same functions.
* The CLI stays **authoritative where a path was passed as a flag**: `cmd_tui` runs
  `source_error`/`base_error`/`check_output` on every flag that was provided and exits 2 (no TUI)
  on failure. The Load screen then re-runs the identical functions on the pre-filled value
  (idempotent), so a flag value never bypasses the screen's validation. When a path is omitted,
  only the Load screen runs these functions.
* An invalid path typed in the TUI does **not** exit — the Load screen stays and shows the inline
  error (§15). Quitting from the Load screen without writing exits 0.

* `inspect` prints one line per item: `STATUS  id  label  [warnings count]`, grouped by
  section. `--json` prints a list of `{id, section, label, status, reason, warnings, deps}`. It
  never prints source XML.
* Exit codes: 0 = written. 1 = validation errors (nothing written). 2 = usage/input error
  (unmapped NIC, bad selection id, unparseable XML, refusing to overwrite).

Selection JSON (`selection.py`, version 1):
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
* If `selected` is present (even an empty list), the selection is exactly
  `closure(selected)` plus the included interfaces. Otherwise it's
  `default_selected` items minus `deselected`.
* `--select-all-ok` means "all default_selected items" (OK and PARTIAL, minus items a
  translator defaulted off).
* `--map-nic` overrides `nic_map` entries. Interfaces not listed in `interfaces` default to
  `include=true` if their kind isn't `unsupported`.
* If `source_sha256` doesn't match, print a warning (not an error).
* Unknown ids in `selected`/`deselected` → exit 2 with the list.

---

## 10. Writer (`writer.py`)

### 10.1 Algorithm
```python
def build_output(plan: Plan, src: SourceContext) -> etree._Element:
    out = copy.deepcopy(src.base)
    ctx = WriteContext(src, plan)
    for t in all_translators():                      # by order
        sel = [i for i in plan.items if i.section == t.section and i.id in plan.selected
               and i.status != Status.UNSUPPORTED]
        if not sel: continue
        apply_base_policy(out, t, sel, ctx)           # §10.2
        for item in sel:
            apply(out, t.render(item, ctx))
        apply(out, t.finalize(sel, ctx))
    stamp_versions(out)                              # §10.3
    stamp_revision(out, src)                         # §10.4
    return out

def write_output(out, path: Path) -> None:
    etree.indent(out, space="  ")
    data = etree.tostring(out, xml_declaration=True, encoding="UTF-8")
    # write to path + ".tmp" with os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600), fsync, os.replace
```
`apply()`: `APPEND` creates missing path segments as plain elements, then appends. `SET`
creates the path and sets text. `REPLACE` removes existing elements at `path` and inserts at
the same index (or appends if none existed). `REMOVE` uses `out.xpath(path)`.

### 10.2 Base policies
* `merge`: keep base children. Before appending, if the base has an element with the same
  identity key (per translator: alias `name`, gateway `name`, vlan `vlanif`, user `name`,
  ca/cert `refid`, interface key, Unbound host `hostname+domain`), remove the base element and
  add a warning to the report ("replaced base entry X").
* `replace`: remove all children of the managed kind under `container` first.
* `none`: the translator handles it in `finalize`.

### 10.3 Model versions (`versions.py`)
```python
MODEL_VERSIONS = {   # path relative to <opnsense> -> version attr (26.7.8)
  "OPNsense/Firewall/Alias": "1.0.1",
  "OPNsense/Firewall/Filter": "1.0.6",
  "OPNsense/Gateways": "1.0.0",
  "dnsmasq": "1.0.9",
  "OPNsense/unboundplus": "1.0.15",
  "OPNsense/wireguard/server": "1.0.1",
  "OPNsense/wireguard/client": "1.0.0",
  "OPNsense/wireguard/general": "0.0.1",
  "OPNsense/Syslog": "1.0.2",
  "OPNsense/DynDNS": "1.5.1",
  "vlans": "1.0.0",
  "virtualip": "1.0.1",
  "staticroutes": "1.0.1",
}
```
`stamp_versions`: for each path that exists in the output, keep the version attribute if the
element already has one (it came from the base). If it doesn't, set the table value. If the
base value differs from the table, add a report warning ("base model X is version A, pf2opn
was written for B"). Don't touch "+" mounts (`ca`, `cert`, `system/user`, `system/group`,
`nat/rule`). They carry no version attribute.

### 10.4 Revision
Replace or create `revision` with `<username>pf2opn</username><time>{int(time.time())}</time>
<description>pf2opn: migrated from pfSense {hostname}</description>`.

---

## 11. Secrets policy (`secrets.py`)

```python
SECRET_TAGS = frozenset({
  "bcrypt-hash", "sha512-hash", "md5-hash", "nt-hash", "password", "passwordenc",
  "prv", "privatekey", "privkey", "presharedkey", "psk", "pre-shared-key", "ipsecpsk",
  "authorizedkeys", "otp_seed", "apikeys", "key", "secret", "shared_key", "tls",
  "auth_pass", "rocommunity", "ddnsdomainkey", "ddns_domain_key_secret",
  "custom_options", "sshkeyfile", "data", "crt"  # crt isn't secret, but it's huge: redact for display
})
def is_secret(tag: str) -> bool
def redact_element(el) -> etree._Element
    # deep copy; every SECRET_TAGS element with text gets "«redacted N chars»"
def redacted_xml(el) -> str    # pretty string of redact_element(el), used by TUI Source/Preview tabs
class RedactingFilter(logging.Filter)   # masks known secret values registered via register_secret(value)
```
Rules:
1. Item `label`, `reason`, `warnings`, `followups` must never contain values from SECRET_TAGS
   fields. Translators reference them only by name ("private key", "PSK").
2. The TUI Source and Preview tabs always go through `redacted_xml`.
3. The report is built only from labels/reasons/warnings/followups and counts.
4. The output config.xml naturally contains secrets. It's written with mode 0600.
5. Logging: `cli.main()` installs the filter on the root logger (`secrets.install_logging_filter()`)
   **before** it parses any file or builds a plan — the filter must already be attached when
   `SourceContext.load` starts, or a secret can reach a log during parsing. While parsing, `xmlio.parse`
   calls `register_secret(text)` for every SECRET_TAGS text longer than 8 chars.
6. Tests enforce rules 1–3 (§16.4).

---

## 12. Section mapping

Notation: `pf:` paths are relative to `<pfsense>` and `opn:` paths are relative to `<opnsense>`.
"→" means copy the stripped text. `[]` means a list item (one target element per source
element), with a `uuid` attribute unless stated otherwise.

### 12.0 Overview table (every top-level section in the real config)

| pf section | Tier | translator (order) | target | notes |
|---|---|---|---|---|
| system (basics) | 1 | system (10) | `system/*` | facets, §12.1.1 |
| system/user, group | 1 | users (20) | `system/user[]`, `system/group[]` | §12.1.15 |
| cert, ca | 1 | trust (30) | `ca[]`, `cert[]` | §12.1.14 |
| interfaces | 1 | interfaces (40) | `interfaces/<key>` | §12.1.2 |
| vlans | 1 | vlans (45) | `vlans/vlan[]` | §12.1.3 |
| installedpackages/wireguard | 1 | wg_tunnel (50), wg_peer (51) | `OPNsense/wireguard/*` | §12.1.17 |
| gateways | 1 | gateways (60) | `OPNsense/Gateways/gateway_item[]` | §12.1.4 |
| staticroutes | 1 | routes (65) | `staticroutes/route[]` | §12.1.5 |
| virtualip | 1 | vips (70) | `virtualip/vip[]` | §12.1.12 |
| aliases | 1 | aliases (80) | `OPNsense/Firewall/Alias/aliases/alias[]` | §12.1.6 |
| filter | 1 | filter (90) | `OPNsense/Firewall/Filter/rules/rule[]` | §12.1.7 |
| nat/rule | 1 | nat_pf (100) | `nat/rule[]` | §12.1.8 |
| nat/outbound | 1 | nat_out (101) | `nat/outbound/mode` and `OPNsense/Firewall/Filter/snatrules/rule[]` | §12.1.9 |
| nat/onetoone | 1 | nat_1to1 (102) | `OPNsense/Firewall/Filter/onetoone/rule[]` | §12.1.10 |
| dhcpd | 1 | dhcp_range (110), dhcp_static (111) | `dnsmasq` (`ArrayField` entries `dhcp_ranges`, `hosts` — repeated direct children, §12.1.11) | §12.1.11 |
| unbound | 1 | dns_general (120), dns_host (121), dns_static_host (122), dns_domain (123), dns_acl (124) | `OPNsense/unboundplus/*` | §12.1.13 |
| dyndnses | 1 | dyndns (130) | `OPNsense/DynDNS/*` (plugin os-ddclient) | §12.1.18 |
| syslog | 1 | syslog (140) | `OPNsense/Syslog/destinations/destination[]` | §12.1.19 |
| cron | 1 (classifier) | cron (150) | – (never written) | §12.1.16 |
| dhcpdv6, RA | 2 | – | Dnsmasq DHCPv6 / radvd | |
| sysctl | 2 | – | `sysctl/item[]` | |
| ifgroups | 2 | – | `ifgroups/ifgroupentry[]` | |
| snmpd | 2 | – | os-net-snmp | |
| ipsec / openvpn / l2tp / captiveportal / shaper / packages | Unsupported | unsupported (900) | – | §12.4 |
| revision, rrd, widgets, traffic_graphs, diag, sshdata, dhcpddata, dhcpbackend, ovpnserver, proxyarp, wol, dnshaper, igmpproxy, ppps(empty) | ignored | unsupported (900) | – | listed once in the report as "not applicable" |

### 12.1 Tier 1 mappings

#### 12.1.1 system (`system.py`, base_policy `none`)
One item per facet. All facets SET leaf values in `opn:system/…`.

| item id | pf source | opn target / transform | default | status |
|---|---|---|---|---|
| `system:hostname` | `system/hostname`, `system/domain` | `system/hostname`, `system/domain` | on | OK |
| `system:timezone` | `system/timezone` | `system/timezone` | on | OK |
| `system:ntp` | `system/timeservers` (space-separated) | `system/timeservers` = same string. Also REPLACE `ntpd/ispool` with the space-joined subset containing "pool", and REMOVE `ntpd/prefer` if its value isn't in the new list | on | PARTIAL if any server contains `pfsense.pool.ntp.org` (warning: "pfSense pool servers; consider opnsense.pool.ntp.org") |
| `system:dns` | `system/dnsserver[]`, `system/dns{N}gw` (N=1..8), `system/dnsallowoverride` | REMOVE `system/dnsserver`, APPEND one `system/dnsserver` per server in order. For each N with a gateway name ≠ `none`/empty, SET `system/dns{N}gw` = name and add dep `gw:<name>`. `dnsallowoverride` presence → SET `1`, else SET `0` | on | OK |
| `system:offload` | `system/disablechecksumoffloading`, `disablesegmentationoffloading`, `disablelargereceiveoffloading`, `ipv6allow` | each present → SET `system/<same tag>` = `1`; absent → REMOVE `system/<tag>` | on | OK |
| `system:ssh` | `system/ssh/enable` (=`enabled`), `system/ssh/port` | `system/ssh/enabled` = `enabled`; `system/ssh/port` if non-empty | on | PARTIAL, warning: "OPNsense denies root login and password auth by default; check System ▸ Settings ▸ Administration". Followup: "SSH host keys change; clients will warn". |
| `system:console` | `system/primaryconsole`, `system/serialspeed` | same tags | on | OK |
| `system:webgui` | `system/webgui/protocol`, `port`, `ssl-certref` | `system/webgui/protocol`, `port`, `ssl-certref`; dep `cert:<refid>` | **off** | PARTIAL: "theme/CSS and dashboard settings not migrated" |
| `system:root_password` | `system/user[name='admin']/bcrypt-hash` | SET `system/user[name='root']/password` (the base's root user element) | **off** | PARTIAL: "replaces OPNsense root password with pfSense admin password" |

pfSense-only keys like `webguicss`, `logincss`, `powerd_*`, `bogons`, `maximumtableentries`,
`gitsync`, `acb`, `pkg_repo_conf_path`, `firmware`, and `earlyshellcmd` aren't items. They're
listed once under "not applicable" in the report. Exception: `earlyshellcmd`/`shellcmd`
produce the `unsup:shellcmd` item (§12.4).

#### 12.1.2 interfaces (`interfaces.py`, base_policy `merge` by interface key, container `interfaces`)
Discover one item per `pf:interfaces/<key>`. Status follows from the IfaceTarget kind:
* `unsupported` kind (device `ovpn*`, `ppp*`, `pppoe*`, `lagg*`, `bridge*`, `gif*`, `gre*`,
  `tailscale*`, `l2tp*`, or a WireGuard tunnel that is UNSUPPORTED) → UNSUPPORTED
  "device type <x> is not migrated in v1".
* Physical and unmapped → UNSUPPORTED "NIC not mapped".
* `deps`: `vlan:<device>` for vlan kind, `wgtun:<device>` for wireguard kind.

**Every item this translator emits — OK, PARTIAL *and* UNSUPPORTED — sets
`readonly_selection=True`.** Interface selection is not edited in the item tree: `iface:<key>` is
selected iff `ifmap.targets[key].include`, and only the interface-map screen changes that (§8, §15).
The TUI relies on this flag to refuse `space` and to point the user at the map, so `discover` must
set it on each item it returns.

Render: REPLACE `opn:interfaces/<key>` with:

| pf | opn | transform |
|---|---|---|
| `if` | `if` | `ifmap.device_for(pf if)` |
| `descr` | `descr` | `sanitize_ifdescr(pf_desc)`; warning if changed |
| `enable` (presence) | `enable` | `1` if present, else omit |
| `ipaddr`, `subnet` | same | copied (`dhcp`, `pppoe`(→UNSUPPORTED), static IP). **WireGuard kind: omit both** (the address lives on the WG server, §12.1.17) |
| `ipaddrv6`, `subnetv6` | same | `track6` / `dhcp6` / `slaac` / static copied. Warning if `track6`: "consider OPNsense 'Identity association' mode" |
| `track6-interface`, `track6-prefix-id` | same | copied |
| `gateway`, `gatewayv6` | same | copied; dep `gw:<name>` |
| `blockpriv`, `blockbogons` | same | presence → `1` |
| `spoofmac`, `mtu`, `mss`, `media`, `mediaopt` | same | copied if non-empty |
| `dhcphostname`, `alias-address`, `alias-subnet`, `dhcprejectfrom` | same | copied if non-empty |
| `adv_dhcp_*`, `adv_dhcp6_*` | – | dropped; warning if any is non-empty ("advanced DHCP client options dropped") |

Unknown tags are dropped. Emit one warning listing their names (only if they have text).

#### 12.1.3 VLANs (`vlans.py`, base_policy `merge` by `vlanif`, container `vlans`)
`pf:vlans/vlan[]` → `opn:vlans/vlan[]`:

| pf | opn | transform |
|---|---|---|
| `if` | `if` | `nic_map[if]`; unmapped → UNSUPPORTED "parent NIC <if> not mapped" |
| `tag` | `tag` | copy |
| `pcp` | `pcp` | empty → `0` |
| `descr` | `descr` | `pf_desc` |
| `vlanif` | `vlanif` | `ifmap.vlan_names[vlanif]` (§12.2) |
| – | `proto` | empty element |

#### 12.1.4 Gateways (`gateways.py`, base_policy `merge` by name, container `OPNsense/Gateways`)
`pf:gateways/gateway_item[]` → `opn:OPNsense/Gateways/gateway_item[]`. Follows `Routing/Migrations/M1_0_0.php`:

| pf | opn | transform |
|---|---|---|
| `name` | `name` | must match `^[a-zA-Z0-9_\-]{1,32}$`, else UNSUPPORTED |
| `descr` | `descr` | `pf_desc` |
| `interface` | `interface` | key must be included; else UNSUPPORTED "interface <k> not migrated" (this catches `QSOL_WGGW` → `opt11`). dep `iface:<k>` |
| `ipprotocol` | `ipprotocol` | empty → `inet` |
| `gateway` | `gateway` | `dynamic` → empty |
| `monitor` | `monitor` | copy |
| `monitor_disable` | `monitor_disable` | presence → `1` else `0` |
| `disabled` | `disabled` | presence → `1` else `0` |
| `nonlocalgateway` | `fargw` | presence → `1` else `0` |
| `force_down` | `force_down` | presence → `1` else `0` |
| `gw_down_kill_states` | `monitor_killstates` | non-empty text → `1` else `0` |
| `weight` | `weight` | empty → `1`; clamp 1..10 |
| – | `priority` | `255` |
| `latencylow/high`, `losslow/high`, `interval`, `time_period`, `loss_interval`, `data_length` | same | copy if non-empty |
| `action_disable` | – | dropped; warning "‘disable gateway monitoring action’ has no direct equivalent" |
| name == `pf:gateways/defaultgw4` or `defaultgw6` | `defaultgw` | `1` else `0` |

#### 12.1.5 Static routes (`routes.py`, base_policy `merge` by network, container `staticroutes`)
`pf:staticroutes/route[]` → `opn:staticroutes/route[]`: `network`→`network`,
`gateway`→`gateway` (dep `gw:<name>`), `descr`→`descr`, `disabled` presence →
`enabled=0` else `1`.

#### 12.1.6 Aliases (`aliases.py`, base_policy `merge` by name, container `OPNsense/Firewall/Alias/aliases`)
`pf:aliases/alias[]` → `opn:OPNsense/Firewall/Alias/aliases/alias[]`:

| pf | opn | transform |
|---|---|---|
| – | `enabled` | `1` |
| `name` | `name` | validate with the AliasNameField regex plus `RESERVED_PF_WORDS` (copy the list from `AliasNameField.php` into `aliases.py`) plus `COMMON_SERVICES = {"http","https","ssh","domain","ntp","smtp","ftp","telnet","snmp","imap","pop3","ldap","tftp","syslog","bootps","bootpc","isakmp","nat-t"}`. Fail → UNSUPPORTED "name invalid on OPNsense: <why>" |
| `type` | `type` | `host`→`host`, `network`→`network`, `port`→`port`, `url`→`url`, `urltable`→`urltable`; `url_ports`/`urltable_ports` → UNSUPPORTED "URL port lists not supported by OPNsense aliases" |
| `address` (space-separated) | `content` | split on whitespace, join with `\n`. For `url`/`urltable` use `url` (or `aliasurl[]`) entries joined with `\n` |
| `updatefreq` | `updatefreq` | copy (days) for urltable |
| `descr` | `description` | `pf_desc` |
| `detail` (`\|\|`-separated) | – | dropped; warning "per-entry descriptions dropped" only if any entry is non-empty and isn't `Entry added …` |
| – | `proto`, `counters`, `interface`, `categories` | `proto` empty; `counters` `0`; others omitted |

Deps: for every content entry that is itself a pfSense alias name → dep `alias:<entry>`.

Special cases:
* `name` starts with `pfB_` → UNSUPPORTED "managed by pfBlockerNG; recreate as a GeoIP alias
  (Firewall ▸ Aliases, needs a MaxMind key) or use Unbound blocklists".
* The name collides with an OPNsense built-in (`bogons`, `bogonsv6`, `virusprot`,
  `sshlockout`, or anything starting with `__`) → UNSUPPORTED "name collides with OPNsense
  built-in alias".

#### 12.1.7 Filter rules (`filter.py`, base_policy `replace` unless `keep_base_rules`, container `OPNsense/Firewall/Filter/rules`)
`pf:filter/rule[]` → `opn:OPNsense/Firewall/Filter/rules/rule[]`. Mirrors `list_legacy_rules.php`:

| opn field | value |
|---|---|
| `enabled` | `0` if `<disabled/>` present else `1`. **Follow `list_legacy_rules.php` exactly: ignore pfSense `<enabled>on/off</enabled>`.** The real config has two rules with `<enabled>on</enabled>` (one also has `<disabled/>`); upstream treats those as enabled=1 and enabled=0 respectively. |
| `statetype` | first word of pf `statetype` (`keep state`→`keep`, `sloppy state`→`sloppy`, `synproxy state`→`synproxy`, `modulate state`→`modulate`, `none`→`none`); empty → `keep` |
| `state-policy` | pf `state-policy` text if it is `if-bound` or `floating`; any other non-empty value → PARTIAL "state policy <x> has no OPNsense equivalent, dropped". Empty → empty. |
| `sequence` | see ordering below |
| `action` | pf `type` (`pass`/`block`/`reject`); empty → `pass` |
| `quick` | if pf `quick` present → `1`; else if `floating` present → `0`; else `1` |
| `interfacenot` | `0` (`1` if pf `interfacenot` present) |
| `interface` | pf `interface` split on `,`, each mapped: an included iface key stays as-is; `WireGuard`→`wireguard`; `openvpn`,`enc0` as-is; `any` (or empty) → empty string meaning "all interfaces". An unknown key or excluded iface → UNSUPPORTED "interface <x> not migrated". `l2tp`/`pppoe` → UNSUPPORTED "L2TP/PPPoE server not supported". Join with `,`. Upstream `list_legacy_rules.php` copies the value through unchanged, so we only translate the pf interface keys. |
| `direction` | pf `direction` or `in` |
| `ipprotocol` | pf `ipprotocol` or `inet` |
| `protocol` | pf `protocol` upper-cased (`tcp/udp`→`TCP/UDP`), empty → `any` |
| `icmptype` | pf `icmptype`; `any` → empty |
| `icmp6type` | pf `icmp6-type` names → numbers via the table in `list_legacy_rules.php` |
| `source_net`,`source_not`,`source_port` | `pf_endpoint(source)` → `map_net`; port copied verbatim then only `-any`→`-65535` and `any-`→`1-` (match `list_legacy_rules.php`; do **not** rewrite `:`) |
| `destination_net`,`destination_not`,`destination_port` | same for destination |
| `gateway` | pf `gateway` (name); dep `gw:<name>` |
| `log`,`allowopts`,`nosync`,`nopfsync`,`disablereplyto` | presence → `1` else `0` |
| `replyto` | pf `reply-to` |
| `statetimeout`,`max`,`max-src-nodes`,`max-src-conn`,`max-src-states`,`max-src-conn-rate`,`max-src-conn-rates`,`adaptivestart`,`adaptiveend`,`tag`,`tagged`,`tcpflags1`,`tcpflags2`,`prio`,`set-prio`,`set-prio-low`,`tos` | copy if non-empty |
| `overload` | pf `overload` names a pfSense alias → write **that alias's uuid** `ctx.uuid("alias:<name>")`, with dep `alias:<name>`. If it isn't an output alias (absent/unselected) → empty + PARTIAL warning. `overload` is a uuid `ModelRelationField` (into `Firewall/Alias`); a bare name would fail OPNsense validation. |
| `categories` | **dropped**, PARTIAL warning "rule categories not migrated; recreate them under Firewall ▸ Rules ▸ Categories if needed". `categories` is a uuid `ModelRelationField` (into `Firewall/Category`) and pf2opn creates no category rows in v1, so the pfSense names cannot be copied. |
| `tcpflags_any` | presence → `1` else `0`. (Upstream `list_legacy_rules.php` omits this field; the model has it, so we keep it — golden tests pin the choice.) |
| `description` | `pf_desc` |

> **`overload` / `categories` diverge from `list_legacy_rules.php` on purpose.** The converter copies
> the pfSense `overload` alias name and the `category` names verbatim into the `overload`/`categories`
> keys of its export array, but in the MVC Filter model both are `ModelRelationField`s keyed by uuid,
> and `BaseListField::getValidators()` rejects any value not in that uuid option list
> (`Related category not found.`/`Alias not found.`). OPNsense's own model migrations resolve names →
> uuids first (`MFP1_0_3`/`MFP1_0_4` call `Category()->getByName()`), which is the behaviour we mirror:
> `overload` → the migrated alias uuid (deterministic, D7); `categories` → dropped with a warning.

Dropped, with a warning if non-empty: `sched` ("schedule not migrated"), `os` ("OS
fingerprint filter dropped"), `dnpipe`/`pdnpipe`/`defaultqueue`/`ackqueue` ("traffic shaper
not migrated"), `vlanprio*`. Ignored silently: `id`, `tracker`, `created`, `updated`,
`associated-rule-id` (but see warning below).

Deps: `iface:<k>` for each interface, plus `net_deps` of source/destination nets, plus
`alias:<p>` if a port is an alias name, plus `alias:<name>` if `overload` names an alias, plus
`gw:<name>`.

Warnings:
* `associated-rule-id` present → "was the auto rule of a NAT port forward; now a standalone rule".
* `floating` present with exactly one interface and `quick=0` → "non-quick floating rule
  becomes an interface rule; check ordering".
* Interface `openvpn` or `enc0`, with no OpenVPN/IPsec migrated → "references a VPN that isn't migrated".

Defaults: `default_selected=False` when the description ends with ` auto rule` and the source
or destination is a `pfB_` alias (pfBlockerNG). These usually end up UNSUPPORTED via deps
anyway.

Ordering: pfSense evaluates floating rules first, then interface rules, in document order.
Sequences are given in two passes: floating rules first (document order), then the rest
(document order). `sequence = 10 * (n+1)`. pfSense `separator` blocks are dropped, with one
info note in the report.

#### 12.1.8 NAT port forwards (`nat.py: NatPortForward`, base_policy `merge` (append only), container `nat`)
`pf:nat/rule[]` → `opn:nat/rule[]` (DNat model, uuid attr):

| opn | value |
|---|---|
| `sequence` | `10*(n+1)` |
| `disabled` | presence → `1` (omit otherwise) |
| `nordr` | presence → `1` |
| `interface` | mapped key (excluded → UNSUPPORTED) |
| `ipprotocol` | **derive, do not default** (the same rule as §12.1.9, required by §13 check 13). Explicit pf `ipprotocol` (`inet`/`inet6`) is kept; empty → `inet6` when any of the rendered `source`/`destination` addresses or `target` is an IPv6 literal or CIDR (contains `:`), otherwise `inet`. **Never let an IPv6 endpoint keep `inet`:** `pfctl` rejects the rule ("rule expands to no valid combination") and the whole ruleset fails to load (0 rules). |
| `protocol` | pf `protocol` as-is (lower case) |
| `source`, `destination` | rebuild with `network`/`address`/`port`/`not`, keyword keys validated via `map_net`. **Do not copy pfSense's `<any/>`:** `DNat.xml` has no `any` child, so a copied `<any/>` is a stray node the model ignores. For an `any` endpoint emit `<network>any</network>` instead (the `NetworkAliasField` keyword `any`, same token the filter rules use for `source_net`; an empty `network` also means any, but be explicit). |
| `target` | pf `target` (IP or alias; dep alias) |
| `local-port` | pf `local-port` |
| `natreflection` | `disable`→`disable`; `enable`, `pfsync`, `pfsync+enable` → `purenat` + warning "NAT+proxy/pfsync reflection has no equivalent, using pure NAT"; empty → omit |
| `descr` | `pf_desc` |
| `tag`,`tagged`,`log`,`nosync` | copy / presence → `1` |

Deps: iface, aliases, and **the associated filter rule item** (`rule:<tracker>` of the filter
rule whose `associated-rule-id` equals this rule's) so selecting a port forward also selects
its pass rule. If pf `associated-rule-id` == `pass` → PARTIAL "pfSense ‘pass’ association:
add an explicit pass rule manually".

#### 12.1.9 Outbound NAT (`nat.py: NatOutbound`, base_policy `replace`, container `OPNsense/Firewall/Filter/snatrules`)
**Mode.** `finalize` SETs `opn:nat/outbound/mode` to the **mapped** pfSense mode, not the raw
one, and the `snat:mode` item label uses the mapped value too:

| pf `nat/outbound/mode` | opn mode |
|---|---|
| (empty), `automatic` | `automatic` |
| `advanced` | `advanced` |
| `disabled` | `disabled` |
| `hybrid`, ≥1 `nat/outbound/rule` | **`advanced`** |
| `hybrid`, 0 rules | `automatic` |

pfSense `hybrid` renders the stored `nat/outbound/rule` list (`filter.inc`, `case "hybrid"` →
`get_anynat_rules_list('nat')`) **and** a runtime automatic set
(`filter_nat_rules_outbound_automatic()`); the stored list already contains pfSense's
*materialized* automatic rules (the `Auto created rule…` entries — 30 of the 37 in the reference
config). OPNsense `hybrid` would render our migrated rules *plus* OPNsense's own, different
automatic set (`SourceNatController::getAutomaticOutboundNatRules()` → configd
`filter list automatic_source_nat` → `filter_auto_source_nat()`, `/usr/local/etc/inc/filter.lib.inc`),
whose sources are per-gateway-interface networks + `lo0` + `127.0.0.0/8` only. OPNsense `advanced`
renders exactly the migrated rules and suppresses the automatic set entirely — `filter_auto_source_nat()`
returns nothing unless the mode is `''`/`automatic`/`hybrid` — so `advanced` is the faithful 1:1 and
avoids redundant rules. A `hybrid` config with no explicit rules has nothing to render in `advanced`,
so it maps to `automatic`. Emit a mode-item warning: "pfSense hybrid outbound NAT migrated as
OPNsense advanced (manual); pfSense's materialized rules are copied verbatim and OPNsense's
automatic outbound rule set is not generated".

`pf:nat/outbound/rule[]` → `opn:OPNsense/Firewall/Filter/snatrules/rule[]`. Mirrors `list_legacy_outbound_nat.php`:

| opn | value |
|---|---|
| `enabled` | `0` if `disabled` else `1` |
| `nonat` | presence → `1` else `0` |
| `nosync` | presence → `1` else `0` |
| `sequence` | `10*(n+1)` |
| `interface` | mapped key |
| `ipprotocol` | **derive, do not default.** Explicit pf `ipprotocol` (`inet`/`inet6`) is kept; empty → `inet6` when any of `source_net`, `destination_net` or `target` (after `map_net`/`_outbound_target`) is an IPv6 literal or CIDR (contains `:`), otherwise `inet`. This mirrors `Firewall/Filter.php::performValidation()` ("Source/Destination address type should match selected TCP/IP protocol version"). **Never let an IPv6 source such as `::1/128` keep `inet`:** `pfctl` rejects the rule ("rule expands to no valid combination") and the entire ruleset fails to load (0 rules). |
| `protocol` | upper-cased or `any` |
| `source_net`,`source_not` | `pf_endpoint(source)` → `map_net` |
| `source_port` | pf `sourceport`, `:`→`-` |
| `destination_net`,`destination_not` | `pf_endpoint(destination)` |
| `destination_port` | pf `dstport`, `:`→`-` |
| `target` | pf `target`: empty → empty; `<iface>ip` where iface == rule interface → empty (interface address); other `<k>ip` → keep if `k` included else UNSUPPORTED; `other-subnet` → `targetip` + `/targetip_subnet` (omit the mask if 32/128 or empty); `openvpnip` → UNSUPPORTED "OpenVPN not migrated"; anything else (IP, alias, VIP address) → keep (alias dep) |
| `target_port` | pf `natport`, `:`→`-` |
| `poolopts` | **do not copy pfSense `poolopts`** — OPNsense values are opaque `p1`…`p6` keys. If pf `poolopts` is non-empty, leave it empty and add a PARTIAL warning "outbound NAT pool options not migrated; re-select the pool option in the GUI". Empty (the normal case) → empty. |
| `poolopts_sourcehashkey` | pf `source_hash_key` |
| `staticnatport` | presence → `1` else `0` |
| `log` | presence → `1` else `0` |
| `tag`,`tagged` | copy |
| `categories` | **dropped**, PARTIAL warning "outbound NAT categories not migrated; recreate them under Firewall ▸ Rules ▸ Categories if needed". `list_legacy_outbound_nat.php` emits the pfSense `category` names, but `snatrules/rule/categories` is a uuid `ModelRelationField` and pf2opn creates no category rows in v1 (same issue as §12.1.7). |
| `description` | `pf_desc` |

Mode `automatic`/`disabled` with rules present → the rules are still discovered, but as
PARTIAL "rules are inactive in mode <m>".

#### 12.1.10 1:1 NAT (`nat.py: NatOneToOne`, base_policy `replace`, container `OPNsense/Firewall/Filter/onetoone`)
`pf:nat/onetoone[]` → `opn:OPNsense/Firewall/Filter/onetoone/rule[]`. Follows `MFP1_0_4.php` exactly:
`enabled`, `log`, `sequence`, `interface`, `type` (empty→`binat`), `external` (append the
source mask if external has none), `source_net`/`destination_net` (`any` / address with
`/32`|`/128` appended to bare IPs / network keyword), `source_not`, `destination_not`,
`description`, `natreflection` **mapped** (see below). If source, destination, or external is
missing → UNSUPPORTED "incomplete 1:1 rule".

`natreflection` — `onetoone/rule/natreflection` allows only `default` (empty), `enable`, `disable`
(no `purenat`), so mirror the §12.1.8 port-forward intent (reflection stays on; the mechanism is
lost): `enable`→`enable`; `disable`→`disable`; `pfsync` and `pfsync+enable` → `enable` + PARTIAL
warning "pfSense pfsync NAT reflection has no OPNsense equivalent; using proxy NAT reflection";
empty → omit. Do **not** copy `pfsync` verbatim the way `MFP1_0_4.php` does — the model rejects it.

#### 12.1.11 DHCPv4 → Dnsmasq (`dhcp_dnsmasq.py`)

Source: `pf:dhcpd/<ifkey>` (kept keyed by pfSense interface key, D3). Target: `opn:dnsmasq` — model
`OPNsense/Dnsmasq/Dnsmasq.xml`, mount `/dnsmasq`, **version 1.0.9** (read from `26.7.8`; the `ArrayField`s
`dhcp_ranges`, `hosts`, `dhcp_tags`, `dhcp_options`, `dhcp_boot` and every field below are verbatim from
that model).

**Placement (must match the model exactly).** These are `ArrayField`s (see the general rule in §2.1), so
each row is a repeated same-named element **directly under `<dnsmasq>`**:
`<dnsmasq><dhcp_ranges uuid="…">…</dhcp_ranges><dhcp_ranges uuid="…">…</dhcp_ranges>…</dnsmasq>`, and
identically for `hosts` / `dhcp_tags` / `dhcp_options` / `dhcp_boot`. There is **no** `dhcp_ranges` /
`hosts` / … wrapper element; the entry element *is* the field name. (Verified from the stock base config —
two sibling `<dhcp_ranges uuid>` under `<dnsmasq>`, `helpers.toList('dnsmasq.dhcp_ranges')`, and the
generated `dnsmasq.conf`. The M14 import test proved that a nested `<dhcp_ranges><dhcp_ranges uuid>` is
silently ignored: the 8 ranges, 48 reservations, 6 options and 4 tags never appeared on import.) Two
translators keep the familiar item/de-dup/status shape:

* `dhcp_range` (order 110, base_policy `none`): one item per **enabled** `pf:dhcpd/<ifkey>`, id
  `dhcp:<ifkey>`; renders one or more `dhcp_ranges` rows.
* `dhcp_static` (order 111, base_policy `none`): one item per `pf:dhcpd/<ifkey>/staticmap`, id
  `dhcpres:<ifkey>:<mac>`; renders one `hosts` row; deps `dhcp:<ifkey>`.

**Structural difference from the Kea draft: Dnsmasq has no subnet object.** A subnet is expressed as a
*range* (`dhcp_ranges`, keyed by `interface`, with `start_addr` Required) plus per-interface tags on the
DHCP options. There is **no per-interface enable flag**: DHCP is served on an interface the moment a
`dhcp_ranges` row names it, with `dnsmasq/enable=1`. So "enable DHCP on this interface" means "write its
range row and add the interface to `dnsmasq/interface`".

**Range items** (`dhcp:<ifkey>`, translator `dhcp_range`).

Discovery: one per `pf:dhcpd/<ifkey>` **with `enable` present** (a disabled subnet is not listed, as
before). Deps: `iface:<ifkey>`; the interface needs a static IPv4 address, else UNSUPPORTED "Dnsmasq
needs a static interface address".

Render (per item) → **first REMOVE** `opn:dnsmasq/dhcp_ranges[interface='<k>']` (the base's
default row for this interface; the entry element is named `dhcp_ranges`, a direct child of `dnsmasq` —
**not** nested under a `dhcp_ranges` wrapper), **then APPEND** one `<dhcp_ranges>` element per range
under `opn:dnsmasq` (uuid = `ctx.uuid("dhcp:<k>","r<n>")`). The REMOVE is the item's *first* placement so
the writer (§10.1) applies it before the APPENDs; it **inverts** the old Kea rule — a fresh 26.7 sample
ships a default LAN row, so it must be cleared or LAN ends up with two ranges. It **cannot** live in
`finalize`: the writer applies `render` before `finalize`, so a `finalize` REMOVE would delete the rows
the section had just appended.

| field on the `<dhcp_ranges uuid>` entry | value |
|---|---|
| `interface` | mapped iface key (excluded → UNSUPPORTED) |
| `start_addr` | main row: pf `range/from`; each `pool[]/range` row: its own `from`. A subnet with **no** `range`/`pool` (static maps only) is written `mode=static` with `start_addr` = the interface's network address (bare, no mask — `RangeAddressField` accepts it; §2.1) |
| `end_addr` | pf `range/to` (main) / `pool/range/to` (pool); empty for a static-only subnet |
| `subnet_mask` | empty — OPNsense auto-calculates it from the interface |
| `mode` | `static` **only** when the subnet has no dynamic range (no `range` and no `pool`); omitted otherwise. `denyunknown` never sets `static` on its own — see the warnings below |
| `lease_time` | pf `defaultleasetime` if non-empty, else omitted (dnsmasq default 86400; `0` = infinite) |
| `domain` | pf `domain` if non-empty |
| `domain_type` | `interface` (so the domain also covers the interface's other ranges and its static reservations — the docs recommend this, and `range` requires a full `end_addr`) |
| `description` | `"pf2opn: " + iface descr` |

`pool[]/range` entries produce additional `dhcp_ranges` rows (same `interface`, no `domain`).

**Static-map items** (`dhcpres:<ifkey>:<mac>`, translator `dhcp_static`).

One per `pf:dhcpd/<ifkey>/staticmap`; the id de-dups on `ifkey`+`mac` (a MAC listed twice is one item).
`ipaddr` empty → UNSUPPORTED "Dnsmasq reservation needs an IP address"; `ipaddr` outside the interface
subnet → UNSUPPORTED.

Render → **APPEND** a `<hosts>` element under `opn:dnsmasq` (uuid = `ctx.uuid("dhcpres:<k>:<mac>")`; the
entry element is named `hosts`, a direct child — **not** nested under a `hosts` wrapper):

| field on the `<hosts uuid>` entry | value |
|---|---|
| `hwaddr` | pf `mac` (lower-case) |
| `client_id` | pf `cid` if non-empty |
| `ip` | pf `ipaddr` (single IPv4 — the model allows only one address per reservation) |
| `host` | pf `hostname` (may be empty; dnsmasq allows a reservation with only `ip`+`hwaddr`) |
| `domain` | pf `domain` if non-empty (one row is both the DHCP reservation and a static host override) |
| `descr` | `pf_desc(descr)` |
| `comments`,`local`,`cnames`,`lease_time`,`ignore`,`aliases` | empty / `0` |

Reservation semantics match pfSense: dnsmasq reserves that IP for that MAC and never offers it to a
dynamic client. And because `hosts` is also dnsmasq's DNS host table, the name is registered for free —
this is what replaces the Kea-era D6 synthesis (§12.1.13).

**Subnet options: global vs per-range vs per-interface/per-host.**

Dnsmasq has no "subnet options" block, so a pfSense per-subnet option lands at the lowest level that can
express it: **global** (`dnsmasq/dhcp/*` — really only the default/FQDN domain, which falls back to
`system/domain`), **per-range** (`dhcp_ranges.*`), or **per-interface / per-host** via a dnsmasq *tag*
(`dhcp_options`, `dhcp_boot`). Dnsmasq sets an interface tag automatically on every request received on
that interface, so `interface:<k>` means exactly "clients on this subnet"; a *named* tag (`dhcp_tags` +
`set_tag`) is used only for options that one static reservation needs.

| pf `dhcpd/<k>/…` | Dnsmasq target | level |
|---|---|---|
| `gateway` | `dhcp_options` `{type:set, option:"3", interface:<k>, value:<gw>}` (router) | per-interface |
| `dnsserver[]` | `dhcp_options` `{type:set, option:"6", interface:<k>, value:<comma-joined>}` | per-interface |
| `domain` | `dhcp_ranges.domain` + `domain_type=interface`, **and** `dhcp_options` `{option:"15", interface:<k>, value:<domain>}` | per-range + per-interface |
| `domainsearchlist` | `dhcp_options` `{option:"119", interface:<k>, value:<`;`→`,`>}` — **PARTIAL**: dnsmasq wants an RFC 1035-encoded search list | per-interface |
| `ntpserver[]` | `dhcp_options` `{option:"42", interface:<k>, value:<comma-joined>}` | per-interface |
| `tftp` | `dhcp_boot.servername` (option 66) | per-interface |
| `filename` | `dhcp_boot.filename` (option 67), `interface:<k>` | per-interface |
| `nextserver` | `dhcp_boot.address` (siaddr) | per-interface |
| `defaultleasetime` | `dhcp_ranges.lease_time` | per-range |
| `maxleasetime` | – | **PARTIAL**: dnsmasq has one lease time per range; the model's nearest field, `dhcp/lease_max`, is a *lease count*. Warning: "max lease time not represented" |

`dhcp_options.option` holds the **numeric IANA code as a string** (the model fills it from
`iana/dhcpv4-options.csv`; the config template renders `dhcp-option=…,<option>,<value>`). `nextserver`
**or `tftp`** without a `filename` cannot use `dhcp_boot` (`filename` is Required) → PARTIAL ("… without
a boot filename cannot use dhcp_boot").

Per-host options (a `staticmap` with its own `dnsserver`/`gateway`/`domain`/`filename`/`nextserver` — the
`dhcp.xml` fixture's third case): create a `dhcp_tags` row (tag name `pf2opn_<ifkey>_<mac-slug>`, unique),
set it via `hosts.set_tag`, and attach the same `dhcp_options`/`dhcp_boot` rows keyed by `tag` instead of
`interface`. No per-host options → no tag is created.

> **`tag`/`set_tag` values are the `dhcp_tags` row's uuid, not the tag name.** `hosts.set_tag`,
> `dhcp_options.tag` and `dhcp_boot.tag` are `ModelRelationField`s into `dhcp_tags`, keyed by uuid. Every
> such placement written for this item uses the row's uuid `ctx.uuid(item.id, "tag")` (the same uuid the
> `dhcp_tags` row itself carries) — never the tag text. §13 check 8 compares these fields against the
> `dhcp_tags` row **uuids**.

Warnings (PARTIAL) when non-empty: `denyunknown` **with a dynamic range** ("pfSense deny-unknown has no
Dnsmasq equivalent; the dynamic range is still served"), `failover_peerip` ("dnsmasq HA uses split pools
+ `dhcp/reply_delay`, not a failover peer"), `mac_allow`/`mac_deny` ("per-range MAC ACLs not represented;
use tags"), `ddnsdomain*` ("RFC 2136 dynamic DNS updates are not supported by dnsmasq"),
`numberoptions/item` ("custom DHCP options not migrated (Tier 2)"), `staticarp`, `ldap`, `rootpath`,
`filename32/64/*arm`, `uefihttpboot`, `ignoreclientuids`, and `maxleasetime` as above.

**finalize (range translator).**

* (The base's default `dhcp_ranges` row for an interface is cleared by the **range-item render step**
  above, not here: the writer applies `render` before `finalize`, so a REMOVE in `finalize` would delete
  the rows the section had just appended.)
* SET `opn:dnsmasq/enable` = `1`.
* SET `opn:dnsmasq/dhcp/fqdn` = `1` (register qualified lease names, matching pfSense's
  `regdhcp`-with-domain behaviour).
* SET `opn:dnsmasq/interface` = the migrated keys unioned with the base's existing list. OPNsense only
  registers the DHCP firewall rules for interfaces named here, so this is required for DHCP to answer.
* **Unbound glue (this replaces D6).** If Unbound is enabled in the output
  (`OPNsense/unboundplus/general/enabled=1`): if `dnsmasq/port` is empty or equals Unbound's port, SET it
  to `53053`; then for each distinct migrated DHCP domain D, APPEND `OPNsense/unboundplus/dots/dot`
  `{enabled:1, type:forward, domain:D, server:"127.0.0.1", port:<dnsmasq port>, forward_tcp_upstream:0,
  forward_first:0, description:"pf2opn: local DHCP domain via Dnsmasq"}` (skip if an identical forward
  exists). This is the OPNsense-documented Unbound+Dnsmasq combo and is what makes **dynamic** lease
  names resolve. Followup: add the matching `in-addr.arpa` reverse forward per subnet.
* If Unbound is **not** enabled in the output, SET `opn:dnsmasq/port` = `53` (Dnsmasq is the resolver)
  and emit no forwards.
* If the base has Kea DHCP enabled, add a warning: dnsmasq will not start while Kea holds the DHCP ports.

**Knock-on: §12.1.13 `regdhcp`/`regdhcpstatic`, and D6.**

With Kea nothing fed DNS, so we synthesized Unbound host overrides. With Dnsmasq it inverts — Dnsmasq
registers names itself:

* `unbound/regdhcp` → `OPNsense/unboundplus/general/regdhcp` stays **`0`**, with a corrected warning:
  "Unbound's DHCP registration reads ISC DHCP leases (`/var/dhcpd/var/db/dhcpd.leases`); this box has
  none. Dnsmasq registers lease names — reach them via the local-domain query-forwarding the DHCP
  translator writes."
* `unbound/regdhcpstatic` → same: **`0`**, because Unbound's static registration reads the ISC/Kea
  `static_mapping` plugin hook, which Dnsmasq does not provide.
* **D6 is demoted to a fallback, default off.** `dns_static_host` is still discovered, but
  `default_selected=False` with the note "redundant while Dnsmasq serves DNS for this domain (the
  default); enable only if Dnsmasq DNS is disabled". In the default path the Dnsmasq `hosts` rows plus
  the Unbound forward cover both static and dynamic names.

**Resolved: the static-only `start_addr` (was an open item).**

A pfSense subnet with `enable` but **no** `range`/`pool` (static reservations only) has no dynamic range.
Dnsmasq still needs a `dhcp_ranges` row to serve DHCP on that interface (`mode=static`), and `start_addr`
is Required. The design uses the interface's network address (e.g. `192.168.1.0`, written bare — no
mask). Verified against 26.7.8: `RangeAddressField extends NetworkField` with `NetMaskAllowed=N`, so
`isValidInput` accepts a bare network address (`192.168.1.0`) and rejects a `/mask` (§2.1). Keep the
network-address form.

#### 12.1.12 Virtual IPs (`vips.py`, base_policy `merge` by `interface+subnet`, container `virtualip`)
`pf:virtualip/vip[]` → `opn:virtualip/vip[]`: `mode` (`ipalias`, `carp`, `proxyarp`; `other`
→ UNSUPPORTED), `interface` (mapped key, or `lo0` as-is), `subnet`, `subnet_bits`,
`descr`→`descr`, `noexpand` presence → `1`/`0`, `nobind` `0`, CARP: `vhid`, `advbase`,
`advskew`, `password` (secret), and `nosync` `0`. Drop `uniqid`/`type`.
* `descr` contains `DNSBL` → `default_selected=False`, warning "pfBlockerNG DNSBL VIP; not
  needed on OPNsense".
* CARP → PARTIAL "CARP needs HA setup on both nodes".

#### 12.1.13 Unbound (`unbound.py`)
**`dns:general`** (translator `dns_general`, base_policy `none`). SET under `opn:OPNsense/unboundplus/`:

| pf `unbound/…` | opn | transform |
|---|---|---|
| `enable` | `general/enabled` | presence → `1` |
| `port` | `general/port` | empty → `53` |
| `active_interface` | `general/active_interface` | comma list; `all` → empty; each key mapped; `lo0`/`localhost` kept as `lo0`; excluded keys are dropped with a warning |
| `outgoing_interface` | `general/outgoing_interface` | same |
| `dnssec` | `general/dnssec` | presence → `1`/`0` |
| `regdhcp` | `general/regdhcp` | `0` + warning "Unbound's DHCP registration reads ISC leases, which this box has none of; Dnsmasq registers the lease names — reach them via the local-domain query-forwarding the DHCP translator writes (§12.1.11)" |
| `regdhcpstatic` | `general/regdhcpstatic` | `0` (the static maps live in Dnsmasq `hosts` and are served by Dnsmasq's own DNS; the old synthesis, D6, is now a fallback — §12.1.11) |
| `system_domain_local_zone_type` | `general/local_zone_type` | copy (`transparent` default) |
| `hideidentity`,`hideversion`,`dnssecstripped`,`prefetch`,`prefetchkey`,`qname-minimisation-strict`→`qnameminstrict`,`serveexpired` | `advanced/<same>` | presence → `1`/`0` |
| `msgcachesize`,`outgoing_num_tcp`→`outgoingnumtcp`,`incoming_num_tcp`→`incomingnumtcp`,`cache_max_ttl`→`cachemaxttl`,`cache_min_ttl`→`cacheminttl` | `advanced/…` | copy if non-empty |
| `forwarding` (presence) and **not** `forward_tls_upstream` | `forwarding/enabled` | `1` |
| `forwarding` **and** `forward_tls_upstream` | `dots/dot[]` | for each **non-loopback/self** `pf:system/dnsserver[i]` (see the note below the table): APPEND `dot` (uuid `ctx.uuid("dns:general","dot<i>")`) with `enabled=1`, `type=dot`, `domain` empty, `server`=IP, `port`=`853`, `verify`=`pf:system/dns{i+1}host` (empty allowed), `forward_tcp_upstream=0`, `forward_first=0`, `description`="pf2opn DoT upstream". Also SET `forwarding/enabled`=`0` |
| `custom_options` | – | warning "custom options present (<n> lines) not migrated"; followup "re-create in /usr/local/etc/unbound.opnsense.d/*.conf" (never print content) |
| `enablessl`,`sslcertref`,`tlsport` | – | warning "DNS-over-TLS listener not migrated" |

> **Loopback/self upstreams are skipped.** pfSense's `system/dnsserver` may list the box's own
> address (`127.0.0.1` and/or one of its interface IPs) because pfSense's own Unbound is a
> configured resolver — the real config's list begins with `127.0.0.1`. Rendering that as an
> OPNsense upstream (`dot` `server=127.0.0.1 port=853`, or a plain forward to itself) points at
> nothing that listens, so it is meaningless and only adds log noise. When turning a
> `system/dnsserver` value into an Unbound upstream — the `dots/dot[]` DoT list above, and the
> plain `forwarding/enabled` path that relies on the system nameservers — **skip every
> loopback/self address**: `127.0.0.0/8`, `::1`, the unspecified `0.0.0.0`/`::`, and any address
> that is one of the source box's own interface addresses. A `domainoverrides` target that is
> loopback/self is skipped the same way. Emit one info warning counting how many were skipped.
> If **every** upstream is skipped, do not leave a `dot`/forward list with no servers: keep
> `forwarding/enabled`=`0` (Unbound falls back to recursive resolution via root hints) and add a
> followup to set the real upstream by hand (Services ▸ Unbound ▸ Query Forwarding /
> DNS-over-TLS). The all-skipped case is not an error — it is the same box that has no external
> forwarder configured.

**Host overrides** (`dnshost:<host>.<domain>`, translator `dns_host`, base_policy `merge` by
hostname+domain).

> **pfSense XML quirk (must not be misread):** pfSense repeats the *plural* container once per entry —
> `unbound` has 3 children literally named `<hosts>` in the real config when there are 3 host overrides (47
> of them there). So discovery is `for h in unbound.findall('hosts')`, **not**
> `unbound.find('hosts').findall('host')`. Same for `<acls>` (one element per ACL). Each `<hosts>` element is
> one override with `host`, `domain`, `ip`, `descr`, optional `aliases/item`.

`pf:unbound/hosts[]` → APPEND `opn:OPNsense/unboundplus/hosts/host`:
`enabled=1`, `hostname`←`host`, `domain`←`domain`, `rr`=`AAAA` if `ip` is IPv6 else `A`,
`server`←`ip`, `description`←`pf_desc(descr)`, `addptr=1`, plus empty `mxprio`, `mx`, `ttl`,
`txtdata`. A pfSense `ip` holding several comma-separated IPs → one `host` per IP (suffix
`/ip<n>` in the uuid) + warning. For each `aliases/item` → APPEND
`opn:OPNsense/unboundplus/aliases/alias`: `enabled=1`, `host`=uuid of the first host,
`hostname`←**the `<aliases/item>` text** (the alias's own name, not the parent `host` field),
`domain`←`domain`, `description`←`description`. (`aliases/alias/host` is the `ModelRelationField`
into `hosts/host`; `hostname` is the alias name.)

**Static-map hosts** (`dnsstatic:<k>:<mac>`, translator `dns_static_host`) — **fallback, D6**.
Discovered when pfSense `unbound/regdhcpstatic` is present *or* any staticmap carries a hostname, but with
`default_selected=False`: with Dnsmasq as the DHCP backend the names are already served by Dnsmasq's own
DNS and reachable via the local-domain query-forward (§12.1.11), so this only matters if Dnsmasq DNS is
disabled. One per DHCP staticmap with a non-empty `hostname` and `ipaddr`. Domain = staticmap `domain`,
else the dhcpd interface `domain`, else `system/domain`. Deps: `dhcpres:<k>:<mac>`. If a `dns_host` item
with the same host+domain exists → UNSUPPORTED "duplicate of explicit host override". Render: like a host
override with description `"from DHCP static map"` plus warning "fallback path: enable only if Dnsmasq
DNS is disabled".

**Domain overrides** (`dnsdom:<domain>`, translator `dns_domain`). `pf:unbound/domainoverrides[]` →
APPEND `opn:OPNsense/unboundplus/dots/dot`: `enabled=1`, `type=forward`, `domain`, `server`
= `ip` before `@`, `port` = after `@` or empty, `verify`←`tls_hostname`,
`forward_tcp_upstream=0`, `forward_first=0`, `description`←`pf_desc(descr)`. If `forward_tls_upstream` is
set for the override (`tls_hostname` present) → `type=dot`.

**ACLs** (`dnsacl:<aclid>`, translator `dns_acl`). `pf:unbound/acls[]` (see the quirk above — one `<acls>`
element per ACL) → APPEND `opn:OPNsense/unboundplus/acls/acl`: `enabled=1`, `name` = `sanitize_name(aclname)`,
`action` map {`allow`→`allow`, `deny`→`deny`, `refuse`→`refuse`, `allow snoop`→`allow_snoop`,
`deny nonlocal`→`deny_non_local`, `refuse nonlocal`→`refuse_non_local`}, `networks` = the
comma-join of `row/acl_network + "/" + row/mask`, `description`←`pf_desc(description)`. The per-row
`row/description` is dropped (one warning per ACL if any row description is non-empty).
`aclname` contains `DO NOT EDIT` in its description (package-made) → `default_selected=False`.

#### 12.1.14 Trust (`trust.py`, base_policy `merge` by refid)
`pf:ca[]` → `opn:ca[]` (uuid attr): `refid`, `descr`←`pf_desc`, `crt`, `prv` (if present),
`serial` (empty → `0`), `caref` (intermediate). Drop `trust`, `randomserial`. Deps:
`ca:<caref>` if present.
`pf:cert[]` → `opn:cert[]`: `refid`, `descr`, `caref` (dep `ca:<caref>`), `crt`, `prv`,
`csr`. Drop `type`.
Validation in discover: base64-decode `crt`. It must contain **exactly one**
`-----BEGIN CERTIFICATE-----`. If there are more → PARTIAL, keep the first block
(re-base64), warning "chain trimmed to leaf (OPNsense 26.7.8 single-cert rule)". If it doesn't
decode → UNSUPPORTED. Certs whose description contains `webConfigurator default` →
`default_selected=False`, because the OPNsense box has its own.

#### 12.1.15 Users and groups (`users.py`, base_policy `merge` by name)
Users: `pf:system/user[]` with `scope` == `user` → APPEND `opn:system/user` (uuid attr):

| pf | opn | transform |
|---|---|---|
| `name` | `name` | – |
| `descr` | `descr` | `pf_desc` |
| – | `scope` | `user` |
| `disabled` | `disabled` | presence → `1` else `0` |
| `expires` | `expires` | copy |
| `bcrypt-hash` | `password` | copy (bcrypt `$2y$` is accepted by 26.7). `sha512-hash` only → UNSUPPORTED "unsupported hash type; reset password after import" |
| `authorizedkeys` | `authorizedkeys` | copy (both base64) |
| `uid` | `uid` | copy if not already used in base, else `max(base uids, nextuid)` + warning |
| `priv[]` | `priv` | comma-joined, kept only if in `KNOWN_PRIVS = {"page-all","user-shell-access"}`; others dropped with warning listing the names |
| `ipsecpsk`, `webguicss`, `dashboardcolumns`, `cert[]` | – | dropped (warning only for `ipsecpsk`/`cert` if non-empty) |

`scope` == `system` (pfSense `admin`) → not an item (see `system:root_password`).
Groups: `pf:system/group[]` with `scope` != `system` → APPEND `opn:system/group`: `name`,
`description`, `scope=user`, `gid`, `member` = comma-join of the member uids whose users are
selected (or `0`), `priv` filtered as above. For system groups, `admins` with extra members →
an item `group:admins` (merge) that only adds selected member uids to the base admins
`member` list. `all` → ignored.

`gid` follows the **same rule as `uid`** (do not copy it verbatim): the model's
`Auth/FieldTypes/GidField.php` auto-allocates a GID from 2000 only when the value is empty and
applies **no uniqueness check**, so a copied `gid` can silently collide with a base group. Keep the
pfSense `gid` if it isn't already used in the base; otherwise assign `max(base gids, nextgid)` (then
the next free number above it) and warn `"gid N already used in the base; assigned M"`.
`finalize`: SET `system/nextuid` = max(existing, max uid + 1) and `system/nextgid` =
max(existing, max gid + 1).

#### 12.1.16 Cron (`cron.py`, tier 1 classifier, nothing rendered)
Every `pf:cron/item` → UNSUPPORTED item, label `"<minute> <hour> <mday> <month> <wday> <command[:60]>"`, reason from the first matching rule:

| command contains | reason |
|---|---|
| `adjkerntz`, `rc.periodic`, `newsyslog`, `rc.update_bogons`, `rc.dyndns.update`, `expiretable`, `rc.update_urltables`, `rc.update_pkg_metadata` | "pfSense system job; OPNsense has its own built-in equivalent" |
| `pfblockerng` | "pfBlockerNG job; not applicable" |
| `acme` | "ACME renewals are handled by os-acme-client" |
| `servicewatchdog` | "use Monit (Services ▸ Monit)" |
| (anything else) | "OPNsense cron only runs configd actions; wrap the command in an action under /usr/local/opnsense/service/conf/actions.d/ and add a cron job" + followup |

#### 12.1.17 WireGuard (`wireguard.py`)
Source: `pf:installedpackages/wireguard/{tunnels,peers}/item[]`.

**Tunnels** (`wgtun:<name>`, translator `wg_tunnel`, base_policy `merge`, container
`OPNsense/wireguard/server/servers`, **identity = the output `instance`**).

Output-instance assignment, in document order:
1. `N` = the integer from `tun_wgN` (the pfSense `name` field is the *device* name); `0` when it
   doesn't match the pattern. `ifmap.wg_names[name] = f"wg{instance}"` uses the final value.
2. `used` = the `instance` of every base server **except** one this migration replaces. A base
   server is "this tunnel" when its `name` equals the tunnel's output friendly name — the same
   deterministic value a previous pf2opn run wrote. Such a server's `instance` is **reused**, so
   re-running against pf2opn's own output is a no-op instead of allocating fresh instances each time.
3. If `N` ∉ `used`, `instance = N`. Otherwise `instance` = the next free number
   (≥ `max(used ∪ {N}) + 1`), with warning `"instance N already used in the base; assigned M"`.
   **The colliding base tunnel is kept** — it is a different tunnel and is not touched.

Because the merge identity is the *output* instance and that value is chosen to differ from every
unrelated base instance, `apply_base_policy` can only remove a base server this run is replacing (a
prior pf2opn output of the same tunnel); an unrelated base tunnel survives. Render → APPEND
`opn:OPNsense/wireguard/server/servers/server`:

| opn | value |
|---|---|
| `enabled` | `1` if pf `enabled`=`yes` else `0` |
| `name` | `sanitize_name(descr or name)`, unique **within the migration only** (suffix `_2`…; the running set is never seeded from base names, so a name that also exists in the base is the entry being replaced, not a reason to rename). **In pfSense the `name` field holds the *device* name (`tun_wg0`, `tun_wg1`, one per tunnel), so the friendly name must come from `descr`** (ProtonVPN / Smartass Wireguard in the real config). |
| `instance` | the final output instance from the assignment above (usually `N`, reassigned on a base collision). This is also the merge identity. |
| `pubkey` / `privkey` | `publickey` / `privatekey` |
| `port` | `listenport` |
| `mtu` | `mtu` |
| `dns` | empty |
| `tunneladdress` | comma-join of `addresses/row` `address/mask`; a row with an empty `mask` means the host mask — `/32` (IPv4) or `/128` (IPv6). **If empty**, use the pfSense interface assigned to this tunnel (`interfaces/*/if == name`): `ipaddr/subnet` |
| `disableroutes` | `1` if any peer of this tunnel has `0.0.0.0/0` or `::/0` among its **row-parsed** allowed-IP CIDRs (a `allowedips/row` with `address=0.0.0.0`/`mask=0` → `0.0.0.0/0`; see the peer note below), else `0` |
| `gateway` | when `disableroutes=1`: the `gateway` IP of the first pf gateway_item whose interface is this tunnel's assigned key; else empty |
| `peers` | comma-join of `ctx.uuid("wgpeer:<i>")` for selected peers with `tun == name` |
| `carp_depend_on`,`debug`,`endpoint`,`peer_dns` | empty / `0` |

Warnings: when `disableroutes=1` → "routes for peer allowed IPs disabled (a peer has a
default route); add static routes for remote subnets: <list of the row-parsed, non-tunnel allowed-IP
CIDRs>". If the tunnel isn't assigned to an interface → note "unassigned tunnel".
`finalize`: SET `OPNsense/wireguard/general/enabled` = `1`.

**Peers** (`wgpeer:<i>`, translator `wg_peer`, base_policy `merge`, **identity = the peer's
assigned output `name`**). `tun` = `unassigned` or a tunnel not in the source → UNSUPPORTED "peer
not attached to a tunnel". Deps: `wgtun:<tun>`. The output `name` is `sanitize_name(descr)` (or
`peer<index>` when the descr is empty), uniquified **within this migration only**: the running set
starts empty and is never seeded from base client names. So:
* two peers **in the same migration** whose `descr` → the same name get `_2`, `_3`… in peer-index
  order, which is deterministic, so a re-run assigns the same suffixes;
* a name that also exists in the base is a **merge-replace**, not a rename: the base client with
  that name is removed by `apply_base_policy` (the identity is that name) and re-added under it.

This is the rule that makes re-runs idempotent. The identity must be the assigned name, and name
uniqueness must ignore the base; deduping against the base and then using the deduped name as the
identity can never match the base peer, so the base entry is never replaced and every re-run appends
another copy. Render → APPEND `opn:OPNsense/wireguard/client/clients/client`
(uuid = `ctx.uuid("wgpeer:<i>")`): `enabled` (`yes`→`1`), `name` = the assigned name,
`pubkey`←`publickey`, `psk`←`presharedkey`, `keepalive`←`persistentkeepalive`, `privkey` empty
(pfSense peers do not store the remote private key), `servers` = comma list containing the server
uuid when the peer's tunnel is migrated.

**`allowedips` is a `row` list, not text** — the same shape as a tunnel's `addresses`:

```xml
<allowedips>
  <row><address>10.0.5.15</address><mask>32</mask><descr/></row>
</allowedips>
```

Parse every `allowedips/row` into the CIDR string `address/mask` — exactly as a tunnel's
`addresses/row` are parsed (the `tunneladdress` row of the tunnel table above) and as an Unbound
ACL's `row/acl_network`+`row/mask` are (§12.1.13). **A row whose `mask` is empty takes the host
mask for its address family: `/32` for an IPv4 `address`, `/128` for an IPv6 one.** Do **not** read
`allowedips` as whitespace/newline-separated text: a real `<allowedips>` element carries no text of
its own, so splitting its text yields an empty list and *every* peer is wrongly reported "no allowed
IPs" — the exact failure a text-shaped synthetic fixture hid (§16.1).

`tunneladdress` — **the one genuinely lossy mapping in this translator, so decide it explicitly:**

pfSense peer `allowedips` are the *server's view of the addresses behind the peer*, which is **not**
the same thing as OPNsense's client `tunneladdress` (the address that peer holds on the tunnel).
The real config shows all three shapes, so the rule is:

1. `server_net` = the network of the peer's tunnel's `tunneladdress` (e.g. `10.0.5.1/24` → `10.0.5.0/24`).
2. `tunnel_ips` = the row-parsed allowed-IP CIDRs that are host addresses (`/32` or `/128`) **and** fall inside `server_net`.
3. `remote_nets` = all other row-parsed allowed-IP CIDRs.

* If `tunnel_ips` is non-empty → `tunneladdress` = those (comma-joined), status OK.
* If `tunnel_ips` is empty but `remote_nets` is non-empty → `tunneladdress` = the first `remote_nets`
  entry verbatim (so nothing is silently invented), status **PARTIAL**, warning "peer has no host address
  inside the tunnel subnet; set its tunnel address on the OPNsense side (site-to-site peer)", followup
  naming the peer.
* If both are empty → UNSUPPORTED "peer has no allowed IPs".

Whenever `remote_nets` is non-empty, add a warning "peer routes <remote_nets>; add these as static routes or
in the peer's Allowed IPs" and a followup. `0.0.0.0/0`/`::/0` in `remote_nets` is not itself an error (it is
the "route everything" peer) but must be listed. Both come from a row `address=0.0.0.0`+`mask=0`
(`::`+`mask=0`), which the row parser renders **with** its mask — never as a bare `0.0.0.0` — so the
`0.0.0.0/0` detection in `disableroutes` and here compares against the parsed CIDR, not raw text.

`serveraddress`/`serverport` are always left empty: pfSense peers carry **no `endpoint`/`port`**
(`installedpackages/wireguard/peers/item` has only `allowedips`, `enabled`, `tun`, `descr`,
`persistentkeepalive`, `publickey`, `presharedkey`). OPNsense stores this as a server-side peer — a client
entry referenced from the server's `peers` list — and does not need an endpoint. For roaming peers that were
road-warrior configs, the endpoint must be filled in by hand; the report says so.

Real config ground truth for the fixtures and for M10 (counted during the design pass):

| tunnel | own addresses | peers |
|---|---|---|
| `tun_wg1` (descr *Smartass Wireguard*, server) | none → uses `opt10` = `10.0.5.1/24` | 8 — six hosts `10.0.5.10–.15,.21`, one `0.0.0.0/0` only (*GL.iNet Slate Router* → PARTIAL per rule above), two with 192.168.8.0/24 remote nets |
| `tun_wg0` (descr *ProtonVPN*, client tunnel) | `10.2.0.2/32` | 1 (*ProtonVPN Connection*, `0.0.0.0/0`, keepalive 25 → PARTIAL) |
| *(unassigned)* | – | 1 (*Mullvad Wireguard*, `0.0.0.0/0`, keepalive 25 → UNSUPPORTED) |

So of the 10 peers: 6 OK, 3 PARTIAL, 1 UNSUPPORTED.

#### 12.1.18 Dynamic DNS (`dyndns.py`, requires_plugin `os-ddclient`, base_policy `merge`)
`pf:dyndnses/dyndns[]` → APPEND `opn:OPNsense/DynDNS/accounts/account`:
`enabled` (presence `enable`), `service` (`cloudflare`→`cloudflare`, `cloudflare-v6`→`cloudflare` + warning,
`dyndns`/`dyndns-static`/`dyndns-custom`→`dyndns2`, `custom`→`custom` with `server`←`updateurl` (PARTIAL),
others: if in `{"duckdns":"duckdns","noip":"noip","namecheap":"namecheap","he-net":"he-net","route53":"route53"}`
map, else UNSUPPORTED "provider <x> not mapped"), `protocol` empty, `username`, `password`,
`hostnames`: pfSense `host` is `dynamic` (the current WAN address), `@` (apex), or a literal hostname.
**`dynamic`, `@`, and empty all mean the apex → use `domainname` alone.** Only for any other value use
`host + "." + domainname`. (The current spec would have produced the wrong `dynamic.asmartass.me`.)
`zone`←`domainname`, `checkip`=`if`, `interface` = mapped `requestif` (or `interface`), `ttl` (empty →
`300`), `force_ssl=1`, `wildcard` (presence of `wildcard`), `description`←`pf_desc(descr)`.
`finalize`: SET `OPNsense/DynDNS/general/enabled`=`1`, `general/backend`=`opnsense`.
Followup: "install os-ddclient before importing, or the section stays inert until installed".

#### 12.1.19 Syslog (`syslog.py`, base_policy `merge`)
For N in `remoteserver`, `remoteserver2`, `remoteserver3` with text and `pf:syslog/enable`
present → APPEND `opn:OPNsense/Syslog/destinations/destination`: `enabled=1`, `transport` =
`udp6` if `ipproto`==`ipv6` else `udp4`, `level` empty, `facility` empty, `program` empty,
`rfc5424=0`, `description`=`"pf2opn remote syslog"`. The value is split into `hostname`/`port` by
**shape, not on the first `:`** — an IPv6 literal contains colons, so a naive split turns `::1`
into an empty hostname (`hostname=""`, `port=":1"`):
* `[v6]` or `[v6]:port` → `hostname` = the text inside the brackets (brackets dropped), `port` =
  the text after `]:`, else `514`.
* a bare IPv6 literal (two or more `:`, no brackets: `::1`, `2001:db8::514`) → the **whole value**
  is `hostname` and `port` = `514`. A port with IPv6 must be written bracketed; a bare literal is
  never split, because its last group cannot be told apart from a port.
* otherwise (hostname or IPv4; zero or one `:`) → `hostname` = before the `:` (or the whole value),
  `port` = after the `:` or `514`.
`hostname` is OPNsense's `HostnameField` (`IpAllowed` defaults to `true`), so the bare IPv6 literal
is written unbracketed into the config. pfSense-only toggles (`filterdescriptions`,
`logall`, per-category flags) → one info item `syslog:local` UNSUPPORTED "local log
settings differ; review System ▸ Settings ▸ Logging". For the real config this is the only
syslog item.

### 12.2 Interface mapping rules (`ifmap.py`)
```python
def classify(device: str, pf: etree._Element) -> str
    # vlan if device is a pf vlans/vlan/vlanif; "wireguard" if startswith "tun_wg";
    # "unsupported" for prefixes ovpn, ppp, pppoe, l2tp, lagg, bridge, gif, gre, tailscale, enc, lo;
    # else "physical"
def default_ifmap(src: SourceContext) -> IfaceMap
    # nic_map: physical pf NICs -> "" (unmapped) unless the base's interfaces/<same key>/if exists, then that NIC
    #   (e.g. pf wan=mlxen0, base wan=igc0 => mlxen0->igc0 suggestion; same for lan)
    # vlan_names: for each pf vlan in document order: "vlan0%d" % n, n = 1 + max existing vlan0<n> in base vlans
    # wg_names: tun_wgN -> wgN (collisions handled in wireguard discover; ifmap uses the final value)
def target_nics(src) -> list[str]
    # choices for the TUI: base interfaces/*/if that are physical, plus every NIC given via --map-nic, sorted
def validate_ifmap(m: IfaceMap) -> list[str]
    # errors: included physical iface unmapped; two included ifaces mapping to the same opn device;
    #         two pf NICs mapped to the same target NIC
```
Edge case: a pf VLAN whose parent NIC is unmapped → its vlanif has no device → the dependent
iface is UNSUPPORTED.

### 12.3 Tier 2 (later milestones; discover as UNSUPPORTED "planned (Tier 2)" in v1)
DHCPv6 and router advertisements (Dnsmasq DHCPv6 + `OPNsense/Radvd`). DHCP custom options
(`numberoptions` → `dnsmasq/dhcp_options`). NPt (`nat/npt` → `Filter/npt`). Gateway groups
(`/gateways/gateway_group+`). Firewall schedules. Interface groups (`ifgroups`). Tunables
(`sysctl/item[]`). SNMP (`os-net-snmp`). UPnP/miniupnpd (`os-upnp`) and the UDP broadcast relay
(`os-udpbroadcastrelay`) — both **stay Tier 2 for now** (Q6). Filter separators → categories. OpenVPN
instances. IPsec tunnels (Swanctl). LAGG/bridge/PPPoE/GIF/GRE devices. Static ARP.

mDNS/Avahi (`os-mdns-repeater`) has moved out of Tier 2: it is spec'd as **M15** (§17). Kea remains the
only plausible *alternative* DHCP backend — it is the right tool only for HA lease-sync or thousands of
leases — but it is **not** used here (D5). If it is ever added, it slots in as a second backend behind the
same `dhcp:<ifkey>` items.

### 12.4 Unsupported catalogue (`unsupported.py`; each is one item, always UNSUPPORTED)
Emit an item only if the section/package is present **and non-empty**. "Non-empty" means the
element has non-whitespace text of its own, **or** at least one child element that is itself
non-empty. A **present-but-empty child does not count**: `<pfblockerng/>`,
`<snmpd><syslocation/></snmpd>` and `<…><enable/></…>` are *not* non-empty and must not raise an
item. Detectors must test the *content* of the child, not merely its existence (e.g. `snmpd` with
`syslocation` text, not an empty `<syslocation/>`; a `pfblockerng` element with children).

| id | detected by | reason / alternative |
|---|---|---|
| `unsup:pfblockerng` | `installedpackages/pfblockerng*` | "pfBlockerNG has no OPNsense port. Use Unbound DNS blocklists (Services ▸ Unbound ▸ Blocklist) and GeoIP aliases" |
| `unsup:snort` | `installedpackages/snortglobal` | "use the built-in Suricata IDS/IPS (Services ▸ Intrusion Detection); rules and policies must be rebuilt" |
| `unsup:suricata` | `installedpackages/suricata` | "OPNsense Suricata uses a different config model; reconfigure" |
| `unsup:captiveportal` | `captiveportal/*` | "OPNsense captive portal uses a different zone/template model; rebuild" |
| `unsup:ntopng` | `installedpackages/ntopng` | "install os-ntopng and reconfigure" |
| `unsup:bandwidthd` | `installedpackages/bandwidthd` | "use Reporting ▸ Insight (NetFlow)" |
| `unsup:traffictotals` | `installedpackages/traffictotals` | "use Reporting ▸ Insight" |
| `unsup:miniupnpd` | `installedpackages/miniupnpd` | "install os-upnp and reconfigure (Tier 2 candidate)" |
| `unsup:telegraf` | `installedpackages/telegraf` | "install os-telegraf and reconfigure" |
| `unsup:acme` | `installedpackages/acme` | "install os-acme-client; re-create accounts and certificates (account keys not migrated)" |
| `unsup:pimd` | `installedpackages/pimd*` | "multicast routing: os-frr (PIM) or os-igmp-proxy" |
| `unsup:udpbroadcastrelay` | `installedpackages/udpbroadcastrelay` | "install os-udpbroadcastrelay (Tier 2 candidate)" |
| `unsup:freeradius` | `installedpackages/freeradius*` | "install os-freeradius and reconfigure; client secrets not migrated" |
| `unsup:frr` | `installedpackages/frr*` | "install os-frr 2.x and rebuild BGP config" |
| `unsup:tailscale` | `installedpackages/tailscale*` | "install os-tailscale and re-authenticate" |
| `unsup:avahi` | `installedpackages/avahi` | "install os-mdns-repeater; migrated as M15 (§17)" |
| `unsup:shellcmd` | `installedpackages/shellcmdsettings` or `system/earlyshellcmd`/`shellcmd` | "use /usr/local/etc/rc.syshook.d/ scripts" |
| `unsup:servicewatchdog` | `installedpackages/servicewatchdog` | "use Monit" |
| `unsup:openvpn_export` | `installedpackages/vpn_openvpn_export` | "built into OPNsense OpenVPN" |
| `unsup:l2tp` | `l2tp` with `mode` != `off` | "no L2TP server on OPNsense; use WireGuard or OpenVPN" |
| `unsup:ipsec` | `ipsec/phase1` **or `ipsec/mobilekey` or `ipsec/client`** exists (the real config has mobile clients, not tunnels — the old `phase1`-only detector would have missed it) | "IPsec (mobile clients / tunnels) not migrated in v1 (Tier 2)" |
| `unsup:openvpn` | `openvpn/openvpn-server` or `openvpn-client` | "OpenVPN not migrated in v1 (Tier 2)" |
| `unsup:shaper` | `shaper/queue` or `dnshaper/queue` | "traffic shaper models differ; rebuild" |
| `unsup:snmpd` | `snmpd` has any of `syslocation`, `syscontact`, `rocommunity`, `enable` (the real config has no `<enable>` — the old detector required it and would have missed the section) | "install os-net-snmp (Tier 2); community string not shown" |
| `unsup:dhcpdv6` | `dhcpdv6/*/enable` | "Tier 2: Dnsmasq DHCPv6 / router advertisements" |
| `unsup:sysctl` | `sysctl/item` | "Tier 2: review tunables manually (System ▸ Settings ▸ Tunables)" |
| `unsup:ifgroups` | `ifgroups/ifgroupentry` | "Tier 2; package-created groups (WireGuard/Tailscale) aren't needed" |
| `unsup:sshdata` | `sshdata` | "SSH host keys not migrated; OPNsense generates new ones" |

`installedpackages/package`, `menu`, `service` are ignored (they are package bookkeeping, not policy).

**Non-empty but ignored** (listed once in the report under "not applicable", not as unsupported items):
`ovpnserver` (5 children in the real config — OpenVPN-wizard leftovers; the actual `openvpn` section is empty,
so there is no live VPN to migrate), `diag`, `rrd`, `widgets`, `traffic_graphs`, `revision`, `lastchange`,
`dhcpbackend`, `proxyarp`, `wol`, `dnshaper`, `igmpproxy`, `ppps`, and `installedpackages/*` entries that
only carry `config`/`menu`/`service` stubs. The report gives a one-line reason per ignored section so nothing
is silently dropped.

**Data payloads that are ignored** (never copied, never printed — see §11): `dhcpd/dhcpddata`,
`dhcpdv6/dhcpdv6data` (base64 lease/state blobs), `sshdata` (host keys).

---

## 13. Validation (`validate.py`)

```python
@dataclass
class Finding: level: str  # "error" | "warning"
               message: str; path: str | None = None
def validate(out: etree._Element, plan: Plan) -> list[Finding]
```
Errors block writing (exit 1 / TUI review shows them, Write disabled):
1. **Well-formed round trip**: `etree.fromstring(etree.tostring(out))` succeeds; root tag is `opnsense`.
2. **Unique uuids** across the whole document.
3. **Interfaces**: every `interfaces/*/if` is non-empty and unique; every `if` that looks like
   `vlan0\d+` exists in `vlans/vlan/vlanif`; every `wg\d+` matches a server `instance`.
4. **VLAN parents**: every `vlans/vlan/if` equals some `interfaces/*/if` or is in the ifmap's target NIC list.
5. **Filter / SNAT / 1:1 / DNat references**: every interface token is an output interface
   key, `wireguard`/`openvpn`/`enc0`, or empty; every `*_net` / address token is `any`,
   `(self)`, IP/CIDR, `<key>`/`<key>ip` of an output interface, or an output alias name;
   every port token is numeric, a range, or a port-alias name; every filter `gateway` is an
   output gateway name. For `nat/rule` read the `source`/`destination` **endpoint elements
   themselves** and validate whichever address child is present — `network` **or** the
   volatile `address` (a single-host/alias endpoint renders as `<address>`, §12.1.8) — plus
   `target`. Reading `source/network` / `destination/network` alone silently skips every
   `<address>` endpoint; this is the same gap check 13 fixes (see below).
6. **Gateways**: names unique; `interface` exists in output interfaces.
7. **Static routes**: `gateway` exists.
8. **Dnsmasq DHCP**: every migrated `<dhcp_ranges>` entry's `interface` is an output interface key; no
   `<dhcp_ranges>` entry has `mode=static` together with a non-empty `end_addr` (`Dnsmasq.php` rejects that
   — "Static only accepts a starting address"); reservation IPs are unique and each falls inside **at
   least one** output interface subnet (a `<hosts>` entry carries no interface, so a per-reservation
   interface check isn't possible); every `<hosts>` `set_tag` / `<dhcp_options>` `tag` / `<dhcp_boot>`
   `tag` value equals the **uuid** of an existing `<dhcp_tags>` entry (compared against the entry uuids,
   not the tag text); every emitted `<dhcp_options>` entry has `type=set` (we never emit `match`). These
   are the repeated same-named siblings directly under `<dnsmasq>` (§2.1, §12.1.11), not children of a
   wrapper.
9. **WireGuard** (M10): each server `peers` uuid exists in clients; instances unique; names unique.
10. **Unbound**: each alias `host` uuid exists.
11. **Aliases**: names unique; nested alias entries exist.
12. **system/dns{N}gw**: the named gateway exists (M4).
13. **Address-family match on NAT rules**: `ipprotocol` must match the family of the rule's
   address-bearing fields.
   * `snatrules/rule`: `source_net`, `destination_net`, `target`.
   * `onetoone/rule`: **no-op** — the model's 1:1 rule has **no `ipprotocol` field** (it carries only
     `source_net`/`destination_net`/`external`, and `MFP1_0_4.php` emits no such field), so there is
     nothing to compare; check 13 does not apply to 1:1 and must not pretend to test it.
   * `nat/rule` (port forward): read the `source` and `destination` **endpoint elements themselves**,
     not just `source/network`/`destination/network`. A DNat endpoint stores its address in **either**
     `network` **or** `address` (`DNat.xml` marks `address` volatile; a host/alias port-forward endpoint
     renders as `<address>`), so the check must inspect both children — reading `network` alone silently
     misses every `<address>` endpoint. Also include `target`.
   An address that contains `:` requires `inet6`, a colon-free address requires `inet`; a keyword
   (`any`, `(self)`, `<key>`/`<key>ip`), an alias name, or an empty value imposes no constraint.
   This mirrors `Firewall/Filter.php::performValidation()`; a mismatch is a **model error**, and on
   OPNsense a lone `nat … inet from ::1/128` makes `pfctl` reject the whole `/tmp/rules.debug`
   ("rule expands to no valid combination" → 0 rules loaded).

Warnings (shown, not blocking): a base model version mismatch (§10.3); descriptions changed by
sanitizing; `nat/outbound/mode` automatic with snat rules present; pfSense `hybrid` outbound NAT
mapped to OPNsense `advanced` (§12.1.9).

---

## 14. Report (`report.py`)

`render_report(plan, findings, src, out_path) -> str` (Markdown). Sections in order:
1. Header: pf2opn version, date, source hostname + pfSense `<version>`, base hostname,
   output path, SHA-256 of source and output.
2. **Interface map** table: pf key | pf descr | pf device → OPN device | included.
3. **Summary** table per section: migrated / skipped by user / not translatable.
4. **Migrated** per section: `- label` + indented warnings.
4b. **Settings & notes** per section: `- label` + indented warnings, for selected items that emit
    no config object (see the counting convention below). These are not migrated rules.

**Counting convention — "migrated" counts emitted objects, not items.** The summary's *migrated*
column counts **items that emit at least one config object**: selected OK/PARTIAL items that
produce config, not note/settings/classifier items that produce none. A non-emitting item is
marked `Item.emits=False` (§6); today those are `filter:separator` ("Firewall rule separators")
and `snat:mode` ("Outbound NAT mode: …"). They are excluded from the migrated count **and** from
the per-section *Migrated* list, so the reference config reads **Firewall rules 73** and
**Outbound NAT 36** (matching the emitted `<rule>` objects) instead of the old item counts
(74 / 37, which read as one rule of data loss). Selected non-emitting items are listed under the
**Settings & notes** block (item 4b) so nothing is silently dropped. This keeps the count
implementable from the `Plan` alone — `_section_counts` counts `item.emits and item.id in
plan.selected` for OK/PARTIAL, with no output-tree access and no `render()` call at report time.
Skipped-by-user and not-translatable counts are unchanged; a non-emitting item that is skipped is
simply not counted.

5. **Skipped by user** per section: `- label`.
6. **Not translatable**: `- label — reason`.
7. **Plugins to install first**: the unique `requires_plugin` of selected items.
8. **Manual follow-up checklist**: `- [ ]` lines. These are the de-duplicated `followups`
   of all items, plus these fixed lines:
   * "Import via System ▸ Configuration ▸ Backups ▸ Restore (whole config), then reboot."
   * "Verify interface assignments on the console before connecting WAN."
   * "SSH host keys and web GUI certificate fingerprints will differ."
   * "Dynamic DHCP leases are not migrated; clients will re-request."
9. **Validation findings** (warnings).

The report never includes XML or field values from SECRET_TAGS (§11).

**File mode (deliberate):** the report is written with the process's normal mode — `0644` under the
default umask, via `Path.write_text` — and **not** the `0600` used for the output config. This is
intentional, not an oversight: by construction the Markdown holds only labels, reasons, warnings,
counts, paths and hashes, so it carries no secrets and a world-readable mode lets a reviewer or a
diff tool read it without the config's permissions. If a secret value could reach the report, that
would already be a §11 rule-3 violation, independent of the file mode.

---

## 15. TUI (`tui/`)

`Pf2OpnApp(App)` gets `(args)` plus an **optional** preloaded `src`. `pf2opn tui` with no paths
constructs the app without a context and the Load screen builds it on continue; `pf2opn tui SOURCE
--base BASE -o OUT` preloads the context as today and the Load screen starts in the loaded state.
The app's `output_path`/`report_path` come from the **collected** output after the Load screen runs
(`report_path` = `--report` if given, else the collected output with `.report.md`); the Review
screen writes there. Screen flow: `LoadScreen → IfaceMapScreen → SelectScreen → ReviewScreen →
DoneScreen`. Esc/`b` goes back one screen. `q` quits from anywhere (confirm modal if the selection
changed); quitting from the Load screen without writing exits 0.

**LoadScreen**: collects/confirms the three paths, then summarises them.

Three text `Input`s, in order — **pfSense source**, **OPNsense base**, **output** — each pre-filled
from the matching `tui` flag (`SOURCE`, `--base`, `-o`) when given, else empty. The value collected
here is what the run uses (a flag pre-fills; an edit supersedes it). Simple `Input`s, **not** a
filesystem browser: Textual has no built-in file dialog and a custom picker is out of scope.
`--report` and `--selection` are not fields (they keep their defaults/behaviour — §9).

Validation, shown as inline error text under the offending field (`paths.source_error` /
`paths.base_error` / `paths.check_output`, §9):
* **source**: exists, is a readable file, parses, root is `<pfsense>`.
* **base**: exists, is a readable file, parses, root is `<opnsense>`.
* **output**: not the same file as source or base, and if it exists then `--force` must be set.

The cheap filesystem checks (`exists`, `is_file`, not-a-directory, `same_file`, exists + `--force`)
run on every change; the parse + root check is **debounced** (~0.4 s timer reset on each keystroke)
so a multi-megabyte config is not reparsed per character. (Rejected: parse on every keystroke — too
costly; parse only on submit — then `enter` could not be gated on validity.)

When source and base parse cleanly the screen loads the context (`SourceContext.load` +
`build_plan` in a worker) and shows the previous summary — source path, pfSense version, hostname,
counts per top-level section, base path, base hostname, base version-mismatch warnings, selection
hash warning. Editing a loaded path clears the summary until it parses again.

`enter` (continue) is **disabled** until all three are valid: the binding's action checks validity
and, while any field is invalid, stays on the screen and focuses the first invalid field (Textual
has no per-binding `disabled`, so "disabled" is enforced in the action). Keys: `enter` continue
(loads the context if needed, hands it to the app, pushes `IfaceMapScreen`); `tab`/`shift+tab` move
between the fields.

**IfaceMapScreen**: a `DataTable` with columns `Incl | Key | Description | pfSense device |
Kind | OPNsense device`. VLAN/WG rows show their derived device (read-only, greyed). Physical
rows are editable. Keys:
* `space`: toggle Incl (not allowed for `unsupported` kind).
* `enter`: on a physical row, open `NicPickerModal`: a `Select` of `target_nics(src)` plus an
  `Input` for free text. Applies to every row that uses the same pf NIC.
* `c`: continue. Runs `validate_ifmap`. On errors, show them in a footer `Static` and stay.
  On success, `build_plan()` runs again (keeping selection by id).

**SelectScreen**: a horizontal split.
* Left: `ItemTree(Tree)`. The root holds one node per translator `title`; children are items.
  **Only tier-0 translators** (the `unsupported` catalogue, §12.4) are collected into a single
  trailing "Not translatable" node. A tier-1 or tier-2 translator **always** renders as its own
  section under its `title`, **even when every one of its items is UNSUPPORTED** — e.g. `cron` (the
  tier-1 classifier, §12.1.16) keeps a "Cron" section, it does not move under "Not translatable".
  The grouping key is `translator.tier == 0`; it is **not** "all items in this section are
  UNSUPPORTED".
  * Item label: `"[x] ✔ label"` / `"[ ] ⚠ label"` / `"    ✖ label"`. Colours: OK green,
    PARTIAL yellow, UNSUPPORTED red/dim.
  * Section label: `"[x]"` all / `"[-]"` some / `"[ ]"` none, plus `title (selected/total)`.
* Right: `DetailPane` = `TabbedContent` with:
  * "Source": `TextArea(read_only=True, language="xml")` with `redacted_xml(item.source)`.
  * "Preview": the redacted serialization of `render()` placements for this item (path
    header + XML).
  * "Notes": status, reason, warnings, deps (labels plus selected-state), requires_plugin, followups.
* Keys: `space` toggle item/section (dependency rules §8); `a` select all OK/PARTIAL in the
  current section; `n` select none in the section; `A` select all default items globally;
  `f` cycle filter (all → selected → problems (PARTIAL+UNSUPPORTED)); `/` search by label
  (Input in the footer, Esc clears); `e`/`E` expand/collapse all; `i` back to the interface
  map; `r` review; `?` help modal listing keys.
* Toggling an UNSUPPORTED item or an interface item shows a notification explaining why it
  can't be toggled. Interface items (`iface:*`) carry `readonly_selection=True` (§12.1.2), so
  `space` on one never changes selection — the user is sent to the interface-map screen (`i`).

**ReviewScreen**: runs `build_output` and `validate` in a worker thread (`@work(thread=True)`).
It shows the summary table (as in the report), errors (red), and warnings (yellow), plus the
output/report paths. Keys: `w` write (disabled when there are errors; confirm if OUT exists
and `--force` is set); `s` save selection JSON (Input modal with the path, default
`OUT.selection.json`); `b` back.

**DoneScreen**: the written paths, counts, the plugin list, and "open report: <path>". `q` quits.

Tests use `async with app.run_test() as pilot` with the synthetic fixtures (§16).

---

## 16. Testing strategy

### 16.1 Fixtures (synthetic only)
`tests/fixtures/pfsense/` holds small, complete `<pfsense>` documents. Each has a
`<version>24.0</version>`, `system/hostname`, `domain`, and only the sections under test.
They're written by hand and modelled on the real structure documented in this spec:
* `minimal.xml`: system + wan (`em0`, dhcp) + lan (`em1`, 192.168.1.1/24).
* `interfaces_vlans.xml`: wan, lan, two VLANs on `em1` (tags 10, 20) assigned as opt1/opt2,
  an unassigned VLAN, an `ovpns1` interface (unsupported), and descriptions with spaces and `&amp;amp;`.
* `aliases.xml`: host/network/port/urltable, nested alias, a `pfB_` alias, a reserved name (`any`), and a bad name `1abc`.
* `filter.xml`: 12 rules covering floating multi-iface, floating `any`, a `<disabled/>` rule,
  a `<enabled>on</enabled>` rule (real config quirk), gateway, alias src/dst, port alias, `(self)`,
  `opt1ip`, icmptype `any`, `WireGuard` group, an `l2tp` rule, an associated-rule-id rule, a rule
  using a `pfB_` alias, and a rule with `state-policy` set to a junk value (PARTIAL path).
* `nat.xml`: 3 port forwards (one with associated rule, one with an alias target, one with
  `natreflection enable`), outbound hybrid with 4 rules (`wanip` target, `opt7ip`, `openvpnip`,
  `other-subnet`) plus one rule with a non-empty `poolopts` (PARTIAL path), and 2 one-to-one rules.
* `dhcp.xml`: two interfaces, explicit dnsserver on one, 3 static maps (one without an IP, one
  with an underscore hostname, one with a per-host DNS), and failover set on one interface.
* `unbound.xml`: **three repeated `<hosts>` elements** (the real quirk — one with an inline
  `aliases/item` and one with a multi-IP `ip`), 2 repeated `<acls>` elements (one with a row
  `description`), 2 domain overrides (one with `@5353`), `forward_tls_upstream` with 2 system DNS
  servers + `dns1host`/`dns2host`, `regdhcpstatic`, `custom_options`.
* `trust_users.xml`: 1 CA, 2 certs (one chain with 2 PEM blocks), admin + 1 user, admins group with extra member.
  **Certificates/keys must be freshly generated throwaway test material** (generate them once
  with `openssl` at fixture creation and commit them; never reuse anything from a real box).
* `wireguard.xml`: 2 tunnels (**`name` values `tun_wg0`/`tun_wg1` with real `descr`, and one with
  no `addresses`**), 4 peers (one unassigned, one with `0.0.0.0/0` plus a host address, one with a
  remote subnet, one with no host address). **Both the tunnel `addresses` and the peer `allowedips`
  MUST be written in the real `row` shape** — `<row><address>…</address><mask>…</mask><descr/></row>`,
  never a text/newline string — because that is exactly what hid the bug: an earlier fixture encoded
  `allowedips` as text, so the translator's whitespace-split passed every test while every real peer
  parsed as "no allowed IPs" (§12.1.17). Include at least one peer row with an **empty `mask`** to pin
  the `/32` (IPv4) / `/128` (IPv6) fallback. Keys are random 32-byte base64.
* `edge_cases.xml`: a `snmpd` with **no `<enable>`**, an `ipsec` with only `mobilekey`/`client`,
  a `dyndnses` entry with `host=dynamic`, an `ovpnserver` block, and a `cron` item whose command is a
  custom script (the "wrap it in a configd action" path).
* `misc.xml`: gateways (incl. one on a missing interface), a static route, VIPs (one DNSBL),
  dyndns cloudflare, syslog remote, cron items, plus the unsupported package stubs.
* `tests/fixtures/opnsense/base_26_7.xml`: `config.xml.sample` from 26.7.8 (copied), with
  wan `if=igc0`, lan `if=igc1`, and version attributes on `OPNsense/Firewall/Filter` (`1.0.6`)
  and `dnsmasq` (`1.0.9`). **`config.xml.sample`'s Dnsmasq section must keep its real structure: each
  row is a repeated same-named element directly under `<dnsmasq>` (the sample ships one
  `<dhcp_ranges uuid=…>` for LAN — a sibling, not inside a `dhcp_ranges` wrapper).** Keep that sibling
  shape in the fixture; goldens must assert migrated rows are emitted the same way (§2.1 rule, §12.1.11).
  A nested `dhcp_ranges`/`hosts` wrapper in the fixture would hide the M14 import bug again.

### 16.2 Golden tests (per translator)
`tests/test_<translator>.py`: load the fixture, build the plan with a fixed ifmap
(`em0→igc0`, `em1→igc1`), render each selected item, wrap the placements as
`<golden><placement path="…" mode="…">…</placement>…</golden>`, pretty-print, and compare
with `tests/golden/<translator>/<case>.xml`. `pytest --update-golden` (an option registered
in `conftest.py`) rewrites the goldens. Every golden change must be reviewed in the diff.
Each test also asserts the status, warnings count, and deps of the interesting items.

### 16.3 End-to-end
`tests/test_e2e.py`: run `cli.main(["migrate", fixture, "--base", base, "-o", tmp/out.xml,
"--select-all-ok", "--map-nic", "em0=igc0", "--map-nic", "em1=igc1"])` and check:
exit 0; the output parses; `validate()` finds no errors; the input file's SHA-256 is unchanged;
the output mode is `0o600`; the report exists. A second run without `--force` → exit 2.
Running with `-o` equal to the input → exit 2.

### 16.4 Secret leakage test
`tests/test_secrets.py`: collect every SECRET_TAGS text from all fixtures. Run `inspect
--json`, build the report, and render the TUI Source/Preview text for every item. Assert none
of the secret strings (len > 8) appear.

### 16.5 TUI tests
Pilot tests: the mapping screen blocks on an unmapped NIC; space toggles; the dependency
cascade notification; review shows errors; `w` writes the file.

### 16.6 Manual acceptance (M14, Dave)
Run against `local/config-gateway…xml` (never committed) with Dave's real base export. Import the output
into a throwaway OPNsense 26.7 VM on the **import-test host `cf99p03`** (Q3, §18) and confirm that the
Firewall ▸ Rules, NAT, **Dnsmasq DHCP**, Unbound, and WireGuard pages load without validation errors.

---

## 17. Milestones

Each milestone ends with `pytest` green and a commit. "Read" lists the sections needed.

**M0 — Scaffold** (read §0, §5, §11)
Create pyproject, package skeleton, `xmlio.py` (`parse_readonly(path) -> etree._ElementTree`
using `etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
huge_tree=True, remove_blank_text=True)`; `to_string(el) -> str`), `secrets.py`, and `cli.py`
with subcommand stubs.
Accept: `pip install -e .[dev]` works; `pf2opn --help` works; a test that an XXE payload isn't
expanded; tests for `redact_element`.

**M1 — Model, registry, contexts, deps, selection, inspect** (§6, §7, §8, §9)
`model.py`, `context.py`, `registry.py`, `deps.py`, `selection.py`, `plan.py`, the
`translators/base.py` helpers, `system.py` (discover + render), and `inspect`.
Accept: `pf2opn inspect minimal.xml --base base.xml` lists the system facets. Unit tests cover
every §7.3 helper, `resolve`/`closure`/`dependents`, and selection JSON round-trip and semantics.

**M2 — Writer, validation core, report, migrate** (§10, §13 items 1–2, §14)
`writer.py`, `versions.py`, `validate.py` (checks 1–2), `report.py`, and `migrate`.
Accept: the §16.3 e2e test passes with `minimal.xml` (system only); the system golden test passes.

**M3 — Interface mapping, interfaces, VLANs** (§12.1.2, §12.1.3, §12.2, §13 items 3–4)
`ifmap.py`, `interfaces.py`, `vlans.py`, and the `--map-nic` flag.
Accept: goldens for `interfaces_vlans.xml`; an unmapped NIC → exit 2; the VLAN naming continues
after the base's existing `vlan0N`.

**M4 — Aliases, gateways, routes, VIPs** (§12.1.4–6, §12.1.12, §13 items 6, 7, 11, 12)
Accept: goldens; `pfB_` and reserved names → UNSUPPORTED; a gateway on a missing interface → UNSUPPORTED;
`system/dns{N}gw` naming a gateway that is not in the output is an error (check 12).

**M5 — Filter rules** (§12.1.7, §13 item 5 for filter)
Accept: the goldens for `filter.xml`; ordering puts floating rules first; the dependency
cascade makes the pfB rule UNSUPPORTED; base rules are replaced (and kept with `--keep-base-rules`);
a `<enabled>on</enabled>` rule is enabled and a `<disabled/>` rule is not; a junk `state-policy`
becomes a PARTIAL warning.

**M6 — NAT** (§12.1.8–10, §13 item 5 for NAT)
Accept: goldens; the associated filter rule is auto-selected via deps; `nat/outbound/mode` is set.

**M7 — Dnsmasq DHCPv4** (§12.1.11, §13 item 8)
Accept: goldens; each migrated interface gets its `dhcp_ranges` rows and the base's default range for
that interface is **replaced, not duplicated**; `dnsmasq/enable=1`, `dhcp/fqdn=1` and the interface list
are set; static maps render as `hosts` rows with lower-cased MACs and reservations keep their IPs; a
static-only subnet (no `range`/`pool`) → `mode=static` with the bare network address as `start_addr`,
while `denyunknown` on a subnet that *has* a dynamic range keeps the range and warns; `hosts.set_tag` /
`dhcp_options.tag` / `dhcp_boot.tag` carry the `dhcp_tags` row **uuid** (never the name); the validator
rejects `mode=static` combined with a non-empty `end_addr`; per-interface options land as
`dhcp_options`/`dhcp_boot` rows; the Unbound query-forward to Dnsmasq is emitted and `dnsmasq/port` is
not left colliding with Unbound's.

**M8 — Unbound** (§12.1.13, §13 item 10)
Accept: goldens incl. DoT upstreams with `verify` hostnames, the ACL action mapping, and `regdhcp`/
`regdhcpstatic` forced to `0` with the new warnings; the fallback `dns_static_host` items are discovered
but `default_selected=False` and de-dup against an explicit host override; the repeated-`<hosts>`/
`<acls>` fixtures are parsed as N entries (not one).

**M9 — Trust, users, groups** (§12.1.14–15)
Accept: goldens; chain trimming; uid collision handling; `system:root_password` only applies when selected.

**M10 — WireGuard, DynDNS, syslog, cron, unsupported catalogue** (§12.1.16–19, §12.4, §13 item 9)
Accept: goldens; `disableroutes` + gateway logic; the unassigned peer → UNSUPPORTED; a peer whose
allowed-IPs are wider than its host address → PARTIAL; the WG interface has no ipaddr and the server
takes it; `host=dynamic` gives the apex hostname; `snmpd` with no `<enable>` and IPsec `mobilekey` are
both reported unsupported; the unsupported items appear in `inspect`.

**M11 — TUI shell: Load + IfaceMap screens** (§15, §9)
Accept: `pf2opn tui` with no arguments reaches the Load screen; the Load screen collects
source/base/output, pre-fills provided flags, blocks `enter` with inline errors until all three
validate (source/base parse with the right root; output not source/base and `--force` honoured),
then loads the context; `--report`/`--selection` are not collected. Pilot tests for path
validation and mapping validation and the NIC picker.

**M12 — TUI Select screen** (§15, §8)
Accept: pilot tests for toggle, section toggle, cascade confirm, filter, and the detail tabs
showing redacted text.

**M13 — TUI Review/Write/Done + save selection** (§15, §9)
Accept: a pilot test writes the output; the saved selection JSON reproduces the identical
output through `migrate --selection` (byte-compare excluding `revision/time`).

**M14 — Hardening and real-config acceptance** (§13 full, §16.4, §16.6)
All validation checks, the secret-leak test, and README usage. Dave runs it against the real config and
imports the result into a throwaway OPNsense 26.7 VM on **cf99p03** (the import-test host, Q3). Fix
whatever the import reveals (file follow-up issues).

**M15 — mDNS reflector / Avahi (stub)** (§12.3; `unsup:avahi` in §12.4)
Needed soon (Q6). pfSense `installedpackages/avahi` → OPNsense `os-mdns-repeater` (plugin model
`//OPNsense/MDNSRepeater`, **v1.0.1**). Mapping sketch: `enabled` ← avahi enable; `interfaces` ← the
pfSense reflect-interface list, mapped keys, comma-joined (`InterfaceField Multiple`); `enablecarp` = `0`;
`blocklist` empty. **Open item:** the exact pfSense `avahi` field names are not yet verified against the
package — confirm them before building (the OPNsense target fields *are* verified against the plugin
model). UPnP (`os-upnp`) and the UDP broadcast relay (`os-udpbroadcastrelay`) **stay Tier 2 for now**, not
folded into M15.

---

## 18. Decisions record — Dave's answers (2026-11-03)

The seven questions below were answered by Dave on **2026-11-03**. The question text and the reasoning
behind each are kept for context; every entry now carries the answer and its consequence. Where an answer
changed a decision, the affected section and the decision table (§3) were revised in place rather than
restated here.

* **Q1 DHCP backend**: Kea (recommended, D5) or Dnsmasq? You rely on pfSense
  `regdhcp`/`regdhcpstatic`. With Kea, static maps become Unbound host overrides (D6), but
  *dynamic* lease names stop resolving. Dnsmasq would keep dynamic names but maps less cleanly.
  **→ Dnsmasq.** Consequence: §12.1.11 was rewritten from Kea to Dnsmasq (`dhcp_dnsmasq.py`); D5/D6
  revised (§3); the Unbound `regdhcp`/`regdhcpstatic` handling and the D6 fallback updated (§12.1.13).
  Dnsmasq registers dynamic *and* static lease names itself, so both resolve — via the
  Unbound→Dnsmasq local-domain query-forward the DHCP translator now emits. Kea is no longer "the
  alternative"; it is at most a future backend for HA/scalability (§12.3).
* **Q2 Base config**: OK to require `--base`, meaning a fresh 26.7 install on the new box with
  WAN/LAN assigned at the console and its config exported first? (The alternative is a bundled
  minimal base with no real NIC names, which I advise against.)
  **→ Confirmed — `--base` is required.** Consequence: none; D2 stands unchanged.
* **Q3 VM acceptance**: is there a Proxmox host where a throwaway OPNsense 26.7 VM can be used
  for the M14 import test?
  **→ Use `cf99p03`.** Consequence: M14 and §16.6 name `cf99p03` as the import-test host.
  Reachability (checked 2026-11-03): `cf99p03` resolves on the LAN as `cf99p03.home.asmartass.me` →
  `192.168.100.202`, a node of the 5-node `cf99` Proxmox cluster (`cf99p01`–`cf99p05`,
  `192.168.100.200`–`.204`); it answers ICMP. Cluster nodes have no direct SSH from here, so the
  throwaway VM is created/imported through Proxmox (web UI) rather than an SSH session.
* **Q4 Policy-routing VPNs**: ProtonVPN (`tun_wg0`) is used for policy routing, and the server
  tunnel `tun_wg1` has peers with `0.0.0.0/0` and remote subnets (`192.168.8.0/24`). My rule turns
  routes off for any tunnel with a default-route peer and warns about the remote subnets. Of the 10
  peers, 6 map cleanly, 3 are PARTIAL (no inferable tunnel address / extra remote nets), 1 is
  UNSUPPORTED (unassigned Mullvad peer). OK, or do you want per-peer handling (for example keep
  routes on for `tun_wg0` and add explicit routes for `192.168.8.0/24`)?
  **→ Accepted as-is: 6 OK / 3 PARTIAL / 1 UNSUPPORTED.** Consequence: §12.1.17 is unchanged; those
  peers keep their warnings + followups.
* **Q5 Admin password**: should `system:root_password` (copy pfSense admin hash to OPNsense
  root) default to off as designed?
  **→ Confirmed — default off.** Consequence: none; §12.1.1 stands (the item exists but is not selected
  by default).
* **Q6 Scope**: are the Tier 2 items you actually need soon UPnP, the UDP broadcast relay, and
  mDNS (Avahi)? If so, I'd spec them next as M15–M17.
  **→ mDNS/Avahi is the one needed soon.** Consequence: Avahi (`os-mdns-repeater`) is spec'd as **M15**
  (§17) — a lightweight stub, since it is small. UPnP (`os-upnp`) and the UDP broadcast relay
  (`os-udpbroadcastrelay`) **stay Tier 2 for now** (§12.3); no M16/M17 is spec'd.
* **Q7 Floating "any" rules**: for a floating rule with `interface` `any`, I write an empty
  `interface` value (matching how the GUI shows "all"). If OPNsense's import rejects an empty
  interface, the fallback is to expand to the comma-joined list of every included interface key
  (M5 acceptance covers both paths). Confirm you're happy with the empty-value default.
  **→ Confirmed — empty `interface` by default, keep the expand-to-all fallback.** Consequence: §12.1.7
  (filter `interface` row) stands; M5 keeps testing both paths.

Open items carried forward from these answers: **Q3** — the `cf99p03` VM provisioning path (web UI);
**Q6/M15** — verify the pfSense `avahi` field names against the package. (The static-only-subnet
`start_addr` question was resolved on 2026-11-03 — `RangeAddressField` accepts a bare network address;
see the note at the end of §12.1.11 and the AP-5 entry in §19.)

---

## 19. Verification log (2026-11-03)

Everything in §2 and §2.1 was checked against `opnsense/core` tag **`26.7.8`** (the newest stable tag;
`27.1.a` exists as a dev tag) on this date — not from memory. Files read:

* Models: `Firewall/{Alias,Filter,DNat}.xml`, `Routing/Gateways.xml`, `Routes/Route.xml`,
  `Interfaces/{Vlan,Vip,NetworkInterface}.xml` (+ `Interfaces/Vlan.php` for the device-name rule),
  `Kea/KeaDhcpv4.xml`, `Dnsmasq/Dnsmasq.xml`, `Unbound/Unbound.xml`,
  `Wireguard/{Server,Client,General}.xml`, `Trust/{Ca,Cert}.xml`, `Auth/{User,Group}.xml`,
  `Cron/Cron.xml`, `Syslog/Syslog.xml`, `Core/Tunables.xml`.
* Converters we copy: `src/opnsense/scripts/filter/list_legacy_rules.php`,
  `.../list_legacy_outbound_nat.php`, `Firewall/Migrations/MFP1_0_4.php`,
  `Routing/Migrations/M1_0_0.php` (all present).
* Defaults: `src/etc/config.xml.sample` (two default MVC LAN allow rules, `nat/outbound/mode=automatic`,
  Dnsmasq DHCP on LAN).

The source config was inspected read-only with Python's `ElementTree`. The following were **wrong or
missing in the earlier draft and are corrected above**:

1. **Dynamic DNS** would have produced `dynamic.asmartass.me` — pfSense `host=dynamic` means the current
   WAN address, i.e. the apex (§12.1.18).
2. **IPsec detector** only looked for `phase1`; this box has `mobilekey` + `client` and no `phase1`, so the
   whole section would have been silently missed (§12.4).
3. **SNMP detector** required `<enable>`; the real `snmpd` has none (§12.4).
4. **Unbound** repeats the plural container per entry (`<hosts>` ×47, `<acls>` ×3); the draft was easy to
   misread as `hosts/host` (§12.1.13).
5. **Source-NAT `poolopts`** are opaque `p1`…`p6`, not pfSense pool strings (§12.1.9).
6. **WireGuard** peer `allowedips` are remote addresses, not tunnel addresses; the peers mapping is now
   an explicit heuristic with OK/PARTIAL/UNSUPPORTED outcomes (§12.1.17). Tunnel `name` is a device name.
7. **VLAN naming** is validated by `Interfaces/Vlan.php` (`^vlan0([0-9.]){1,10}$`), not the controller that
   was cited (§2).
8. **pfSense `<enabled>on/off</enabled>`** appears on filter rules and must be handled the way upstream
   `list_legacy_rules.php` does (only `<disabled>` counts) (§12.1.7).
9. Filter port conversion now matches upstream exactly (no `:` rewriting); `overload`/`adaptivestart`/
   `adaptiveend`/`category` added; `tcpflags_any` flagged as a deliberate divergence (§12.1.7).
10. `ovpnserver` (5 children) and other non-empty-but-ignored sections are now named explicitly rather than
    assumed empty (§12.4).

Sections confirmed present in the real config and accounted for by this spec: `system` (59 children),
`interfaces` (11), `staticroutes` (0), `dhcpd` (8 enabled subnets), `dhcpdv6`, `snmpd`, `diag`, `syslog`
(only `filterdescriptions`), `nat` (10 port forwards, 37 outbound rules, mode `hybrid`, no 1:1),
`filter` (79 rules + 1 separator), `shaper` (0), `ipsec` (mobile), `aliases` (37, incl. 4 `pfB_*`),
`cron` (16, all classified), `rrd`, `widgets`, `openvpn` (0), `unbound` (47 hosts, 3 ACLs),
`revision`, `cert` (×3), `ca` (×4), `vlans` (8), `gateways` (7 items, `opt11` referenced but unassigned),
`installedpackages` (87, incl. wireguard/tunnels+peers, pfblockerng*, suricata, snort, frr, tailscale,
avahi, telegraf, ntopng, bandwidthd, miniupnpd, traffictotals, acme, servicewatchdog, shellcmdsettings,
udpbroadcastrelay, pimd*, freeradiuseapconf, vpn_openvpn_export), `virtualip` (1, DNSBL), `dyndnses` (3),
`l2tp` (server), `ovpnserver` (wizard leftovers), `captiveportal` (1 zone), `traffic_graphs`, `ifgroups`,
`dhcpbackend` (`isc`), `sysctl` (1), `sshdata` (host keys — never migrated).

### 2026-11-03 — DHCP backend re-verified after Q1 (Kea → Dnsmasq)

Dave chose **Dnsmasq** over Kea (Q1). Before rewriting §12.1.11 I re-read the Dnsmasq model and its
config generation against `26.7.8` rather than from memory:

* `Dnsmasq/Dnsmasq.xml` (**v1.0.9**) — the containers and fields cited in §12.1.11 (`dhcp_ranges` with
  `interface`/`start_addr`/`end_addr`/`mode`/`lease_time`/`domain`/`domain_type`; `hosts` with
  `host`/`domain`/`ip`/`hwaddr`/`client_id`/`set_tag`/`descr`; `dhcp_tags`; `dhcp_options`; `dhcp_boot`)
  are verbatim from it, together with `Dnsmasq.php` (`getDhcpInterfaces()`, the `static`-range rule, and
  the single-IPv4-per-reservation rule) and `scripts/dnsmasq/dnsmasq_dhcp_options.py` (option values are
  numeric IANA codes, stored as strings).
* `service/templates/OPNsense/Dnsmasq/dnsmasq.conf` — shows how `dhcp_ranges` become `dhcp-range=`, how
  `hosts` become `dhcp-host=`, and that DHCP options/boot are tag-scoped (interface tag is automatic).
* `etc/inc/plugins.inc.d/dnsmasq.inc` + `unbound.inc`, `scripts/dnsmasq/dnsmasq_watcher.py`,
  `scripts/unbound/unbound_watcher.py` — confirm `regdhcp`/`regdhcpstatic` read **ISC** leases / the
  `static_mapping` plugin hook (which Dnsmasq does not provide), i.e. the §12.1.13 knock-on.
* `etc/config.xml.sample` — the default LAN `dhcp_ranges` row and `dnsmasq/port=53053` (the reason M7 must
  *replace*, not duplicate, the base range).
* `docs.opnsense.org/manual/dnsmasq.html` — the documented Unbound+Dnsmasq combination (Unbound forwards
  the local domain to Dnsmasq on a non-standard port), which is what the DHCP finalize now emits.
* For the new M15 stub: `opnsense/plugins` `net/mdns-repeater` model (`//OPNsense/MDNSRepeater`, v1.0.1).

The ten corrections in the list above still stand; the DHCP correction is the Kea→Dnsmasq change captured
in §12.1.11, §3 (D5/D6), §12.1.13 and §17 (M7). The session's own input config was **not** touched.

### 2026-11-03 — spec amendments after the AP-5 review

AP-5 reviewed the M0–M8 implementation against this spec and found places where the *spec* (not the code)
was wrong or incomplete. Re-verified against `26.7.8` for this pass:

* `Dnsmasq/Dnsmasq.xml` v1.0.9 — `hosts/set_tag`, `dhcp_options/tag`, `dhcp_boot/tag`,
  `dhcp_ranges/set_tag` are `ModelRelationField`s into `dhcp_tags` (uuid-keyed), and
  `Dnsmasq.php::performValidation()` rejects `mode=static` with a non-empty `end_addr`
  ("Static only accepts a starting address") and requires `end_addr` for a non-static IPv4 range (§12.1.11).
* `Dnsmasq/FieldTypes/RangeAddressField.php` — `extends NetworkField` with `NetMaskAllowed=N`, so a bare
  network address is a valid `start_addr` (the §12.1.11 open item is resolved).
* `Firewall/Filter.xml` — `rules/rule/categories`, `snatrules/rule/categories`,
  `onetoone/rule/categories` and `rules/rule/overload` are `ModelRelationField`s; `overload` relates to
  `Firewall/Alias`. `BaseListField::getValidators()` rejects any value not in the uuid option list, and
  `MFP1_0_3`/`MFP1_0_4` resolve category names → uuids via `Category()->getByName()` before assigning.
  So §12.1.7/§12.1.9 must **not** copy the pfSense names the way `list_legacy_rules.php` /
  `list_legacy_outbound_nat.php` do (`overload` → migrated alias uuid; `categories` → dropped + warning).
* `Firewall/Migrations/MFP1_0_4.php` — for 1:1, copies `natreflection` verbatim; the `onetoone` model
  allows only `default`/`enable`/`disable`, so §12.1.10 now maps `pfsync` variants → `enable` + warning
  rather than copying them.
* `Firewall/DNat.xml` — `source`/`destination` have no `any` child, so §12.1.8 now emits
  `<network>any</network>` instead of copying pfSense's `<any/>`.
* §13 check 8 tightened and extended (tag uuids; `static`+`end_addr` guard), check 12 assigned to M4,
  check 9 marked M10; §11 rule 5 now names `cli.main()` as the install point for `RedactingFilter`.
  The `categories`/`overload` and `mode=static` amendments change what M5/M6/M7 must render; golden
  tests will need to move with them.

### 2026-11-03 — spec amendments after the second review

A second review (M10–M14 implementation) found the spec wrong or silent in the places below; each
is now stated in the referenced section. Re-verified against `26.7.8` for this pass:

* `Wireguard/Server.xml` — `instance` is an `AutoNumberField` and `ServerField.php` derives the
  device name as `wg{instance}`; the server `name` is a free label. So the tunnel merge identity is
  the **output instance**, not the pfSense `N`, and an instance collision reassigns while keeping
  the base tunnel (§12.1.17).
* `Wireguard/Client.xml` — the client `name` carries **no** `UniqueConstraint` (the only one is on
  `pubkey`+`serveraddress`+`serverport`), but two clients still must not share a name in our
  output, so peer identity is our own assigned `name` and a base name collision is a merge-replace,
  not a `_2` rename (§12.1.17).
* `Auth/FieldTypes/GidField.php` — auto-allocates a gid from 2000 only when the value is empty and
  applies no uniqueness check, so a verbatim `gid` can collide with a base group. The gid rule now
  mirrors the uid rule (§12.1.15).
* `Base/FieldTypes/HostnameField.php` (the Syslog `hostname` field) — `IpAllowed` defaults to
  `true`, so a bare IPv6 literal is a valid value; the syslog split is therefore shape-based, not
  colon-based, and a bare IPv6 address is kept whole (§12.1.19).
* `pfsense/pfsense` `syslog.inc::system_syslogd_fixup_server()` — confirms pfSense stores an
  unbracketed IPv6 literal for a remote server (it brackets it only when writing `syslog.conf`),
  which is why the split must not treat the colons as a host/port separator (§12.1.19).

### 2026-11-03 — spec amendment after the real-config run (WireGuard `allowedips` rows)

A real-config run against `config-gateway.home.asmartass.me-20261101163750.xml` exposed a parse bug the
synthetic fixtures had masked: every WireGuard peer came back UNSUPPORTED "peer has no allowed IPs"
(0 of 10 migrated). Root cause was an ambiguity in §12.1.17, now fixed there:

* **`installedpackages/wireguard/peers/item/allowedips` is a `row` list**, not a text/newline field —
  `allowedips/row` with `address` + `mask`, the same shape as a tunnel's `addresses/row` and an Unbound
  ACL's `row/acl_network`+`mask`. The spec and the fixture (§16.1) both described it as text, and the
  translator split it on whitespace, so a real (empty-text) `<allowedips>` yielded an empty list. Now
  parsed row-wise, with an empty `mask` meaning `/32` (IPv4) / `/128` (IPv6). `0.0.0.0/0`/`::/0` are a
  row `address=0.0.0.0`+`mask=0` (`::`+`mask=0`), and `disableroutes` / the `tunnel_ips`/`remote_nets`
  heuristic compare the parsed CIDRs. The `wireguard.xml` fixture description now requires the `row`
  shape. No other field in this spec describes a `row`-stored value as text: the Unbound `acls` rows
  (§12.1.13) and the tunnel `addresses/row` were already correct, and the remaining multi-value fields
  (alias `address`, NTP `timeservers`, Unbound `ip`, filter `interface`) really are text in pfSense.

### 2026-11-03 — M14 blocker: outbound NAT will not load on 26.7.8 (`ipprotocol`), and `hybrid` mode

Migrating `config-gateway.home.asmartass.me-20261101163750.xml` produced `nat/outbound/mode=hybrid`
plus 36 MVC `snatrules`. Importing it on OPNsense **26.7.8_2**, `configctl filter reload`;
`pfctl -nf /tmp/rules.debug` returned two `rule expands to no valid combination` errors, so `pfctl`
rejected the file and **0 rules loaded**. Bisected on a throwaway VM. Two independent findings:

1. **The failing rules are pf2opn's own copied rules, not OPNsense's automatic set.** The two error
   lines carry pfSense's wording — `# Auto created rule for ISAKMP - localhost to WAN` /
   `# Auto created rule - localhost to WAN` — and both read `nat on vtnet0 inet from ::1/128 …`.
   They are `snatrules/rule` entries rendered by `plugins.inc.d/pf.inc:180-182`
   (`$fw->registerSourceNatRule(50, $rule->serialize())`, prio 50). OPNsense's genuine automatic set
   renders separately as `# Automatic outbound rule` (prio 200) and **contained no `::1/128`**: it is
   built by `filter_auto_source_nat()` (`/usr/local/etc/inc/filter.lib.inc:176`) from
   `pf_get_default_nat_outbound_networks()` (`plugins.inc.d/pf.inc:285`), whose sources are
   per-gateway-interface network keywords plus `127.0.0.0/8` only (hardcoded `ipprotocol => inet`, always
   valid). The earlier hypothesis ("OPNsense's automatic set hardcodes `inet` for `::1/128`") is wrong.
   **Root cause:** pfSense stores its materialized automatic rules with an **empty** `<ipprotocol>`,
   and `nat.py` mapped empty → `inet` unconditionally (`pf_text(rule, "ipprotocol") or "inet"`, line
   ~345). The two loopback IPv6 rules (`source_net=::1/128`) therefore became `inet`. OPNsense itself
   calls this invalid: `Firewall/Filter.php::performValidation()` requires the family of `source_net`
   to match `ipprotocol` ("Source address type should match selected TCP/IP protocol version"), and
   `pfctl` agrees. With the family fixed (`::1/128` → `inet6`) the file loads (0 errors) in any mode;
   fix removed and it fails again in both `hybrid` and `advanced` — so the fix is mandatory and
   mode-independent.
2. **Mode.** pfSense `hybrid` renders the stored `nat/outbound/rule` list **and** a runtime automatic
   set (`filter.inc`: `case "hybrid"` falls through to the `advanced` block, and the automatic block
   runs for `''`/`automatic`/`hybrid`). That stored list already contains pfSense's *materialized*
   automatic rules: **30 of the 37** are `Auto created rule…`; only **7** are genuinely custom
   (rule 0 `opt7→wanip`; Xbox One / PS4 / Switch / Switch Lite / Meraki MX100 static-port `/32`s;
   `Wireguard_Networks`). OPNsense `hybrid` would add OPNsense's own automatic set on top, which is
   **not** what pfSense rendered, and OPNsense `automatic` ignores manual rules (so the 7 custom rules
   would be lost). Decision: map pfSense `hybrid` (with ≥1 rule) → OPNsense **`advanced`**, which
   `filter_auto_source_nat()` leaves with no automatic set and renders exactly the migrated rules
   (§12.1.9). Verified on the VM: `advanced` + family fix → `pfctl -nf` exit 0, exactly 36 nat rules,
   0 automatic lines; `hybrid` + family fix → exit 0 but 96 nat lines (migrated rules duplicated by
   OPNsense's automatic set); `advanced` without the fix → 2 errors again.

Spec edits: §12.1.9 mode table + `ipprotocol` row, §13 check 13 + warning. Not determined: whether any
pfSense `hybrid` config exists whose stored rule list is *not* a superset of its runtime automatic
set — if so, mapping to `advanced` would drop that automatic coverage; the `hybrid`+0-rules →
`automatic` guard covers the empty case only.

### 2026-11-03 — M14 import findings: Dnsmasq `ArrayField` placement, and check-13 gaps

The first real import into the throwaway OPNsense 26.7 VM (M14, `cf99p03`) exposed three spec errors the
synthetic tests could not (`dhcp_ranges` check 8 passing while the imported rows were ignored):

* **Dnsmasq arrays are repeated same-named siblings, not nested.** pf2opn wrote
  `<dnsmasq><dhcp_ranges><dhcp_ranges uuid=…>…` but the model mounts the `ArrayField`s directly under
  `/dnsmasq`, so the rows are `<dnsmasq><dhcp_ranges uuid=…>…</dhcp_ranges><dhcp_ranges uuid=…>…`.
  Verified against the stock base config (two sibling `<dhcp_ranges uuid>`), `helpers.toList('dnsmasq.dhcp_ranges')`
  and the generated `dnsmasq.conf`; the nested form is silently ignored (8 ranges, 48 reservations, 6
  options, 4 tags never appeared). §12.1.11 now states the placement and the general `ArrayField` rule
  (§2.1); §13 check 8's path wording and the §16.1 base-fixture description were corrected to match.
* **§12.1.8 `ipprotocol`** still said "pf or `inet`" — now "derive, do not default", matching §12.1.9 and
  required by §13 check 13.
* **§13 check 13**: for `nat/rule` the family must be read from the `source`/`destination` endpoint
  elements themselves (`DNat.xml` stores the address in either `network` or the volatile `address`, so a
  host/alias endpoint rendered as `<address>` was missed); `onetoone/rule` is now an explicit no-op
  because the 1:1 rule has no `ipprotocol` field.

### 2026-11-03 — final review amendments (check 5, Unbound loopback upstreams, report counting)

Three loose ends from the AP-5 / real-config reviews:

* **§13 check 5** now mirrors the check-13 fix: `nat/rule` reference validation reads the
  `source`/`destination` endpoint elements themselves (`network` **or** the volatile `address`) plus
  `target`, not `source/network`/`destination/network` alone. (Implementation note for the follow-up
  diff: `validate._check_nat_refs` still passes `("source/network", "destination/network", "target")`
  and must be changed to the same endpoint walk as `_nat_endpoint_addresses`.)
* **§12.1.13** now states that loopback/self `system/dnsserver` values (`127.0.0.0/8`, `::1`,
  unspecified, or a source-box interface address — the real config starts with `127.0.0.1`) are
  skipped when building Unbound upstreams (`dots/dot[]`, the plain forwarding path, and a
  loopback `domainoverrides` target), with an info warning; if every upstream is skipped, forwarding
  stays `0` (recursive resolution) plus a followup. Rationale: a DoT upstream of `127.0.0.1:853`
  listens to nothing and only makes log noise.
* **§14** now defines the counting convention: section "migrated" counts **emitted objects**, so the
  reference config reads Firewall rules **73** / Outbound NAT **36** (matching the emitted `<rule>`
  counts) rather than 74 / 37. Non-emitting items (`filter:separator`, `snat:mode`) carry
  `Item.emits=False` (§6) and are listed under a separate **Settings & notes** block; `_section_counts`
  counts `emits and selected` OK/PARTIAL items with no output-tree access and no render at report time.

