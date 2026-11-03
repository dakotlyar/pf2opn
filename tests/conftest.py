"""Shared pytest configuration and fixtures (design §16)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable

import pytest
from lxml import etree

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
PFSENSE_DIR = FIXTURES_DIR / "pfsense"
OPNSENSE_DIR = FIXTURES_DIR / "opnsense"
GOLDEN_DIR = TESTS_DIR / "golden"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite golden files with the current output (review the diff!)",
    )


@pytest.fixture(scope="session")
def pfsense_dir() -> Path:
    return PFSENSE_DIR


@pytest.fixture(scope="session")
def opnsense_base() -> Path:
    return OPNSENSE_DIR / "base_26_7.xml"


@pytest.fixture
def minimal_path(pfsense_dir: Path) -> Path:
    return pfsense_dir / "minimal.xml"


def _fixture_plan(name: str, base: Path, nic_map: dict[str, str]):
    from pf2opn.context import SourceContext
    from pf2opn.ifmap import default_ifmap
    from pf2opn.plan import build_plan

    src = SourceContext.load(PFSENSE_DIR / name, base)
    return src, build_plan(src, default_ifmap(src, nic_map))


@pytest.fixture
def minimal_plan(minimal_path: Path, opnsense_base: Path):
    """(SourceContext, Plan) for the minimal fixture with a fixed NIC map."""
    return _fixture_plan("minimal.xml", opnsense_base, {"em0": "igc0", "em1": "igc1"})


@pytest.fixture
def interfaces_vlans_plan(opnsense_base: Path):
    """(SourceContext, Plan) for ``interfaces_vlans.xml`` (fixed NIC map)."""
    return _fixture_plan("interfaces_vlans.xml", opnsense_base, {"em0": "igc0", "em1": "igc1"})


@pytest.fixture
def aliases_plan(opnsense_base: Path):
    """(SourceContext, Plan) for ``aliases.xml``."""
    return _fixture_plan("aliases.xml", opnsense_base, {"em0": "igc0", "em1": "igc1"})


@pytest.fixture
def filter_plan(opnsense_base: Path):
    """(SourceContext, Plan) for ``filter.xml`` (fixed NIC map)."""
    return _fixture_plan(
        "filter.xml", opnsense_base, {"em0": "igc0", "em1": "igc1", "em2": "igc2"}
    )


@pytest.fixture
def nat_plan(opnsense_base: Path):
    """(SourceContext, Plan) for ``nat.xml`` (fixed NIC map)."""
    return _fixture_plan(
        "nat.xml",
        opnsense_base,
        {"em0": "igc0", "em1": "igc1", "em2": "igc2", "em3": "igc3"},
    )


@pytest.fixture
def dhcp_plan(opnsense_base: Path):
    """(SourceContext, Plan) for ``dhcp.xml`` (fixed NIC map)."""
    return _fixture_plan(
        "dhcp.xml", opnsense_base, {"em0": "igc0", "em1": "igc1", "em2": "igc2"}
    )


@pytest.fixture
def misc_plan(opnsense_base: Path):
    """(SourceContext, Plan) for ``misc.xml``."""
    return _fixture_plan(
        "misc.xml", opnsense_base, {"em0": "igc0", "em1": "igc1", "em2": "igc2"}
    )


@pytest.fixture
def section_placements() -> Callable:
    """Render the default-selected, migratable items of one section (§16.2)."""
    from pf2opn.context import WriteContext
    from pf2opn.model import Status
    from pf2opn.registry import get

    def collect(src, plan, section: str) -> list:
        translator = get(section)
        ctx = WriteContext(src, plan)
        placements = []
        for item in plan.items:
            if (
                item.section == section
                and item.id in plan.selected
                and item.status != Status.UNSUPPORTED
            ):
                placements.extend(translator.render(item, ctx))
        return placements

    return collect


@pytest.fixture
def wrap_placements() -> Callable[[list], str]:
    """Wrap rendered placements as a golden ``<golden>`` document (§16.2)."""

    def wrap(placements) -> str:
        root = etree.Element("golden")
        for placement in placements:
            node = etree.SubElement(root, "placement")
            node.set("path", placement.path)
            node.set("mode", placement.mode.value)
            if placement.element is not None:
                node.append(copy.deepcopy(placement.element))
            elif placement.text is not None:
                node.text = placement.text
        return etree.tostring(root, pretty_print=True, encoding="unicode")

    return wrap


@pytest.fixture
def golden_checker(request: pytest.FixtureRequest) -> Callable[[str, str], None]:
    """Return a callable comparing a string against a golden file.

    With ``--update-golden`` the golden is (re)written instead of compared.
    """
    update = bool(request.config.getoption("--update-golden"))

    def check(relative_path: str, actual: str) -> None:
        path = GOLDEN_DIR / relative_path
        if update:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not actual.endswith("\n"):
                actual += "\n"
            path.write_text(actual, encoding="utf-8")
            return
        assert path.exists(), (
            f"missing golden {path}; review the output and run "
            f"pytest --update-golden to create it"
        )
        expected = path.read_text(encoding="utf-8")
        assert actual == expected, (
            f"golden mismatch for {relative_path}; review the diff and run "
            f"pytest --update-golden to accept it"
        )

    return check