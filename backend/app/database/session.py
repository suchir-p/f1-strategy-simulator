from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(self, url: str) -> None:
        self.engine: Engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.sessions()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()
