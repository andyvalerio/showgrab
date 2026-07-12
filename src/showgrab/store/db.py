"""SQLite engine/session setup shared by every store."""

from __future__ import annotations

from sqlalchemy import create_engine
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


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
