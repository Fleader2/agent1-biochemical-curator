"""Enumerated types shared across Agent 1's scientific data model.

Values are transcribed verbatim from ``docs/02_database_schema.md``
("Enumerated Types") and must not be invented, renamed, reordered, or omitted
(``01-scientific-integrity.mdc``). Each class is a ``StrEnum`` so it can be
passed directly to SQLAlchemy's ``sa.Enum`` to back a native PostgreSQL
``ENUM`` column; no table or column is defined in this module.

``kinetic_measurement.parameter_type`` is deliberately not represented here.
The specification requires it to remain an open ``VARCHAR`` ("Do not restrict
the database so tightly that future parameter types cannot be added"), so it
must never be backed by one of these closed enums.
"""

from __future__ import annotations

from enum import StrEnum


class CurationState(StrEnum):
    """Human-review lifecycle state for a curated record."""

    PROPOSED = "PROPOSED"
    MACHINE_REVIEWED = "MACHINE_REVIEWED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    HUMAN_ACCEPTED = "HUMAN_ACCEPTED"
    REJECTED = "REJECTED"


class ConfidenceClass(StrEnum):
    """Coarse confidence bucket derived from a 0-100 confidence score."""

    VERY_HIGH = "VERY_HIGH"
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class EvidenceType(StrEnum):
    """Category of scientific evidence supporting a claim."""

    DIRECT_BIOCHEMICAL = "DIRECT_BIOCHEMICAL"
    DIRECT_IN_VIVO = "DIRECT_IN_VIVO"
    GENETIC = "GENETIC"
    LOCALIZATION = "LOCALIZATION"
    PROTEOMICS = "PROTEOMICS"
    METABOLOMICS = "METABOLOMICS"
    FLUXOMICS = "FLUXOMICS"
    TRANSCRIPTOMICS = "TRANSCRIPTOMICS"
    STRUCTURAL = "STRUCTURAL"
    CURATED_DATABASE = "CURATED_DATABASE"
    COMPUTATIONAL = "COMPUTATIONAL"
    HOMOLOGY = "HOMOLOGY"
    REVIEW = "REVIEW"
    AUTHOR_HYPOTHESIS = "AUTHOR_HYPOTHESIS"
    OTHER = "OTHER"


class ClaimStatus(StrEnum):
    """Resolution status of a scientific claim."""

    SUPPORTED = "SUPPORTED"
    CONFLICTED = "CONFLICTED"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class ReactionParticipantRole(StrEnum):
    """Role a compound plays in a reaction."""

    REACTANT = "REACTANT"
    PRODUCT = "PRODUCT"
    MODIFIER = "MODIFIER"


class RegulatoryEffect(StrEnum):
    """Effect a regulator has on its target."""

    ACTIVATION = "ACTIVATION"
    INHIBITION = "INHIBITION"
    INDUCTION = "INDUCTION"
    REPRESSION = "REPRESSION"
    PHOSPHORYLATION = "PHOSPHORYLATION"
    DEPHOSPHORYLATION = "DEPHOSPHORYLATION"
    STABILIZATION = "STABILIZATION"
    DESTABILIZATION = "DESTABILIZATION"
    DEGRADATION = "DEGRADATION"
    TRANSLOCATION = "TRANSLOCATION"
    UNKNOWN = "UNKNOWN"
    OTHER = "OTHER"


class EnzymeStateType(StrEnum):
    """The category of a distinct biochemical/catalytic ``EnzymeState``.

    Added in Agent 1.x Increment B (``docs/25_enzyme_regulatory_states_contract.md``).
    A state's *category* here is independent of which child rows
    (``EnzymeModification``/``AllostericInteraction``) it actually carries
    -- a state with both modifications and a bound allosteric ligand is
    still only one row, and this value records the curator's judgment of
    its predominant nature, never a computed union of "has modifications"
    and "has ligands." ``BASE`` is the default, unmodified,
    ligand-free state of a ``Protein``/``EnzymeComplex``. ``OTHER`` is the
    catch-all for a state that does not cleanly fit ``MODIFIED``/
    ``ALLOSTERICALLY_BOUND`` -- never a reason to omit a curated state.
    """

    BASE = "BASE"
    MODIFIED = "MODIFIED"
    ALLOSTERICALLY_BOUND = "ALLOSTERICALLY_BOUND"
    OTHER = "OTHER"


