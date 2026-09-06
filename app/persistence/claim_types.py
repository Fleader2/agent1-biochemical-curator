"""Data contract for Claim/Evidence persistence (Increment 19).

Two types:

1. ``ExperimentalConditionInput`` -- a fully caller-specified
   ``ExperimentalCondition`` row. Every field is taken verbatim; this
   package never derives any of them from ``EvidenceExtraction``'s free-text
   ``experimental_system``/``assay``/``perturbation`` fields, because no
   deterministic mapping from that free text to ``ExperimentalCondition``'s
   structured columns (``medium``, ``carbon_source``, ``temperature_c``,
   ``ph``, ...) exists anywhere in this repository -- inventing one would be
   exactly the "reinterpret evidence" this package's fundamental rule
   forbids. A caller only constructs one of these when it already has
   genuinely structured, deterministic condition data from elsewhere (see
   ``docs/15_claim_persistence_contract.md``'s architecture-gap section:
   today, nothing upstream of this package produces that data, so this
   parameter is expected to be omitted in the current pipeline).
2. ``ClaimPersistenceResult`` -- the outcome of one
   ``app.persistence.claim.persist_claim_with_evidence`` call.

Unlike every other ``app.persistence.*`` entity, ``Claim`` has no
``NormalizationResult``-driven MATCHED/NEW/AMBIGUOUS/CONFLICTED/UNRESOLVED
status machinery: no canonical Claim-identity/deduplication mechanism exists
anywhere in this schema (verified directly against ``app/models/claim.py``
-- no unique constraint on subject/predicate/object/organism at all), and
this package does not invent one (Increment 19 instructions: report the
absence as an architecture gap, never approximate it). Every call therefore
either creates exactly one new ``Claim`` row, or fails outright --
``ClaimPersistenceResult.action`` is only ever ``CREATED`` or ``FAILED``,
reusing ``app.persistence.types.PersistenceAction``'s existing vocabulary
rather than inventing a parallel one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.persistence.types import PersistenceAction


@dataclass(frozen=True, slots=True)
class ExperimentalConditionInput:
    """A fully caller-specified ``ExperimentalCondition`` row. See module docstring."""

    medium: str | None = None
    carbon_source: str | None = None
    carbon_concentration: Decimal | None = None
    carbon_concentration_unit: str | None = None
    nitrogen_source: str | None = None
    oxygen_status: str | None = None
    temperature_c: Decimal | None = None
    ph: Decimal | None = None
    growth_phase: str | None = None
    growth_rate: Decimal | None = None
    growth_rate_unit: str | None = None
    culture_mode: str | None = None
    notes: str | None = None

    def identity_key(
        self,
    ) -> tuple[
        str | None,
        str | None,
        Decimal | None,
        str | None,
        str | None,
        str | None,
        Decimal | None,
        Decimal | None,
        str | None,
        Decimal | None,
        str | None,
        str | None,
        str | None,
    ]:
        """An exact-match identity key used only for in-call deduplication.

        No cross-call/database-wide deduplication exists for
        ``ExperimentalCondition`` (the schema defines no unique constraint on
        it at all -- the same absence-of-identity finding as ``Claim``
        itself). This key only prevents two condition inputs that are
        byte-for-byte identical *within one persistence call* from producing
        two rows; it is never used to search the database for a
        pre-existing "similar enough" row (Increment 19 instructions:
        "create conservatively ... do not merge heuristically").
        """
        return (
            self.medium,
            self.carbon_source,
            self.carbon_concentration,
            self.carbon_concentration_unit,
            self.nitrogen_source,
            self.oxygen_status,
            self.temperature_c,
            self.ph,
            self.growth_phase,
            self.growth_rate,
            self.growth_rate_unit,
            self.culture_mode,
            self.notes,
        )


@dataclass(frozen=True, slots=True)
class ClaimPersistenceResult:
    """The outcome of one ``persist_claim_with_evidence`` call. See module docstring."""

    action: PersistenceAction
    claim_id: UUID | None = None
    evidence_ids: tuple[UUID, ...] = ()
    skipped_duplicate_indices: tuple[int, ...] = ()
    experimental_condition_ids: tuple[UUID, ...] = ()
    evidence_condition_ids: tuple[UUID, ...] = ()
    source_cross_reference_ids: tuple[UUID, ...] = ()
    external_record_id: UUID | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.action not in (PersistenceAction.CREATED, PersistenceAction.FAILED):
            raise ValueError(
                "ClaimPersistenceResult.action must be CREATED or FAILED -- Claim "
                "persistence has no MATCHED/REUSED_EXISTING/REQUIRES_REVIEW/NO_ACTION "
                "concept (see module docstring: no canonical Claim identity exists to "
                f"match against), got {self.action!r}"
            )
        if self.action is PersistenceAction.CREATED:
            if self.claim_id is None:
                raise ValueError("CREATED requires claim_id")
            if not self.evidence_ids:
                raise ValueError(
                    "CREATED requires at least one evidence_id -- every supported claim "
                    "must have at least one evidence record (docs/02_database_schema.md)"
                )
        else:
            if self.claim_id is not None:
                raise ValueError("FAILED must not carry claim_id")
            if self.evidence_ids:
                raise ValueError("FAILED must not carry evidence_ids")


__all__ = ["ClaimPersistenceResult", "ExperimentalConditionInput"]
