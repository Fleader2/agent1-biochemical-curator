"""Tests for Reaction<->enzyme association identity normalization (Phase 4, Increment 9).

Pure unit tests: no database, no HTTP, no live external access.
``FakeReactionEnzymeLookup`` is an in-memory, read-only stand-in for
``app.normalization.reaction_enzyme.ReactionEnzymeLookup`` -- there is no
SQLAlchemy adapter in this increment, consistent with
``app.normalization.reaction_enzyme``'s own module docstring. This module
has no organism dimension of its own (``reaction_enzyme`` has no
``organism_id`` column), so tests use distinct reaction/protein/complex
UUIDs to prove independence rather than distinct organism UUIDs.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.models.enums import SourceType
from app.normalization.reaction_enzyme import (
    ReactionEnzymeCandidate,
    ReactionEnzymeIdentity,
    ReactionEnzymeLookup,
    normalize_reaction_enzyme,
)
from app.normalization.types import MatchMethod, NormalizationStatus

pytestmark = pytest.mark.unit

REACTION_A = UUID("11111111-1111-1111-1111-111111111111")
REACTION_B = UUID("22222222-2222-2222-2222-222222222222")
PROTEIN_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
PROTEIN_B = UUID("aaaaaaaa-0000-0000-0000-000000000002")
COMPLEX_A = UUID("cccccccc-0000-0000-0000-000000000001")
COMPLEX_B = UUID("cccccccc-0000-0000-0000-000000000002")


@dataclass(frozen=True, slots=True)
class FakeReactionEnzymeLookup:
    """In-memory ``ReactionEnzymeLookup``: exact-match filtering over a fixed candidate list."""

    associations: Sequence[ReactionEnzymeCandidate] = ()

    def by_reaction_and_protein(
        self, reaction_id: UUID, protein_id: UUID
    ) -> Sequence[ReactionEnzymeCandidate]:
        return [
            a
            for a in self.associations
            if a.reaction_id == reaction_id and a.protein_id == protein_id
        ]

    def by_reaction_and_complex(
        self, reaction_id: UUID, complex_id: UUID
    ) -> Sequence[ReactionEnzymeCandidate]:
        return [
            a
            for a in self.associations
            if a.reaction_id == reaction_id and a.complex_id == complex_id
        ]


def _candidate(
    *,
    reaction_id: UUID = REACTION_A,
    protein_id: UUID | None = None,
    complex_id: UUID | None = None,
    relationship: str | None = "CATALYZES",
    association_id: UUID | None = None,
) -> ReactionEnzymeCandidate:
    return ReactionEnzymeCandidate(
        id=association_id or uuid4(),
        reaction_id=reaction_id,
        protein_id=protein_id,
        complex_id=complex_id,
        relationship=relationship,
    )


# --- ReactionEnzymeIdentity construction / validation --------------------------------


def test_identity_rejects_neither_protein_nor_complex() -> None:
    with pytest.raises(ValueError, match="exactly one of protein_id or complex_id"):
        ReactionEnzymeIdentity(
            source=SourceType.OTHER, source_identifier="req-1", reaction_id=REACTION_A
        )


def test_identity_rejects_both_protein_and_complex() -> None:
    with pytest.raises(ValueError, match="exactly one of protein_id or complex_id"):
        ReactionEnzymeIdentity(
            source=SourceType.OTHER,
            source_identifier="req-2",
            reaction_id=REACTION_A,
            protein_id=PROTEIN_A,
            complex_id=COMPLEX_A,
        )


def test_identity_requires_reaction_id() -> None:
    with pytest.raises(ValueError, match="requires reaction_id"):
        ReactionEnzymeIdentity(
            source=SourceType.OTHER,
            source_identifier="req-3",
            reaction_id=None,  # type: ignore[arg-type]
            protein_id=PROTEIN_A,
        )


def test_identity_rejects_blank_source_identifier() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        ReactionEnzymeIdentity(
            source=SourceType.OTHER,
            source_identifier="   ",
            reaction_id=REACTION_A,
            protein_id=PROTEIN_A,
        )


def test_identity_trims_whitespace_and_blank_relationship_becomes_none() -> None:
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-4",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
        relationship="   ",
    )
    assert identity.relationship is None


def test_identity_has_no_evidence_shaped_fields() -> None:
    """confidence/publication/reviewer/notes belong to a later evidence-normalization layer."""
    params = inspect.signature(ReactionEnzymeIdentity).parameters
    for forbidden in (
        "confidence",
        "confidence_summary",
        "publication",
        "reviewer",
        "notes",
        "evidence",
    ):
        assert forbidden not in params


# --- Lookup API shape ------------------------------------------------------------------


def test_lookup_has_exactly_the_two_intended_methods() -> None:
    method_names = {
        name
        for name, _ in inspect.getmembers(ReactionEnzymeLookup, inspect.isfunction)
        if not name.startswith("_")
    }
    assert method_names == {"by_reaction_and_protein", "by_reaction_and_complex"}


def test_lookup_has_no_ec_number_method() -> None:
    assert not hasattr(ReactionEnzymeLookup, "by_ec_number")


def test_lookup_has_no_evidence_or_publication_method() -> None:
    method_names = {
        name for name, _ in inspect.getmembers(ReactionEnzymeLookup, inspect.isfunction)
    }
    assert not any("evidence" in name or "publication" in name for name in method_names)


def test_lookup_has_no_gene_or_pathway_method() -> None:
    method_names = {
        name for name, _ in inspect.getmembers(ReactionEnzymeLookup, inspect.isfunction)
    }
    assert not any("gene" in name or "pathway" in name for name in method_names)


def test_lookup_has_no_free_text_method() -> None:
    method_names = {
        name for name, _ in inspect.getmembers(ReactionEnzymeLookup, inspect.isfunction)
    }
    assert not any("text" in name for name in method_names)


# --- Relationship identity: exact match -------------------------------------------------


def test_exact_reaction_protein_pair_matched() -> None:
    association_id = uuid4()
    lookup = FakeReactionEnzymeLookup(
        associations=(
            _candidate(reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_id),
        )
    )
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-5",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.EXACT_IDENTIFIER
    assert result.matched_entity_id == association_id
    assert result.organism_id is None


def test_exact_reaction_complex_pair_matched() -> None:
    association_id = uuid4()
    lookup = FakeReactionEnzymeLookup(
        associations=(
            _candidate(reaction_id=REACTION_A, complex_id=COMPLEX_A, association_id=association_id),
        )
    )
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-6",
        reaction_id=REACTION_A,
        complex_id=COMPLEX_A,
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == association_id


def test_duplicate_rows_for_same_pair_is_ambiguous() -> None:
    association_a, association_b = uuid4(), uuid4()
    lookup = FakeReactionEnzymeLookup(
        associations=(
            _candidate(reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_a),
            _candidate(reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_b),
        )
    )
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-7",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.matched_entity_id is None
    assert set(result.candidate_entity_ids) == {association_a, association_b}


def test_different_proteins_remain_different() -> None:
    association_a, association_b = uuid4(), uuid4()
    lookup = FakeReactionEnzymeLookup(
        associations=(
            _candidate(reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_a),
            _candidate(reaction_id=REACTION_A, protein_id=PROTEIN_B, association_id=association_b),
        )
    )
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-8",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == association_a


def test_different_complexes_remain_different() -> None:
    association_a, association_b = uuid4(), uuid4()
    lookup = FakeReactionEnzymeLookup(
        associations=(
            _candidate(reaction_id=REACTION_A, complex_id=COMPLEX_A, association_id=association_a),
            _candidate(reaction_id=REACTION_A, complex_id=COMPLEX_B, association_id=association_b),
        )
    )
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-9",
        reaction_id=REACTION_A,
        complex_id=COMPLEX_A,
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == association_a


# --- Conflict-style behavior: different reaction / different target -----------------------


def test_same_reaction_different_protein_are_independent_not_conflicted() -> None:
    """Two proteins for one reaction (isoenzymes) are independent associations --
    this is not a conflict, each normalizes on its own.
    """
    lookup = FakeReactionEnzymeLookup(
        associations=(_candidate(reaction_id=REACTION_A, protein_id=PROTEIN_A),)
    )
    identity_for_b = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-10",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_B,
        relationship="CATALYZES",
    )

    result = normalize_reaction_enzyme(identity_for_b, lookup=lookup)

    assert result.status is NormalizationStatus.NEW


def test_same_reaction_different_complex_are_independent() -> None:
    lookup = FakeReactionEnzymeLookup(
        associations=(_candidate(reaction_id=REACTION_A, complex_id=COMPLEX_A),)
    )
    identity_for_b = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-11",
        reaction_id=REACTION_A,
        complex_id=COMPLEX_B,
        relationship="CATALYZES",
    )

    result = normalize_reaction_enzyme(identity_for_b, lookup=lookup)

    assert result.status is NormalizationStatus.NEW


def test_protein_association_and_complex_association_never_merged() -> None:
    """A Reaction+Protein association and a Reaction+Complex association for the SAME
    reaction are structurally independent -- even if, biologically, that protein is a
    member of that complex, this module never bridges the two identity spaces.
    """
    protein_association_id = uuid4()
    lookup = FakeReactionEnzymeLookup(
        associations=(
            _candidate(
                reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=protein_association_id
            ),
        )
    )
    identity_for_complex = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-12",
        reaction_id=REACTION_A,
        complex_id=COMPLEX_A,
        relationship="CATALYZES",
    )

    result = normalize_reaction_enzyme(identity_for_complex, lookup=lookup)

    # The existing Reaction+Protein row is invisible to a Reaction+Complex query --
    # this is NEW, not MATCHED against the unrelated protein association, and not
    # any kind of conflict with it either.
    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id != protein_association_id


# --- Catalytic-role (``relationship``) policy: inert metadata ------------------------------


def test_differing_relationship_value_does_not_prevent_matched() -> None:
    """relationship is inert metadata here (open policy question, see module docstring) --
    a resolved (reaction_id, protein_id) match stands regardless of relationship disagreement.
    """
    association_id = uuid4()
    lookup = FakeReactionEnzymeLookup(
        associations=(
            _candidate(
                reaction_id=REACTION_A,
                protein_id=PROTEIN_A,
                relationship="PUTATIVE_CATALYST",
                association_id=association_id,
            ),
        )
    )
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-13",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
        relationship="CATALYZES",
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == association_id


def test_relationship_is_never_queried_or_compared_for_lookup() -> None:
    """The lookup Protocol methods take no relationship parameter at all."""
    for name in ("by_reaction_and_protein", "by_reaction_and_complex"):
        params = list(inspect.signature(getattr(ReactionEnzymeLookup, name)).parameters)
        assert "relationship" not in params
        assert "catalytic_role" not in params


# --- Isoenzyme / multi-function safety --------------------------------------------------


def test_two_proteins_catalyzing_one_reaction_both_allowed() -> None:
    lookup = FakeReactionEnzymeLookup(associations=())
    identity_a = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-14a",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
        relationship="CATALYZES",
    )
    identity_b = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-14b",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_B,
        relationship="ISOENZYME",
    )

    result_a = normalize_reaction_enzyme(identity_a, lookup=lookup)
    result_b = normalize_reaction_enzyme(identity_b, lookup=lookup)

    assert result_a.status is NormalizationStatus.NEW
    assert result_b.status is NormalizationStatus.NEW


def test_one_protein_catalyzing_two_reactions_both_allowed() -> None:
    lookup = FakeReactionEnzymeLookup(associations=())
    identity_reaction_a = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-15a",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
        relationship="CATALYZES",
    )
    identity_reaction_b = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-15b",
        reaction_id=REACTION_B,
        protein_id=PROTEIN_A,
        relationship="CATALYZES",
    )

    result_a = normalize_reaction_enzyme(identity_reaction_a, lookup=lookup)
    result_b = normalize_reaction_enzyme(identity_reaction_b, lookup=lookup)

    assert result_a.status is NormalizationStatus.NEW
    assert result_b.status is NormalizationStatus.NEW


def test_existing_isoenzyme_associations_do_not_interfere_with_each_other() -> None:
    association_a, association_b = uuid4(), uuid4()
    lookup = FakeReactionEnzymeLookup(
        associations=(
            _candidate(reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_a),
            _candidate(reaction_id=REACTION_A, protein_id=PROTEIN_B, association_id=association_b),
        )
    )
    identity_a = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-16a",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
    )
    identity_b = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-16b",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_B,
    )

    result_a = normalize_reaction_enzyme(identity_a, lookup=lookup)
    result_b = normalize_reaction_enzyme(identity_b, lookup=lookup)

    assert result_a.matched_entity_id == association_a
    assert result_b.matched_entity_id == association_b


# --- Evidence neutrality --------------------------------------------------------------------


def test_ec_number_plays_no_role_in_construction_or_lookup() -> None:
    assert "ec_number" not in inspect.signature(ReactionEnzymeIdentity).parameters
    assert "ec_number" not in inspect.signature(ReactionEnzymeCandidate).parameters


def test_normalize_reaction_enzyme_has_no_evidence_confidence_or_claim_parameters() -> None:
    params = inspect.signature(normalize_reaction_enzyme).parameters
    for forbidden in ("evidence", "confidence", "claim", "publication"):
        assert forbidden not in params


def test_result_never_carries_organism_id() -> None:
    lookup = FakeReactionEnzymeLookup(associations=())
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-17",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
        relationship="CATALYZES",
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.organism_id is None


# --- NEW vs UNRESOLVED ------------------------------------------------------------------------


def test_duplicate_blocks_new() -> None:
    association_id = uuid4()
    lookup = FakeReactionEnzymeLookup(
        associations=(
            _candidate(reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_id),
        )
    )
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-18",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
        relationship="CATALYZES",
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.status is not NormalizationStatus.NEW


def test_complete_new_relationship_is_new() -> None:
    lookup = FakeReactionEnzymeLookup(associations=())
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-19",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
        relationship="CATALYZES",
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None


def test_missing_relationship_is_unresolved_not_new() -> None:
    lookup = FakeReactionEnzymeLookup(associations=())
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-20",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW


# --- Determinism ---------------------------------------------------------------------------


def test_candidate_order_does_not_affect_status() -> None:
    association_a, association_b = uuid4(), uuid4()
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-21",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
    )

    forward = normalize_reaction_enzyme(
        identity,
        lookup=FakeReactionEnzymeLookup(
            associations=(
                _candidate(
                    reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_a
                ),
                _candidate(
                    reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_b
                ),
            )
        ),
    )
    backward = normalize_reaction_enzyme(
        identity,
        lookup=FakeReactionEnzymeLookup(
            associations=(
                _candidate(
                    reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_b
                ),
                _candidate(
                    reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_a
                ),
            )
        ),
    )

    assert forward.status == backward.status
    assert forward.candidate_entity_ids == backward.candidate_entity_ids


def test_duplicate_lookup_rows_are_deduplicated() -> None:
    association_id = uuid4()
    candidate = _candidate(
        reaction_id=REACTION_A, protein_id=PROTEIN_A, association_id=association_id
    )
    lookup = FakeReactionEnzymeLookup(associations=(candidate, candidate))
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier="req-22",
        reaction_id=REACTION_A,
        protein_id=PROTEIN_A,
    )

    result = normalize_reaction_enzyme(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == association_id
