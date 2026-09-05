"""Dispatches one entity mention to the right ``app.normalization.*`` module.

This is the "use the existing normalization layer, do not duplicate it"
boundary (Increment 13 instructions, Step 4): every actual identity
comparison happens inside ``app.normalization.*``, exactly as it already
does for every other consumer of that layer. This module's only job is
deciding *which* normalizer to call for a given ``EntityKind``, building
the minimal ``*Identity`` object from the mention's free text, and
translating the result into a ``CandidateEntityReference`` -- including
translating the layer's own "not enough identity signal" convention (a
plain ``ValueError`` from an ``Identity`` dataclass's ``__post_init__``)
into an unresolved reference rather than an error, since that is an
ordinary, expected outcome for a bare text mention with no external
identifier attached, not a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.claim_generation.errors import EntityTypingError, NormalizationFailureError
from app.claim_generation.types import CandidateEntityReference, EntityKind
from app.models.enums import SourceType
from app.normalization.compartment import (
    CompartmentIdentity,
    CompartmentLookup,
    normalize_compartment,
)
from app.normalization.compound import CompoundIdentity, CompoundLookup, normalize_compound
from app.normalization.gene import GeneIdentity, GeneLookup, normalize_gene
from app.normalization.organism import OrganismIdentity, OrganismLookup, normalize_organism
from app.normalization.protein import ProteinIdentity, ProteinLookup, normalize_protein
from app.normalization.publication import (
    PublicationIdentity,
    PublicationLookup,
    normalize_publication,
)
from app.normalization.reaction import ReactionIdentity, ReactionLookup, normalize_reaction
from app.normalization.types import NormalizationResult, NormalizationStatus


@dataclass(frozen=True, slots=True)
class NormalizationLookups:
    """The read-only ``Lookup`` implementations this call has available, by entity kind.

    Every field is optional: a kind with no corresponding lookup here
    simply cannot be normalized (its mentions stay unresolved, never
    guessed) -- this module never fabricates a lookup or falls back to
    a different one.
    """

    organism: OrganismLookup | None = None
    publication: PublicationLookup | None = None
    gene: GeneLookup | None = None
    protein: ProteinLookup | None = None
    compound: CompoundLookup | None = None
    compartment: CompartmentLookup | None = None
    reaction: ReactionLookup | None = None


def _normalize_by_kind(
    *,
    kind: EntityKind,
    text: str,
    source: SourceType,
    source_identifier: str,
    organism_id: UUID | None,
    lookups: NormalizationLookups,
) -> NormalizationResult | None:
    """Return a ``NormalizationResult``, or ``None`` if normalization cannot be attempted.

    ``None`` means "no lookup available" or "this kind requires a resolved
    organism and none exists" -- both are ordinary, silent no-ops, not
    errors. Raises ``ValueError`` when the bare text is not sufficient
    identity signal for the target ``*Identity`` type -- the caller
    (``resolve_entity_reference``) is responsible for treating that as an
    unresolved outcome rather than letting it propagate.
    """
    if kind is EntityKind.ORGANISM:
        if lookups.organism is None:
            return None
        identity = OrganismIdentity(
            source=source, source_identifier=source_identifier, scientific_name=text
        )
        return normalize_organism(identity, lookup=lookups.organism)

    if kind is EntityKind.PUBLICATION:
        if lookups.publication is None:
            return None
        identity = PublicationIdentity(
            source=source, source_identifier=source_identifier, title=text
        )
        return normalize_publication(identity, lookup=lookups.publication)

    if kind is EntityKind.COMPOUND:
        if lookups.compound is None:
            return None
        identity = CompoundIdentity(
            source=source, source_identifier=source_identifier, canonical_name=text
        )
        return normalize_compound(identity, lookup=lookups.compound)

    if kind is EntityKind.COMPARTMENT:
        if lookups.compartment is None:
            return None
        identity = CompartmentIdentity(
            source=source, source_identifier=source_identifier, name=text
        )
        return normalize_compartment(
            identity, organism_id=organism_id, lookup=lookups.compartment
        )

    if kind is EntityKind.GENE:
        if lookups.gene is None or organism_id is None:
            return None
        identity = GeneIdentity(source=source, source_identifier=source_identifier, symbol=text)
        return normalize_gene(identity, organism_id=organism_id, lookup=lookups.gene)

    if kind is EntityKind.PROTEIN:
        if lookups.protein is None or organism_id is None:
            return None
        identity = ProteinIdentity(source=source, source_identifier=source_identifier, name=text)
        return normalize_protein(identity, organism_id=organism_id, lookup=lookups.protein)

    if kind is EntityKind.REACTION:
        if lookups.reaction is None or organism_id is None:
            return None
        identity = ReactionIdentity(source=source, source_identifier=source_identifier, name=text)
        return normalize_reaction(identity, organism_id=organism_id, lookup=lookups.reaction)

    raise EntityTypingError(f"no normalization dispatch exists for entity kind {kind!r}")


def resolve_entity_reference(
    *,
    text: str | None,
    kind: EntityKind,
    source: SourceType,
    source_identifier: str,
    organism_id: UUID | None,
    lookups: NormalizationLookups,
) -> CandidateEntityReference | None:
    """Resolve one entity mention to a ``CandidateEntityReference``, or ``None`` if there is none.

    Returns ``None`` only when ``text`` itself is ``None`` -- there is
    nothing to reference. Otherwise always returns a
    ``CandidateEntityReference`` (possibly unresolved): a mention is never
    silently dropped just because it could not be normalized.
    """
    if text is None:
        return None

    if kind is EntityKind.UNKNOWN:
        return CandidateEntityReference(original_text=text, entity_kind=EntityKind.UNKNOWN)

    try:
        result = _normalize_by_kind(
            kind=kind,
            text=text,
            source=source,
            source_identifier=source_identifier,
            organism_id=organism_id,
            lookups=lookups,
        )
    except EntityTypingError:
        raise
    except ValueError:
        # Not enough identity signal in the bare text alone (e.g. a
        # Publication mention with no PMID/PMCID/DOI) -- an ordinary,
        # expected outcome, not a bug. Leave unresolved.
        return CandidateEntityReference(original_text=text, entity_kind=kind)
    except Exception as exc:
        raise NormalizationFailureError(
            f"normalizing {kind.value} text {text!r} failed unexpectedly: {exc}"
        ) from exc

    if result is None:
        return CandidateEntityReference(original_text=text, entity_kind=kind)

    normalized_id = (
        result.matched_entity_id if result.status is NormalizationStatus.MATCHED else None
    )
    return CandidateEntityReference(
        original_text=text,
        entity_kind=kind,
        normalization_result=result,
        normalized_id=normalized_id,
    )


__all__ = [
    "NormalizationLookups",
    "resolve_entity_reference",
]
