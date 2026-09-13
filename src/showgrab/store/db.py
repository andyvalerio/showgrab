"""SQLite engine/session setup shared by every store."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from sqlalchemy.orm import Session


class Base(DeclarativeBase):
    pass


def make_engine(db_path: str):
    """db_path is a filesystem path (real usage) or ':memory:' (tests).

    ':memory:' uses StaticPool so every session shares the same in-memory
    database — SQLite's default is a fresh, independent DB per connection,
    which would make a normal connection pool useless here. Tests use this to
    exercise the exact same ORM/serialization code with zero disk I/O (SQLite
    commits fsync on WSL2's filesystem, which otherwise dominates test time
    without adding any real coverage — the thing actually under test is our
    mapping/serialization code, not sqlite3's on-disk durability).
    """
    if db_path == ":memory:":
        return create_engine(
            "sqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(f"sqlite:///{db_path}", future=True)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine) -> None:
    """Apply additive schema changes to a database that already exists
    (REQ-SG-049).

    create_all() creates missing TABLES but never missing COLUMNS, so shipping
    a new mapped column would otherwise leave every existing deployment
    raising "no such column" on the first query — the tests would all pass,
    because they build the schema from scratch every run.

    Strictly additive: it only ever ADDs columns the model declares and the
    table lacks, never drops, renames or retypes. A new non-nullable column
    needs a server_default for SQLite to backfill existing rows, so one
    without a default is reported rather than half-applied."""
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue  # create_all just made it, with every column present
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = (
                f"ALTER TABLE {table.name} ADD COLUMN "
                f"{column.name} {column.type.compile(engine.dialect)}"
            )
            if column.server_default is not None:
                ddl += f" DEFAULT {column.server_default.arg.text}"
            elif not column.nullable:
                raise RuntimeError(
                    f"cannot add non-nullable column {table.name}.{column.name} to an "
                    "existing table without a server_default to backfill existing rows"
                )
            with engine.begin() as conn:
                conn.execute(text(ddl))


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
