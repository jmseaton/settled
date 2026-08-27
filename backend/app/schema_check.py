"""Does the live database still match the ORM metadata?

`create_all` creates tables that do not exist. It does **not** alter tables
that do. That is fine for a fresh install and quietly wrong for an upgrade:
pull a version that added a column, restart, and every table already exists,
so `create_all` does nothing and the app runs against a schema missing the
column. The failure surfaces later as an `UndefinedColumn` error from
whichever page happens to read it first, which is a long way from the cause.

So the entrypoint calls this and says so on startup. It reports rather than
repairs — guessing an `ALTER` for a column whose type or default it cannot
infer is how a personal tool loses data. Alembic is the migration tool of
record (§13); until the first revision exists, the honest interim is to know
the drift is there and to recreate the schema deliberately.

Extra columns in the database are not drift worth reporting: a rollback to
an older app version leaves them behind harmlessly, and nothing reads them.
Missing ones are the failure mode.
"""

from dataclasses import dataclass, field

from sqlalchemy import inspect

from app import models  # noqa: F401  — registers every mapper on Base.metadata
from app.db import Base, engine


@dataclass
class SchemaDrift:
    missing_tables: list[str] = field(default_factory=list)
    #: table -> the columns the ORM expects and the database does not have.
    missing_columns: dict[str, list[str]] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.missing_tables and not self.missing_columns

    def report(self) -> str:
        if self.clean:
            return "Schema matches the ORM metadata."

        lines = ["SCHEMA DRIFT — the database does not match this version of the app."]
        if self.missing_tables:
            lines.append(f"  Missing tables: {', '.join(sorted(self.missing_tables))}")
        for table in sorted(self.missing_columns):
            columns = ", ".join(sorted(self.missing_columns[table]))
            lines.append(f"  {table} is missing: {columns}")
        lines.append(
            "  create_all() adds tables but never alters them, so this will not "
            "resolve itself on restart."
        )
        lines.append("  See docs/deployment.md, 'Upgrading when the schema changed'.")
        return "\n".join(lines)


def detect_drift(target_engine=None) -> SchemaDrift:
    inspector = inspect(target_engine or engine)
    present = set(inspector.get_table_names())
    drift = SchemaDrift()

    for name, table in Base.metadata.tables.items():
        if name not in present:
            drift.missing_tables.append(name)
            continue
        actual = {c["name"] for c in inspector.get_columns(name)}
        missing = [c.name for c in table.columns if c.name not in actual]
        if missing:
            drift.missing_columns[name] = missing

    return drift


if __name__ == "__main__":
    print(detect_drift().report())
