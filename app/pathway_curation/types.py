"""Core contract types for the Pathway Curation Planner (Agent 1.x Increment C).

Every type here is an immutable, self-validating data contract, mirroring
the convention already established throughout this repository (see, for
example, ``app.entity_resolution.types``/``app.knowledge_gaps.types``).
Nothing in this module performs a connector call, a normalization
decision, or a database write -- it only defines the shapes the rest of
``app.pathway_curation`` reads and produces. See
``docs/26_autonomous_pathway_curation_planner.md`` for the full contract.

Sections:

1. **Policy vocabularies** -- ``CompletionPolicy``, ``CurationMode``,
   ``PlannerAction``, ``PlanStepStatus``, ``FrontierReason``,
   ``CompletionStatus``. Small, controlled enums -- never an opaque
   numeric threshold or an arbitrary free-text action.
2. **Request** -- ``PathwayCurationRequest``, the bounded input contract.
3. **Plan** -- ``CurationPlanStep``, ``PathwayCurationPlan``.
4. **Frontier** -- ``CurationFrontierItem``, the deterministic
   representation of one unresolved pathway-curation concern.
5. **Execution audit/result** -- ``CurationIterationRecord``,
   ``PathwayCurationResult``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.agent1.types import Agent1CuratedKnowledgeView, Agent1KnowledgePackage
from app.claim_generation.types import EntityKind
from app.models.enums import SourceType
from app.pathway_curation.readiness import Agent2ReadinessAssessment

#: This package's own policy version -- bump only when the planning/
#: execution *behavior* changes materially (see
#: ``docs/26_autonomous_pathway_curation_planner.md`` §33 for the
#: versioning convention this mirrors from the sibling Agent 2 repository).
#:
#: Bumped to ``v1.1`` by Agent 1.x Increment C.1: pathway reaction-membership
#: discovery, catalyst discovery, and the readiness-emptiness rule all
#: changed materially (see ``docs/26_...md``'s C.1 section) -- a released
#: behavior change, not merely a bug fix invisible to this version's own
#: callers. Still a pre-1.0-style dotted increment, not a new ``v2``: no
#: request/result field was removed or repurposed, only added to or
#: corrected.
#:
#: Bumped to ``v1.2`` by Agent 1.x Increment C.2: catalyst-discovery
#: precedence changed materially -- direct organism-specific KGML
#: reaction->gene evidence is now tried first and, when present, entirely
#: preempts the EC-based search (see ``docs/26_...md`` §47).
PATHWAY_CURATION_POLICY_VERSION = "pathway-curation-v1.2"


#: KEGG's own stable pathway-id shape: an organism/database code (2-5 lowercase
#: letters -- "map"/"ko"/"rn" generic databases, or an organism code like
#: "sce"/"hsa"/"eco"/"ath") followed by a 5-digit pathway number. Deliberately
#: not overfit to "sce" alone (Increment C.1, F4/Step 12).
_KEGG_PATHWAY_ID_PATTERN = re.compile(r"^[a-z]{2,5}[0-9]{5}$")


def _require_non_empty_str(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")
    return value.strip()


def _clean_optional_str(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str or None, got {value!r}")
    stripped = value.strip()
    return stripped or None


def _require_str_tuple(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{field_name} must be a tuple of str, got {value!r}")
    return value


def _require_positive_int(value: int, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive int, got {value!r}")
    return value


# =================================================================================================
# 1. Policy vocabularies
# =================================================================================================


class CompletionPolicy(StrEnum):
    """What "done" means for one pathway-curation run. Never an opaque numeric threshold.

    Each successive member is strictly additive over the one before it --
    see ``docs/26_autonomous_pathway_curation_planner.md`` §4 for the
    exact requirement checklist each value implies. None of the three
    ever requires *zero* gaps; ``COMPLETE_WITH_GAPS`` remains a legitimate
    terminal status under every policy.
    """

    #: Pathway reactions identified; participants normalized; enzyme
    #: associations resolved where available; no unresolved
    #: high-priority structural frontier above the configured limit.
    STRUCTURAL_COVERAGE = "STRUCTURAL_COVERAGE"

    #: STRUCTURAL_COVERAGE, plus: supporting claims/evidence/publications
    #: attempted for curated reactions and enzymes.
    STRUCTURAL_AND_EVIDENCE = "STRUCTURAL_AND_EVIDENCE"

    #: STRUCTURAL_AND_EVIDENCE, plus: kinetic measurements, regulation,
    #: and state-specific kinetics attempted where available.
    STRUCTURAL_EVIDENCE_AND_KINETICS = "STRUCTURAL_EVIDENCE_AND_KINETICS"


class CurationMode(StrEnum):
    """How conservatively one run should behave. See §5 for ``PILOT``'s exact defaults."""

    PILOT = "PILOT"
    STANDARD = "STANDARD"


