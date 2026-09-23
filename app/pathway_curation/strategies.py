"""Source-neutral connector routing: "find reactions," "find enzyme," never "call URL X."

Every function here answers one source-neutral question the planner core
asks (Step 17 of the original increment instructions) by calling into
already-existing capability -- a real connector's ``search``/``fetch``/
``normalize`` methods, an already-existing ``app.normalization.*``
``*identity_from_*`` adapter or ``app.entity_resolution.adapters
.resolve_*_via_*`` combined adapter, an already-existing ``normalize_X``,
and an already-existing ``persist_X`` -- never a new duplicate-resolution
or persistence algorithm.

Two distinct retrieval shapes, deliberately kept separate:

* **Discovery** (a free-text query, possibly several hits): reuses
  ``app.entity_resolution.adapters.resolve_*_via_*`` directly, then this
  module's own ``_classify_candidates`` decides, from the *existing*
  ``app.entity_resolution.ranking.classify_outcome`` verdict, whether to
  persist a lone ``NEW`` candidate or report an unresolved/ambiguous
  frontier item -- multiple distinct ``NEW`` candidates from one free-text
  search are never resolved arbitrarily; they become an
  ``AMBIGUOUS_IDENTITY`` frontier item asking for a more specific query.
* **Expansion** (an exact external id already known from a previously
  fetched structured record, e.g. one KEGG pathway's own ``REACTION``
  field): fetches that one id directly, never searches -- structurally
  incapable of the free-text multi-candidate ambiguity above.

Connector I/O failures (``app.connectors.exceptions.ConnectorError``) are
deliberately not caught here -- ``executor.py`` is what converts them
into a ``SOURCE_FAILURE`` frontier item, mirroring
``app.entity_resolution.resolver``'s own "how do we call this connector"
vs. "how do we report that it failed" separation.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.claim_generation.types import EntityKind
from app.connectors.kegg import KeggCompoundRecord, KeggReactionRecord
from app.entity_resolution.adapters import (
    KeggSearchAndFetch,
    PubMedSearchAndFetch,
    SgdSearchAndFetch,
    UniProtSearchAndFetch,
    resolve_compound_via_kegg,
    resolve_gene_via_sgd,
    resolve_protein_via_uniprot,
    resolve_publication_via_pubmed,
    resolve_reaction_via_kegg,
)
from app.entity_resolution.ranking import classify_outcome, sort_candidates
from app.entity_resolution.types import IdentifierCandidate, MentionResolutionStatus
from app.models.enums import SourceType
from app.normalization.compartment import CompartmentLookup
from app.normalization.compound import (
    CompoundLookup,
    compound_identity_from_kegg,
    normalize_compound,
)
from app.normalization.gene import GeneLookup
from app.normalization.kinetic_measurement import (
    KineticMeasurementIdentity,
    kinetic_identity_from_oed,
    kinetic_identity_from_sabiork,
)
from app.normalization.organism import OrganismIdentity, OrganismLookup, normalize_organism
from app.normalization.protein import (
    ProteinLookup,
)
from app.normalization.publication import (
    PublicationLookup,
    normalize_publication,
    publication_identity_from_pubmed,
)
from app.normalization.reaction import (
    ReactionLookup,
    ReactionParticipantIdentity,
    normalize_reaction,
    reaction_identity_from_kegg,
)
from app.normalization.reaction_enzyme import (
    ReactionEnzymeIdentity,
    ReactionEnzymeLookup,
    normalize_reaction_enzyme,
)
from app.normalization.types import NormalizationResult, NormalizationStatus
from app.pathway_curation.types import FrontierReason
from app.persistence.compound import persist_compound
from app.persistence.gene import persist_gene
from app.persistence.organism import persist_organism
from app.persistence.protein import persist_protein
from app.persistence.publication import persist_publication
from app.persistence.reaction import persist_reaction
from app.persistence.reaction_enzyme import persist_reaction_enzyme
from app.persistence.types import PersistenceAction

__all__ = [
    "ResolutionOutcome",
    "associate_catalyst",
    "discover_kinetics_oed",
    "discover_kinetics_sabiork",
    "discover_pathway",
    "discover_publications",
    "discover_reactions_in_pathway",
    "fetch_kegg_reaction_record",
    "resolve_compound_by_text",
    "resolve_gene_by_text",
    "resolve_organism",
    "resolve_participant_compound",
    "resolve_protein_by_text",
    "resolve_publication_by_text",
    "resolve_reaction_by_kegg_id",
    "resolve_reaction_by_text",
    "resolve_reference_compartment_by_name",
]


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    """The uniform result of one entity-resolution attempt, for the executor to interpret.

    ``frontier_reason`` is ``None`` exactly when ``entity_id`` is
    populated (a match or a successful creation) -- never both set, never
    both unset.
    """

    entity_kind: EntityKind
    query: str
    entity_id: UUID | None = None
    created: bool = False
    frontier_reason: FrontierReason | None = None
    source: SourceType | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if (self.entity_id is None) == (self.frontier_reason is None):
            raise ValueError(
                "ResolutionOutcome requires exactly one of entity_id/frontier_reason to be set, "
                f"got entity_id={self.entity_id!r} frontier_reason={self.frontier_reason!r}"
            )


def _outcome_for_result(
    *,
    entity_kind: EntityKind,
    query: str,
    source: SourceType,
    result: NormalizationResult,
    unresolved_reason: FrontierReason,
    persist,
) -> ResolutionOutcome:
    """Classify one already-computed ``NormalizationResult`` and persist a ``NEW`` verdict.

    ``persist`` is a zero-argument callable performing exactly one
    ``persist_X(identity, result, ...)`` call -- supplied by the caller so
    this helper never needs to know each entity type's own extra keyword
    arguments (``organism_id``, ...).
    """
    if result.status is NormalizationStatus.MATCHED:
        return ResolutionOutcome(
            entity_kind=entity_kind,
            query=query,
            entity_id=result.matched_entity_id,
            created=False,
            source=source,
            notes="matched an existing entity",
        )
    if result.status is NormalizationStatus.NEW:
        persisted = persist()
        if persisted.action is PersistenceAction.CREATED:
            return ResolutionOutcome(
                entity_kind=entity_kind,
                query=query,
                entity_id=persisted.entity_id,
                created=True,
                source=source,
                notes="created a new entity",
            )
        if persisted.action is PersistenceAction.REUSED_EXISTING:
            return ResolutionOutcome(
                entity_kind=entity_kind,
                query=query,
                entity_id=persisted.entity_id,
                created=False,
                source=source,
                notes="reused an existing entity found at persistence time",
            )
        return ResolutionOutcome(
            entity_kind=entity_kind,
            query=query,
            frontier_reason=unresolved_reason,
            source=source,
            notes=f"persistence did not create a row: {persisted.reason}",
        )
    if result.status is NormalizationStatus.AMBIGUOUS:
        return ResolutionOutcome(
            entity_kind=entity_kind,
            query=query,
            frontier_reason=FrontierReason.AMBIGUOUS_IDENTITY,
            source=source,
            notes=result.reason,
        )
    if result.status is NormalizationStatus.CONFLICTED:
        return ResolutionOutcome(
            entity_kind=entity_kind,
            query=query,
            frontier_reason=FrontierReason.CONFLICTED_IDENTITY,
            source=source,
            notes=result.reason,
        )
    return ResolutionOutcome(
        entity_kind=entity_kind,
        query=query,
        frontier_reason=unresolved_reason,
        source=source,
        notes=result.reason or "unresolved",
    )


def _classify_candidates(
    *,
    entity_kind: EntityKind,
    query: str,
    source: SourceType,
    candidates: list[IdentifierCandidate],
    unresolved_reason: FrontierReason,
    persist,
) -> ResolutionOutcome:
    """Apply the existing, already-tested ``classify_outcome``/``sort_candidates`` to one
    free-text discovery search's candidates, then act on the verdict.

    A lone ``NEW_CANDIDATE`` is persisted; more than one distinct
    candidate landing on ``NEW_CANDIDATE`` is reported as
    ``AMBIGUOUS_IDENTITY`` rather than choosing one arbitrarily (see
    module docstring) -- ``classify_outcome`` itself only ever returns one
    ``NEW_CANDIDATE`` verdict regardless of count, so the count check
    happens here, one layer up.
    """
    ordered = sort_candidates(candidates)
    status, resolved_id, reason = classify_outcome(ordered)

    if status is MentionResolutionStatus.RESOLVED:
        return ResolutionOutcome(
            entity_kind=entity_kind, query=query, entity_id=resolved_id, source=source, notes=reason
        )
    if status is MentionResolutionStatus.NEW_CANDIDATE:
        new_candidates = [
            c for c in ordered if c.normalization_result.status is NormalizationStatus.NEW
        ]
        if len(new_candidates) > 1:
            return ResolutionOutcome(
                entity_kind=entity_kind,
                query=query,
                frontier_reason=FrontierReason.AMBIGUOUS_IDENTITY,
                source=source,
                notes=(
                    f"{len(new_candidates)} distinct new candidates from one free-text search -- "
                    "a more specific query/exact id is required, never chosen arbitrarily"
                ),
            )
        return _outcome_for_result(
            entity_kind=entity_kind,
            query=query,
            source=source,
            result=new_candidates[0].normalization_result,
            unresolved_reason=unresolved_reason,
            persist=persist,
        )
    if status is MentionResolutionStatus.AMBIGUOUS:
        return ResolutionOutcome(
            entity_kind=entity_kind,
            query=query,
            frontier_reason=FrontierReason.AMBIGUOUS_IDENTITY,
            source=source,
            notes=reason,
        )
    if status is MentionResolutionStatus.CONFLICTED:
        return ResolutionOutcome(
            entity_kind=entity_kind,
            query=query,
            frontier_reason=FrontierReason.CONFLICTED_IDENTITY,
            source=source,
            notes=reason,
        )
    return ResolutionOutcome(
        entity_kind=entity_kind,
        query=query,
        frontier_reason=unresolved_reason,
        source=source,
        notes=reason,
    )


# --- Organism (Step 11: resolved directly from request text, no connector required) -----------


def resolve_organism(
    *,
    scientific_name: str,
    strain: str | None,
    ncbi_taxonomy_id: int | None,
    lookup: OrganismLookup,
    session: Session,
) -> ResolutionOutcome:
    """Resolve/persist the organism named by the request's own ``organism_text``.

    Never calls a connector: the request itself is the authoritative
    source of the organism's identity text (Step 11 -- organism
    resolution happens before any organism-scoped search, from whatever
    the caller actually stated, never a strain inferred on their behalf).
    ``ncbi_taxonomy_id`` is passed through only when the request itself
    already supplied one -- ``normalize_organism`` requires a strong
    identifier (or an existing match) to ever create a new organism from
    a bare scientific name alone; this function never invents one.
    """
    identity = OrganismIdentity(
        source=SourceType.OTHER,
        source_identifier=f"request::{scientific_name}",
        scientific_name=scientific_name,
        strain=strain,
        ncbi_taxonomy_id=ncbi_taxonomy_id,
    )
    result = normalize_organism(identity, lookup=lookup)
    return _outcome_for_result(
        entity_kind=EntityKind.ORGANISM,
        query=scientific_name,
        source=SourceType.OTHER,
        result=result,
        unresolved_reason=FrontierReason.MISSING_ORGANISM_CONTEXT,
        persist=lambda: persist_organism(identity, result, session=session),
    )


# --- Pathway/reaction discovery (KEGG) ----------------------------------------------------------


def discover_pathway(connector: KeggSearchAndFetch, query: str) -> tuple[str, ...]:
    """Search KEGG's ``pathway`` database for ``query``. Returns every hit's KEGG pathway id.

    Structural discovery only -- returns ids for the caller (the
    executor) to choose among deterministically (e.g. the first, sorted,
    hit); never itself guesses which pathway "is" the requested
    biological process.
    """
    hits = connector.search(query, database="pathway")
    return tuple(sorted(hit.entry_id for hit in hits))


def discover_reactions_in_pathway(
    connector: KeggSearchAndFetch, pathway_id: str
) -> tuple[str, ...]:
    """Fetch one KEGG pathway flat file and return every reaction id its ``REACTION`` field names.

    Deterministic, mechanical parsing only: each ``REACTION`` line's
    first whitespace-delimited token is a KEGG reaction id (KEGG's own
    flat-file convention) -- never inferred, never a reaction not
    literally named in the fetched record. Order-preserving, deduplicated.
    """
    record = connector.fetch(pathway_id)
    if record is None:
        return ()
    normalized = connector.normalize(record)
    fields = getattr(normalized, "fields", None)
    if not fields:
        return ()
    reaction_lines = fields.get("REACTION", ())
    seen: set[str] = set()
    ordered: list[str] = []
    for line in reaction_lines:
        tokens = line.split()
        if not tokens:
            continue
        reaction_id = tokens[0]
        if reaction_id not in seen:
            seen.add(reaction_id)
            ordered.append(reaction_id)
    return tuple(ordered)


def fetch_kegg_reaction_record(
    connector: KeggSearchAndFetch, kegg_reaction_id: str
) -> KeggReactionRecord | None:
    """Fetch and normalize one exact, already-known KEGG reaction id, returning the typed
    record itself (never a persistence outcome). ``None`` for a legitimate "not found, or not
    a reaction entry" outcome -- never raised.

    Exists so a caller needing the record for more than one purpose (resolving the reaction
    itself, *and* -- Increment C pre-commit revision -- parsing its ``equation`` into
    participants) fetches it exactly once; pass the result back into
    ``resolve_reaction_by_kegg_id`` as ``prefetched_record`` to avoid a second connector call.
    """
    flat_record = connector.fetch(kegg_reaction_id)
    if flat_record is None:
        return None
    normalized_record = connector.normalize(flat_record)
    return normalized_record if isinstance(normalized_record, KeggReactionRecord) else None


def resolve_reaction_by_kegg_id(
    connector: KeggSearchAndFetch,
    kegg_reaction_id: str,
    *,
    organism_id: UUID,
    lookup: ReactionLookup,
    session: Session,
    participants: tuple[ReactionParticipantIdentity, ...] = (),
    prefetched_record: KeggReactionRecord | None = None,
) -> ResolutionOutcome:
    """Expansion path: fetch one exact, already-known KEGG reaction id, normalize, persist.

    ``participants`` (Increment C pre-commit revision) is attached to the identity via
    ``dataclasses.replace`` before normalization -- ``reaction_identity_from_kegg`` itself is
    never modified and always still returns empty participants; only this orchestration-layer
    call site enriches them, from participants the caller (``executor.py``) already resolved
    via ``app.pathway_curation.equation_parser``/``resolve_participant_compound``.
    ``prefetched_record``, when supplied, skips the fetch/normalize step entirely (see
    ``fetch_kegg_reaction_record``) -- this function performs at most one connector call,
    never two, regardless of which caller supplies what.
    """
    if prefetched_record is not None:
        normalized_record = prefetched_record
    else:
        normalized_record = fetch_kegg_reaction_record(connector, kegg_reaction_id)
        if normalized_record is None:
            return ResolutionOutcome(
                entity_kind=EntityKind.REACTION,
                query=kegg_reaction_id,
                frontier_reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
                source=SourceType.KEGG,
                notes="KEGG reaction id not found (fetch returned None) or not a reaction record",
            )
    identity = reaction_identity_from_kegg(normalized_record)
    if participants:
        identity = dataclasses.replace(identity, participants=participants)
    result = normalize_reaction(identity, organism_id=organism_id, lookup=lookup)
    return _outcome_for_result(
        entity_kind=EntityKind.REACTION,
        query=kegg_reaction_id,
        source=SourceType.KEGG,
        result=result,
        unresolved_reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
        persist=lambda: persist_reaction(
            identity, result, organism_id=organism_id, session=session
        ),
    )


def resolve_reaction_by_text(
    connector: KeggSearchAndFetch,
    query: str,
    *,
    organism_id: UUID,
    lookup: ReactionLookup,
    session: Session,
) -> ResolutionOutcome:
    """Discovery path: free-text KEGG reaction search -- see module docstring's ambiguity policy."""
    candidates = resolve_reaction_via_kegg(
        query=query,
        original_mention=query,
        organism_id=organism_id,
        connector=connector,
        lookup=lookup,
    )
    return _classify_candidates(
        entity_kind=EntityKind.REACTION,
        query=query,
        source=SourceType.KEGG,
        candidates=candidates,
        unresolved_reason=FrontierReason.UNRESOLVED_REACTION_IDENTITY,
        persist=lambda: persist_reaction(
            candidates[0].normalization_input,
            candidates[0].normalization_result,
            organism_id=organism_id,
            session=session,
        ),
    )


