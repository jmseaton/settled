"""The drift detector the deployment entrypoint relies on.

`create_all` never alters an existing table, so an upgrade that adds a
column leaves the database silently behind the code. These tests are the
reason the deployment can promise to *notice* that rather than discover it
from an `UndefinedColumn` error three pages later.
"""

from sqlalchemy import text

from app.db import Base, engine
from app.schema_check import detect_drift


def test_a_freshly_created_schema_has_no_drift():
    assert detect_drift(engine).clean


def test_a_missing_column_is_detected_and_named():
    # Exactly what pulling a version that added a column looks like from the
    # database's side: the table is there, one column is not.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE trades DROP COLUMN pnl_ticks"))

    drift = detect_drift(engine)
    try:
        assert not drift.clean
        assert drift.missing_columns["trades"] == ["pnl_ticks"]
        assert not drift.missing_tables

        report = drift.report()
        assert "pnl_ticks" in report
        # The report has to say what to do about it, not just that it
        # happened — it is read once, in a log, by someone whose app just
        # stopped working.
        assert "create_all() adds tables but never alters them" in report
        assert "docs/deployment.md" in report
    finally:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)


def test_a_missing_table_is_detected():
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS regime_markers CASCADE"))

    drift = detect_drift(engine)
    try:
        assert not drift.clean
        assert "regime_markers" in drift.missing_tables
        # A table reported as missing is not also reported column by column.
        assert "regime_markers" not in drift.missing_columns
    finally:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)


def test_an_extra_column_in_the_database_is_not_drift():
    # A rollback to an older app version leaves columns behind. Nothing
    # reads them and nothing breaks, so reporting them would train the
    # reader to ignore the warning that matters.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE trades ADD COLUMN retired_metric numeric"))

    try:
        assert detect_drift(engine).clean
    finally:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
