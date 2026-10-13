"""ModelSpecification persistence: the header row and its eight child
association tables.

**Header.** Idempotent on ``ModelSpecification.subproject_id`` (one
specification per subproject in this increment, backed by a unique index).
Reuse never touches an existing specification's own fields or its
already-persisted child rows.

**Child rows** (species/reaction/gene_protein/compartment/
regulatory_interaction/enzyme_state/pathway). Unlike the header, these
tables carry no database uniqueness constraint (a specification may
legitimately need the identical reaction under two different roles, or the
same raw, unresolved identifier recorded twice is still harmless) -- so
idempotency here is enforced at the application level: each ``add_*``
function queries for an existing row with the identical full field set
before inserting, and reuses it rather than creating a duplicate. This
mirrors ``app.persistence.knowledge_gap``'s identity-key-based idempotency,
scaled down to a plain equality match since no hashing is needed for this
few a number of columns.

``raw_identifier`` is always preserved when supplied, regardless of whether
a resolved entity FK was also supplied -- never discarded (see
``app.models.model_specification``'s own module docstring).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import (
    ModelSpecificationGeneProteinRole,
    ModelSpecificationPathwayRole,
    ModelSpecificationReactionRole,
    ModelSpecificationSpeciesRole,
)
from app.models.model_specification import (
    ModelSpecification,
    ModelSpecificationCompartment,
    ModelSpecificationEnzymeState,
    ModelSpecificationGeneProtein,
    ModelSpecificationPathway,
    ModelSpecificationReaction,
    ModelSpecificationRegulatoryInteraction,
    ModelSpecificationSpecies,
)
from app.persistence.model_specification_types import (
    ModelSpecificationChildPersistenceResult,
    ModelSpecificationPersistenceResult,
)
from app.persistence.types import PersistenceAction


def _existing_specification_by_subproject(
    session: Session, subproject_id: UUID
) -> ModelSpecification | None:
    return session.execute(
        select(ModelSpecification).where(ModelSpecification.subproject_id == subproject_id)
    ).scalar_one_or_none()


def persist_model_specification(
    *, subproject_id: UUID, session: Session, notes: str | None = None
) -> ModelSpecificationPersistenceResult:
    """Persist one ``ModelSpecification`` header row. See module docstring."""
    existing = _existing_specification_by_subproject(session, subproject_id)
    if existing is not None:
        return ModelSpecificationPersistenceResult(
            action=PersistenceAction.REUSED_EXISTING,
            model_specification_id=existing.id,
            created=False,
            reused_existing=True,
            subproject_id=subproject_id,
            reason=(f"reused existing model specification {existing.id} for this subproject"),
        )

    try:
        with session.begin_nested():
            row = ModelSpecification(subproject_id=subproject_id, notes=notes)
            session.add(row)
            session.flush()
    except IntegrityError:
        raced_existing = _existing_specification_by_subproject(session, subproject_id)
        if raced_existing is not None:
            return ModelSpecificationPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                model_specification_id=raced_existing.id,
                created=False,
                reused_existing=True,
                subproject_id=subproject_id,
                reason="reused existing model specification after a concurrent insert won",
            )
        return ModelSpecificationPersistenceResult(
            action=PersistenceAction.FAILED,
            model_specification_id=None,
            created=False,
            reused_existing=False,
            subproject_id=subproject_id,
            reason=(
                "model specification creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    return ModelSpecificationPersistenceResult(
        action=PersistenceAction.CREATED,
        model_specification_id=row.id,
        created=True,
        reused_existing=False,
        subproject_id=subproject_id,
        reason="created new model specification",
    )


def get_model_specification(
    session: Session, model_specification_id: UUID
) -> ModelSpecification | None:
    """One ``ModelSpecification`` row by id, or ``None``. Read-only."""
    return session.get(ModelSpecification, model_specification_id)


def _add_child_row(
    *,
    session: Session,
    model_cls: type,
    table_name: str,
    identity_fields: dict[str, object],
    extra_fields: dict[str, object],
) -> ModelSpecificationChildPersistenceResult:
    """Shared add-one-child-row implementation for all seven child tables.

    ``identity_fields`` is the full set of columns that determine whether a
    repeat call is a duplicate (see module docstring); ``extra_fields``
    (``raw_identifier``/``notes``) are written only on creation and never
    participate in the duplicate check, consistent with "reuse never
    touches existing content".
    """
    where_clauses = [getattr(model_cls, name) == value for name, value in identity_fields.items()]
    existing = session.execute(select(model_cls).where(*where_clauses)).scalars().first()
    if existing is not None:
        return ModelSpecificationChildPersistenceResult(
            action=PersistenceAction.REUSED_EXISTING,
            child_id=existing.id,
            created=False,
            reused_existing=True,
            table_name=table_name,
            reason=f"reused existing {table_name} row with the identical field set",
        )

    try:
        with session.begin_nested():
            row = model_cls(**identity_fields, **extra_fields)
            session.add(row)
            session.flush()
    except IntegrityError:
        raced_existing = session.execute(select(model_cls).where(*where_clauses)).scalars().first()
        if raced_existing is not None:
            return ModelSpecificationChildPersistenceResult(
                action=PersistenceAction.REUSED_EXISTING,
                child_id=raced_existing.id,
                created=False,
                reused_existing=True,
                table_name=table_name,
                reason=f"reused existing {table_name} row after a concurrent insert won",
            )
        return ModelSpecificationChildPersistenceResult(
            action=PersistenceAction.FAILED,
            child_id=None,
            created=False,
            reused_existing=False,
            table_name=table_name,
            reason=(
                f"{table_name} row creation rolled back due to a database integrity "
                "violation, and no matching row was found on recheck"
            ),
        )

    return ModelSpecificationChildPersistenceResult(
        action=PersistenceAction.CREATED,
        child_id=row.id,
        created=True,
        reused_existing=False,
        table_name=table_name,
        reason=f"created new {table_name} row",
    )


def add_model_specification_species(
    *,
    model_specification_id: UUID,
    role: ModelSpecificationSpeciesRole,
    session: Session,
    compound_id: UUID | None = None,
    compartment_id: UUID | None = None,
    raw_identifier: str | None = None,
    notes: str | None = None,
) -> ModelSpecificationChildPersistenceResult:
    """Add one input/output/boundary/seed species reference. See module docstring."""
    return _add_child_row(
        session=session,
        model_cls=ModelSpecificationSpecies,
        table_name="model_specification_species",
        identity_fields={
            "model_specification_id": model_specification_id,
            "role": role,
            "compound_id": compound_id,
            "compartment_id": compartment_id,
        },
        extra_fields={"raw_identifier": raw_identifier, "notes": notes},
    )


def add_model_specification_reaction(
    *,
    model_specification_id: UUID,
    role: ModelSpecificationReactionRole,
    session: Session,
    reaction_id: UUID | None = None,
    raw_identifier: str | None = None,
    notes: str | None = None,
) -> ModelSpecificationChildPersistenceResult:
    """Add one seed/required/excluded reaction reference. See module docstring."""
    return _add_child_row(
        session=session,
        model_cls=ModelSpecificationReaction,
        table_name="model_specification_reaction",
        identity_fields={
            "model_specification_id": model_specification_id,
            "role": role,
            "reaction_id": reaction_id,
        },
        extra_fields={"raw_identifier": raw_identifier, "notes": notes},
    )


def add_model_specification_gene_protein(
    *,
    model_specification_id: UUID,
    role: ModelSpecificationGeneProteinRole,
    session: Session,
    gene_id: UUID | None = None,
    protein_id: UUID | None = None,
    localization_compartment_id: UUID | None = None,
    raw_identifier: str | None = None,
    notes: str | None = None,
) -> ModelSpecificationChildPersistenceResult:
    """Add one seed/important gene or protein reference.

    At most one of ``gene_id``/``protein_id`` should be set -- enforced by
    the caller, mirroring ``NetworkNode``'s own "at most one entity FK"
    application-level rule (see ``app.models.model_specification``).
    """
    if gene_id is not None and protein_id is not None:
        raise ValueError(
            "add_model_specification_gene_protein requires at most one of gene_id/protein_id"
        )
    return _add_child_row(
        session=session,
        model_cls=ModelSpecificationGeneProtein,
        table_name="model_specification_gene_protein",
        identity_fields={
            "model_specification_id": model_specification_id,
            "role": role,
            "gene_id": gene_id,
            "protein_id": protein_id,
            "localization_compartment_id": localization_compartment_id,
        },
        extra_fields={"raw_identifier": raw_identifier, "notes": notes},
    )


def add_model_specification_compartment(
    *,
    model_specification_id: UUID,
    session: Session,
    compartment_id: UUID | None = None,
    raw_identifier: str | None = None,
    notes: str | None = None,
) -> ModelSpecificationChildPersistenceResult:
    """Add one compartment-to-include reference. See module docstring."""
    return _add_child_row(
        session=session,
        model_cls=ModelSpecificationCompartment,
        table_name="model_specification_compartment",
        identity_fields={
            "model_specification_id": model_specification_id,
            "compartment_id": compartment_id,
        },
        extra_fields={"raw_identifier": raw_identifier, "notes": notes},
    )


def add_model_specification_regulatory_interaction(
    *,
    model_specification_id: UUID,
    session: Session,
    regulatory_interaction_id: UUID | None = None,
    raw_identifier: str | None = None,
    notes: str | None = None,
) -> ModelSpecificationChildPersistenceResult:
    """Add one regulatory-interaction-to-include reference. See module docstring."""
    return _add_child_row(
        session=session,
        model_cls=ModelSpecificationRegulatoryInteraction,
        table_name="model_specification_regulatory_interaction",
        identity_fields={
            "model_specification_id": model_specification_id,
            "regulatory_interaction_id": regulatory_interaction_id,
        },
        extra_fields={"raw_identifier": raw_identifier, "notes": notes},
    )


def add_model_specification_enzyme_state(
    *,
    model_specification_id: UUID,
    session: Session,
    enzyme_state_id: UUID | None = None,
    raw_identifier: str | None = None,
    notes: str | None = None,
) -> ModelSpecificationChildPersistenceResult:
    """Add one PTM/protein-state-to-include reference. See module docstring."""
    return _add_child_row(
        session=session,
        model_cls=ModelSpecificationEnzymeState,
        table_name="model_specification_enzyme_state",
        identity_fields={
            "model_specification_id": model_specification_id,
            "enzyme_state_id": enzyme_state_id,
        },
        extra_fields={"raw_identifier": raw_identifier, "notes": notes},
    )


def add_model_specification_pathway(
    *,
    model_specification_id: UUID,
    role: ModelSpecificationPathwayRole,
    session: Session,
    external_pathway_id: str | None = None,
    name: str | None = None,
    notes: str | None = None,
) -> ModelSpecificationChildPersistenceResult:
    """Add one include/exclude pathway-scope reference. See module docstring."""
    return _add_child_row(
        session=session,
        model_cls=ModelSpecificationPathway,
        table_name="model_specification_pathway",
        identity_fields={
            "model_specification_id": model_specification_id,
            "role": role,
            "external_pathway_id": external_pathway_id,
            "name": name,
        },
        extra_fields={"notes": notes},
    )


__all__ = [
    "add_model_specification_compartment",
    "add_model_specification_enzyme_state",
    "add_model_specification_gene_protein",
    "add_model_specification_pathway",
    "add_model_specification_reaction",
    "add_model_specification_regulatory_interaction",
    "add_model_specification_species",
    "get_model_specification",
    "persist_model_specification",
]