class PlannerAction(StrEnum):
    """The complete, deliberately small vocabulary of actions a plan step may name.

    Every value maps to a capability this repository's existing
    connectors/normalization/persistence/knowledge-gap machinery can
    already execute deterministically (see
    ``docs/26_autonomous_pathway_curation_planner.md`` §7) -- this
    package never encodes arbitrary executable Python or shell commands,
    and never adds an action with no real, already-implemented backing
    capability.
    """

    RESOLVE_ORGANISM = "RESOLVE_ORGANISM"
    DISCOVER_PATHWAY = "DISCOVER_PATHWAY"
    DISCOVER_REACTIONS = "DISCOVER_REACTIONS"
    RESOLVE_REACTION = "RESOLVE_REACTION"
    DISCOVER_ENZYMES = "DISCOVER_ENZYMES"
    RESOLVE_GENE = "RESOLVE_GENE"
    RESOLVE_PROTEIN = "RESOLVE_PROTEIN"
    DISCOVER_PUBLICATIONS = "DISCOVER_PUBLICATIONS"
    RETRIEVE_PUBLICATION = "RETRIEVE_PUBLICATION"
    DISCOVER_KINETICS = "DISCOVER_KINETICS"
    DISCOVER_REGULATION = "DISCOVER_REGULATION"
    DISCOVER_ENZYME_STATES = "DISCOVER_ENZYME_STATES"
    ANALYZE_GAPS = "ANALYZE_GAPS"
    ASSESS_COMPLETION = "ASSESS_COMPLETION"


