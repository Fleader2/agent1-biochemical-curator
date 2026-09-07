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


class SourceType(StrEnum):
    """External scientific source that a record or piece of evidence came from.

    ``SABIORK``/``OED`` added in Agent 1.x Increment A (kinetic-data
    curation): ``SABIORK`` is SABIO-RK (https://sabiork.h-its.org), a
    kinetic-law/parameter database; ``OED`` is Open Enzyme Database
    (https://openenzymedb.platform.moleculemaker.org), which itself
    aggregates data originating from BRENDA/SABIO-RK (see
    ``docs/24_kinetic_data_curation_and_handoff.md`` §7 for the
    source-lineage policy this enables). Adding a value to this native
    PostgreSQL ``ENUM`` requires migration ``0013_kinetic_measurement_sources``
    (``ALTER TYPE ... ADD VALUE``, which Postgres cannot reverse -- see that
    migration's own docstring for its downgrade policy). No existing value
    was renamed or removed.
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
    OTHER = "OTHER"


__all__ = [
    "ClaimStatus",
    "ConfidenceClass",
    "CurationState",
    "EvidenceType",
    "ReactionParticipantRole",
    "RegulatoryEffect",
    "SourceType",
]
