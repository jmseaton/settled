"""Hardening findings from the 2026-08 penetration test.

None of these were exploitable break-ins; they are the gaps that pass a
black-box probe and still deserve closing. Each test names the finding it
covers so a future reader can tell what it is defending.
"""

import io

import pytest
from fastapi.testclient import TestClient

from app.importer.pipeline import import_file
from app.main import app
from app.models import ImportFile
from app.parsers.flex import FlexParseError, parse_flex

# Five nested levels of ten. A real attack uses nine or more; this is enough
# to prove expansion happens without asking CI to allocate a gigabyte.
BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY a "aaaaaaaaaa">
 <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
 <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
 <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
 <!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">
]>
<FlexQueryResponse><x>&e;</x></FlexQueryResponse>"""

XXE = """<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<FlexQueryResponse><x>&x;</x></FlexQueryResponse>"""


def test_entity_expansion_is_refused():
    """§3.6 — ElementTree expands internal entities with no configurable
    limit, so a small upload becomes gigabytes of memory. The DTD is what
    carries them, and a Flex statement never has one."""
    with pytest.raises(FlexParseError) as caught:
        parse_flex(BILLION_LAUGHS)
    assert "DTD" in str(caught.value)


def test_external_entity_is_refused_at_the_same_gate():
    # ElementTree already declined to resolve these; now they do not reach
    # the parser at all.
    with pytest.raises(FlexParseError):
        parse_flex(XXE)


def test_a_doctype_inside_a_comment_is_not_a_dtd():
    """The check is a real parse, not a text search — a comment mentioning
    <!DOCTYPE is legal and must still import."""
    doc = (
        "<?xml version='1.0'?><!-- mentions <!DOCTYPE lolz [ in passing -->"
        "<FlexQueryResponse><FlexStatements count='0'></FlexStatements></FlexQueryResponse>"
    )
    # Reaches the ordinary "no statement in here" path rather than the DTD one.
    with pytest.raises(FlexParseError) as caught:
        parse_flex(doc)
    assert "DTD" not in str(caught.value)


def test_the_real_flex_fixture_still_imports(client_with_flex):
    """The guard must not cost the happy path. This is the whole fixture
    going through the API, so a regression that rejected real statements
    would fail here rather than in production at 05:00."""
    response = client_with_flex.get("/api/executions")
    assert response.status_code == 200
    assert response.json()["total"] > 0


def test_undecodable_upload_raises_a_parse_error_not_a_decode_error(db, tmp_path, monkeypatch):
    """A binary upload raised UnicodeDecodeError, which nothing caught and
    FastAPI served as a 500. It is a bad file, so it now takes the same path
    as any other unreadable upload: recorded FAILED, then re-raised for the
    API to turn into a 400."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path), raising=False)
    with pytest.raises(FlexParseError) as caught:
        import_file(db, "junk.xml", b"<FlexQueryResponse>\xff\xfe\x00bad</FlexQueryResponse>")
    assert "UTF-8" in str(caught.value)

    # The audit trail is the point of the FAILED record — a rejected upload
    # should still be visible afterwards.
    record = db.query(ImportFile).filter(ImportFile.filename == "junk.xml").one()
    assert record.status.value == "FAILED"
    assert any("UTF-8" in w for w in record.warnings)


def test_malformed_uploads_answer_400_through_the_api(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path), raising=False)
    client = TestClient(app)
    for label, payload in [
        ("binary", b"\xff\xfe\x00\x01"),
        ("billion laughs", BILLION_LAUGHS.encode()),
        ("xxe", XXE.encode()),
    ]:
        response = client.post(
            "/api/import", files={"file": ("x.xml", io.BytesIO(payload), "text/xml")}
        )
        assert response.status_code == 400, f"{label} gave {response.status_code}"
