"""Engine and session management.

The engine and session factory are process-cached rather than created at import
time, so that configuration changes (notably in tests) take effect and no
connection is attempted merely by importing a module.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the cached SQLAlchemy engine for the configured database."""
    settings = get_settings()
    return create_engine(
        settings.sqlalchemy_url,
        echo=settings.database_echo,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Return the cached session factory bound to the application engine."""
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a database session.

    The session is rolled back on error and always closed. Committing is the
    responsibility of the service performing the write, so that a request cannot
    partially persist a scientific record.
    """
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Dispose the cached engine and session factory.

    Used when configuration changes within a process, for example between test
    sessions. Not used during normal request handling.
    """
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
