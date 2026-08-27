import os
import pathlib

os.environ.setdefault(
    "SETTLED_DATABASE_URL", "postgresql+psycopg://settled:settled_dev@localhost:5432/settled_test"
)
# §16.1 — "mocked HTTP, no live calls in CI". The benchmark price fetch
# (§7.5) rides the sync schedule, so leaving it enabled would have every
# sync test reach for a price provider. Tests that exercise the fetch pass
# their own adapter explicitly.
os.environ.setdefault("SETTLED_PRICE_FETCH_ENABLED", "false")

import pytest
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.importer.pipeline import import_tlg_file

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "fixtures"
ACCOUNT_ID = "U0000000"


@pytest.fixture(autouse=True)
def clean_schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def complete_report(db):
    return import_tlg_file(db, "fixture-complete.tlg", _read("fixture-complete.tlg"))


@pytest.fixture
def orphan_report(db):
    return import_tlg_file(db, "fixture-orphan.tlg", _read("fixture-orphan.tlg"))


@pytest.fixture
def complete_bytes():
    return _read("fixture-complete.tlg")


@pytest.fixture
def client_with_flex(tmp_path, monkeypatch):
    """A TestClient with the Flex fixture already imported and the §3.2
    payload store pointed at a temp directory."""
    import io

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path), raising=False)
    client = TestClient(app)
    content = (FIXTURES / "fixture-flex.xml").read_bytes()
    client.post("/api/import", files={"file": ("fixture-flex.xml", io.BytesIO(content), "text/xml")})
    return client
