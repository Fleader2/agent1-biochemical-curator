"""Gene records.

See ``docs/02_database_schema.md`` ("Table: gene").
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.organism import Organism
    from app.models.protein import Protein


class Gene(Base):
    """An organism-specific gene.

    ``sgd_id``, ``ncbi_gene_id``, ``uniprot_id``, and ``kegg_gene_id`` are the
    four external-identifier columns the specification defines on this table;
    each is unique when present, applied globally rather than scoped by
    ``organism_id`` (``docs/02_database_schema.md``: "Uniqueness should be
    enforced where reliable external identifiers exist"). No uniqueness is
    placed on ``symbol`` or ``systematic_name``: the specification requires it
    only for the four identifier columns above.
    """

    __tablename__ = "gene"
    __table_args__ = (
        Index("uq_gene_sgd_id", "sgd_id", unique=True, postgresql_where=text("sgd_id IS NOT NULL")),
        Index(
            "uq_gene_ncbi_gene_id",
            "ncbi_gene_id",
            unique=True,
            postgresql_where=text("ncbi_gene_id IS NOT NULL"),
        ),
        Index(
            "uq_gene_uniprot_id",
            "uniprot_id",
            unique=True,
            postgresql_where=text("uniprot_id IS NOT NULL"),
        ),
        Index(
            "uq_gene_kegg_gene_id",
            "kegg_gene_id",
            unique=True,
            postgresql_where=text("kegg_gene_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    organism_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organism.id", ondelete="RESTRICT"), nullable=False
    )

    symbol: Mapped[str | None] = mapped_column(String, index=True)
    systematic_name: Mapped[str | None] = mapped_column(String, index=True)
    name: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)

    sgd_id: Mapped[str | None] = mapped_column(String)
    ncbi_gene_id: Mapped[str | None] = mapped_column(String)
    uniprot_id: Mapped[str | None] = mapped_column(String)
    kegg_gene_id: Mapped[str | None] = mapped_column(String)

    chromosome: Mapped[str | None] = mapped_column(String)

    aliases_json: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    organism: Mapped[Organism] = relationship(back_populates="genes")
    proteins: Mapped[list[Protein]] = relationship(back_populates="gene")