class ModificationType(StrEnum):
    """A covalent/post-translational modification type on an ``EnzymeState``.

    Added in Agent 1.x Increment B. ``CYSTEINYLATION`` here means
    specifically *S-cysteinylation*: the covalent, disulfide-linked
    addition of a free cysteine to a protein cysteine residue -- a single,
    specific, well-defined modification, deliberately **not** a catch-all
    for every chemically distinct cysteine modification (S-nitrosylation,
    S-glutathionylation, sulfenylation, or an intramolecular/intermolecular
    protein-protein disulfide bond are each a different chemical event and
    would each need their own future ``ModificationType`` member, never
    folded into ``CYSTEINYLATION`` without evidence that the source
    actually means this specific modification). ``OTHER`` is the catch-all
    for any modification this vocabulary does not yet name -- never a
    reason to omit a curated modification.
    """

    PHOSPHORYLATION = "PHOSPHORYLATION"
    ACETYLATION = "ACETYLATION"
    CYSTEINYLATION = "CYSTEINYLATION"
    UBIQUITINATION = "UBIQUITINATION"
    METHYLATION = "METHYLATION"
    OTHER = "OTHER"


class AllostericEffect(StrEnum):
    """The qualitative regulatory effect of an allosteric ligand on an ``EnzymeState``.

    Added in Agent 1.x Increment B. Never inferred from a kinetic value
    (e.g. a lower ``Km`` does not by itself imply ``ACTIVATOR``) -- set
    only when a source explicitly states the effect. ``MODULATOR`` is for
    a source-stated effect that is neither purely activating nor purely
    inhibiting (e.g. mixed or allosteric-but-unspecified-direction).
    ``UNKNOWN`` is for a curated binding relationship whose effect the
    source does not state -- never silently omitted or guessed at.
    """

    ACTIVATOR = "ACTIVATOR"
    INHIBITOR = "INHIBITOR"
    MODULATOR = "MODULATOR"
    UNKNOWN = "UNKNOWN"


class EnzymeStateTransitionType(StrEnum):
    """The category of a transition between two ``EnzymeState`` rows.

    Added in Agent 1.x Increment B. Kept deliberately minimal -- ``OTHER``
    is the catch-all for a transition that does not cleanly fit the four
    named categories.
    """

    MODIFICATION = "MODIFICATION"
    DEMODIFICATION = "DEMODIFICATION"
    LIGAND_BINDING = "LIGAND_BINDING"
    LIGAND_RELEASE = "LIGAND_RELEASE"
    OTHER = "OTHER"


class QuantitativeEvidenceClass(StrEnum):
    """The provenance category of one ``QuantitativeObservation`` value.

    Added in Agent 1.x Increment "Experimental Context and Quantitative
    Observation Framework". A deliberately small, closed vocabulary --
    unlike ``QuantitativeObservationType``/``PerturbationCategory``
    (``app.normalization.quantitative_observation``), which are open-ended
    and never backed by a closed enum (mirrors
    ``kinetic_measurement.parameter_type``'s own "never a closed enum"
    policy), this axis is deliberately closed: a value's evidence class is
    always one of exactly these four kinds, decided by the curator/
    connector constructing the record, never left ambiguous. A ``DERIVED``
    or ``MODEL_PREDICTED`` value must never be represented as
    ``EXPERIMENT_SPECIFIC``/``REFERENCE_BASELINE`` -- those two describe a
    genuinely-measured value only.
    """

    #: A value measured under one specific, non-baseline experimental
    #: condition (a perturbation, a specific replicate/time point).
    EXPERIMENT_SPECIFIC = "EXPERIMENT_SPECIFIC"
    #: A value measured (or curated) as the standard/untreated/baseline
    #: condition for its organism -- e.g. SGD's reference protein
    #: abundance.
    REFERENCE_BASELINE = "REFERENCE_BASELINE"
    #: A value produced by a predictive model (e.g. an AI/ML predictor),
    #: never a direct experimental measurement.
    MODEL_PREDICTED = "MODEL_PREDICTED"
    #: A value computed from one or more other ``QuantitativeObservation``
    #: rows plus stated assumptions (see
    #: ``QuantitativeObservationDependency``) -- never itself a direct
    #: measurement or a model prediction.
    DERIVED = "DERIVED"