class PlanStepStatus(StrEnum):
    """One plan step's own lifecycle -- distinct from ``CompletionStatus`` (the whole run's)."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


class FrontierReason(StrEnum):
    """A controlled, deterministic vocabulary for *why* one frontier item is unresolved.

    Never free-text-only (Step 14 of the original increment instructions)
    -- every reason a real code path in this package can actually produce
    is named here, and ``policy.FRONTIER_PRIORITY_ORDER`` is the single
    deterministic priority ranking over this vocabulary (Step 15).
    """

    UNRESOLVED_REACTION_IDENTITY = "UNRESOLVED_REACTION_IDENTITY"
    #: A pathway was successfully resolved (by structured id or by free-text search), but
    #: KEGG's own pathway<->reaction link operation returned zero reaction ids for it --
    #: a real, legitimate "this pathway currently has no linked reactions" outcome,
    #: distinct from ``UNRESOLVED_REACTION_IDENTITY`` (the *pathway itself* was never
    #: found) and from ``SOURCE_FAILURE`` (the link call itself failed). Added in
    #: Increment C.1 (F1/F9): Pilot 1 Run 1's central failure was that this exact
    #: condition previously produced no frontier item at all.
    PATHWAY_REACTION_MEMBERSHIP_EMPTY = "PATHWAY_REACTION_MEMBERSHIP_EMPTY"
    #: A reaction resolved structurally (it exists, it has a name/identifier) but ended up
    #: with zero exported participants -- e.g. its equation could not be parsed at all, or
    #: every one of its participant tokens failed to resolve. Added in the Increment C
    #: pre-commit revision: distinct from ``UNRESOLVED_REACTION_PARTICIPANT`` (one specific
    #: participant token failed), this is the whole-reaction "nothing came of it" case.
    REACTION_MISSING_PARTICIPANTS = "REACTION_MISSING_PARTICIPANTS"
    UNRESOLVED_REACTION_PARTICIPANT = "UNRESOLVED_REACTION_PARTICIPANT"
    UNRESOLVED_CATALYST = "UNRESOLVED_CATALYST"
    #: A conservatively-supported reaction/protein catalyst pairing (the request's own
    #: seed text, corroborated by an exact EC-number match -- see ``strategies
    #: .associate_catalyst``) was identified, but ``persist_reaction_enzyme`` itself
    #: reported ``FAILED``/``REQUIRES_REVIEW``. Added in the Increment C pre-commit
    #: revision.
    REACTION_ENZYME_PERSISTENCE_FAILED = "REACTION_ENZYME_PERSISTENCE_FAILED"
    #: A resolved reaction carries a structured catalyst-discovery clue (an EC number,
    #: KEGG's own ``ENZYME`` annotation) and autonomous candidate discovery (Increment
    #: C.1, F2) was attempted from it, but no candidate reached enough organism-specific,
    #: structured evidence to safely persist a ``ReactionEnzyme`` association -- the
    #: catalyst-discovery analogue of ``UNRESOLVED_CATALYST`` (which is reserved for an
    #: explicitly caller-*seeded* text that failed to resolve). Never produced merely
    #: because no clue existed at all -- only when a real clue led nowhere conclusive.
    REACTION_CATALYST_UNRESOLVED = "REACTION_CATALYST_UNRESOLVED"
    MISSING_ORGANISM_CONTEXT = "MISSING_ORGANISM_CONTEXT"
    MISSING_COMPARTMENT_CONTEXT = "MISSING_COMPARTMENT_CONTEXT"
    MISSING_PUBLICATION = "MISSING_PUBLICATION"
    MISSING_KINETICS = "MISSING_KINETICS"
    #: ``include_kinetics=True`` was requested, but no (protein, EC number) pair was ever
    #: resolved this run to search kinetics sources against -- kinetics enrichment was
    #: never even attempted, distinct from ``MISSING_KINETICS`` (attempted, no measurements
    #: found). Added in the Increment C pre-commit revision.
    KINETICS_REQUESTED_NOT_ATTEMPTED = "KINETICS_REQUESTED_NOT_ATTEMPTED"
    MISSING_REGULATION = "MISSING_REGULATION"
    #: ``include_regulation=True`` (or ``include_enzyme_states=True``) was requested, but
    #: this executor has no regulation/enzyme-state discovery route at all (§25 of
    #: ``docs/26_autonomous_pathway_curation_planner.md``) -- never silently implying
    #: comprehensive curation was attempted. Added in the Increment C pre-commit revision.
    REGULATION_REQUESTED_NOT_SUPPORTED = "REGULATION_REQUESTED_NOT_SUPPORTED"
    MISSING_ENZYME_STATE = "MISSING_ENZYME_STATE"
    AMBIGUOUS_IDENTITY = "AMBIGUOUS_IDENTITY"
    CONFLICTED_IDENTITY = "CONFLICTED_IDENTITY"
    SOURCE_FAILURE = "SOURCE_FAILURE"
    NO_CONNECTOR_AVAILABLE = "NO_CONNECTOR_AVAILABLE"


class CompletionStatus(StrEnum):
    """The terminal status of one pathway-curation run.

    Never assigned ``COMPLETE`` merely because no more connector calls
    were attempted -- see ``policy.assess_completion`` for the actual
    deterministic decision logic.
    """

    COMPLETE = "COMPLETE"
    COMPLETE_WITH_GAPS = "COMPLETE_WITH_GAPS"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


# =================================================================================================
# 2. Request
# =================================================================================================


@dataclass(frozen=True, slots=True)
class PathwayCurationRequest:
    """A bounded request to curate the biochemical knowledge for one biological scope.

    ``organism_text`` is always required -- even when ``organism_id`` is
    already known, the free text is preserved as the human-auditable
    statement of intent (Step 11: organism resolution happens from
    whichever of the two is available, never inferred from strain
    conventions the caller did not state).

    ``organism_ncbi_taxonomy_id`` is optional, strong-identifier context a
    caller may already know (e.g. ``4932`` for *Saccharomyces cerevisiae*)
    -- **discovered as a genuine capability gap during implementation**:
    ``app.normalization.organism.normalize_organism`` requires at least
    one strong identifier (NCBI taxonomy id / KEGG code / BioCyc id) or an
    existing matching row to ever return ``NEW``; a bare
    ``scientific_name`` alone is deliberately always ``UNRESOLVED``, never
    auto-created (see that module's own ``_resolve_from_scientific_name_only``).
    This field lets a caller supply a real, externally-verifiable
    identifier it already has -- never fabricated by this package -- so
    organism resolution is not permanently blocked for a first-ever run
    against an empty database. Leaving it ``None`` is fully legitimate; it
    simply means resolution can only succeed by matching an
    already-persisted organism.

    ``include_entity_kinds`` is an **allow-list**: empty means "no
    restriction beyond what ``completion_policy``/connectors already
    imply," a non-empty tuple restricts this run strictly to those kinds
    -- documented explicitly since "empty" is ambiguous between "nothing"
    and "everything" and this type deliberately picks the latter.

    ``default_compartment_text`` (Increment C pre-commit revision) is an
    optional, conservative **scope assumption the caller explicitly
    asserts**, never an inference this package makes on its own: "model
    otherwise-unlocalized pathway participants in this compartment for
    this curation request." It is resolved only against an existing
    *reference* compartment (``Compartment.organism_id IS NULL``) by
    exact name (``strategies.resolve_reference_compartment_by_name``) --
    never fuzzy-matched, never used to create a new compartment row, and
    never applied when a participant already carries explicit source
    compartment evidence (there is none in this increment -- no connector
    exposes per-participant compartment localization -- so today this is
    the *only* compartment evidence path this executor has, not one of
    several competing ones). Leaving it ``None`` is fully legitimate: a
    participant's ``compartment_id`` then simply stays ``None``, which
    the existing schema permits (``ReactionParticipant.compartment_id`` is
    nullable) -- this package never defaults an unresolved compartment to
    ``"cytosol"`` or any other value.

    ``source_pathway_id`` (Increment C.1, F4) is an optional, explicit,
    structured pathway identifier (e.g. ``"sce00061"``, ``"map00061"``) --
    the only pathway source this executor resolves against is KEGG today,
    so this is documented as a KEGG pathway id rather than paired with a
    separate ``source`` field (a source-neutral field would be unresolvable
    overhead with only one real source behind it; see
    ``docs/26_...md``'s C.1 section for the "smallest clear design"
    reasoning). When supplied, it takes deterministic precedence over
    ``biological_process`` for *structural* pathway resolution -- no
    free-text KEGG pathway search is ever attempted
    (``strategies.discover_pathway`` is simply not called). ``biological_process``
    remains required regardless (human-readable scope, and still used as-is
    for literature queries, §17) and is used for structural discovery only
    when ``source_pathway_id`` is left ``None``. Validated at construction
    time against KEGG's own stable identifier shape (organism/database code
    letters followed by a 5-digit pathway number, e.g. ``sce00061``/
    ``map00061``/``ko00061``/``hsa00061`` -- never overfit to ``sce`` alone)
    -- a malformed value raises here, at request-construction time, rather
    than surfacing as a confusing connector-level failure later.

    ``strain_text`` (Increment C.1, F3) is optional, free-text strain
    context (e.g. ``"S288C"``), threaded through unchanged to
    ``strategies.resolve_organism``'s own pre-existing ``strain`` parameter
    (which already existed and already did nothing before C.1, because the
    executor previously always called it with a hard-coded ``None``
    regardless of what a caller might have wanted -- see that module's
    ``OrganismIdentity``/``normalize_organism``, both entirely unmodified by
    this field). Never concatenated into ``organism_text`` or any other
    field -- organism resolution's own existing strain-aware identity
    matching (``(scientific_name, strain)``) is reused exactly as already
    implemented for every other caller of ``normalize_organism``.
    """

    request_id: str
    organism_text: str
    biological_process: str

    organism_id: UUID | None = None
    organism_ncbi_taxonomy_id: int | None = None
    strain_text: str | None = None
    scope_description: str | None = None
    default_compartment_text: str | None = None
    source_pathway_id: str | None = None
    seed_entity_texts: tuple[str, ...] = ()
    include_entity_kinds: tuple[EntityKind, ...] = ()

    include_kinetics: bool = False
    include_regulation: bool = False
    include_enzyme_states: bool = False
    include_publications: bool = True

    exclusions: tuple[str, ...] = ()

    max_iterations: int = 5
    max_connector_calls: int = 50
    max_publications: int = 10

    completion_policy: CompletionPolicy = CompletionPolicy.STRUCTURAL_COVERAGE
    mode: CurationMode = CurationMode.PILOT

    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "request_id", _require_non_empty_str(self.request_id, field_name="request_id")
        )
        object.__setattr__(
            self,
            "organism_text",
            _require_non_empty_str(self.organism_text, field_name="organism_text"),
        )
        object.__setattr__(
            self,
            "biological_process",
            _require_non_empty_str(self.biological_process, field_name="biological_process"),
        )
        if self.organism_ncbi_taxonomy_id is not None and (
            not isinstance(self.organism_ncbi_taxonomy_id, int)
            or isinstance(self.organism_ncbi_taxonomy_id, bool)
            or self.organism_ncbi_taxonomy_id <= 0
        ):
            raise ValueError(
                "organism_ncbi_taxonomy_id must be a positive int or None, "
                f"got {self.organism_ncbi_taxonomy_id!r}"
            )
        object.__setattr__(
            self, "strain_text", _clean_optional_str(self.strain_text, field_name="strain_text")
        )
        object.__setattr__(
            self,
            "scope_description",
            _clean_optional_str(self.scope_description, field_name="scope_description"),
        )
        object.__setattr__(
            self,
            "default_compartment_text",
            _clean_optional_str(
                self.default_compartment_text, field_name="default_compartment_text"
            ),
        )
        cleaned_pathway_id = _clean_optional_str(
            self.source_pathway_id, field_name="source_pathway_id"
        )
        if cleaned_pathway_id is not None and not _KEGG_PATHWAY_ID_PATTERN.match(
            cleaned_pathway_id
        ):
            raise ValueError(
                "source_pathway_id must look like a KEGG pathway id (2-5 lowercase "
                f"letters followed by 5 digits, e.g. 'sce00061'/'map00061'), got "
                f"{cleaned_pathway_id!r}"
            )
        object.__setattr__(self, "source_pathway_id", cleaned_pathway_id)
        object.__setattr__(
            self,
            "seed_entity_texts",
            _require_str_tuple(self.seed_entity_texts, field_name="seed_entity_texts"),
        )
        if not isinstance(self.include_entity_kinds, tuple) or any(
            not isinstance(item, EntityKind) for item in self.include_entity_kinds
        ):
            raise TypeError(
                "include_entity_kinds must be a tuple of EntityKind, "
                f"got {self.include_entity_kinds!r}"
            )
        for name in (
            "include_kinetics",
            "include_regulation",
            "include_enzyme_states",
            "include_publications",
        ):
            value = getattr(self, name)
            if not isinstance(value, bool):
                raise TypeError(f"{name} must be a bool, got {value!r}")
        object.__setattr__(
            self, "exclusions", _require_str_tuple(self.exclusions, field_name="exclusions")
        )
        object.__setattr__(
            self,
            "max_iterations",
            _require_positive_int(self.max_iterations, field_name="max_iterations"),
        )
        object.__setattr__(
            self,
            "max_connector_calls",
            _require_positive_int(self.max_connector_calls, field_name="max_connector_calls"),
        )
        object.__setattr__(
            self,
            "max_publications",
            _require_positive_int(self.max_publications, field_name="max_publications"),
        )
        if not isinstance(self.completion_policy, CompletionPolicy):
            raise TypeError(
                f"completion_policy must be a CompletionPolicy, got {self.completion_policy!r}"
            )
        if not isinstance(self.mode, CurationMode):
            raise TypeError(f"mode must be a CurationMode, got {self.mode!r}")
        object.__setattr__(self, "notes", _clean_optional_str(self.notes, field_name="notes"))

        overlap = set(self.seed_entity_texts) & set(self.exclusions)
        if overlap:
            raise ValueError(
                f"seed_entity_texts and exclusions must be disjoint, overlap: {sorted(overlap)}"
            )


# =================================================================================================
# 3. Plan
# =================================================================================================


@dataclass(frozen=True, slots=True)
class CurationPlanStep:
    """One deterministic unit of planned work.

    ``query_text``/``structured_input`` are mutually exclusive-ish in
    practice but not enforced as such here (a step may reasonably carry
    both, e.g. a human-readable ``query_text`` plus the structured
    identifier that produced it) -- both are plain data, never
    executable code (Step 5: "do not encode arbitrary executable Python
    or shell commands").
    """

    step_id: str
    action_type: PlannerAction
    target_entity_kind: EntityKind | None
    dependencies: tuple[str, ...] = ()
    priority: int = 1
    rationale: str = ""
    expected_output_kind: str = ""
    query_text: str | None = None
    structured_input: tuple[tuple[str, str], ...] = ()
    source: SourceType | None = None
    status: PlanStepStatus = PlanStepStatus.PENDING

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "step_id", _require_non_empty_str(self.step_id, field_name="step_id")
        )
        if not isinstance(self.action_type, PlannerAction):
            raise TypeError(f"action_type must be a PlannerAction, got {self.action_type!r}")
        if self.target_entity_kind is not None and not isinstance(
            self.target_entity_kind, EntityKind
        ):
            raise TypeError(
                f"target_entity_kind must be an EntityKind or None, got {self.target_entity_kind!r}"
            )
        object.__setattr__(
            self, "dependencies", _require_str_tuple(self.dependencies, field_name="dependencies")
        )
        object.__setattr__(
            self, "priority", _require_positive_int(self.priority, field_name="priority")
        )
        object.__setattr__(
            self, "query_text", _clean_optional_str(self.query_text, field_name="query_text")
        )
        if self.source is not None and not isinstance(self.source, SourceType):
            raise TypeError(f"source must be a SourceType or None, got {self.source!r}")
        if not isinstance(self.status, PlanStepStatus):
            raise TypeError(f"status must be a PlanStepStatus, got {self.status!r}")


@dataclass(frozen=True, slots=True)
class PathwayCurationPlan:
    """The complete, deterministic initial plan for one request.

    Read-only by construction: nothing that builds this type ever
    touches a database session or a connector (Step 42). Later plan
    steps that depend on data only discoverable at run time (e.g. "which
    reactions does this pathway actually contain") are represented as
    the initial ``DISCOVER_*``/``RESOLVE_*`` steps that will *produce*
    that data -- the concrete downstream steps they unlock are recorded
    at execution time in each ``CurationIterationRecord``, never guessed
    at plan time.
    """

    plan_id: str
    request_id: str
    policy_version: str
    completion_policy: CompletionPolicy
    mode: CurationMode
    steps: tuple[CurationPlanStep, ...] = ()
    initial_frontier: tuple[CurationFrontierItem, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "plan_id", _require_non_empty_str(self.plan_id, field_name="plan_id")
        )
        object.__setattr__(
            self, "request_id", _require_non_empty_str(self.request_id, field_name="request_id")
        )
        object.__setattr__(
            self,
            "policy_version",
            _require_non_empty_str(self.policy_version, field_name="policy_version"),
        )
        if not isinstance(self.completion_policy, CompletionPolicy):
            raise TypeError(
                f"completion_policy must be a CompletionPolicy, got {self.completion_policy!r}"
            )
        if not isinstance(self.mode, CurationMode):
            raise TypeError(f"mode must be a CurationMode, got {self.mode!r}")
        if not isinstance(self.steps, tuple) or any(
            not isinstance(item, CurationPlanStep) for item in self.steps
        ):
            raise TypeError(f"steps must be a tuple of CurationPlanStep, got {self.steps!r}")
        step_ids = tuple(step.step_id for step in self.steps)
        if len(set(step_ids)) != len(step_ids):
            raise ValueError(f"steps[].step_id must be unique, got {step_ids!r}")
        known_ids = set(step_ids)
        for step in self.steps:
            unknown = set(step.dependencies) - known_ids
            if unknown:
                raise ValueError(
                    f"step {step.step_id!r} depends on unknown step id(s): {sorted(unknown)}"
                )
        object.__setattr__(self, "notes", _require_str_tuple(self.notes, field_name="notes"))


# =================================================================================================
# 4. Frontier
# =================================================================================================


@dataclass(frozen=True, slots=True)
class CurationFrontierItem:
    """One deterministic, unresolved pathway-curation concern.

    Requires at least one of ``entity_text``/``entity_id`` -- otherwise
    there is nothing this item could be re-attempted against. ``resolved``
    defaults to ``False``; this package never mutates a
    ``CurationFrontierItem`` in place to mark it resolved -- a resolved
    item is simply omitted from the next iteration's frontier, and the
    audit trail (``CurationIterationRecord.frontier_before``/
    ``.frontier_after``) is what shows the transition.
    """

    frontier_id: str
    entity_kind: EntityKind
    reason: FrontierReason
    priority: int
    entity_text: str | None = None
    entity_id: UUID | None = None
    parent_entity_id: UUID | None = None
    attempted_sources: tuple[SourceType, ...] = ()
    resolved: bool = False
    blocked_reason: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frontier_id", _require_non_empty_str(self.frontier_id, field_name="frontier_id")
        )
        if not isinstance(self.entity_kind, EntityKind):
            raise TypeError(f"entity_kind must be an EntityKind, got {self.entity_kind!r}")
        if not isinstance(self.reason, FrontierReason):
            raise TypeError(f"reason must be a FrontierReason, got {self.reason!r}")
        object.__setattr__(
            self, "priority", _require_positive_int(self.priority, field_name="priority")
        )
        object.__setattr__(
            self, "entity_text", _clean_optional_str(self.entity_text, field_name="entity_text")
        )
        if self.entity_text is None and self.entity_id is None:
            raise ValueError("CurationFrontierItem requires at least one of entity_text/entity_id")
        if not isinstance(self.attempted_sources, tuple) or any(
            not isinstance(item, SourceType) for item in self.attempted_sources
        ):
            raise TypeError(
                f"attempted_sources must be a tuple of SourceType, got {self.attempted_sources!r}"
            )
        if not isinstance(self.resolved, bool):
            raise TypeError(f"resolved must be a bool, got {self.resolved!r}")
        object.__setattr__(
            self,
            "blocked_reason",
            _clean_optional_str(self.blocked_reason, field_name="blocked_reason"),
        )
        object.__setattr__(self, "notes", _clean_optional_str(self.notes, field_name="notes"))


# =================================================================================================
# 5. Execution audit/result
# =================================================================================================


@dataclass(frozen=True, slots=True)
class CurationIterationRecord:
    """One fully-explainable iteration of the bounded execution loop (Step 29).

    Every iteration the executor runs produces exactly one of these,
    regardless of whether it made progress -- ``no_progress=True`` marks
    the iteration that triggered the no-progress stopping rule (Step 33),
    it is never silently omitted from the audit trail.
    """

    iteration_number: int
    frontier_before: tuple[CurationFrontierItem, ...]
    plan_steps_executed: tuple[str, ...]
    new_entity_ids: tuple[UUID, ...]
    new_reaction_ids: tuple[UUID, ...]
    new_publication_ids: tuple[UUID, ...]
    frontier_after: tuple[CurationFrontierItem, ...]
    connector_calls_made: int
    no_progress: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.iteration_number, int)
            or isinstance(self.iteration_number, bool)
            or self.iteration_number < 1
        ):
            raise ValueError(
                f"iteration_number must be a positive int, got {self.iteration_number!r}"
            )
        if (
            not isinstance(self.connector_calls_made, int)
            or isinstance(self.connector_calls_made, bool)
            or self.connector_calls_made < 0
        ):
            raise ValueError(
                "connector_calls_made must be a non-negative int, "
                f"got {self.connector_calls_made!r}"
            )
        object.__setattr__(
            self,
            "plan_steps_executed",
            _require_str_tuple(self.plan_steps_executed, field_name="plan_steps_executed"),
        )


@dataclass(frozen=True, slots=True)
class PathwayCurationResult:
    """The complete, auditable outcome of one ``execute_pathway_curation`` call.

    ``agent1_knowledge_package``/``curated_knowledge_view`` are always
    populated (never ``None``) whenever this type is successfully
    constructed at all -- ``execute_pathway_curation`` always calls
    through to ``app.agent1.service.get_agent1_knowledge_package``/
    ``app.agent1.export.get_agent1_curated_knowledge_view`` at the end of
    a run, using whichever ``organism_id`` was actually resolved (``None``
    if organism resolution itself never succeeded), so Agent 1's output
    remains exactly ``Agent1CuratedKnowledgeView`` regardless of how far
    the run progressed (Step 41's own requirement).

    ``agent2_readiness`` (Increment C pre-commit revision) is likewise
    always populated: ``app.pathway_curation.readiness
    .validate_agent2_readiness`` is always run, read-only, over
    ``curated_knowledge_view``/``agent1_knowledge_package`` at the end of
    every call, regardless of ``completion_status``. It answers a
    deliberately distinct question from ``completion_status`` -- see
    that module's own docstring -- and a caller must never need to
    reconstruct it by hand from the raw export tuples.
    """

    request: PathwayCurationRequest
    final_plan: PathwayCurationPlan
    iterations: tuple[CurationIterationRecord, ...]
    iterations_completed: int
    connector_calls_made: int
    queries_executed: tuple[str, ...]
    organism_id: UUID | None
    discovered_entity_ids: tuple[UUID, ...]
    discovered_reaction_ids: tuple[UUID, ...]
    discovered_publication_ids: tuple[UUID, ...]
    unresolved_frontier: tuple[CurationFrontierItem, ...]
    knowledge_gap_ids: tuple[UUID, ...]
    completion_status: CompletionStatus
    completion_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    agent1_knowledge_package: Agent1KnowledgePackage
    curated_knowledge_view: Agent1CuratedKnowledgeView
    agent2_readiness: Agent2ReadinessAssessment

    def __post_init__(self) -> None:
        if not isinstance(self.request, PathwayCurationRequest):
            raise TypeError(f"request must be a PathwayCurationRequest, got {self.request!r}")
        if not isinstance(self.final_plan, PathwayCurationPlan):
            raise TypeError(f"final_plan must be a PathwayCurationPlan, got {self.final_plan!r}")
        if not isinstance(self.completion_status, CompletionStatus):
            raise TypeError(
                f"completion_status must be a CompletionStatus, got {self.completion_status!r}"
            )
        object.__setattr__(
            self,
            "completion_reasons",
            _require_str_tuple(self.completion_reasons, field_name="completion_reasons"),
        )
        if not self.completion_reasons:
            raise ValueError(
                "PathwayCurationResult.completion_reasons must not be empty -- every "
                "completion_status must be explained"
            )
        object.__setattr__(
            self, "warnings", _require_str_tuple(self.warnings, field_name="warnings")
        )
        object.__setattr__(
            self,
            "queries_executed",
            _require_str_tuple(self.queries_executed, field_name="queries_executed"),
        )
        if not isinstance(self.agent1_knowledge_package, Agent1KnowledgePackage):
            raise TypeError(
                "agent1_knowledge_package must be an Agent1KnowledgePackage, "
                f"got {self.agent1_knowledge_package!r}"
            )
        if not isinstance(self.curated_knowledge_view, Agent1CuratedKnowledgeView):
            raise TypeError(
                "curated_knowledge_view must be an Agent1CuratedKnowledgeView, "
                f"got {self.curated_knowledge_view!r}"
            )
        if not isinstance(self.agent2_readiness, Agent2ReadinessAssessment):
            raise TypeError(
                f"agent2_readiness must be an Agent2ReadinessAssessment, "
                f"got {self.agent2_readiness!r}"
            )


__all__ = [
    "PATHWAY_CURATION_POLICY_VERSION",
    "CompletionPolicy",
    "CompletionStatus",
    "CurationFrontierItem",
    "CurationIterationRecord",
    "CurationMode",
    "CurationPlanStep",
    "FrontierReason",
    "PathwayCurationPlan",
    "PathwayCurationRequest",
    "PathwayCurationResult",
    "PlanStepStatus",
    "PlannerAction",
]