def resolve_compound_by_text(
    connector: KeggSearchAndFetch, query: str, *, lookup, session: Session
) -> ResolutionOutcome:
    """Discovery path: free-text KEGG compound search."""
    candidates = resolve_compound_via_kegg(
        query=query, original_mention=query, connector=connector, lookup=lookup
    )
    return _classify_candidates(
        entity_kind=EntityKind.COMPOUND,
        query=query,
        source=SourceType.KEGG,
        candidates=candidates,
        unresolved_reason=FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
        persist=lambda: persist_compound(
            candidates[0].normalization_input, candidates[0].normalization_result, session=session
        ),
    )


def resolve_participant_compound(
    connector: KeggSearchAndFetch,
    kegg_compound_id: str,
    *,
    lookup: CompoundLookup,
    session: Session,
) -> ResolutionOutcome:
    """Expansion path: fetch one exact, already-known KEGG compound id (a reaction
    participant token, from ``app.pathway_curation.equation_parser``), never a free-text
    search -- mirrors ``resolve_reaction_by_kegg_id``'s identical expansion pattern.
    A KEGG compound id that cannot be fetched, or that fetches to something other than a
    compound record, is reported as ``UNRESOLVED_REACTION_PARTICIPANT``, never dropped
    silently and never treated as a different kind of failure.
    """
    flat_record = connector.fetch(kegg_compound_id)
    if flat_record is None:
        return ResolutionOutcome(
            entity_kind=EntityKind.COMPOUND,
            query=kegg_compound_id,
            frontier_reason=FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
            source=SourceType.KEGG,
            notes="KEGG compound id not found (fetch returned None)",
        )
    normalized_record = connector.normalize(flat_record)
    if not isinstance(normalized_record, KeggCompoundRecord):
        return ResolutionOutcome(
            entity_kind=EntityKind.COMPOUND,
            query=kegg_compound_id,
            frontier_reason=FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
            source=SourceType.KEGG,
            notes="KEGG entry is not a compound record",
        )
    identity = compound_identity_from_kegg(normalized_record)
    result = normalize_compound(identity, lookup=lookup)
    return _outcome_for_result(
        entity_kind=EntityKind.COMPOUND,
        query=kegg_compound_id,
        source=SourceType.KEGG,
        result=result,
        unresolved_reason=FrontierReason.UNRESOLVED_REACTION_PARTICIPANT,
        persist=lambda: persist_compound(identity, result, session=session),
    )


