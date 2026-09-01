"""Publication metadata records.

See ``docs/02_database_schema.md`` ("Table: publication").
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.claim import Evidence
    from app.models.kinetic_measurement import KineticMeasurement


class Publication(Base):
    """Metadata for a scientific publication.

    ``pmid``, ``pmcid``, and ``doi`` should each be unique when present
    (``docs/02_database_schema.md``), implemented as partial unique indexes so
    that multiple publications may share a ``NULL`` value for any of them.
    """

    __tablename__ = "publication"
    __table_args__ = (
        Index(
            "uq_publication_pmid", "pmid", unique=True, postgresql_where=text("pmid IS NOT NULL")
        ),
        Index(
            "uq_publication_pmcid",
            "pmcid",
            unique=True,
            postgresql_where=text("pmcid IS NOT NULL"),
        ),
        Index(
            "uq_publication_doi", "doi", unique=True, postgresql_where=text("doi IS NOT NULL")
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    pmid: Mapped[str | None] = mapped_column(String)
    pmcid: Mapped[str | None] = mapped_column(String)
    doi: Mapped[str | None] = mapped_column(String)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    journal: Mapped[str | None] = mapped_column(String)
    year: Mapped[int | None] = mapped_column()

    authors_json: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONB)

    abstract: Mapped[str | None] = mapped_column(Text)

    open_access: Mapped[bool | None] = mapped_column(Boolean)
    full_text_available: Mapped[bool | None] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    evidence_records: Mapped[list[Evidence]] = relationship(back_populates="publication")
    kinetic_measurements: Mapped[list[KineticMeasurement]] = relationship(
        back_populates="publication"
    )
