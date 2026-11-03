# pf2opn — Design Spec

Status: **design, ready for implementation** (author: Sabine, 2026-11-03)
Target: **OPNsense 26.7.x** (latest tag `26.7.8`, released 2026-10-30)
Source: **pfSense config.xml** (`<pfsense><version>24.0</version>`, i.e. pfSense CE 2.8.x / Plus 25.x)

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
| `Kea/KeaDhcpv4.xml` | `//OPNsense/Kea/dhcp4` | 1.0.7 |
| `Dnsmasq/Dnsmasq.xml` | `/dnsmasq` | 1.0.9 |
| `Unbound/Unbound.xml` | `//OPNsense/unboundplus` | 1.0.15 |
| `Wireguard/Server.xml` / `Client.xml` / `General.xml` | `//OPNsense/wireguard/{server,client,general}` | 1.0.1 / 1.0.0 / 0.0.1 |
| `Trust/Ca.xml` / `Trust/Cert.xml` | `/ca+` / `/cert+` | 1.0.0 |
| `Auth/User.xml` / `Auth/Group.xml` | `/system/user+` / `/system/group+` | 1.0.0 |
| `Cron/Cron.xml` | `//OPNsense/cron` | 1.0.4 |
| `Syslog/Syslog.xml` | `//OPNsense/Syslog` | 1.0.2 |
| `Core/Tunables.xml` | `//sysctl` | 1.0.2 |
| plugins `dns/ddclient/…/DynDNS/DynDNS.xml` (branch `stable/26.7`) | `//OPNsense/DynDNS` | 1.5.1 |

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
* VLAN device names generated by OPNsense: `sprintf('%s0%d', 'vlan', n)` → `vlan01`, `vlan02`, …
  (`VlanSettingsController.php`).
* Kea/Unbound hostnames use `IsDNSName=Y`, so underscores are allowed.
* Filter rule address keywords (`NetworkAliasField`): `any`, `(self)`, `<ifkey>`,
  `<ifkey>ip`, alias names, IPs and CIDRs. This is the same vocabulary as pfSense.
* Built-in interface groups (`GroupField`): `openvpn`, `enc0`, `wireguard`.

---

## 3. Key decisions (and what I rejected)