def resolve_reference_compartment_by_name(name: str, *, lookup: CompartmentLookup) -> UUID | None:
    """Exact, reference-scope-only compartment resolution for
    ``PathwayCurationRequest.default_compartment_text`` (Increment C pre-commit revision).

    Deliberately **not** ``app.normalization.compartment.normalize_compartment``: that
    module's own identity policy treats a bare ``name`` as Level 2/3 candidate-generation
    only, so a name-only claim can never independently reach ``MATCHED`` (see that
    module's own docstring) -- appropriate for a *connector's* identity claim, but not for
    this case, which is not a claim at all. ``default_compartment_text`` is the human
    caller's own explicit scope assertion, checked here only against the standard/
    reference compartments seeded by migration ``0002_reference_data``
    (``organism_id IS NULL``) by exact name. Returns the resolved id only when exactly one
    such reference compartment matches; returns ``None`` for zero or more than one match
    -- never fuzzy, never creates a new compartment row, never guesses among several.
    """
    candidates = lookup.by_name(None, name)
    unique_ids = {candidate.id for candidate in candidates}
    if len(unique_ids) == 1:
        return next(iter(unique_ids))
    return None


def associate_catalyst(
    *,
    reaction_id: UUID,
    protein_id: UUID,
    relationship: str,
    session: Session,
    lookup: ReactionEnzymeLookup,
) -> ResolutionOutcome:
    """Persist one conservatively-supported reaction/protein catalyst association.

    The caller (``executor._associate_catalysts``) only ever calls this for a protein
    that was both (a) explicitly named in the request's own ``seed_entity_texts`` and
    (b) carries an EC number that exactly matches this specific reaction's own
    ``ec_number`` -- see that function's own docstring for the full evidence-hierarchy
    rationale (no connector in this repository exposes a structured, organism-specific
    reaction<->gene mapping; EC-number equality alone is explicitly insufficient per
    ``app.normalization.reaction_enzyme``'s own module docstring, so this package never
    calls this function from EC equality alone -- only from an explicit seed plus a
    corroborating EC match). ``source`` is ``SourceType.OTHER``: this association is
    derived by this package's own conservative policy, not fetched from any one
    connector record.
    """
    identity = ReactionEnzymeIdentity(
        source=SourceType.OTHER,
        source_identifier=f"pathway-curation::{reaction_id}::{protein_id}",
        reaction_id=reaction_id,
        protein_id=protein_id,
        relationship=relationship,
    )
    result = normalize_reaction_enzyme(identity, lookup=lookup)
    return _outcome_for_result(
        entity_kind=EntityKind.PROTEIN,
        query=f"{reaction_id}:{protein_id}",
        source=SourceType.OTHER,
        result=result,
        unresolved_reason=FrontierReason.REACTION_ENZYME_PERSISTENCE_FAILED,
        persist=lambda: persist_reaction_enzyme(identity, result, session=session),
    )


