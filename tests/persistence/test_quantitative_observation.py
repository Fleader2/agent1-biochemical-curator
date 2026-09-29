"""Tests for ``app.persistence.quantitative_observation`` (Agent 1.x Increment
"Experimental Context and Quantitative Observation Framework" / "SGD Reference
Protein Abundance Integration")."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    ExperimentalContextClassification,
    QuantitativeEvidenceClass,
    SourceType,
)
from app.models.experimental_context import ExperimentalContext
from app.models.organism import Organism
from app.models.protein import Protein
from app.models.quantitative_observation import QuantitativeObservation
from app.normalization.quantitative_observation import (
    QuantitativeObservationIdentity,
    QuantitativeObservationType,
)
from app.persistence.quantitative_observation import (
    get_or_create_sgd_abundance_reference_context,
    persist_quantitative_observation,
)
from app.persistence.quantitative_observation_types import (
    QuantitativeObservationPersistenceResult,
)
from app.persistence.types import PersistenceAction

pytestmark = pytest.mark.database


def _identity(**overrides: object) -> QuantitativeObservationIdentity:
    base = {
        "observation_type": QuantitativeObservationType.PROTEIN_ABUNDANCE.value,
        "value": Decimal("6670"),
        "unit": "molecules/cell",
        "evidence_class": QuantitativeEvidenceClass.REFERENCE_BASELINE,
        "source": SourceType.SGD,
        "source_id": f"sgd-protein-abundance:{uuid4()}",
    }
    base.update(overrides)
    return QuantitativeObservationIdentity(**base)


def _organism(session: Session) -> Organism:
    organism = Organism(scientific_name=f"Test organism {uuid4()}")
    session.add(organism)
    session.flush()
    return organism


def _protein(session: Session, *, organism: Organism | None = None) -> Protein:
    organism = organism or _organism(session)
    row = Protein(organism_id=organism.id, name="test protein")
    session.add(row)
    session.flush()
    return row


# --- create --------------------------------------------------------------------------


def test_create_new_observation(db_session):
    identity = _identity()
    result = persist_quantitative_observation(identity, session=db_session)

    assert isinstance(result, QuantitativeObservationPersistenceResult)
    assert result.action is PersistenceAction.CREATED
    assert result.created is True
    assert result.quantitative_observation_id is not None

    row = db_session.get(QuantitativeObservation, result.quantitative_observation_id)
    assert row is not None
    assert row.observation_type == QuantitativeObservationType.PROTEIN_ABUNDANCE.value
    assert row.value == Decimal("6670")
    assert row.unit == "molecules/cell"
    assert row.evidence_class is QuantitativeEvidenceClass.REFERENCE_BASELINE
    assert row.normalized_value == Decimal("6670")
    assert row.normalized_unit == "molecules_per_cell"


def test_create_preserves_uncertainty(db_session):
    identity = _identity(uncertainty=Decimal("1539"))
    result = persist_quantitative_observation(identity, session=db_session)
    row = db_session.get(QuantitativeObservation, result.quantitative_observation_id)
    assert row.uncertainty == Decimal("1539")


def test_create_passes_through_resolved_protein_and_organism(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism=organism)
    identity = _identity(protein_id=protein.id, organism_id=organism.id)
    result = persist_quantitative_observation(identity, session=db_session)
    row = db_session.get(QuantitativeObservation, result.quantitative_observation_id)
    assert row.protein_id == protein.id
    assert row.organism_id == organism.id


# --- idempotent replay -----------------------------------------------------------------


def test_exact_replay_reuses_existing_row(db_session):
    identity = _identity()
    first = persist_quantitative_observation(identity, session=db_session)
    second = persist_quantitative_observation(identity, session=db_session)

    assert first.action is PersistenceAction.CREATED
    assert second.action is PersistenceAction.REUSED_EXISTING
    assert second.quantitative_observation_id == first.quantitative_observation_id

    rows = (
        db_session.execute(
            select(QuantitativeObservation).where(
                QuantitativeObservation.source_id == identity.source_id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_independent_observations_with_identical_value_both_persist(db_session):
    """Never numeric-equality-based deduplication -- distinct source_id, both persist."""
    identity_a = _identity(source_id="sgd-protein-abundance:a", value=Decimal("1000"))
    identity_b = _identity(source_id="sgd-protein-abundance:b", value=Decimal("1000"))

    result_a = persist_quantitative_observation(identity_a, session=db_session)
    result_b = persist_quantitative_observation(identity_b, session=db_session)

    assert result_a.action is PersistenceAction.CREATED
    assert result_b.action is PersistenceAction.CREATED
    assert result_a.quantitative_observation_id != result_b.quantitative_observation_id


def test_reuse_never_overwrites_existing_row(db_session):
    identity = _identity(value=Decimal("1000"), notes="first")
    first = persist_quantitative_observation(identity, session=db_session)

    replay_with_different_notes = _identity(
        source=identity.source,
        source_id=identity.source_id,
        value=Decimal("1000"),
        notes="second",
    )
    persist_quantitative_observation(replay_with_different_notes, session=db_session)

    row = db_session.get(QuantitativeObservation, first.quantitative_observation_id)
    assert row.notes == "first"  # never rewritten by the "replay"


def test_no_source_identity_always_creates_a_new_row(db_session):
    """A caller with no deterministic source identity cannot be idempotency-checked at
    all -- this module never guesses at equality, it always creates."""
    identity_a = _identity(source=None, source_id=None, value=Decimal("42"))
    identity_b = _identity(source=None, source_id=None, value=Decimal("42"))

    result_a = persist_quantitative_observation(identity_a, session=db_session)
    result_b = persist_quantitative_observation(identity_b, session=db_session)

    assert result_a.action is PersistenceAction.CREATED
    assert result_b.action is PersistenceAction.CREATED
    assert result_a.quantitative_observation_id != result_b.quantitative_observation_id


# --- get_or_create_sgd_abundance_reference_context --------------------------------------


def test_get_or_create_context_creates_reference_context(db_session):
    organism = _organism(db_session)
    context = get_or_create_sgd_abundance_reference_context(db_session, organism_id=organism.id)

    assert isinstance(context, ExperimentalContext)
    assert context.organism_id == organism.id
    assert context.classification is ExperimentalContextClassification.REFERENCE
    assert context.source is SourceType.SGD
    # Never invents medium/growth phase/temperature/pH -- SGD documents none of these.
    assert context.medium is None
    assert context.growth_phase is None
    assert context.temperature_c is None
    assert context.ph is None
    assert context.strain is None


def test_get_or_create_context_is_idempotent(db_session):
    organism = _organism(db_session)
    first = get_or_create_sgd_abundance_reference_context(db_session, organism_id=organism.id)
    second = get_or_create_sgd_abundance_reference_context(db_session, organism_id=organism.id)

    assert first.id == second.id
    rows = (
        db_session.execute(
            select(ExperimentalContext).where(ExperimentalContext.organism_id == organism.id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_get_or_create_context_distinct_per_organism(db_session):
    organism_a = _organism(db_session)
    organism_b = _organism(db_session)
    context_a = get_or_create_sgd_abundance_reference_context(db_session, organism_id=organism_a.id)
    context_b = get_or_create_sgd_abundance_reference_context(db_session, organism_id=organism_b.id)

    assert context_a.id != context_b.id


def test_observation_can_attach_to_the_reusable_reference_context(db_session):
    organism = _organism(db_session)
    context = get_or_create_sgd_abundance_reference_context(db_session, organism_id=organism.id)
    identity = _identity(organism_id=organism.id, experimental_context_id=context.id)

    result = persist_quantitative_observation(identity, session=db_session)
    row = db_session.get(QuantitativeObservation, result.quantitative_observation_id)
    assert row.experimental_context_id == context.id