class ExperimentalContextClassification(StrEnum):
    """Whether one ``ExperimentalContext`` describes a reference/baseline
    condition or an experiment-specific one.

    Added in Agent 1.x Increment "Experimental Context and Quantitative
    Observation Framework". Distinct from ``QuantitativeEvidenceClass``:
    this classifies the *context* itself (a growth condition can be "the"
    standard reference condition for an organism regardless of which
    specific observation later reuses it), not any one observation's own
    evidence class.
    """

    REFERENCE = "REFERENCE"
    EXPERIMENT_SPECIFIC = "EXPERIMENT_SPECIFIC"


class TimeReferenceBasis(StrEnum):
    """What a ``QuantitativeObservation``'s own time value is measured relative to.

    Added in Agent 1.x Increment "Experimental Context and Quantitative
    Observation Framework". ``NULL`` on the observation itself (this enum
    is never required) means "no time / a reference value" (task's own
    first case) -- there is deliberately no ``NONE``/``REFERENCE`` member
    here, since the *absence* of a value already represents that case
    without needing its own enum value to mean the same thing twice.
    """

    EXPERIMENT_START = "EXPERIMENT_START"
    PERTURBATION_ONSET = "PERTURBATION_ONSET"


class ContextCompatibility(StrEnum):
    """A minimal, deterministic vocabulary for comparing two ``ExperimentalContext``
    records (task's own "Context compatibility," §6).

    Added in Agent 1.x Increment "Experimental Context and Quantitative
    Observation Framework". Deliberately not a numeric/weighted score --
    see ``app.normalization.quantitative_observation
    .classify_context_compatibility`` for the one, deterministic,
    unweighted classifier this increment implements (field-subset
    equality only; never fuzzy, never learned, never tuned). This is a
    comparison *result*, computed on demand between two contexts -- never
    persisted as a column on either ``ExperimentalContext`` or
    ``QuantitativeObservation``, since compatibility is a property of a
    *pair*, not of one record alone.
    """

    EXACT_CONTEXT = "EXACT_CONTEXT"
    COMPATIBLE_REFERENCE = "COMPATIBLE_REFERENCE"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    CONTEXT_UNKNOWN = "CONTEXT_UNKNOWN"


class NetworkType(StrEnum):
    """KEGG's own top-level classification of a ``Network`` record's source
    category.

    Added in the KEGG NETWORK support increment.
    """

    REFERENCE = "REFERENCE"
    VARIANT = "VARIANT"
    VIRUS = "VIRUS"
    DRUG = "DRUG"
    OTHER = "OTHER"


class NetworkNodeType(StrEnum):
    """The kind of thing one ``NetworkNode`` represents.

    Added in the KEGG NETWORK support increment. Independent of whether the
    node was successfully mapped to a normalized Agent 1 entity --
    ``NetworkNodeMappingStatus`` answers that separately. ``UNKNOWN`` is for
    a token this increment's parser cannot yet classify -- never a reason to
    drop the node.
    """

    GENE = "GENE"
    PROTEIN = "PROTEIN"
    COMPLEX = "COMPLEX"
    COMPOUND = "COMPOUND"
    REACTION = "REACTION"
    VARIANT = "VARIANT"
    DRUG = "DRUG"
    VIRAL_FACTOR = "VIRAL_FACTOR"
    ENVIRONMENTAL_FACTOR = "ENVIRONMENTAL_FACTOR"
    UNKNOWN = "UNKNOWN"


class NetworkNodeMappingStatus(StrEnum):
    """Whether a ``NetworkNode`` was successfully linked to an existing,
    normalized Agent 1 entity.

    Added in the KEGG NETWORK support increment. Deliberately distinct from
    ``NormalizationStatus`` (``app.normalization.types``): a network node is
    never itself a candidate for *creating* a new biological entity -- there
    is no ``NEW`` member here, only for *linking* to one that already
    exists.
    """

    MAPPED = "MAPPED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTED = "CONFLICTED"


