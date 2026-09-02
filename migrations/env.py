"""Alembic environment.

The database URL is resolved in this order:

1. ``-x db_url=...`` passed on the command line
2. ``sqlalchemy.url`` in ``alembic.ini`` (blank by default)
3. ``DATABASE_URL`` from application configuration

No URL is hard-coded, so credentials never enter version control.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config.settings import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would silently disable
    # every application logger already registered under a name not listed in
    # alembic.ini's [loggers] section (only root, sqlalchemy, and alembic are
    # listed there). That has nothing to do with configuring Alembic's own
    # logging, so it is turned off rather than accepted as a side effect.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """Return the database URL for this migration run."""
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override

    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured

    return get_settings().sqlalchemy_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
