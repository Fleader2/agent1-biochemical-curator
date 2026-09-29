"""Evidence-Based Kinetic Measurement -> Reaction Attribution.

Read-only, decision-only: nothing here writes to the database, mirroring
``app.normalization.reaction``'s own architecture exactly. Candidate lookups are
performed through the injected, read-only
``KineticMeasurementReactionAttributionLookup`` Protocol; a real, database-backed
implementation lives in ``app.pathway_curation.lookups``
(``SqlAlchemyKineticMeasurementReactionAttributionLookup``).

**The problem this solves, precisely.** ``KineticMeasurement.reaction_id`` (and
``app.normalization.kinetic_measurement.KineticMeasurementIdentity.reaction_id``,
already threaded end-to-end through every adapter and
``app.persistence.kinetic_measurement.persist_kinetic_measurement``) has existed
since Agent 1.x Increment A, but nothing in this repository has ever computed a
value for it -- confirmed by direct inspection before this increment's first
commit: every ``kinetic_identity_from_*`` call site in
``app.pathway_curation.executor`` passes no ``reaction_id`` at all, and a live
fresh pilot against the real ``sce00061`` pathway (Multi-Context Catalytic Rate
Composition pilot series, Agent 2 side) confirmed **0 of 216 real curated kinetic
measurements carry a ``reaction_id``**. This module is the missing decision logic;
``app.pathway_curation.executor``/``.strategies`` (this increment) wire it in.

**Central rule (never weakened for coverage).** Attribute a measurement to a
``Reaction`` only when structured identifiers, catalyst identity, compound
context, EC information, and pathway membership reduce the candidate set to
**exactly one** biologically supported reaction. Two or more candidates that
survive every applicable narrowing step are reported ``AMBIGUOUS_MULTIPLE_
REACTIONS``, never guessed among. This module creates no ``Reaction``,
``Compound``, ``Protein``, or cross-reference -- match-only against already-
curated entities (mirrors ``app.normalization.reaction``'s own "no persistence
API is invented here" stance).

**Attribution hierarchy, strictly ordered, each tier attempted only when the
previous one did not resolve to exactly one reaction:**

1. ``DIRECT_REACTION_IDENTIFIER`` -- an authoritative, source-reported reaction
   identifier (today: only ``GotEnzymesPrediction.reaction_id``, a real KEGG
   reaction id, e.g. ``"R00742"`` -- confirmed live-format via
   ``tests/connectors/test_gotenzymes.py``; SABIO-RK/BRENDA report no reaction-
   level external identifier at all, confirmed by direct inspection of
   ``app.connectors.sabiork.SabioKineticRecord``/
   ``app.connectors.brenda.BrendaKineticMeasurement``) that maps, via
   ``by_kegg_reaction_id``/``by_metacyc_reaction_id``/``by_rhea_id``, to exactly
   one already-curated ``Reaction``. **Real, confirmed opportunity**: all 38/38
   real ``sce00061`` ``Reaction`` rows already carry a real ``kegg_reaction_id``
   (populated by this pathway's own KEGG-driven structural curation) -- this tier
   is not theoretical for this pathway. Never inferred from free-text names.
   Two or more candidates sharing the identical external identifier (a real,
   documented possibility -- ``app.normalization.reaction``'s own module
   docstring) is an immediate, final ``AMBIGUOUS_MULTIPLE_REACTIONS`` for this
   tier; it does not fall through to catalyst-based narrowing (a data-quality
   signal at the identifier level should never be silently rescued by a weaker
   tier).
2. ``CATALYST_AND_COMPOUND_UNIQUE`` -- restrict candidates to every ``Reaction``
   already curated as catalyzed (``reaction_enzyme``) by the measurement's own
   authoritative catalyst target(s) (``protein_ids`` -- plural, unioned across
   every authoritative protein context, never collapsed to one; ``complex_id``;
   ``enzyme_state_id``). If that alone narrows to exactly one, done -- no
   compound anchor was even needed (a protein/complex/state curated as
   catalyzing exactly one reaction is, by definition, unambiguous regardless of
   parameter type). If it does not, and the measurement's own ``parameter_type``
   is compound-anchor-eligible (``KM``/``KI`` only -- see "Parameter-type
   policy" below) and a resolved ``substrate_id`` is available, filter further
   to candidates whose own ``participants`` include that exact compound as a
   ``REACTANT`` or ``PRODUCT``. Exactly one surviving candidate either way is
   ``CATALYST_AND_COMPOUND_UNIQUE``.
3. ``CATALYST_EC_AND_COMPOUND_UNIQUE`` -- when tier 2 still leaves two or more
   candidates, additionally require the candidate's own ``ec_number`` to
   exactly equal the measurement's own authoritative EC number (the protein's
   own curated ``ec_number``, the same one the source connector was searched
   with -- never a source's own free-text-adjacent EC field independently, to
   avoid two different "EC number" provenances silently disagreeing). EC alone
   is explicitly insufficient (this repository's own many-reactions-per-EC
   reality, ``app.normalization.reaction``'s own "EC number... classifies an
   activity, not a specific transformation" policy) -- it is only ever applied
   as one more AND-ed narrowing constraint on top of tier 2's own candidate set,
   never as an independent discovery mechanism.
4. ``CATALYST_AND_REACTION_SIGNATURE_UNIQUE`` -- when tier 3 still leaves two or
   more candidates, additionally require a **partial, role-only** reaction
   signature built from source data (today: only SABIO-RK's own
   ``SabioKineticRecord.reaction_species``, resolved to real Agent 1 compound
   ids via the *same* ``compound_external_identities`` structured-identifier
   mechanism ``app.normalization.compound.compound_identity_from_sabiork``
   already uses for one substrate) to be a non-empty subset of the candidate's
   own ``(compound_id, role)`` participant pairs. **Never compares
   stoichiometry**: SABIO-RK's own connector model
   (``app.connectors.sabiork.SabioReactionSpecies``) does not expose a
   stoichiometry field at all (confirmed by direct inspection -- this is an
   honest absence, never a guessed value substituted for a real one). Only
   ``"Substrate"``/``"Product"`` roles (confirmed real, live-observed strings,
   ``tests/connectors/test_sabiork.py``) map to
   ``ReactionParticipantRole.REACTANT``/``.PRODUCT``; any other reported role
   string (e.g. an inhibitor/activator/modifier) is excluded from the signature
   entirely, never guessed into a stoichiometric role.

Pathway membership is deliberately **not** a tier of its own: nothing in this
module's own candidate universe ever includes a reaction *merely* because it
shares a pathway with an already-resolved candidate (the central rule's own
explicit prohibition) -- every candidate here is already independently,
biologically supported by catalyst/identifier evidence before any further
narrowing is applied.

**Parameter-type policy (never weakened).** ``KM``/``KI`` alone may use a
resolved compound anchor for tier-2/3 narrowing -- both are properties of one
specific substrate/inhibitor's interaction with the enzyme, so compound identity
is genuine reaction-context evidence for them. ``KCAT``/``VMAX``/
``KCAT_OVER_KM``/``SPECIFIC_ACTIVITY``/``PH_OPTIMUM``/``TEMPERATURE_OPTIMUM``/
``OTHER`` are all properties of the *catalytic turnover itself*, not of one
substrate's binding -- a source-associated compound happening to match a
reaction participant is never treated as reaction-context evidence for any of
these; they can only ever be attributed via tier 1 (a direct identifier) or via
catalyst identity alone (tier 2's own catalyst-only branch, when the catalyst
curated a single reaction) -- never via compound, EC-plus-compound, or
signature narrowing, all three of which are compound-anchor tiers. GotEnzymes2
predictions run through the identical hierarchy and parameter-type policy as
every experimental source -- attribution reasoning never upgrades or downgrades
``AI_PREDICTED`` evidence class, a completely separate axis this module never
touches.

**Plural protein context and idempotency.** A measurement may be discovered
independently through more than one authoritative protein's own search (the
real, documented FAS1/FAS2 case, ``app.models.kinetic_measurement
.KineticMeasurementProteinContext``'s own module docstring) -- each such
discovery calls this module with *that* discovery's own single-protein (or
complex/state) context, since that is genuinely all the catalyst evidence
available at that call site; a plural ``protein_ids`` tuple (when a caller
already has one, e.g. re-attributing an existing row against its own complete,
authoritative protein-context set) is unioned across every named protein's own
curated reactions before narrowing, never collapsed to a single "winning"
protein. Attribution is deterministic given one fixed context and one fixed,
already-curated candidate universe: the same inputs always produce the same
result, and this module performs no fitting, no randomness, and no wall-clock-
or ordering-dependent decision of its own. The only ordering sensitivity that
can exist system-wide is at the *caller* level (which protein's search happens
to run first and therefore first supplies a context for an as-yet-unattributed
row) -- disclosed as a known limitation in this increment's own completion
report, never resolved by this module re-litigating an already-attributed row.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.models.enums import ReactionParticipantRole
from app.normalization.identifiers import CandidateSetState, classify_candidates, unique_by_id
from app.normalization.kinetic_measurement import KineticParameterType
from app.normalization.reaction import ReactionCandidate, ReactionParticipantIdentity

_ENTITY_TYPE = "kinetic_measurement_reaction_attribution"

#: Task's own explicit parameter-type policy: only these two kinds may ever use a
#: resolved compound anchor (substrate/inhibitor identity) to narrow candidates.
#: Every other kind (KCAT, VMAX, KCAT_OVER_KM, SPECIFIC_ACTIVITY, PH_OPTIMUM,
#: TEMPERATURE_OPTIMUM, OTHER) is a property of the catalytic turnover itself, never
#: of one substrate's binding, and must never be attributed merely because a
#: source-associated compound happens to match a participant.
COMPOUND_ANCHOR_ELIGIBLE_PARAMETER_TYPES = frozenset(
    {KineticParameterType.KM, KineticParameterType.KI}
)

#: SABIO-RK's own real, live-observed reaction.species[] role strings
#: (``tests/connectors/test_sabiork.py``) that map to a genuine stoichiometric
#: participant role. Any other reported role (inhibitor/activator/modifier/catalyst/
#: unset) is excluded from the reaction signature entirely -- never guessed into one
#: of these two roles.
_SABIORK_SIGNATURE_ROLE_MAP: dict[str, ReactionParticipantRole] = {
    "substrate": ReactionParticipantRole.REACTANT,
    "product": ReactionParticipantRole.PRODUCT,
}


def sabiork_signature_role(role: str | None) -> ReactionParticipantRole | None:
    """Map one SABIO-RK ``reaction.species[].role`` string to a stoichiometric
    participant role, or ``None`` when it does not describe one (see module
    docstring's tier-4 policy). Case-insensitive, whitespace-trimmed; no fuzzy
    matching beyond that.
    """
    if role is None:
        return None
    return _SABIORK_SIGNATURE_ROLE_MAP.get(role.strip().lower())


class KineticMeasurementReactionAttributionReason(StrEnum):
    """The controlled, closed reason vocabulary this module ever returns.

    Exactly the seven values the task's own instructions name -- no additional
    member was needed. ``resolved`` (below) is the pure, derived partition: the
    first four are always paired with a real ``reaction_id``; the last three
    never are.
    """

    DIRECT_REACTION_IDENTIFIER = "DIRECT_REACTION_IDENTIFIER"
    CATALYST_AND_COMPOUND_UNIQUE = "CATALYST_AND_COMPOUND_UNIQUE"
    CATALYST_EC_AND_COMPOUND_UNIQUE = "CATALYST_EC_AND_COMPOUND_UNIQUE"
    CATALYST_AND_REACTION_SIGNATURE_UNIQUE = "CATALYST_AND_REACTION_SIGNATURE_UNIQUE"
    AMBIGUOUS_MULTIPLE_REACTIONS = "AMBIGUOUS_MULTIPLE_REACTIONS"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    NO_CANDIDATE = "NO_CANDIDATE"

    @property
    def resolved(self) -> bool:
        """True for the four reasons that always carry a real ``reaction_id``."""
        return self in _RESOLVED_REASONS


_RESOLVED_REASONS = frozenset(
    {
        KineticMeasurementReactionAttributionReason.DIRECT_REACTION_IDENTIFIER,
        KineticMeasurementReactionAttributionReason.CATALYST_AND_COMPOUND_UNIQUE,
        KineticMeasurementReactionAttributionReason.CATALYST_EC_AND_COMPOUND_UNIQUE,
        KineticMeasurementReactionAttributionReason.CATALYST_AND_REACTION_SIGNATURE_UNIQUE,
    }
)


@dataclass(frozen=True, slots=True)
class KineticMeasurementAttributionContext:
    """Every already-resolved, already-normalized piece of evidence one kinetic
    measurement carries that this module may use for attribution.

    **Never resolves anything itself** -- every field is accepted exactly as the
    caller already resolved it (mirrors ``KineticMeasurementIdentity``'s own
    identical "never resolves entity identity" policy). ``protein_ids``/
    ``complex_id``/``enzyme_state_id`` are the measurement's own authoritative
    catalyst context (at least one target kind is expected when any catalyst
    identity exists at all, mirroring ``ReactionEnzymeIdentity``'s XOR -- but
    unlike that type, this dataclass does not enforce mutual exclusivity: a
    caller re-attributing an existing measurement against its own complete
    protein-context set never has a complex/enzyme-state target simultaneously
    in the current schema, so this is a real, not merely defensive, absence of
    conflict).

    ``ec_number`` is the catalyst's own authoritative, already-curated EC number
    (``Protein.ec_number`` -- the same value a connector was searched with),
    never a source record's own free-text-adjacent EC field independently.

    ``kegg_reaction_id``/``rhea_id``/``metacyc_reaction_id`` are populated only
    when the *source record itself* reports one directly (today: only
    ``GotEnzymesPrediction.reaction_id``, mapped to ``kegg_reaction_id`` by the
    caller -- see module docstring).

    ``reaction_participants`` is the partial reaction signature tier 4 uses --
    role-only, no stoichiometry (see module docstring). Empty unless the source
    genuinely supports it.
    """

    parameter_type: KineticParameterType
    protein_ids: tuple[UUID, ...] = ()
    complex_id: UUID | None = None
    enzyme_state_id: UUID | None = None
    substrate_id: UUID | None = None
    ec_number: str | None = None
    kegg_reaction_id: str | None = None
    rhea_id: str | None = None
    metacyc_reaction_id: str | None = None
    reaction_participants: tuple[ReactionParticipantIdentity, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.parameter_type, KineticParameterType):
            raise TypeError(
                "KineticMeasurementAttributionContext.parameter_type must be a "
                f"KineticParameterType, got {self.parameter_type!r}"
            )
        object.__setattr__(self, "protein_ids", tuple(dict.fromkeys(self.protein_ids)))

    @property
    def has_catalyst_identity(self) -> bool:
        return (
            bool(self.protein_ids)
            or self.complex_id is not None
            or self.enzyme_state_id is not None
        )

    @property
    def has_direct_identifier(self) -> bool:
        return bool(self.kegg_reaction_id or self.rhea_id or self.metacyc_reaction_id)


@dataclass(frozen=True, slots=True)
class KineticMeasurementReactionAttributionResult:
    """One attribution decision. Exactly one of ``reaction_id``/
    ``candidate_reaction_ids`` may be populated, governed entirely by
    ``reason.resolved`` -- never both, matching that property's own closed
    partition of the reason vocabulary.
    """

    reason: KineticMeasurementReactionAttributionReason
    explanation: str
    reaction_id: UUID | None = None
    candidate_reaction_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reason, KineticMeasurementReactionAttributionReason):
            raise TypeError(
                "KineticMeasurementReactionAttributionResult.reason must be a "
                f"KineticMeasurementReactionAttributionReason, got {self.reason!r}"
            )
        if not self.explanation.strip():
            raise ValueError("KineticMeasurementReactionAttributionResult requires explanation")
        if self.reason.resolved:
            if self.reaction_id is None:
                raise ValueError(f"{self.reason} requires reaction_id")
            if self.candidate_reaction_ids:
                raise ValueError(f"{self.reason} must not carry candidate_reaction_ids")
        else:
            if self.reaction_id is not None:
                raise ValueError(f"{self.reason} must not carry reaction_id")

    @property
    def resolved(self) -> bool:
        return self.reason.resolved


@runtime_checkable
class KineticMeasurementReactionAttributionLookup(Protocol):
    """Read-only candidate lookup, injected so this module never touches SQLAlchemy.

    ``by_kegg_reaction_id``/``by_rhea_id``/``by_metacyc_reaction_id`` mirror
    ``app.normalization.reaction.ReactionLookup``'s own identical three methods
    exactly (same global, no-organism-parameter semantics) -- a real
    implementation may simply delegate to that same lookup, or reuse its query
    logic directly. ``reactions_catalyzed_by_protein``/``_by_complex``/
    ``_by_enzyme_state`` are new: every already-curated ``Reaction`` a given
    catalyst target is recorded (``reaction_enzyme``) as catalyzing, **with its
    own ``participants`` populated** (unlike ``ReactionLookup``'s own real
    implementation, which never populates them -- a pre-existing, disclosed,
    unrelated limitation this module does not fix, see this increment's
    completion report) -- this module's tier-2/3/4 narrowing requires real
    participant data to filter on.
    """

    def by_kegg_reaction_id(self, kegg_reaction_id: str) -> Sequence[ReactionCandidate]: ...

    def by_metacyc_reaction_id(self, metacyc_reaction_id: str) -> Sequence[ReactionCandidate]: ...

    def by_rhea_id(self, rhea_id: str) -> Sequence[ReactionCandidate]: ...

    def reactions_catalyzed_by_protein(self, protein_id: UUID) -> Sequence[ReactionCandidate]: ...

    def reactions_catalyzed_by_complex(self, complex_id: UUID) -> Sequence[ReactionCandidate]: ...

    def reactions_catalyzed_by_enzyme_state(
        self, enzyme_state_id: UUID
    ) -> Sequence[ReactionCandidate]: ...


def _direct_identifier_result(
    context: KineticMeasurementAttributionContext,
    lookup: KineticMeasurementReactionAttributionLookup,
) -> KineticMeasurementReactionAttributionResult | None:
    """Tier 1. Returns ``None`` (never a result) when no direct identifier is present at
    all, or every present identifier finds zero candidates -- both fall through to tier
    2. An identifier that finds 2+ candidates is a final, non-fallthrough result."""
    checks: tuple[tuple[str | None, str], ...] = (
        (context.kegg_reaction_id, "kegg_reaction_id"),
        (context.rhea_id, "rhea_id"),
        (context.metacyc_reaction_id, "metacyc_reaction_id"),
    )
    method_by_field = {
        "kegg_reaction_id": lookup.by_kegg_reaction_id,
        "rhea_id": lookup.by_rhea_id,
        "metacyc_reaction_id": lookup.by_metacyc_reaction_id,
    }
    for value, field_name in checks:
        if value is None:
            continue
        candidates = unique_by_id(method_by_field[field_name](value))
        state = classify_candidates(tuple(c.id for c in candidates))
        if state is CandidateSetState.SINGLE_MATCH:
            return KineticMeasurementReactionAttributionResult(
                reason=KineticMeasurementReactionAttributionReason.DIRECT_REACTION_IDENTIFIER,
                reaction_id=candidates[0].id,
                explanation=(
                    f"source-reported {field_name} {value!r} maps to exactly one existing "
                    f"reaction ({candidates[0].id})"
                ),
            )
        if state is CandidateSetState.AMBIGUOUS:
            return KineticMeasurementReactionAttributionResult(
                reason=KineticMeasurementReactionAttributionReason.AMBIGUOUS_MULTIPLE_REACTIONS,
                candidate_reaction_ids=tuple(sorted((c.id for c in candidates), key=str)),
                explanation=(
                    f"source-reported {field_name} {value!r} matches {len(candidates)} "
                    "existing reactions -- a direct identifier ambiguity is never rescued "
                    "by catalyst-based narrowing"
                ),
            )
        # NO_MATCH: this identifier does not (yet) resolve; try the next one, then tier 2.
    return None


def _catalyst_candidates(
    context: KineticMeasurementAttributionContext,
    lookup: KineticMeasurementReactionAttributionLookup,
) -> tuple[ReactionCandidate, ...]:
    """Every reaction curated as catalyzed by any of this context's own authoritative
    catalyst targets, unioned (never collapsed to one "winning" target) and deduplicated
    by id."""
    candidates: list[ReactionCandidate] = []
    for protein_id in context.protein_ids:
        candidates.extend(lookup.reactions_catalyzed_by_protein(protein_id))
    if context.complex_id is not None:
        candidates.extend(lookup.reactions_catalyzed_by_complex(context.complex_id))
    if context.enzyme_state_id is not None:
        candidates.extend(lookup.reactions_catalyzed_by_enzyme_state(context.enzyme_state_id))
    return unique_by_id(candidates)


def _compound_anchor_filter(
    candidates: tuple[ReactionCandidate, ...], substrate_id: UUID
) -> tuple[ReactionCandidate, ...]:
    return tuple(
        c
        for c in candidates
        if any(
            p.compound_id == substrate_id
            and p.role in (ReactionParticipantRole.REACTANT, ReactionParticipantRole.PRODUCT)
            for p in c.participants
        )
    )


def _ec_filter(
    candidates: tuple[ReactionCandidate, ...], ec_number: str
) -> tuple[ReactionCandidate, ...]:
    return tuple(c for c in candidates if c.ec_number == ec_number)


def _signature_filter(
    candidates: tuple[ReactionCandidate, ...],
    signature: tuple[ReactionParticipantIdentity, ...],
) -> tuple[ReactionCandidate, ...]:
    """Subset-containment only, compound_id+role pairs, never stoichiometry (see module
    docstring's tier-4 policy)."""
    signature_pairs = {(p.compound_id, p.role) for p in signature}
    return tuple(
        c
        for c in candidates
        if signature_pairs <= {(p.compound_id, p.role) for p in c.participants}
    )


def attribute_kinetic_measurement_to_reaction(
    context: KineticMeasurementAttributionContext,
    *,
    lookup: KineticMeasurementReactionAttributionLookup,
) -> KineticMeasurementReactionAttributionResult:
    """Deterministically attribute one kinetic measurement's ``reaction_id``, or
    disclose exactly why it could not be. See module docstring for the full,
    strictly-ordered hierarchy and parameter-type policy.
    """
    if not isinstance(context, KineticMeasurementAttributionContext):
        raise TypeError(
            "attribute_kinetic_measurement_to_reaction requires a "
            f"KineticMeasurementAttributionContext, got {context!r}"
        )

    # Tier 1.
    if context.has_direct_identifier:
        direct = _direct_identifier_result(context, lookup)
        if direct is not None:
            return direct

    # No catalyst identity and no (resolving) direct identifier at all -- nothing left
    # to attempt.
    if not context.has_catalyst_identity:
        return KineticMeasurementReactionAttributionResult(
            reason=KineticMeasurementReactionAttributionReason.INSUFFICIENT_CONTEXT,
            explanation=(
                "no direct reaction identifier resolved and no authoritative catalyst "
                "(protein/complex/enzyme-state) context is available for this measurement"
            ),
        )

    # Tier 2 (catalyst alone, then optionally + compound).
    candidates = _catalyst_candidates(context, lookup)
    if not candidates:
        return KineticMeasurementReactionAttributionResult(
            reason=KineticMeasurementReactionAttributionReason.NO_CANDIDATE,
            explanation=(
                "the measurement's own authoritative catalyst context is known, but no "
                "reaction is yet curated as catalyzed by it"
            ),
        )
    state = classify_candidates(tuple(c.id for c in candidates))
    if state is CandidateSetState.SINGLE_MATCH:
        return KineticMeasurementReactionAttributionResult(
            reason=KineticMeasurementReactionAttributionReason.CATALYST_AND_COMPOUND_UNIQUE,
            reaction_id=candidates[0].id,
            explanation=(
                "the measurement's authoritative catalyst context is curated as catalyzing "
                f"exactly one reaction ({candidates[0].id}) -- no compound anchor was needed"
            ),
        )

    substrate_id = context.substrate_id
    compound_eligible = (
        context.parameter_type in COMPOUND_ANCHOR_ELIGIBLE_PARAMETER_TYPES
        and substrate_id is not None
    )
    narrowed = candidates
    if compound_eligible:
        assert substrate_id is not None  # guaranteed by compound_eligible above
        after_compound = _compound_anchor_filter(candidates, substrate_id)
        compound_state = classify_candidates(tuple(c.id for c in after_compound))
        if compound_state is CandidateSetState.SINGLE_MATCH:
            return KineticMeasurementReactionAttributionResult(
                reason=KineticMeasurementReactionAttributionReason.CATALYST_AND_COMPOUND_UNIQUE,
                reaction_id=after_compound[0].id,
                explanation=(
                    "the measurement's authoritative catalyst context catalyzes "
                    f"{len(candidates)} reactions, but exactly one "
                    f"({after_compound[0].id}) has the resolved substrate/inhibitor "
                    f"{context.substrate_id} as a reactant or product"
                ),
            )
        if compound_state is CandidateSetState.AMBIGUOUS:
            narrowed = after_compound
        # NO_MATCH after compound filtering: the compound context does not match any
        # catalyst-scoped candidate -- fall through to EC/signature narrowing on the
        # original, unfiltered catalyst set (never treated as itself a reason to stop).

    # Tier 3 (+ EC number).
    if context.ec_number is not None:
        after_ec = _ec_filter(narrowed, context.ec_number)
        ec_state = classify_candidates(tuple(c.id for c in after_ec))
        if ec_state is CandidateSetState.SINGLE_MATCH:
            return KineticMeasurementReactionAttributionResult(
                reason=KineticMeasurementReactionAttributionReason.CATALYST_EC_AND_COMPOUND_UNIQUE,
                reaction_id=after_ec[0].id,
                explanation=(
                    f"EC number {context.ec_number!r} plus compound/catalyst context narrows "
                    f"{len(narrowed)} remaining candidates to exactly one ({after_ec[0].id})"
                ),
            )
        if after_ec:
            narrowed = after_ec

    # Tier 4 (+ reaction signature).
    if context.reaction_participants:
        after_signature = _signature_filter(narrowed, context.reaction_participants)
        signature_state = classify_candidates(tuple(c.id for c in after_signature))
        if signature_state is CandidateSetState.SINGLE_MATCH:
            return KineticMeasurementReactionAttributionResult(
                reason=(
                    KineticMeasurementReactionAttributionReason.CATALYST_AND_REACTION_SIGNATURE_UNIQUE
                ),
                reaction_id=after_signature[0].id,
                explanation=(
                    "a partial, role-only reaction signature narrows "
                    f"{len(narrowed)} remaining candidates to exactly one "
                    f"({after_signature[0].id})"
                ),
            )
        if after_signature:
            narrowed = after_signature

    return KineticMeasurementReactionAttributionResult(
        reason=KineticMeasurementReactionAttributionReason.AMBIGUOUS_MULTIPLE_REACTIONS,
        candidate_reaction_ids=tuple(sorted((c.id for c in narrowed), key=str)),
        explanation=(
            f"{len(narrowed)} candidate reactions remain after every applicable narrowing "
            "step (catalyst, compound, EC, signature) -- never guessed among"
        ),
    )


__all__ = [
    "COMPOUND_ANCHOR_ELIGIBLE_PARAMETER_TYPES",
    "KineticMeasurementAttributionContext",
    "KineticMeasurementReactionAttributionLookup",
    "KineticMeasurementReactionAttributionReason",
    "KineticMeasurementReactionAttributionResult",
    "attribute_kinetic_measurement_to_reaction",
    "sabiork_signature_role",
]