class NetworkEdgeInteractionType(StrEnum):
    """The normalized interpretation of a KEGG topology operator between two
    ``NetworkNode`` rows.

    Added in the KEGG NETWORK support increment. This is Agent 1's own
    interpretation; ``NetworkEdge.raw_operator`` always separately preserves
    KEGG's own literal operator text. ``UNKNOWN`` is for any operator the
    topology parser does not yet recognize -- the edge is still persisted,
    never dropped.
    """

    ACTIVATION = "ACTIVATION"
    INHIBITION = "INHIBITION"
    EXPRESSION = "EXPRESSION"
    REPRESSION = "REPRESSION"
    BINDING = "BINDING"
    COMPLEX_FORMATION = "COMPLEX_FORMATION"
    REACTION = "REACTION"
    TRANSPORT = "TRANSPORT"
    UNKNOWN = "UNKNOWN"


class NetworkPerturbationType(StrEnum):
    """The category of perturbation one ``NetworkPerturbation`` row describes.

    Added in the KEGG NETWORK support increment. KEGG's own ``VARIANT``/
    ``PERTURBANT`` field content, never inferred from a network's own
    ``NetworkType``.
    """

    GENE_VARIANT = "GENE_VARIANT"
    DRUG = "DRUG"
    VIRAL_FACTOR = "VIRAL_FACTOR"
    ENVIRONMENTAL = "ENVIRONMENTAL"
    GENETIC = "GENETIC"
    OTHER = "OTHER"


class ProjectType(StrEnum):
    """The kind of biological system a ``Project`` studies.

    Added in the Project/Subproject/Model/ModelSpecification foundation
    increment (``docs/30_project_subproject_model_foundation.md``).
    """

    MICROBIAL_METABOLISM = "MICROBIAL_METABOLISM"
    REGULATORY_NETWORK = "REGULATORY_NETWORK"


class ProjectLifecycleStatus(StrEnum):
    """A coarse, generic lifecycle state shared by ``Project.status`` and
    ``Subproject.status``.

    Added in the same increment. Deliberately not ``CurationState``: that
    enum describes a human-review verdict on one specific scientific
    record, not whether a multi-month project or modeling effort is still
    active -- the two questions are unrelated and must not be conflated.
    """

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ON_HOLD = "ON_HOLD"
    ARCHIVED = "ARCHIVED"


class SubprojectTaskType(StrEnum):
    """What kind of modeling effort one ``Subproject`` represents.

    Added in the same increment. ``EXTEND_MODEL`` is reserved now even
    though no increment yet implements automatic model extension.
    """

    BUILD_MODEL = "BUILD_MODEL"
    IMPORT_MODEL = "IMPORT_MODEL"
    TEST_MODEL = "TEST_MODEL"
    CALIBRATE_MODEL = "CALIBRATE_MODEL"
    EXTEND_MODEL = "EXTEND_MODEL"


class ModelSourceType(StrEnum):
    """How one ``Model`` row's own executable content came to exist.

    Added in the same increment.
    """

    GENERATED = "GENERATED"
    IMPORTED = "IMPORTED"
    DERIVED = "DERIVED"


class ModelStatus(StrEnum):
    """A ``Model`` row's own lifecycle state.

    Added in the same increment. Distinct from ``ProjectLifecycleStatus``:
    ``SUPERSEDED`` (replaced by a newer version via ``parent_model_id``)
    has no equivalent meaning for a ``Project``/``Subproject``.
    """

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class ModelSpecificationSpeciesRole(StrEnum):
    """Which part of a ``ModelSpecification`` one
    ``ModelSpecificationSpecies`` row describes.

    Added in the same increment. Covers model input/start species, model
    output/end species, boundary species, and seed metabolites in one
    table, the same single-table-plus-role-column convention already used
    by ``ReactionParticipant.role``.
    """

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    BOUNDARY = "BOUNDARY"
    SEED = "SEED"


class ModelSpecificationReactionRole(StrEnum):
    """Covers seed reactions, required reactions, and excluded reactions in
    one ``ModelSpecificationReaction`` table.

    Added in the same increment.
    """

    SEED = "SEED"
    REQUIRED = "REQUIRED"
    EXCLUDED = "EXCLUDED"