| # | Decision | Rejected alternatives and why |
|---|---|---|
| D1 | **lxml** for all XML. | stdlib ElementTree has no `getparent()` (we need in-place replacement inside the base tree), weaker XPath, and no parser switches to hard-disable entities/DTD/network. lxml 6.x already has cp314 wheels. |
| D2 | **`--base` is required.** Output = deep copy of base plus the selected items. | Building from scratch or from the bundled `config.xml.sample` would lose the target's real NIC names, root password, model `version` attributes, and firmware/plugin state. A wrong or missing `version` attribute makes OPNsense re-run model migrations on import (for example `MFP1_0_3` deletes `nat/npt`). That fails silently. |
| D3 | **Keep pfSense logical interface keys** (`wan`, `lan`, `optN`) in the output and only remap *devices* (`mlxen1` → `igc1`, `mlxen1.10` → `vlan03`, `tun_wg0` → `wg0`). | Re-keying interfaces would mean rewriting every rule, NAT, DHCP, gateway and Unbound reference, with no user benefit. OPNsense uses the same key scheme. |
| D4 | **Firewall rules → MVC** `OPNsense/Firewall/Filter/rules/rule`. **Outbound NAT → MVC `snatrules`.** **1:1 → MVC `onetoone`.** **Port forwards → `/nat/rule`** (the DNat model lives there). | Writing legacy `<filter><rule>` / `nat/outbound/rule` would work in 26.7, but both are on the way out. The user would then have to run the in-GUI migration assistant anyway. |
| D5 | **DHCPv4 → Kea** (`OPNsense/Kea/dhcp4`). If Kea takes an interface, remove the base's Dnsmasq `dhcp_ranges` for that interface. If none are left, disable Dnsmasq. | ISC: it's a plugin and deprecated, so it's a dead end. Dnsmasq: it's the fresh-install default and registers DHCP names into DNS, but it has a different shape (no subnets, ranges keyed by interface, tag-based options). That makes the mapping lossier for per-subnet DNS/gateway/domain. **Open question Q1** (Dnsmasq can be added as a Tier 2 alternative translator). |
| D6 | pfSense `unbound/regdhcpstatic` is replaced by **synthesizing Unbound host overrides from the DHCP static maps** (separate items, selected by default). | Kea doesn't feed Unbound in OPNsense, so static-map hostnames would stop resolving. Dynamic-lease names are lost. The report says so. |
| D7 | **Deterministic UUIDs**: `uuid5(PF2OPN_NS, item_id + "/" + suffix)`. | uuid4 makes golden tests impossible and cross-references harder (a peer's uuid has to be known before the server renders). A collision with base uuid4 values is negligible. The validator checks uniqueness anyway. |
| D8 | Port-forward **associated filter rules are migrated as standalone MVC rules**. The DNat rule gets no `associated-rule-id`. | Re-creating OPNsense's "associated rule" linkage means generating legacy filter rules, which contradicts D4. pf semantics are the same. |
| D9 | When ≥1 filter rule is selected, the **base's MVC filter rules are replaced** (they're the two default LAN allow rules). Override with `--keep-base-rules`. | Merging gives duplicate "allow LAN" rules, because pfSense has its own. |
| D10 | **Textual `Tree`** with checkbox glyphs in labels, toggled by Space. | Textual has no checkbox-tree widget. `SelectionList` is flat and can't show sections. |
| D11 | Translators are **pure**: `render(item, ctx) -> list[Placement]`. Only `writer.py` mutates the output tree. | Translators that mutate the tree directly can't be previewed in the TUI or golden-tested in isolation. |
| D12 | pfSense cron jobs are **classified, never written**. OPNsense cron only runs configd actions (`ConfigdActionsField`). | Writing shell commands into `OPNsense/cron` produces invalid config. Every cron job in the real config is a pfSense/package internal job anyway. |

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
│   │   ├── dhcp_kea.py        # 2 translators: dhcp_subnet, dhcp_static
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
| dhcp_subnet | `dhcp:<ifkey>` | `dhcp:opt1` |
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
pf2opn tui     SOURCE --base BASE -o OUT [--report REPORT] [--selection FILE]
```
* `REPORT` defaults to `OUT` with the suffix replaced by `.report.md`.
* The tool refuses (exit 2) if OUT already exists and `--force` isn't given, or if OUT resolves
  to the same file as SOURCE or BASE (`os.path.samefile` when it exists, plus a resolved-path
  comparison).
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
  "OPNsense/Kea/dhcp4": "1.0.7",
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
5. Logging: the root logger gets `RedactingFilter`. While parsing, `xmlio.parse` calls
   `register_secret(text)` for every SECRET_TAGS text longer than 8 chars.
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
| dhcpd | 1 | dhcp_subnet (110), dhcp_static (111) | `OPNsense/Kea/dhcp4/*` | §12.1.11 |
| unbound | 1 | dns_general (120), dns_host (121), dns_static_host (122), dns_domain (123), dns_acl (124) | `OPNsense/unboundplus/*` | §12.1.13 |
| dyndnses | 1 | dyndns (130) | `OPNsense/DynDNS/*` (plugin os-ddclient) | §12.1.18 |
| syslog | 1 | syslog (140) | `OPNsense/Syslog/destinations/destination[]` | §12.1.19 |
| cron | 1 (classifier) | cron (150) | – (never written) | §12.1.16 |
| dhcpdv6, RA | 2 | – | Kea dhcp6 / radvd | |
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
| `enabled` | `0` if `disabled` present else `1` |
| `statetype` | first word of pf `statetype` (`keep state`→`keep`, `sloppy state`→`sloppy`, `synproxy state`→`synproxy`, `modulate state`→`modulate`, `none`→`none`); empty → `keep` |
| `state-policy` | pf `statepolicy` or `state-policy` text, or empty |
| `sequence` | see ordering below |
| `action` | pf `type` (`pass`/`block`/`reject`); empty → `pass` |
| `quick` | if pf `quick` present → `1`; else if `floating` present → `0`; else `1` |
| `interfacenot` | `0` (`1` if pf `interfacenot` present) |
| `interface` | pf `interface` split on `,`, each mapped: an included iface key stays as-is; `WireGuard`→`wireguard`; `openvpn`,`enc0` as-is; `any` → (empty list = all). An unknown key or excluded iface → UNSUPPORTED "interface <x> not migrated". `l2tp`/`pppoe` → UNSUPPORTED "L2TP/PPPoE server not supported". Join with `,` |
| `direction` | pf `direction` or `in` |
| `ipprotocol` | pf `ipprotocol` or `inet` |
| `protocol` | pf `protocol` upper-cased (`tcp/udp`→`TCP/UDP`), empty → `any` |
| `icmptype` | pf `icmptype`; `any` → empty |
| `icmp6type` | pf `icmp6-type` names → numbers via the table in `list_legacy_rules.php` |
| `source_net`,`source_not`,`source_port` | `pf_endpoint(source)` → `map_net`; port via `fix_port` |
| `destination_net`,`destination_not`,`destination_port` | same for destination |
| `gateway` | pf `gateway` (name); dep `gw:<name>` |
| `log`,`allowopts`,`nosync`,`nopfsync`,`disablereplyto` | presence → `1` else `0` |
| `replyto` | pf `reply-to` |
| `statetimeout`,`max`,`max-src-nodes`,`max-src-conn`,`max-src-states`,`max-src-conn-rate`,`max-src-conn-rates`,`tag`,`tagged`,`tcpflags1`,`tcpflags2`,`prio`,`set-prio`,`set-prio-low`,`tos` | copy if non-empty |
| `tcpflags_any` | presence → `1` else `0` |
| `description` | `pf_desc` |

Dropped, with a warning if non-empty: `sched` ("schedule not migrated"), `os` ("OS
fingerprint filter dropped"), `dnpipe`/`pdnpipe`/`defaultqueue`/`ackqueue` ("traffic shaper
not migrated"), `vlanprio*`. Ignored silently: `id`, `tracker`, `created`, `updated`,
`associated-rule-id` (but see warning below).

Deps: `iface:<k>` for each interface, plus `net_deps` of source/destination nets, plus
`alias:<p>` if a port is an alias name, plus `gw:<name>`.

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
| `ipprotocol` | pf or `inet` |
| `protocol` | pf `protocol` as-is (lower case) |
| `source/{network\|address\|any,port,not}` | the same structure is copied, with `network` keyword keys validated via `map_net` |
| `destination/{…}` | same |
| `target` | pf `target` (IP or alias; dep alias) |
| `local-port` | pf `local-port` |
| `natreflection` | `purenat`→`purenat`; `disable`→`disable`; `enable`→`purenat` + warning "NAT+proxy reflection mode not available, using pure NAT"; empty → omit |
| `descr` | `pf_desc` |
| `tag`,`tagged`,`log`,`nosync` | copy / presence → `1` |

Deps: iface, aliases, and **the associated filter rule item** (`rule:<tracker>` of the filter
rule whose `associated-rule-id` equals this rule's) so selecting a port forward also selects
its pass rule. If pf `associated-rule-id` == `pass` → PARTIAL "pfSense ‘pass’ association:
add an explicit pass rule manually".

#### 12.1.9 Outbound NAT (`nat.py: NatOutbound`, base_policy `replace`, container `OPNsense/Firewall/Filter/snatrules`)
`finalize` always SETs `opn:nat/outbound/mode` = pf `nat/outbound/mode` (`automatic`,
`hybrid`, `advanced`, `disabled`). An empty value becomes `automatic`. The outbound section is
one extra item `snat:mode` (label "Outbound NAT mode: hybrid") that the rules depend on.

`pf:nat/outbound/rule[]` → `opn:OPNsense/Firewall/Filter/snatrules/rule[]`. Mirrors `list_legacy_outbound_nat.php`:

| opn | value |
|---|---|
| `enabled` | `0` if `disabled` else `1` |
| `nonat` | presence → `1` else `0` |
| `nosync` | presence → `1` else `0` |
| `sequence` | `10*(n+1)` |
| `interface` | mapped key |
| `ipprotocol` | pf or `inet` |
| `protocol` | upper-cased or `any` |
| `source_net`,`source_not` | `pf_endpoint(source)` → `map_net` |
| `source_port` | pf `sourceport`, `:`→`-` |
| `destination_net`,`destination_not` | `pf_endpoint(destination)` |
| `destination_port` | pf `dstport`, `:`→`-` |
| `target` | pf `target`: empty → empty; `<iface>ip` where iface == rule interface → empty (interface address); other `<k>ip` → keep if `k` included else UNSUPPORTED; `other-subnet` → `targetip` + `/targetip_subnet` (omit the mask if 32/128 or empty); `openvpnip` → UNSUPPORTED "OpenVPN not migrated"; anything else (IP, alias, VIP address) → keep (alias dep) |
| `target_port` | pf `natport`, `:`→`-` |
| `poolopts` | pf `poolopts` (values are the same strings, e.g. `round-robin`) |
| `poolopts_sourcehashkey` | pf `source_hash_key` |
| `staticnatport` | presence → `1` else `0` |
| `log` | presence → `1` else `0` |
| `tag`,`tagged` | copy |
| `description` | `pf_desc` |

Mode `automatic`/`disabled` with rules present → the rules are still discovered, but as
PARTIAL "rules are inactive in mode <m>".

#### 12.1.10 1:1 NAT (`nat.py: NatOneToOne`, base_policy `replace`, container `OPNsense/Firewall/Filter/onetoone`)
`pf:nat/onetoone[]` → `opn:OPNsense/Firewall/Filter/onetoone/rule[]`. Follows `MFP1_0_4.php` exactly:
`enabled`, `log`, `sequence`, `interface`, `type` (empty→`binat`), `external` (append the
source mask if external has none), `source_net`/`destination_net` (`any` / address with
`/32`|`/128` appended to bare IPs / network keyword), `source_not`, `destination_not`,
`description`, `natreflection` (copy if non-empty). If source, destination, or external is
missing → UNSUPPORTED "incomplete 1:1 rule".

#### 12.1.11 DHCPv4 → Kea (`dhcp_kea.py`)
**Subnet items** (`dhcp:<ifkey>`, translator `dhcp_subnet`, base_policy `none`): one per
`pf:dhcpd/<ifkey>` with `enable` present. A disabled one isn't listed. Deps:
`iface:<ifkey>`. The interface must have a static IPv4 address, else UNSUPPORTED "Kea needs a
static interface address".

Render → APPEND to `opn:OPNsense/Kea/dhcp4/subnets` a `subnet4` (uuid = `ctx.uuid("dhcp:<k>")`):

| opn `subnet4/…` | value |
|---|---|
| `subnet_id` | 1-based index among selected subnets, after the max `subnet_id` already in base |
| `subnet` | network CIDR of iface `ipaddr/subnet` (via `ipaddress.ip_interface(...).network`) |
| `valid_lifetime` | pf `defaultleasetime` if non-empty |
| `next_server` | pf `nextserver` |
| `option_data_autocollect` | `0` if any of pf `gateway`, `dnsserver`, `ntpserver` is set, else `1` |
| `option_data/routers` | pf `gateway`, or the iface IP when autocollect=0 and gateway is empty; `none` → empty + warning |
| `option_data/domain_name_servers` | pf `dnsserver[]` joined with `,`, or the iface IP when autocollect=0 and none are set |
| `option_data/domain_name` | pf `domain` |
| `option_data/domain_search` | pf `domainsearchlist` (`;`-separated) → `,` |
| `option_data/ntp_servers` | pf `ntpserver[]` joined with `,` |
| `option_data/tftp_server_name` | pf `tftp` |
| `option_data/boot_file_name` | pf `filename` |
| `match-client-id` | `1` |
| `pools` | pf `range/from`-`range/to`, plus any `pool[]/range` entries, newline-joined |
| `description` | `"pf2opn: " + iface descr` |

Warnings (PARTIAL) when non-empty: `failover_peerip` ("failover not migrated; Kea HA is
different"), `mac_allow`/`mac_deny`/`denyunknown`, `ddnsdomain*` ("dynamic DNS updates not
migrated; see Kea DDNS"), `numberoptions/item` ("custom DHCP options not migrated (Tier 2)"),
`staticarp`, `ldap`, `rootpath`, `filename32/64/*arm`, `uefihttpboot`, and
`maxleasetime`/`ignoreclientuids`.

`finalize(selected)` (subnets):
* SET `OPNsense/Kea/dhcp4/general/enabled` = `1`.
* SET `.../general/interfaces` = the comma-joined keys of the selected subnets (union with
  any keys already in the base).
* SET `.../general/fwrules` = `1`.
* REMOVE `dnsmasq/dhcp_ranges[interface='<k>']` for every selected key. If no
  `dnsmasq/dhcp_ranges` remain in the output, SET `dnsmasq/enable` = `0`. Add report
  followup: "Dnsmasq DHCP disabled on <keys> in favour of Kea".

**Reservation items** (`dhcpres:<k>:<mac>`, translator `dhcp_static`, base_policy `none`): one
per `pf:dhcpd/<k>/staticmap`. Deps: `dhcp:<k>`. `ipaddr` empty → UNSUPPORTED "Kea
reservation needs an IP address". `ipaddr` outside the subnet → UNSUPPORTED.

Render → APPEND to `opn:OPNsense/Kea/dhcp4/reservations` a `reservation`:
`subnet` = `ctx.uuid("dhcp:<k>")`, `ip_address`←`ipaddr`, `hw_address`←`mac` (lower-case),
`client_id`←`cid`, `hostname`←`hostname`, `description`←`pf_desc(descr)`,
`next_server`←`nextserver`, `option_data/domain_name_servers`←`dnsserver[]` joined with
`,`, `option_data/routers`←`gateway`, `option_data/domain_name`←`domain`,
`option_data/boot_file_name`←`filename`. Drop `ddns*`/`arp_table_static_entry` with warnings
as above.

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
| `regdhcp` | `general/regdhcp` | `0` + warning "dynamic lease registration not available with Kea" |
| `regdhcpstatic` | `general/regdhcpstatic` | `0` (replaced by the `dns_static_host` items, D6) |
| `system_domain_local_zone_type` | `general/local_zone_type` | copy (`transparent` default) |
| `hideidentity`,`hideversion`,`dnssecstripped`,`prefetch`,`prefetchkey`,`qname-minimisation-strict`→`qnameminstrict`,`serveexpired` | `advanced/<same>` | presence → `1`/`0` |
| `msgcachesize`,`outgoing_num_tcp`→`outgoingnumtcp`,`incoming_num_tcp`→`incomingnumtcp`,`cache_max_ttl`→`cachemaxttl`,`cache_min_ttl`→`cacheminttl` | `advanced/…` | copy if non-empty |
| `forwarding` (presence) and **not** `forward_tls_upstream` | `forwarding/enabled` | `1` |
| `forwarding` **and** `forward_tls_upstream` | `dots/dot[]` | for each `pf:system/dnsserver[i]`: APPEND `dot` (uuid `ctx.uuid("dns:general","dot<i>")`) with `enabled=1`, `type=dot`, `domain` empty, `server`=IP, `port`=`853`, `verify`=`pf:system/dns{i+1}host` (empty allowed), `forward_tcp_upstream=0`, `forward_first=0`, `description`="pf2opn DoT upstream". Also SET `forwarding/enabled`=`0` |
| `custom_options` | – | warning "custom options present (<n> lines) not migrated"; followup "re-create in /usr/local/etc/unbound.opnsense.d/*.conf" (never print content) |
| `enablessl`,`sslcertref`,`tlsport` | – | warning "DNS-over-TLS listener not migrated" |

**Host overrides** (`dnshost:<host>.<domain>`, translator `dns_host`, base_policy `merge` by
hostname+domain). `pf:unbound/hosts[]` → APPEND `opn:OPNsense/unboundplus/hosts/host`:
`enabled=1`, `hostname`←`host`, `domain`←`domain`, `rr`=`AAAA` if `ip` is IPv6 else `A`,
`server`←`ip`, `description`←`pf_desc(descr)`, `addptr=1`, plus empty `mxprio`, `mx`, `ttl`,
`txtdata`. A pfSense `ip` holding several comma-separated IPs → one `host` per IP (suffix
`/ip<n>` in the uuid) + warning. For each `aliases/item` → APPEND
`opn:OPNsense/unboundplus/aliases/alias`: `enabled=1`, `host`=uuid of the first host,
`hostname`←`host`, `domain`←`domain`, `description`←`description`.

**Static-map hosts** (`dnsstatic:<k>:<mac>`, translator `dns_static_host`): only discovered
when pfSense `unbound/regdhcpstatic` is present. One per DHCP staticmap with a non-empty
`hostname` and `ipaddr`. Domain = staticmap `domain`, else the dhcpd interface `domain`, else
`system/domain`. Deps: `dhcpres:<k>:<mac>`. If a `dns_host` item with the same host+domain
exists → UNSUPPORTED "duplicate of explicit host override". Render: like a host override
with description `"from DHCP static map"`.

**Domain overrides** (`dnsdom:<domain>`, translator `dns_domain`). `pf:unbound/domainoverrides[]` →
APPEND `opn:OPNsense/unboundplus/dots/dot`: `enabled=1`, `type=forward`, `domain`, `server`
= `ip` before `@`, `port` = after `@` or empty, `verify`←`tls_hostname`,
`forward_tcp_upstream=0`, `forward_first=0`, `description`←`pf_desc(descr)`. If `forward_tls_upstream` is
set for the override (`tls_hostname` present) → `type=dot`.

**ACLs** (`dnsacl:<aclid>`, translator `dns_acl`). `pf:unbound/acls[]` → APPEND
`opn:OPNsense/unboundplus/acls/acl`: `name` = `sanitize_name(aclname)`, `action` map
{`allow`→`allow`, `deny`→`deny`, `refuse`→`refuse`, `allow snoop`→`allow_snoop`,
`deny nonlocal`→`deny_non_local`, `refuse nonlocal`→`refuse_non_local`}, `networks` = the
comma-join of `row/acl_network + "/" + row/mask`, `description`←`pf_desc(description)`.
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
`finalize`: SET `system/nextuid` = max(existing, max uid + 1) and the same for `nextgid`.

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

**Tunnels** (`wgtun:<name>`, translator `wg_tunnel`, base_policy `merge` by instance).
`instance` = the integer N from `tun_wgN` (if N is already used in base → the next free
number + warning). `ifmap.wg_names[name] = f"wg{instance}"`. Render → APPEND
`opn:OPNsense/wireguard/server/servers/server`:

| opn | value |
|---|---|
| `enabled` | `1` if pf `enabled`=`yes` else `0` |
| `name` | `sanitize_name(descr or name)`, unique (suffix `_2`…) |
| `instance` | N |
| `pubkey` / `privkey` | `publickey` / `privatekey` |
| `port` | `listenport` |
| `mtu` | `mtu` |
| `dns` | empty |
| `tunneladdress` | comma-join of `addresses/row` `address/mask`. **If empty**, use the pfSense interface assigned to this tunnel (`interfaces/*/if == name`): `ipaddr/subnet` |
| `disableroutes` | `1` if any peer of this tunnel has `0.0.0.0/0` or `::/0` in allowed IPs, else `0` |
| `gateway` | when `disableroutes=1`: the `gateway` IP of the first pf gateway_item whose interface is this tunnel's assigned key; else empty |
| `peers` | comma-join of `ctx.uuid("wgpeer:<i>")` for selected peers with `tun == name` |
| `carp_depend_on`,`debug`,`endpoint`,`peer_dns` | empty / `0` |

Warnings: when `disableroutes=1` → "routes for peer allowed IPs disabled (a peer has a
default route); add static routes for remote subnets: <list of non-tunnel allowed-IP
networks>". If the tunnel isn't assigned to an interface → note "unassigned tunnel".
`finalize`: SET `OPNsense/wireguard/general/enabled` = `1`.

**Peers** (`wgpeer:<i>`, translator `wg_peer`). `tun` = `unassigned` or a tunnel not in the
source → UNSUPPORTED "peer not attached to a tunnel". Deps: `wgtun:<tun>`. Render → APPEND
`opn:OPNsense/wireguard/client/clients/client` (uuid = `ctx.uuid("wgpeer:<i>")`):
`enabled` (`yes`→`1`), `name`=`sanitize_name(descr)` (unique), `pubkey`←`publickey`,
`psk`←`presharedkey`, `tunneladdress`=comma-join of `allowedips/row` `address/mask`,
`serveraddress`←`endpoint`, `serverport`←`port`, `keepalive`←`persistentkeepalive`.

#### 12.1.18 Dynamic DNS (`dyndns.py`, requires_plugin `os-ddclient`, base_policy `merge`)
`pf:dyndnses/dyndns[]` → APPEND `opn:OPNsense/DynDNS/accounts/account`:
`enabled` (presence `enable`), `service` (`cloudflare`→`cloudflare`, `cloudflare-v6`→`cloudflare` + warning,
`dyndns`/`dyndns-static`/`dyndns-custom`→`dyndns2`, `custom`→`custom` with `server`←`updateurl` (PARTIAL),
others: if in `{"duckdns":"duckdns","noip":"noip","namecheap":"namecheap","he-net":"he-net","route53":"route53"}`
map, else UNSUPPORTED "provider <x> not mapped"), `protocol` empty, `username`, `password`,
`hostnames` = `domainname` if `host` is `@` else `host + "." + domainname`, `zone`←`domainname`,
`checkip`=`if`, `interface` = mapped `requestif` (or `interface`), `ttl` (empty → `300`),
`force_ssl=1`, `wildcard` (presence of `wildcard`), `description`←`pf_desc(descr)`.
`finalize`: SET `OPNsense/DynDNS/general/enabled`=`1`, `general/backend`=`opnsense`.
Followup: "install os-ddclient before importing, or the section stays inert until installed".

#### 12.1.19 Syslog (`syslog.py`, base_policy `merge`)
For N in `remoteserver`, `remoteserver2`, `remoteserver3` with text and `pf:syslog/enable`
present → APPEND `opn:OPNsense/Syslog/destinations/destination`: `enabled=1`, `transport` =
`udp6` if `ipproto`==`ipv6` else `udp4`, `hostname` = the part before `:`, `port` = the part
after `:` or `514`, `level` empty, `facility` empty, `program` empty, `rfc5424=0`,
`description`=`"pf2opn remote syslog"`. pfSense-only toggles (`filterdescriptions`,
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
DHCPv6 and router advertisements (Kea dhcp6 + `OPNsense/Radvd`). Dnsmasq as an alternative
DHCP backend (`--dhcp-backend dnsmasq`). DHCP custom options (`numberoptions` → `Kea/dhcp4/options`).
NPt (`nat/npt` → `Filter/npt`). Gateway groups (`/gateways/gateway_group+`). Firewall
schedules. Interface groups (`ifgroups`). Tunables (`sysctl/item[]`). SNMP (`os-net-snmp`).
UPnP/miniupnpd (`os-upnp`). UDP broadcast relay (`os-udpbroadcastrelay`). Avahi
(`os-mdns-repeater`). Filter separators → categories. OpenVPN instances. IPsec tunnels
(Swanctl). LAGG/bridge/PPPoE/GIF/GRE devices. Static ARP.

### 12.4 Unsupported catalogue (`unsupported.py`; each is one item, always UNSUPPORTED)
Emit an item only if the section/package is present **and non-empty** (an element with
children or text).

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
| `unsup:avahi` | `installedpackages/avahi` | "install os-mdns-repeater (Tier 2 candidate)" |
| `unsup:shellcmd` | `installedpackages/shellcmdsettings` or `system/earlyshellcmd`/`shellcmd` | "use /usr/local/etc/rc.syshook.d/ scripts" |
| `unsup:servicewatchdog` | `installedpackages/servicewatchdog` | "use Monit" |
| `unsup:openvpn_export` | `installedpackages/vpn_openvpn_export` | "built into OPNsense OpenVPN" |
| `unsup:l2tp` | `l2tp` with `mode` != `off` | "no L2TP server on OPNsense; use WireGuard or OpenVPN" |
| `unsup:ipsec` | `ipsec/phase1` exists | "IPsec tunnels not migrated in v1 (Tier 2)" |
| `unsup:openvpn` | `openvpn/openvpn-server` or `openvpn-client` | "OpenVPN not migrated in v1 (Tier 2)" |
| `unsup:shaper` | `shaper/queue` or `dnshaper/queue` | "traffic shaper models differ; rebuild" |
| `unsup:snmpd` | `snmpd` with `enable` | "install os-net-snmp (Tier 2); community string not shown" |
| `unsup:dhcpdv6` | `dhcpdv6/*/enable` | "Tier 2: Kea DHCPv6 / router advertisements" |
| `unsup:sysctl` | `sysctl/item` | "Tier 2: review tunables manually (System ▸ Settings ▸ Tunables)" |
| `unsup:ifgroups` | `ifgroups/ifgroupentry` | "Tier 2; package-created groups (WireGuard/Tailscale) aren't needed" |
| `unsup:sshdata` | `sshdata` | "SSH host keys not migrated; OPNsense generates new ones" |

`installedpackages/package`, `menu`, `service` are ignored.

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
   key, `wireguard`/`openvpn`/`enc0`, or empty; every `*_net` / `source|destination/network`
   token is `any`, `(self)`, IP/CIDR, `<key>`/`<key>ip` of an output interface, or an output
   alias name; every port token is numeric, a range, or a port-alias name; every filter
   `gateway` is an output gateway name.
6. **Gateways**: names unique; `interface` exists in output interfaces.
7. **Static routes**: `gateway` exists.
8. **Kea**: each reservation's `subnet` uuid exists; reservation IPs unique; pool ranges inside the subnet.
9. **WireGuard**: each server `peers` uuid exists in clients; instances unique; names unique.
10. **Unbound**: each alias `host` uuid exists.
11. **Aliases**: names unique; nested alias entries exist.
12. **system/dns{N}gw**: the named gateway exists.

Warnings (shown, not blocking): a base model version mismatch (§10.3); descriptions changed by
sanitizing; `nat/outbound/mode` automatic with snat rules present.

---

## 14. Report (`report.py`)

`render_report(plan, findings, src, out_path) -> str` (Markdown). Sections in order:
1. Header: pf2opn version, date, source hostname + pfSense `<version>`, base hostname,
   output path, SHA-256 of source and output.
2. **Interface map** table: pf key | pf descr | pf device → OPN device | included.
3. **Summary** table per section: migrated / skipped by user / not translatable.
4. **Migrated** per section: `- label` + indented warnings.
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

---

## 15. TUI (`tui/`)

`Pf2OpnApp(App)` gets `(src: SourceContext, args)`. Screen flow:
`LoadScreen → IfaceMapScreen → SelectScreen → ReviewScreen → DoneScreen`. Esc/`b` goes back
one screen. `q` quits from anywhere (confirm modal if the selection changed).

**LoadScreen**: a static summary. Source path, pfSense version, hostname, counts per
top-level section, base path, base hostname, and any base version-mismatch warnings. Keys:
`enter` continue.

**IfaceMapScreen**: a `DataTable` with columns `Incl | Key | Description | pfSense device |
Kind | OPNsense device`. VLAN/WG rows show their derived device (read-only, greyed). Physical
rows are editable. Keys:
* `space`: toggle Incl (not allowed for `unsupported` kind).
* `enter`: on a physical row, open `NicPickerModal`: a `Select` of `target_nics(src)` plus an
  `Input` for free text. Applies to every row that uses the same pf NIC.
* `c`: continue. Runs `validate_ifmap`. On errors, show them in a footer `Static` and stay.
  On success, `build_plan()` runs again (keeping selection by id).

**SelectScreen**: a horizontal split.
* Left: `ItemTree(Tree)`. The root holds one node per translator `title` (Tier 0/unsupported
  sections last, under a "Not translatable" node). Children are items.
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
  can't be toggled.

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
* `filter.xml`: 12 rules covering floating multi-iface, floating `any`, a disabled rule,
  gateway, alias src/dst, port alias, `(self)`, `opt1ip`, icmptype `any`, `WireGuard`
  group, an `l2tp` rule, an associated-rule-id rule, and a rule using a `pfB_` alias.
* `nat.xml`: 3 port forwards (one with associated rule, one with an alias target, one with
  `natreflection enable`), outbound hybrid with 4 rules (`wanip` target, `opt7ip`, `openvpnip`,
  `other-subnet`), and 2 one-to-one rules.
* `dhcp.xml`: two interfaces, explicit dnsserver on one, 3 static maps (one without an IP, one
  with an underscore hostname, one with a per-host DNS), and failover set on one interface.
* `unbound.xml`: hosts (including an alias item and a multi-IP host), 2 domain overrides (one with `@5353`),
  2 ACLs, `forward_tls_upstream` with 2 system DNS servers + `dns1host`/`dns2host`, `regdhcpstatic`, `custom_options`.
* `trust_users.xml`: 1 CA, 2 certs (one chain with 2 PEM blocks), admin + 1 user, admins group with extra member.
  **Certificates/keys must be freshly generated throwaway test material** (generate them once
  with `openssl` at fixture creation and commit them; never reuse anything from a real box).
* `wireguard.xml`: 2 tunnels (one with addresses, one using the interface IP), 4 peers (one
  unassigned, one with 0.0.0.0/0, one with a remote subnet). Keys are random 32-byte base64.
* `misc.xml`: gateways (incl. one on a missing interface), a static route, VIPs (one DNSBL),
  dyndns cloudflare, syslog remote, cron items, plus the unsupported package stubs.
* `tests/fixtures/opnsense/base_26_7.xml`: `config.xml.sample` from 26.7.8 (copied), with
  wan `if=igc0`, lan `if=igc1`, and version attributes on `OPNsense/Firewall/Filter` (`1.0.6`)
  and `dnsmasq` (`1.0.9`).

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
Run against `local/config-gateway…xml` (never committed) with Dave's real base export.
Import the output into a throwaway OPNsense 26.7 VM and confirm that the Firewall ▸ Rules,
NAT, Kea, Unbound, and WireGuard pages load without validation errors.

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

**M4 — Aliases, gateways, routes, VIPs** (§12.1.4–6, §12.1.12, §13 items 6, 7, 11)
Accept: goldens; `pfB_` and reserved names → UNSUPPORTED; a gateway on a missing interface → UNSUPPORTED.

**M5 — Filter rules** (§12.1.7, §13 item 5 for filter)
Accept: the goldens for `filter.xml`; ordering puts floating rules first; the dependency
cascade makes the pfB rule UNSUPPORTED; base rules are replaced (and kept with `--keep-base-rules`).

**M6 — NAT** (§12.1.8–10, §13 item 5 for NAT)
Accept: goldens; the associated filter rule is auto-selected via deps; `nat/outbound/mode` is set.

**M7 — Kea DHCPv4** (§12.1.11, §13 item 8)
Accept: goldens; Dnsmasq ranges for migrated interfaces are removed and Dnsmasq is disabled
when none remain; subnet ids start after the base max.

**M8 — Unbound** (§12.1.13, §13 item 10)
Accept: goldens incl. DoT upstreams with `verify` hostnames, static-map host synthesis,
duplicate suppression, and the ACL action mapping.

**M9 — Trust, users, groups** (§12.1.14–15)
Accept: goldens; chain trimming; uid collision handling; `system:root_password` only applies when selected.

**M10 — WireGuard, DynDNS, syslog, cron, unsupported catalogue** (§12.1.16–19, §12.4, §13 item 9)
Accept: goldens; `disableroutes` + gateway logic; the unassigned peer → UNSUPPORTED; the WG
interface has no ipaddr and the server takes it; the unsupported items appear in `inspect`.

**M11 — TUI shell: Load + IfaceMap screens** (§15)
Accept: pilot tests for mapping validation and the NIC picker.

**M12 — TUI Select screen** (§15, §8)
Accept: pilot tests for toggle, section toggle, cascade confirm, filter, and the detail tabs
showing redacted text.

**M13 — TUI Review/Write/Done + save selection** (§15, §9)
Accept: a pilot test writes the output; the saved selection JSON reproduces the identical
output through `migrate --selection` (byte-compare excluding `revision/time`).

**M14 — Hardening and real-config acceptance** (§13 full, §16.4, §16.6)
All validation checks, the secret-leak test, and README usage. Dave runs it against the real
config and imports the result into a VM. Fix whatever the import reveals (file follow-up issues).

---

## 18. Open questions for Dave (answer before M7 / M14)

* **Q1 DHCP backend**: Kea (recommended, D5) or Dnsmasq? You rely on pfSense
  `regdhcp`/`regdhcpstatic`. With Kea, static maps become Unbound host overrides (D6), but
  *dynamic* lease names stop resolving. Dnsmasq would keep dynamic names but maps less cleanly.
* **Q2 Base config**: OK to require `--base`, meaning a fresh 26.7 install on the new box with
  WAN/LAN assigned at the console and its config exported first? (The alternative is a bundled
  minimal base with no real NIC names, which I advise against.)
* **Q3 VM acceptance**: is there a Proxmox host where a throwaway OPNsense 26.7 VM can be used
  for the M14 import test?
* **Q4 Policy-routing VPNs**: ProtonVPN (`tun_wg0`) is used for policy routing, and the server
  tunnel `tun_wg1` has a peer with `0.0.0.0/0`. My rule turns routes off for both tunnels and
  warns about the remote subnets (192.168.8.0/24). OK, or do you want per-peer handling?
* **Q5 Admin password**: should `system:root_password` (copy pfSense admin hash to OPNsense
  root) default to off as designed?
* **Q6 Scope**: are the Tier 2 items you actually need soon UPnP, the UDP broadcast relay, and
  mDNS (Avahi)? If so, I'd spec them next as M15–M17.