# --- Genes/proteins (SGD/UniProt) ---------------------------------------------------------------


def resolve_gene_by_text(
    connector: SgdSearchAndFetch,
    query: str,
    *,
    organism_id: UUID,
    organism_context_text: str | None,
    lookup: GeneLookup,
    session: Session,
) -> ResolutionOutcome:
    """Discovery path: free-text SGD gene search."""
    candidates = resolve_gene_via_sgd(
        query=query,
        original_mention=query,
        source_identifier=query,
        organism_id=organism_id,
        organism_context_text=organism_context_text,
        connector=connector,
        lookup=lookup,
    )
    return _classify_candidates(
        entity_kind=EntityKind.GENE,
        query=query,
        source=SourceType.SGD,
        candidates=candidates,
        unresolved_reason=FrontierReason.UNRESOLVED_CATALYST,
        persist=lambda: persist_gene(
            candidates[0].normalization_input,
            candidates[0].normalization_result,
            organism_id=organism_id,
            session=session,
        ),
    )


def resolve_protein_by_text(
    connector: UniProtSearchAndFetch,
    query: str,
    *,
    organism_id: UUID,
    organism_context_text: str | None,
    lookup: ProteinLookup,
    session: Session,
) -> ResolutionOutcome:
    """Discovery path: free-text UniProt protein search."""
    candidates = resolve_protein_via_uniprot(
        query=query,
        original_mention=query,
        organism_id=organism_id,
        organism_context_text=organism_context_text,
        connector=connector,
        lookup=lookup,
    )
    return _classify_candidates(
        entity_kind=EntityKind.PROTEIN,
        query=query,
        source=SourceType.UNIPROT,
        candidates=candidates,
        unresolved_reason=FrontierReason.UNRESOLVED_CATALYST,
        persist=lambda: persist_protein(
            candidates[0].normalization_input,
            candidates[0].normalization_result,
            organism_id=organism_id,
            session=session,
        ),
    )


