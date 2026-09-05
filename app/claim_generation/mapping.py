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

**Increment 16 addition: preferring Entity Resolution when available.**
``resolve_entity_reference`` gained an optional ``connectors`` parameter
(an ``app.entity_resolution.resolver.ConnectorBundle``). When supplied
*and* ``kind`` is one Entity Resolution currently has a real connector for
(``GENE``, ``PROTEIN``, ``COMPOUND``, ``REACTION``, ``PUBLICATION`` --
``_ENTITY_RESOLUTION_KINDS`` below), this function builds an
``EntityMention`` and calls
``app.entity_resolution.resolver.resolve_entity_mention`` first, rather
than normalizing the bare text directly. The one exception is
``MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND`` -- meaning the
supplied ``connectors`` bundle happens not to include the one specific
connector this kind needs (e.g. a bundle with only ``kegg`` set, asked to
resolve a ``GENE`` mention) -- which falls through to the same direct
bare-text normalization this function has always performed. Every other
outcome (``RESOLVED``, ``AMBIGUOUS``, ``CONFLICTED``, ``NEW_CANDIDATE``,
``NO_CANDIDATE``, ``SOURCE_FAILURE``, ``UNRESOLVED``) is wrapped into a
``CandidateEntityReference`` as-is and returned immediately -- in
particular, ``SOURCE_FAILURE`` and ``UNRESOLVED`` are never silently
replaced by a direct-normalization fallback, since doing so would hide a
genuine connector failure or a missing organism-context precondition
behind what looks like an ordinary unresolved bare-text mention.

``ORGANISM``, ``COMPARTMENT``, and ``UNKNOWN`` are never routed through
Entity Resolution (they are not in ``_ENTITY_RESOLUTION_KINDS`` -- this
repository has no enrichment connector for any of them, see
``docs/12_entity_resolution_architecture.md`` §11) -- calls for those
kinds always take the direct-normalization path below, identical to every
prior increment's behavior.

``connectors`` defaults to ``None``, which disables Entity Resolution
entirely and reproduces this function's exact pre-Increment-16 behavior --
every existing caller that does not pass ``connectors`` is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from app.claim_generation.errors import EntityTypingError, NormalizationFailureError
from app.claim_generation.types import CandidateEntityReference, EntityKind
from app.extraction.types import EvidenceExtraction
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

if TYPE_CHECKING:
    # Deferred to avoid a circular import: app.entity_resolution.resolver
    # itself imports NormalizationLookups from this module. See
    # resolve_entity_reference's own docstring for the runtime
    # (local-import) side of this.
    from app.entity_resolution.resolver import ConnectorBundle
    from app.entity_resolution.types import MentionResolutionResult

#: Entity kinds Entity Resolution currently has a real trusted connector
#: for (SGD/Gene, UniProt/Protein, KEGG/Compound, KEGG/Reaction,
#: PubMed/Publication -- see docs/12_entity_resolution_architecture.md
#: §10). ORGANISM/COMPARTMENT/UNKNOWN are deliberately excluded: this
#: repository has no enrichment connector for any of them today (§11), so
#: routing them through Entity Resolution would only ever produce
#: UNSUPPORTED_ENTITY_KIND -- direct normalization remains their only path.
_ENTITY_RESOLUTION_KINDS = frozenset(
    {
        EntityKind.GENE,
        EntityKind.PROTEIN,
        EntityKind.COMPOUND,
        EntityKind.REACTION,
        EntityKind.PUBLICATION,
    }
)


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
        return normalize_compartment(identity, organism_id=organism_id, lookup=lookups.compartment)

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