class ModelSpecificationGeneProteinRole(StrEnum):
    """Covers seed genes, seed proteins/enzymes, and genes/proteins known to
    be important in one ``ModelSpecificationGeneProtein`` table.

    Added in the same increment.
    """

    SEED = "SEED"
    IMPORTANT = "IMPORTANT"


class ModelSpecificationPathwayRole(StrEnum):
    """Pathway scope: inclusion and exclusion.

    Added in the same increment.
    """

    INCLUDE = "INCLUDE"
    EXCLUDE = "EXCLUDE"


class ModelingDecisionStatus(StrEnum):
    """Resolution status of one ``ModelingDecision``.

    Added in the same increment.
    """

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DEFERRED = "DEFERRED"


class ModelingDecisionResolutionSource(StrEnum):
    """Who (or what) actually resolved a ``ModelingDecision``.

    Added in the same increment.
    """

    USER = "USER"
    AGENT = "AGENT"
    RULE = "RULE"


class SourceType(StrEnum):
    """External scientific source that a record or piece of evidence came from.

    ``SABIORK``/``OED`` added in Agent 1.x Increment A (kinetic-data
    curation): ``SABIORK`` is SABIO-RK (https://sabiork.h-its.org), a
    kinetic-law/parameter database; ``OED`` is Open Enzyme Database
    (https://openenzymedb.platform.moleculemaker.org), which itself
    aggregates data originating from BRENDA/SABIO-RK (see
    ``docs/24_kinetic_data_curation_and_handoff.md`` §7 for the
    source-lineage policy this enables). ``GOTENZYMES`` added in Agent 1.x
    Increment C.11: GotEnzymes2 (https://metabolicatlas.org/gotenzymes),
    an AI-predicted (never experimentally measured) kinetic-parameter
    database -- see migration ``0016_gotenzymes_source``'s own docstring
    for why this one value is itself the "never confused with a curated
    experimental measurement" provenance marker Increment C.11's own
    instructions ask for, with no separate column needed. Adding a value
    to this native PostgreSQL ``ENUM`` requires a migration
    (``0013_kinetic_measurement_sources`` for ``SABIORK``/``OED``,
    ``0016_gotenzymes_source`` for ``GOTENZYMES``; ``ALTER TYPE ... ADD
    VALUE``, which Postgres cannot reverse -- see each migration's own
    docstring for its downgrade policy). No existing value was renamed or
    removed.
    """

    PUBMED = "PUBMED"
    PMC = "PMC"
    KEGG = "KEGG"
    BRENDA = "BRENDA"
    BIOCYC = "BIOCYC"
    METACYC = "METACYC"
    SGD = "SGD"
    UNIPROT = "UNIPROT"
    CHEBI = "CHEBI"
    RHEA = "RHEA"
    NCBI = "NCBI"
    SABIORK = "SABIORK"
    OED = "OED"
    GOTENZYMES = "GOTENZYMES"
    OTHER = "OTHER"


__all__ = [
    "AllostericEffect",
    "ClaimStatus",
    "ConfidenceClass",
    "ContextCompatibility",
    "CurationState",
    "EnzymeStateTransitionType",
    "EnzymeStateType",
    "EvidenceType",
    "ExperimentalContextClassification",
    "ModelSourceType",
    "ModelSpecificationGeneProteinRole",
    "ModelSpecificationPathwayRole",
    "ModelSpecificationReactionRole",
    "ModelSpecificationSpeciesRole",
    "ModelStatus",
    "ModelingDecisionResolutionSource",
    "ModelingDecisionStatus",
    "ModificationType",
    "NetworkEdgeInteractionType",
    "NetworkNodeMappingStatus",
    "NetworkNodeType",
    "NetworkPerturbationType",
    "NetworkType",
    "ProjectLifecycleStatus",
    "ProjectType",
    "QuantitativeEvidenceClass",
    "ReactionParticipantRole",
    "RegulatoryEffect",
    "SourceType",
    "SubprojectTaskType",
    "TimeReferenceBasis",
]
