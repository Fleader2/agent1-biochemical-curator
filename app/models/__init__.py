"""SQLAlchemy ORM models.

No scientific tables are defined yet. The schema in ``docs/02_database_schema.md``
is implemented in a later phase; models added here are picked up automatically by
Alembic through ``app.db.base.Base.metadata``.
"""

from app.db.base import Base

__all__ = ["Base"]