# --- Publications (PubMed) ----------------------------------------------------------------------


def discover_publications(
    connector: PubMedSearchAndFetch, query: str, *, max_results: int
) -> tuple[str, ...]:
    """Search PubMed for ``query``. Returns bare PMIDs, deterministically ordered."""
    hits = connector.search(query)
    pmids = tuple(hit.pmid for hit in hits)[:max_results]
    return pmids


def resolve_publication_by_text(
    connector: PubMedSearchAndFetch, query: str, *, lookup: PublicationLookup, session: Session
) -> ResolutionOutcome:
    """Discovery path: free-text PubMed search, verified via a real fetched article per hit."""
    candidates = resolve_publication_via_pubmed(
        query=query, original_mention=query, connector=connector, lookup=lookup
    )
    return _classify_candidates(
        entity_kind=EntityKind.PUBLICATION,
        query=query,
        source=SourceType.PUBMED,
        candidates=candidates,
        unresolved_reason=FrontierReason.MISSING_PUBLICATION,
        persist=lambda: persist_publication(
            candidates[0].normalization_input, candidates[0].normalization_result, session=session
        ),
    )


def resolve_publication_by_pmid(
    connector: PubMedSearchAndFetch, pmid: str, *, lookup: PublicationLookup, session: Session
) -> ResolutionOutcome:
    """Expansion path: fetch one exact, already-known PMID directly."""
    article = connector.fetch(pmid)
    if article is None:
        return ResolutionOutcome(
            entity_kind=EntityKind.PUBLICATION,
            query=pmid,
            frontier_reason=FrontierReason.MISSING_PUBLICATION,
            source=SourceType.PUBMED,
            notes="PMID not found (fetch returned None)",
        )
    normalized_record = connector.normalize(article)
    identity = publication_identity_from_pubmed(normalized_record)
    result = normalize_publication(identity, lookup=lookup)
    return _outcome_for_result(
        entity_kind=EntityKind.PUBLICATION,
        query=pmid,
        source=SourceType.PUBMED,
        result=result,
        unresolved_reason=FrontierReason.MISSING_PUBLICATION,
        persist=lambda: persist_publication(identity, result, session=session),
    )


