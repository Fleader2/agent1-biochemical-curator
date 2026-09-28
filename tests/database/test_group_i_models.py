"""Schema tests for Group I: experimental context and quantitative observation
framework (Agent 1.x Increment "Experimental Context and Quantitative
Observation Framework").

Covers ``ExperimentalContext``, ``Perturbation``, ``QuantitativeObservation``,
``QuantitativeObservationDependency`` (migration ``0017_exp_context_qobs``).
See ``docs/27_experimental_context_and_quantitative_observation_framework.md``
for the full design rationale.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    ExperimentalContextClassification,
    QuantitativeEvidenceClass,
    SourceType,
    TimeReferenceBasis,
)
from app.models.experimental_context import ExperimentalContext
from app.models.organism import Organism
from app.models.perturbation import Perturbation
from app.models.protein import Protein
from app.models.quantitative_observation import (
    QuantitativeObservation,
    QuantitativeObservationDependency,
)
from app.models.reaction import Reaction

pytestmark = pytest.mark.database


def _organism(session, suffix="i") -> Organism:
    organism = Organism(scientific_name=f"Test organism {suffix}")
    session.add(organism)
    session.flush()
    return organism


def _protein(session, organism_id, suffix="i") -> Protein:
    protein = Protein(organism_id=organism_id, name=f"Test protein {suffix}")
    session.add(protein)
    session.flush()
    return protein


def _reaction(session, suffix="i") -> Reaction:
    reaction = Reaction(internal_id=f"TEST_QOBS_R_{suffix}_{uuid4().hex[:8]}", name="test reaction")
    session.add(reaction)
    session.flush()
    return reaction


def _context(session, **overrides) -> ExperimentalContext:
    context = ExperimentalContext(**overrides)
    session.add(context)
    session.flush()
    return context


def _observation(session, **overrides) -> QuantitativeObservation:
    defaults = {
        "observation_type": "PROTEIN_ABUNDANCE",
        "value": Decimal("100"),
        "unit": "molecules/cell",
        "evidence_class": QuantitativeEvidenceClass.REFERENCE_BASELINE,
    }
    defaults.update(overrides)
    obs = QuantitativeObservation(**defaults)
    session.add(obs)
    session.flush()
    return obs


# --- Scenario: static reference observation -------------------------------------------------


def test_static_reference_observation(db_session):
    """A bare reference-baseline observation with no time/perturbation/replicate at all."""
    organism = _organism(db_session)
    obs = _observation(
        db_session,
        organism_id=organism.id,
        evidence_class=QuantitativeEvidenceClass.REFERENCE_BASELINE,
    )
    assert obs.time_reference_basis is None
    assert obs.perturbation_id is None
    assert obs.biological_replicate_id is None


# --- Scenario: time-series observations -------------------------------------------------------


def test_time_series_observation_relative_to_experiment_start(db_session):
    obs = _observation(
        db_session,
        observation_type="GROWTH_RATE",
        value=Decimal("0.3"),
        unit="h-1",
        evidence_class=QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC,
        time_reference_basis=TimeReferenceBasis.EXPERIMENT_START,
        time_value=Decimal("2"),
        time_unit="h",
        time_canonical_s=Decimal("7200"),
    )
    assert obs.time_canonical_s == Decimal("7200")
    assert obs.time_reference_basis is TimeReferenceBasis.EXPERIMENT_START


def test_time_reference_basis_requires_a_time_value(db_session):
    """DB-level CHECK: a stated basis with no actual time value is meaningless."""
    obs = QuantitativeObservation(
        observation_type="GROWTH_RATE",
        value=Decimal("0.3"),
        unit="h-1",
        evidence_class=QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC,
        time_reference_basis=TimeReferenceBasis.EXPERIMENT_START,
    )
    db_session.add(obs)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- Scenario: perturbation with onset/duration -----------------------------------------------


def test_perturbation_with_onset_and_duration(db_session):
    pert = Perturbation(
        perturbation_type="TEMPERATURE_PH",
        target="temperature",
        magnitude=Decimal("37"),
        magnitude_unit="C",
        start_time_value=Decimal("0"),
        start_time_unit="min",
        start_time_canonical_s=Decimal("0"),
        duration_value=Decimal("30"),
        duration_unit="min",
        duration_canonical_s=Decimal("1800"),
    )
    db_session.add(pert)
    db_session.flush()

    obs = _observation(
        db_session,
        observation_type="GROWTH_RATE",
        value=Decimal("0.1"),
        unit="h-1",
        evidence_class=QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC,
        time_reference_basis=TimeReferenceBasis.PERTURBATION_ONSET,
        time_value=Decimal("10"),
        time_unit="min",
        time_canonical_s=Decimal("600"),
        perturbation_id=pert.id,
    )
    assert obs.perturbation.duration_canonical_s == Decimal("1800")
    assert obs.perturbation.start_time_canonical_s == Decimal("0")


# --- Scenario: biological/technical replicates ------------------------------------------------


def test_biological_and_technical_replicates(db_session):
    obs = _observation(
        db_session,
        biological_replicate_id="bio-rep-1",
        technical_replicate_id="tech-rep-2",
    )
    assert obs.biological_replicate_id == "bio-rep-1"
    assert obs.technical_replicate_id == "tech-rep-2"


def test_replicate_ids_are_never_required(db_session):
    """The source lacking replicate information must never block persistence."""
    obs = _observation(db_session)
    assert obs.biological_replicate_id is None
    assert obs.technical_replicate_id is None


# --- Scenario: identity links -------------------------------------------------------------------


def test_protein_linked_observation(db_session):
    organism = _organism(db_session)
    protein = _protein(db_session, organism.id)
    obs = _observation(db_session, protein_id=protein.id)
    assert obs.protein.id == protein.id


def test_compound_linked_observation(db_session):
    from app.models.compound import Compound

    compound = Compound(canonical_name=f"test-only compound {uuid4().hex[:8]}")
    db_session.add(compound)
    db_session.flush()
    obs = _observation(
        db_session,
        observation_type="METABOLITE_CONCENTRATION",
        value=Decimal("5"),
        unit="mM",
        compound_id=compound.id,
    )
    assert obs.compound.id == compound.id


def test_reaction_linked_flux_observation(db_session):
    reaction = _reaction(db_session)
    obs = _observation(
        db_session,
        observation_type="REACTION_FLUX",
        value=Decimal("2.5"),
        unit="mM/s",
        evidence_class=QuantitativeEvidenceClass.EXPERIMENT_SPECIFIC,
        reaction_id=reaction.id,
    )
    assert obs.reaction.id == reaction.id


def test_cell_volume_observation(db_session):
    obs = _observation(
        db_session,
        observation_type="CELL_VOLUME",
        value=Decimal("0.1"),
        unit="pL",
        evidence_class=QuantitativeEvidenceClass.REFERENCE_BASELINE,
    )
    assert obs.observation_type == "CELL_VOLUME"
    assert obs.value == Decimal("0.1")
    assert obs.unit == "pL"


def test_unresolved_identity_remains_explicit_never_fabricated(db_session):
    """No deterministic match was available -- the raw text is preserved, never a guessed link."""
    obs = _observation(
        db_session,
        observation_type="METABOLITE_CONCENTRATION",
        value=Decimal("12"),
        unit="mM",
        unresolved_identity_kind="compound",
        unresolved_identity_text="unrecognized metabolite name from source",
    )
    assert obs.compound_id is None
    assert obs.unresolved_identity_kind == "compound"
    assert obs.unresolved_identity_text == "unrecognized metabolite name from source"


# --- Scenario: uncertainty preservation ---------------------------------------------------------


def test_uncertainty_and_bounds_preserved(db_session):
    obs = _observation(
        db_session,
        uncertainty=Decimal("15"),
        lower_bound=Decimal("80"),
        upper_bound=Decimal("120"),
    )
    assert obs.uncertainty == Decimal("15")
    assert obs.lower_bound == Decimal("80")
    assert obs.upper_bound == Decimal("120")


# --- Scenario: provenance preservation ----------------------------------------------------------


def test_provenance_fields_preserved(db_session):
    organism = _organism(db_session)
    ctx = _context(
        db_session,
        organism_id=organism.id,
        classification=ExperimentalContextClassification.REFERENCE,
        source=SourceType.SGD,
        source_id="sgd-ctx-1",
    )
    obs = _observation(
        db_session,
        experimental_context_id=ctx.id,
        source=SourceType.SGD,
        source_id=f"sgd-obs-{uuid4()}",
        dataset_id="SGD-Abundance-2024",
        measurement_method="western blot",
    )
    assert obs.source is SourceType.SGD
    assert obs.dataset_id == "SGD-Abundance-2024"
    assert obs.measurement_method == "western blot"
    assert obs.experimental_context.classification is ExperimentalContextClassification.REFERENCE


def test_quantitative_observation_source_source_id_idempotency_index(db_session):
    """Partial unique index on (source, source_id) -- mirrors kinetic_measurement's own."""
    _observation(db_session, source=SourceType.SGD, source_id="sgd-dup-1")
    db_session.flush()
    dup = QuantitativeObservation(
        observation_type="PROTEIN_ABUNDANCE",
        value=Decimal("1"),
        unit="molecules/cell",
        evidence_class=QuantitativeEvidenceClass.REFERENCE_BASELINE,
        source=SourceType.SGD,
        source_id="sgd-dup-1",
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- Scenario: derived-observation dependency representation -----------------------------------


def test_derived_observation_dependency_representation(db_session):
    abundance = _observation(db_session, observation_type="PROTEIN_ABUNDANCE")
    volume = _observation(
        db_session, observation_type="CELL_VOLUME", value=Decimal("0.1"), unit="pL"
    )
    derived = _observation(
        db_session,
        observation_type="PROTEIN_CONCENTRATION",
        value=Decimal("8.3"),
        unit="nM",
        evidence_class=QuantitativeEvidenceClass.DERIVED,
    )
    dep1 = QuantitativeObservationDependency(
        derived_observation_id=derived.id,
        input_observation_id=abundance.id,
        role="protein_abundance_input",
    )
    dep2 = QuantitativeObservationDependency(
        derived_observation_id=derived.id,
        input_observation_id=volume.id,
        role="cell_volume_input",
        assumption_notes="assumed reference cell volume of 0.1 pL",
    )
    db_session.add_all([dep1, dep2])
    db_session.flush()

    assert {d.input_observation_id for d in derived.input_dependencies} == {
        abundance.id,
        volume.id,
    }
    assert abundance.dependent_derivations[0].derived_observation_id == derived.id


def test_derived_observation_dependency_cannot_be_self_referential(db_session):
    obs = _observation(db_session, observation_type="PROTEIN_CONCENTRATION")
    dep = QuantitativeObservationDependency(
        derived_observation_id=obs.id, input_observation_id=obs.id
    )
    db_session.add(dep)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_derived_observation_never_computed_by_this_increment(db_session):
    """This increment represents dependency provenance only -- no code anywhere computes a
    DERIVED observation's own value from its declared inputs; the derived row's value is
    always independently supplied, never back-filled."""
    abundance = _observation(
        db_session, observation_type="PROTEIN_ABUNDANCE", value=Decimal("5000")
    )
    derived = _observation(
        db_session,
        observation_type="PROTEIN_CONCENTRATION",
        value=Decimal("8.3"),
        evidence_class=QuantitativeEvidenceClass.DERIVED,
        unit="nM",
    )
    db_session.add(
        QuantitativeObservationDependency(
            derived_observation_id=derived.id, input_observation_id=abundance.id
        )
    )
    db_session.flush()
    # The derived value is exactly whatever was supplied -- never recomputed from abundance.
    assert derived.value == Decimal("8.3")
