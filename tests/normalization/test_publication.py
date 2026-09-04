"""Tests for publication identity normalization (Phase 4, Increment 3).

Pure unit tests: no database, no HTTP, no live PubMed access.
``FakePublicationLookup`` is an in-memory, read-only stand-in for
``app.normalization.publication.PublicationLookup`` -- there is no
SQLAlchemy adapter in this increment (deferred to a later persistence
increment), consistent with ``app.normalization.publication``'s own module
docstring. Synthetic PMIDs/DOIs/PMCIDs use clearly out-of-range/reserved
test namespaces (``docs/05_testing.md``, "Synthetic Scientific Fixtures"):
PMIDs in the 90000000+ range (matching ``tests/connectors/test_pubmed.py``),
and DOIs under the ``10.5555/`` prefix Crossref reserves for testing.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from app.connectors.pubmed import PubMedArticleRecord, PubMedNormalizedRecord
from app.models.enums import SourceType
from app.normalization.publication import (
    PublicationCandidate,
    PublicationIdentity,
    PublicationLookup,
    normalize_publication,
    publication_identity_from_pubmed,
)
from app.normalization.types import MatchMethod, NormalizationStatus

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class FakePublicationLookup:
    """In-memory ``PublicationLookup``: exact-match filtering over a fixed candidate list."""

    publications: Sequence[PublicationCandidate] = ()

    def by_pmid(self, pmid: str) -> Sequence[PublicationCandidate]:
        return [p for p in self.publications if p.pmid == pmid]

    def by_pmcid(self, pmcid: str) -> Sequence[PublicationCandidate]:
        return [p for p in self.publications if p.pmcid == pmcid]

    def by_doi(self, doi: str) -> Sequence[PublicationCandidate]:
        return [p for p in self.publications if p.doi == doi]


def _candidate(
    *,
    pmid: str | None = None,
    pmcid: str | None = None,
    doi: str | None = None,
    title: str = "A test publication",
    journal: str | None = None,
    year: int | None = None,
    publication_id: UUID | None = None,
) -> PublicationCandidate:
    return PublicationCandidate(
        id=publication_id or uuid4(),
        pmid=pmid,
        pmcid=pmcid,
        doi=doi,
        title=title,
        journal=journal,
        year=year,
    )


def _pubmed_record(
    *,
    pmid: str = "90000001",
    title: str | None = "A test publication",
    journal: str | None = "Journal of Testing",
    year: int | None = 2020,
    doi: str | None = None,
    pmcid: str | None = None,
) -> PubMedNormalizedRecord:
    raw = PubMedArticleRecord(
        pmid=pmid,
        title=title,
        abstract_sections=(),
        journal_title=journal,
        year=str(year) if year is not None else None,
        authors=(),
        article_ids=(),
        publication_types=(),
    )
    return PubMedNormalizedRecord(
        pmid=pmid,
        title=title,
        abstract=None,
        journal=journal,
        year=year,
        authors=(),
        doi=doi,
        pmcid=pmcid,
        raw=raw,
    )


# --- PublicationIdentity construction / validation -------------------------------


def test_publication_identity_requires_at_least_one_authoritative_identifier() -> None:
    with pytest.raises(ValueError, match="requires at least one authoritative identifier"):
        PublicationIdentity(
            source=SourceType.PUBMED, source_identifier="90000001", title="A test publication"
        )


def test_publication_identity_rejects_blank_source_identifier() -> None:
    with pytest.raises(ValueError, match="source_identifier must not be empty"):
        PublicationIdentity(source=SourceType.PUBMED, source_identifier="   ", pmid="90000001")


def test_publication_identity_trims_whitespace_and_blanks_become_none() -> None:
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="  90000001  ",
        doi="   ",
        title="  A test publication  ",
    )
    assert identity.pmid == "90000001"
    assert identity.doi is None
    assert identity.title == "A test publication"


def test_lookup_has_no_default_and_must_be_supplied_explicitly() -> None:
    identity = PublicationIdentity(
        source=SourceType.PUBMED, source_identifier="90000001", pmid="90000001"
    )
    with pytest.raises(TypeError):
        normalize_publication(identity)  # type: ignore[call-arg]


# --- Exact matching ----------------------------------------------------------------


def test_pmid_single_candidate_matched() -> None:
    publication_id = uuid4()
    lookup = FakePublicationLookup(
        publications=(_candidate(pmid="90000001", publication_id=publication_id),)
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED, source_identifier="90000001", pmid="90000001"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.match_method is MatchMethod.EXACT_IDENTIFIER
    assert result.matched_entity_id == publication_id
    assert result.organism_id is None


def test_pmcid_single_candidate_matched() -> None:
    publication_id = uuid4()
    lookup = FakePublicationLookup(
        publications=(_candidate(pmcid="PMC9000001", publication_id=publication_id),)
    )
    identity = PublicationIdentity(
        source=SourceType.PMC, source_identifier="PMC9000001", pmcid="PMC9000001"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == publication_id
    assert result.organism_id is None


def test_doi_single_candidate_matched() -> None:
    publication_id = uuid4()
    lookup = FakePublicationLookup(
        publications=(_candidate(doi="10.5555/test.1", publication_id=publication_id),)
    )
    identity = PublicationIdentity(
        source=SourceType.OTHER, source_identifier="10.5555/test.1", doi="10.5555/test.1"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == publication_id
    assert result.organism_id is None


def test_all_three_identifiers_resolving_to_same_publication_matched() -> None:
    publication_id = uuid4()
    lookup = FakePublicationLookup(
        publications=(
            _candidate(
                pmid="90000001",
                pmcid="PMC9000001",
                doi="10.5555/test.1",
                publication_id=publication_id,
            ),
        )
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        pmcid="PMC9000001",
        doi="10.5555/test.1",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == publication_id
    assert "pmid" in result.reason
    assert "pmcid" in result.reason
    assert "doi" in result.reason


# --- New (requires a title -- see "New vs. Unresolved" below) --------------------


def test_pmid_no_candidates_with_title_is_new() -> None:
    lookup = FakePublicationLookup(publications=())
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        title="A brand new publication",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == ()
    assert result.organism_id is None


def test_doi_no_candidates_with_title_is_new() -> None:
    lookup = FakePublicationLookup(publications=())
    identity = PublicationIdentity(
        source=SourceType.OTHER,
        source_identifier="10.5555/test.2",
        doi="10.5555/test.2",
        title="A brand new publication",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.organism_id is None


def test_multiple_identifiers_none_found_with_title_is_new() -> None:
    lookup = FakePublicationLookup(publications=())
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        pmcid="PMC9000001",
        doi="10.5555/test.1",
        title="A brand new publication",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == ()
    assert result.organism_id is None


# --- New vs. Unresolved: Publication.title is NOT NULL, so an unmatched, ----------
# title-less identifier cannot yet justify creating a Publication row. -------------


def test_unmatched_pmid_without_title_is_unresolved_not_new() -> None:
    lookup = FakePublicationLookup(publications=())
    identity = PublicationIdentity(
        source=SourceType.PUBMED, source_identifier="90000001", pmid="90000001"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.status is not NormalizationStatus.NEW
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == ()
    assert result.organism_id is None
    assert result.match_method is MatchMethod.NONE


def test_unmatched_doi_without_title_is_unresolved() -> None:
    lookup = FakePublicationLookup(publications=())
    identity = PublicationIdentity(
        source=SourceType.OTHER, source_identifier="10.5555/test.2", doi="10.5555/test.2"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.matched_entity_id is None
    assert result.organism_id is None


def test_multiple_identifiers_unmatched_without_title_is_unresolved() -> None:
    lookup = FakePublicationLookup(publications=())
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        pmcid="PMC9000001",
        doi="10.5555/test.1",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.matched_entity_id is None
    assert result.candidate_entity_ids == ()
    assert result.organism_id is None


def test_pmid_resolves_existing_publication_with_title_none_is_matched() -> None:
    """title=None must NOT prevent MATCHED when an identifier already resolves a row --
    the incoming record does not need to be creation-complete to match an existing one.
    """
    publication_id = uuid4()
    lookup = FakePublicationLookup(
        publications=(_candidate(pmid="90000001", publication_id=publication_id),)
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED, source_identifier="90000001", pmid="90000001", title=None
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == publication_id


def test_title_match_alone_does_not_resolve_to_an_existing_candidate() -> None:
    """title is never an identity lookup field: an unmatched identifier stays NEW/UNRESOLVED
    even when its title happens to equal an existing (differently-identified) candidate's.
    """
    lookup = FakePublicationLookup(
        publications=(_candidate(pmid="90000002", title="Shared Title", publication_id=uuid4()),)
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        title="Shared Title",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.matched_entity_id is None


def test_blank_title_normalizes_to_none_and_follows_unresolved_path() -> None:
    lookup = FakePublicationLookup(publications=())
    identity = PublicationIdentity(
        source=SourceType.PUBMED, source_identifier="90000001", pmid="90000001", title="   "
    )

    assert identity.title is None

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED


# --- Ambiguity ---------------------------------------------------------------------


def test_single_identifier_multiple_candidates_is_ambiguous() -> None:
    """Defensive: the schema means to prevent this, but the normalizer must not assume it."""
    id_a, id_b = uuid4(), uuid4()
    lookup = FakePublicationLookup(
        publications=(
            _candidate(doi="10.5555/test.3", publication_id=id_a),
            _candidate(doi="10.5555/test.3", publication_id=id_b),
        )
    )
    identity = PublicationIdentity(
        source=SourceType.OTHER, source_identifier="10.5555/test.3", doi="10.5555/test.3"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.matched_entity_id is None
    assert result.organism_id is None
    assert set(result.candidate_entity_ids) == {id_a, id_b}


def test_duplicate_candidate_ids_do_not_manufacture_ambiguity() -> None:
    publication_id = uuid4()
    candidate = _candidate(pmid="90000001", publication_id=publication_id)
    lookup = FakePublicationLookup(publications=(candidate, candidate))
    identity = PublicationIdentity(
        source=SourceType.PUBMED, source_identifier="90000001", pmid="90000001"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == publication_id


# --- Conflicts ------------------------------------------------------------------


def test_pmid_resolves_a_doi_resolves_b_is_conflicted() -> None:
    id_a, id_b = uuid4(), uuid4()
    lookup = FakePublicationLookup(
        publications=(
            _candidate(pmid="90000001", publication_id=id_a),
            _candidate(doi="10.5555/test.4", publication_id=id_b),
        )
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        doi="10.5555/test.4",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id is None
    assert result.organism_id is None
    assert set(result.candidate_entity_ids) == {id_a, id_b}


def test_pmid_resolves_a_candidate_has_different_doi_is_conflicted() -> None:
    publication_id = uuid4()
    lookup = FakePublicationLookup(
        publications=(
            _candidate(pmid="90000001", doi="10.5555/old", publication_id=publication_id),
        )
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        doi="10.5555/new",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.matched_entity_id == publication_id
    assert result.organism_id is None
    assert "doi" in result.reason


def test_pmcid_and_pmid_resolve_different_rows_is_conflicted() -> None:
    id_a, id_b = uuid4(), uuid4()
    lookup = FakePublicationLookup(
        publications=(
            _candidate(pmid="90000001", publication_id=id_a),
            _candidate(pmcid="PMC9000002", publication_id=id_b),
        )
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        pmcid="PMC9000002",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert set(result.candidate_entity_ids) == {id_a, id_b}


# --- Compatible missing metadata --------------------------------------------------


def test_pmid_resolves_a_incoming_doi_missing_on_a_is_matched_not_conflicted() -> None:
    publication_id = uuid4()
    lookup = FakePublicationLookup(
        publications=(_candidate(pmid="90000001", doi=None, publication_id=publication_id),)
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        doi="10.5555/test.5",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.matched_entity_id == publication_id


# --- Safety -----------------------------------------------------------------------


def test_title_alone_cannot_construct_an_identity() -> None:
    """Title alone can never produce MATCHED because it can never even reach
    ``normalize_publication`` -- ``PublicationIdentity`` rejects it at construction.
    """
    with pytest.raises(ValueError, match="requires at least one authoritative identifier"):
        PublicationIdentity(
            source=SourceType.PUBMED, source_identifier="req-1", title="Some shared title"
        )


def test_publication_lookup_protocol_has_no_title_based_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(PublicationLookup, inspect.isfunction)}
    assert not any("title" in name for name in method_names)


def test_normalize_publication_has_no_organism_parameter() -> None:
    parameters = inspect.signature(normalize_publication).parameters
    assert "organism" not in parameters
    assert "organism_id" not in parameters


def test_organism_id_always_none_for_matched() -> None:
    publication_id = uuid4()
    lookup = FakePublicationLookup(
        publications=(_candidate(pmid="90000001", publication_id=publication_id),)
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED, source_identifier="90000001", pmid="90000001"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.MATCHED
    assert result.organism_id is None


def test_organism_id_always_none_for_new() -> None:
    lookup = FakePublicationLookup(publications=())
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        title="A brand new publication",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.NEW
    assert result.organism_id is None


def test_organism_id_always_none_for_unresolved() -> None:
    lookup = FakePublicationLookup(publications=())
    identity = PublicationIdentity(
        source=SourceType.PUBMED, source_identifier="90000001", pmid="90000001"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.UNRESOLVED
    assert result.organism_id is None


def test_organism_id_always_none_for_ambiguous() -> None:
    id_a, id_b = uuid4(), uuid4()
    lookup = FakePublicationLookup(
        publications=(
            _candidate(doi="10.5555/test.6", publication_id=id_a),
            _candidate(doi="10.5555/test.6", publication_id=id_b),
        )
    )
    identity = PublicationIdentity(
        source=SourceType.OTHER, source_identifier="10.5555/test.6", doi="10.5555/test.6"
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.organism_id is None


def test_organism_id_always_none_for_conflicted() -> None:
    id_a, id_b = uuid4(), uuid4()
    lookup = FakePublicationLookup(
        publications=(
            _candidate(pmid="90000001", publication_id=id_a),
            _candidate(doi="10.5555/test.6", publication_id=id_b),
        )
    )
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        doi="10.5555/test.6",
    )

    result = normalize_publication(identity, lookup=lookup)

    assert result.status is NormalizationStatus.CONFLICTED
    assert result.organism_id is None


# --- Determinism ------------------------------------------------------------------


def test_same_candidate_set_different_lookup_order_same_result() -> None:
    id_a, id_b = uuid4(), uuid4()
    candidate_a = _candidate(doi="10.5555/test.7", publication_id=id_a)
    candidate_b = _candidate(doi="10.5555/test.7", publication_id=id_b)
    identity = PublicationIdentity(
        source=SourceType.OTHER, source_identifier="10.5555/test.7", doi="10.5555/test.7"
    )

    forward = normalize_publication(
        identity, lookup=FakePublicationLookup(publications=(candidate_a, candidate_b))
    )
    backward = normalize_publication(
        identity, lookup=FakePublicationLookup(publications=(candidate_b, candidate_a))
    )

    assert forward.status == backward.status
    assert forward.candidate_entity_ids == backward.candidate_entity_ids


def test_same_conflicting_candidate_set_returns_same_canonical_order() -> None:
    """Two anchors each resolving a different entity yields the same sorted
    ``candidate_entity_ids`` regardless of which candidate the fake lookup lists first.
    """
    id_a, id_b = uuid4(), uuid4()
    identity = PublicationIdentity(
        source=SourceType.PUBMED,
        source_identifier="90000001",
        pmid="90000001",
        doi="10.5555/test.8",
    )

    forward = normalize_publication(
        identity,
        lookup=FakePublicationLookup(
            publications=(
                _candidate(pmid="90000001", publication_id=id_a),
                _candidate(doi="10.5555/test.8", publication_id=id_b),
            )
        ),
    )
    backward = normalize_publication(
        identity,
        lookup=FakePublicationLookup(
            publications=(
                _candidate(doi="10.5555/test.8", publication_id=id_b),
                _candidate(pmid="90000001", publication_id=id_a),
            )
        ),
    )

    assert (
        forward.candidate_entity_ids == backward.candidate_entity_ids == tuple(sorted((id_a, id_b)))
    )


# --- PubMed conversion helper ------------------------------------------------------


def test_publication_identity_from_pubmed_preserves_identifiers() -> None:
    record = _pubmed_record(pmid="90000001", doi="10.5555/test.9", pmcid="PMC9000003")

    identity = publication_identity_from_pubmed(record)

    assert identity.source is SourceType.PUBMED
    assert identity.source_identifier == "90000001"
    assert identity.pmid == "90000001"
    assert identity.doi == "10.5555/test.9"
    assert identity.pmcid == "PMC9000003"


def test_publication_identity_from_pubmed_preserves_metadata() -> None:
    record = _pubmed_record(title="Some Title", journal="Some Journal", year=1999)

    identity = publication_identity_from_pubmed(record)

    assert identity.title == "Some Title"
    assert identity.journal == "Some Journal"
    assert identity.year == 1999


def test_publication_identity_from_pubmed_does_not_mutate_original_record() -> None:
    record = _pubmed_record(pmid="90000001")

    publication_identity_from_pubmed(record)

    assert record.pmid == "90000001"