# --- Kinetics (SABIO-RK / Open Enzyme Database) --------------------------------------------------


def discover_kinetics_sabiork(
    connector,
    ec_number: str,
    *,
    organism: str | None,
    protein_id: UUID | None = None,
    organism_id: UUID | None = None,
) -> tuple[KineticMeasurementIdentity, ...]:
    """Search SABIO-RK for ``ec_number`` and build one ``KineticMeasurementIdentity`` per
    reported parameter (never persisted here -- the caller/executor persists each one, since
    ``persist_kinetic_measurement`` has no ``lookup``/normalize step to run first, unlike every
    structural entity type above). ``kinetic_identity_from_sabiork`` returns ``None`` for a
    parameter with no reported value -- never invented, simply skipped.

    ``protein_id``/``organism_id`` are threaded straight through so the persisted measurement
    is actually linked to the catalytic context/organism that motivated this search -- an
    unlinked measurement would be structurally correct but invisible to
    ``app.agent1.service.get_agent1_knowledge_package``'s own organism-scoping rule.
    """
    hits = connector.search(ec_number, organism=organism)
    identities: list[KineticMeasurementIdentity] = []
    for hit in hits:
        record = connector.fetch(hit.entry_id)
        if record is None:
            continue
        for parameter in connector.normalize(record):
            identity = kinetic_identity_from_sabiork(
                record, parameter, protein_id=protein_id, organism_id=organism_id
            )
            if identity is not None:
                identities.append(identity)
    return tuple(identities)


def discover_kinetics_oed(
    connector,
    ec_number: str,
    *,
    organism: str | None,
    protein_id: UUID | None = None,
    organism_id: UUID | None = None,
) -> tuple[KineticMeasurementIdentity, ...]:
    """Search Open Enzyme Database for ``ec_number``. OED has no ``fetch()`` -- ``search()``
    rows are already complete records (see that connector's own contract)."""
    rows = connector.search(ec_number=ec_number, organism=organism)
    identities: list[KineticMeasurementIdentity] = []
    for row in rows:
        for parameter in connector.normalize(row):
            identity = kinetic_identity_from_oed(
                parameter, protein_id=protein_id, organism_id=organism_id
            )
            if identity is not None:
                identities.append(identity)
    return tuple(identities)