def _reference_from_mention_result(result: MentionResolutionResult) -> CandidateEntityReference:
    """Wrap one ``MentionResolutionResult`` as a ``CandidateEntityReference``.

    ``normalized_id`` comes directly from ``result.resolved_entity_id`` --
    ``None`` for every status except ``RESOLVED`` (enforced by
    ``MentionResolutionResult`` itself). ``normalization_result`` is
    populated only when Entity Resolution found exactly one candidate
    (in which case exposing that single candidate's own result loses no
    information); with zero or multiple candidates it is left ``None`` --
    see ``CandidateEntityReference.mention_resolution_result``'s own
    docstring for why forcing one of several corroborating candidates to
    represent the whole reference would be dishonest.
    """
    normalization_result = (
        result.candidates[0].normalization_result if len(result.candidates) == 1 else None
    )
    return CandidateEntityReference(
        original_text=result.mention.original_text,
        entity_kind=result.mention.entity_kind,
        normalization_result=normalization_result,
        normalized_id=result.resolved_entity_id,
        mention_resolution_result=result,
    )


def _resolve_via_entity_resolution(
    *,
    text: str,
    kind: EntityKind,
    source: SourceType,
    source_identifier: str,
    organism_id: UUID | None,
    organism_context_text: str | None,
    evidence_extraction: EvidenceExtraction | None,
    connectors: ConnectorBundle,
    lookups: NormalizationLookups,
) -> CandidateEntityReference | None:
    """Attempt Entity Resolution for one mention; ``None`` means "fall back to direct".

    ``None`` is returned in exactly one case: Entity Resolution itself
    reports ``UNSUPPORTED_ENTITY_KIND`` (the supplied ``connectors`` bundle
    does not include the specific connector this ``kind`` needs). Every
    other outcome -- including ``SOURCE_FAILURE`` and ``UNRESOLVED`` -- is
    wrapped and returned directly, never silently replaced by a
    bare-text-normalization fallback (Increment 16 instructions, §B14).
    """
    # Local imports: app.entity_resolution.resolver imports
    # NormalizationLookups from this module, so a module-level import here
    # would be circular. By the time any caller passes a real
    # ConnectorBundle, app.entity_resolution.resolver has already fully
    # loaded (constructing one requires importing it first).
    from app.entity_resolution.resolver import resolve_entity_mention
    from app.entity_resolution.types import EntityMention, MentionResolutionStatus

    mention = EntityMention(
        original_text=text,
        entity_kind=kind,
        source_context=source,
        source_context_identifier=source_identifier,
        organism_context_text=organism_context_text,
        organism_id=organism_id,
        evidence_extraction=evidence_extraction,
    )
    result = resolve_entity_mention(mention, connectors=connectors, lookups=lookups)
    if result.status is MentionResolutionStatus.UNSUPPORTED_ENTITY_KIND:
        return None
    return _reference_from_mention_result(result)


def resolve_entity_reference(
    *,
    text: str | None,
    kind: EntityKind,
    source: SourceType,
    source_identifier: str,
    organism_id: UUID | None,
    lookups: NormalizationLookups,
    connectors: ConnectorBundle | None = None,
    organism_context_text: str | None = None,
    evidence_extraction: EvidenceExtraction | None = None,
) -> CandidateEntityReference | None:
    """Resolve one entity mention to a ``CandidateEntityReference``, or ``None`` if there is none.

    Returns ``None`` only when ``text`` itself is ``None`` -- there is
    nothing to reference. Otherwise always returns a
    ``CandidateEntityReference`` (possibly unresolved): a mention is never
    silently dropped just because it could not be normalized.

    ``connectors``, ``organism_context_text``, and ``evidence_extraction``
    are Increment 16 additions -- see module docstring for the Entity
    Resolution preference this enables. All three default to values that
    reproduce this function's exact pre-Increment-16 behavior.
    """
    if text is None:
        return None

    if kind is EntityKind.UNKNOWN:
        return CandidateEntityReference(original_text=text, entity_kind=EntityKind.UNKNOWN)

    if connectors is not None and kind in _ENTITY_RESOLUTION_KINDS:
        reference = _resolve_via_entity_resolution(
            text=text,
            kind=kind,
            source=source,
            source_identifier=source_identifier,
            organism_id=organism_id,
            organism_context_text=organism_context_text,
            evidence_extraction=evidence_extraction,
            connectors=connectors,
            lookups=lookups,
        )
        if reference is not None:
            return reference
        # UNSUPPORTED_ENTITY_KIND: the connectors bundle didn't include
        # this kind's specific connector -- fall through to direct
        # bare-text normalization below, exactly as if connectors=None.

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
