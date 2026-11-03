"""Shared pytest configuration and fixtures (design §16)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

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


@pytest.fixture
def minimal_plan(minimal_path: Path, opnsense_base: Path):
    """(SourceContext, Plan) for the minimal fixture with a fixed NIC map."""
    from pf2opn.context import SourceContext
    from pf2opn.plan import build_default_ifmap, build_plan

    src = SourceContext.load(minimal_path, opnsense_base)
    ifmap = build_default_ifmap(src, {"em0": "igc0", "em1": "igc1"})
    return src, build_plan(src, ifmap)


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