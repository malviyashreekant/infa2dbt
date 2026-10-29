import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent / "src"))
sys.path.insert(0, str(ROOT))

from infa2dbt.config import Config  # noqa: E402
from infa2dbt.pipeline import convert_paths  # noqa: E402


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    import make_fixtures
    make_fixtures.OUT.mkdir(exist_ok=True)
    (make_fixtures.OUT / "m_orders.xml").write_text(make_fixtures.orders(), encoding="utf-8")
    (make_fixtures.OUT / "m_customer_summary.xml").write_text(make_fixtures.customer_summary(), encoding="utf-8")
    return make_fixtures.OUT


@pytest.fixture(scope="session")
def converted(tmp_path_factory, fixtures_dir):
    out = tmp_path_factory.mktemp("dbt_out")
    res = convert_paths(sorted(fixtures_dir.glob("*.xml")), out, Config())
    return out, res
