"""Tests for engine and session infrastructure."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.db.session import get_engine, get_session, get_sessionmaker

pytestmark = pytest.mark.database


def test_engine_is_cached(migrated_engine: Engine) -> None:
    assert get_engine() is migrated_engine


def test_engine_targets_configured_database(migrated_engine: Engine) -> None:
    assert migrated_engine.url.render_as_string(hide_password=False) == (
        get_settings().sqlalchemy_url
    )


def test_sessionmaker_is_bound_to_engine(migrated_engine: Engine) -> None:
    assert get_sessionmaker().kw["bind"] is migrated_engine


def test_get_session_yields_usable_session(migrated_engine: Engine) -> None:
    del migrated_engine
    generator = get_session()
    session = next(generator)

    try:
        assert isinstance(session, Session)
        assert session.execute(text("SELECT 1")).scalar() == 1
    finally:
        generator.close()


def test_get_session_rolls_back_on_error(migrated_engine: Engine) -> None:
    """An exception raised by the caller must not leave a transaction open."""
    del migrated_engine
    generator = get_session()
    session = next(generator)
    session.execute(text("SELECT 1"))
    assert session.in_transaction()

    with pytest.raises(RuntimeError):
        generator.throw(RuntimeError("request failed"))

    assert not session.in_transaction()
