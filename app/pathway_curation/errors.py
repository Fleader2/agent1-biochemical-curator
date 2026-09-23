"""Pathway Curation Planner's own error hierarchy (Agent 1.x Increment C).

**Expected incomplete biology is never an exception.** An unresolved
frontier item, a missing publication, an absent kinetic measurement, a
budget exhausted before every category was enriched -- all of these are
normal, disclosed outcomes represented via
``PathwayCurationResult.completion_status``/``unresolved_frontier``/
``knowledge_gap_ids``, never raised as errors. These errors exist only
for a genuine structural/programming/configuration problem: a malformed
request, a planner misconfiguration, a connector asked to do something it
cannot do, or an unexpected failure while executing a plan step.

All subclass ``ValueError``, mirroring every other Agent 1 package's own
error-hierarchy convention (e.g. ``app.knowledge_gaps.errors``,
``app.entity_resolution.errors``).
"""

from __future__ import annotations


class PathwayCurationError(ValueError):
    """Base class for every Pathway Curation Planner failure."""


class InvalidCurationRequestError(PathwayCurationError):
    """A ``PathwayCurationRequest`` is malformed or internally inconsistent.

    Never raised for a request that is merely narrow or ambitious --
    those are legitimate; this is only for a request that cannot be
    planned at all (e.g. a non-positive budget, an empty
    ``biological_process``, an ``exclusions`` entry that also appears in
    ``seed_entity_texts``).
    """


class PlannerConfigurationError(PathwayCurationError):
    """``plan_pathway_curation``/``execute_pathway_curation`` was misconfigured.

    For example: a completion policy that requires a capability
    (``include_kinetics``) the request itself disabled, or an executor
    invoked with a session/connector bundle of the wrong type.
    """


class UnsupportedConnectorCapabilityError(PathwayCurationError):
    """A plan step asked a connector to do something it cannot do.

    Raised only for a genuine programming error inside this package's own
    connector-routing (Step 16/17) -- e.g. calling ``fetch()`` on a
    connector this package already knows has none (Open Enzyme Database).
    Never raised merely because a connector returned zero results, which
    is an ordinary, expected outcome represented in the frontier instead.
    """


class CurationExecutionError(PathwayCurationError):
    """A plan step failed for a reason other than expected missing biology.

    Reserved for a genuine structural inconsistency encountered mid-run
    (e.g. a normalization/persistence call raising an error type this
    package does not otherwise know how to interpret as a frontier item).
    A connector I/O failure is **not** routed through this error -- it is
    caught and recorded as a warning/frontier item, per Step 33 of the
    original increment instructions ("do not interpret API failure as
    absence of evidence").
    """


__all__ = [
    "CurationExecutionError",
    "InvalidCurationRequestError",
    "PathwayCurationError",
    "PlannerConfigurationError",
    "UnsupportedConnectorCapabilityError",
]
