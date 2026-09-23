"""Mutable, in-memory run bookkeeping for one ``execute_pathway_curation`` call.

Internal only -- not part of this package's public API (not re-exported
from ``__init__.py``). ``CurationRunState`` is the single place
``executor.py`` accumulates the frontier, the connector-call budget, and
query-deduplication history across iterations; nothing here is itself a
public contract type (those live in ``types.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.pathway_curation.policy import sort_frontier
from app.pathway_curation.types import CurationFrontierItem


@dataclass(slots=True)
class CurationRunState:
    """One run's mutable frontier/budget/dedup bookkeeping."""

    frontier: dict[str, CurationFrontierItem] = field(default_factory=dict)
    executed_query_identities: set[str] = field(default_factory=set)
    queries_executed: list[str] = field(default_factory=list)
    connector_calls_made: int = 0
    discovered_entity_ids: list[UUID] = field(default_factory=list)
    discovered_reaction_ids: list[UUID] = field(default_factory=list)
    discovered_publication_ids: list[UUID] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_frontier(self, item: CurationFrontierItem) -> None:
        """Add or replace one frontier item by its own ``frontier_id`` (idempotent)."""
        self.frontier[item.frontier_id] = item

    def resolve_frontier(self, frontier_id: str) -> None:
        """Remove one frontier item, if present -- a resolved item is simply dropped
        (the audit trail's ``frontier_before``/``frontier_after`` snapshots are what
        show the transition, per ``CurationFrontierItem``'s own docstring)."""
        self.frontier.pop(frontier_id, None)

    def snapshot_frontier(self) -> tuple[CurationFrontierItem, ...]:
        """The current frontier, in the deterministic priority order."""
        return sort_frontier(tuple(self.frontier.values()))

    def has_run_query(self, identity: str) -> bool:
        return identity in self.executed_query_identities

    def record_query(self, identity: str, *, display_text: str) -> None:
        """Record one logical connector query as executed (Step 34 deduplication)."""
        self.executed_query_identities.add(identity)
        self.queries_executed.append(display_text)

    def record_connector_call(self) -> None:
        self.connector_calls_made += 1

    def record_entity(self, entity_id: UUID | None) -> None:
        if entity_id is not None and entity_id not in self.discovered_entity_ids:
            self.discovered_entity_ids.append(entity_id)

    def record_reaction(self, reaction_id: UUID | None) -> None:
        if reaction_id is not None and reaction_id not in self.discovered_reaction_ids:
            self.discovered_reaction_ids.append(reaction_id)

    def record_publication(self, publication_id: UUID | None) -> None:
        if publication_id is not None and publication_id not in self.discovered_publication_ids:
            self.discovered_publication_ids.append(publication_id)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


__all__ = ["CurationRunState"]
