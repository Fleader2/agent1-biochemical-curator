"""Persistence-layer exceptions.

These are raised only for conditions a caller should not have reached at
all -- a programming error in how this layer is invoked, not an ordinary
"this record can't be safely created right now" outcome. Ordinary
conservative refusals (a missing required field, a stale ``NEW`` collision
detected at persistence time, an unsupported allocation) are reported as a
``app.persistence.types.PersistenceResult`` with
``action=PersistenceAction.FAILED`` instead of raising -- exactly the same
philosophy ``app.normalization`` uses for ``AMBIGUOUS``/``CONFLICTED``
(represent the outcome as data, do not raise for something the system is
designed to encounter routinely).
"""

from __future__ import annotations


class PersistenceError(Exception):
    """Base class for persistence-layer exceptions."""


class EntityTypeMismatchError(PersistenceError):
    """A ``NormalizationResult`` was passed to the wrong entity-specific persist function.

    For example, a result with ``entity_type="gene"`` passed to
    ``persist_organism``. This is always a caller bug -- each
    ``app.normalization`` module fixes its own ``entity_type`` constant, so
    a mismatch here can only mean the wrong result/identity pair was routed
    to the wrong persistence function.
    """


class ContributionConfidenceMismatchError(PersistenceError):
    """An ``AggregateClaimConfidence`` was passed alongside contributions it was not computed from.

    Raised by ``app.persistence.claim.persist_claim_with_evidence`` when the
    supplied ``confidence.contribution_breakdown`` does not structurally
    correspond, entry for entry, to the supplied ``contributions`` (same
    count, same ``source_identifier``/``publication_identifier``/
    ``evidence_type`` in the same order) -- always a caller bug, never an
    ordinary data condition: it means the wrong confidence result was routed
    to the wrong evidence set, not that persistence has anything relevant to
    report about the data itself.
    """


__all__ = [
    "ContributionConfidenceMismatchError",
    "EntityTypeMismatchError",
    "PersistenceError",
]
