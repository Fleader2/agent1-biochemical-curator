"""Normalization persistence layer.

Consumes a ``(identity, NormalizationResult)`` pair already produced by
``app.normalization.*`` and applies exactly the database action that
normalization status permits. **Normalization decides identity; persistence
applies only permitted database actions** -- nothing in this package
re-runs identity inference, re-classifies candidates, or silently overrides
what a normalization decision already decided.

There is no existing repository/service layer for entity creation anywhere
in this repository prior to this increment (verified by inspection: no
`def create_*`/`class *Repository`/`class *Service` writes any ORM row
anywhere in `app/`; the only prior write-capable code is
`app/db/session.py`'s engine/session factory and direct `session.add(...)`
calls in database tests). This package establishes the first one, following
the one-module-per-entity structure `app.normalization` already uses, and
the transaction-ownership convention `app/db/session.py`'s own docstring
already states: "Committing is the responsibility of the service performing
the write" -- no function in this package ever calls
`session.commit()`/`session.rollback()`; every function only `flush()`es
(so assigned primary keys are available to return) within whatever
transaction the caller already opened. This mirrors `tests/conftest.py`'s
`db_session` fixture exactly, and the caller decides whether to commit.

**Status -> action policy** (implemented identically, with entity-specific
field mappings, in every `app.persistence.*` entity module):

* `MATCHED` -> `REUSED_EXISTING`. The matched row is never mutated;
  differing incoming metadata is never written over existing identity
  fields (this increment is not an entity-merging or
  metadata-reconciliation engine). A source cross-reference may be
  attached to the existing row, idempotently.
* `NEW` -> `CREATED`, provided (a) every creation-required field the ORM
  actually enforces is present on the incoming identity, and (b) a
  persistence-time freshness recheck (see below) finds no row matching the
  same strong identifier(s) normalization already checked. Otherwise
  `FAILED` -- nothing is invented to make an incomplete or now-stale `NEW`
  creatable.
* `AMBIGUOUS` / `CONFLICTED` -> `REQUIRES_REVIEW`. No entity is created, no
  candidate is chosen, nothing is merged or overwritten. This package
  never creates a `ReviewEvent` row automatically -- `ReviewEvent.reviewer_type`
  is a trust boundary the API/auth layer owns
  (`app/models/review_event.py`'s own docstring: "nothing here enforces the
  ... rule -- that enforcement belongs to the API/auth layer"), and no
  existing service exists yet to perform a documented curation-state
  transition safely. `REQUIRES_REVIEW` is returned as data instead.
* `UNRESOLVED` -> `NO_ACTION`. Nothing is created, nothing is guessed,
  status is never promoted to `NEW`.

**Stale-`NEW`-result safety.** A `NormalizationResult` is an optimistic
snapshot: database state can change between when normalization ran and
when persistence is asked to act on it. Most entities in this schema have
**no database-level uniqueness constraint at all** on their identifying
fields (`Protein.uniprot_id`, every `Compound`/`Compartment` identifier,
`Reaction`'s external identifiers, and every `ReactionEnzyme` column --
see each entity module's docstring and `docs/07_normalization_design.md`'s
own open questions on this) -- so a database constraint violation cannot be
relied on generically to catch a stale `NEW`. Every entity module's `NEW`
path therefore re-runs an exact-match query, inside the caller's own
transaction, against exactly the same strong identifier(s) the
`NormalizationResult` was based on, immediately before inserting. If that
recheck finds a row, creation is refused (`FAILED`) rather than risking a
duplicate -- callers are expected to re-normalize and retry rather than
replay a stale `NEW` result directly (see `app.persistence._freshness`).
Where a real database uniqueness constraint *does* exist (`Organism`'s
partial unique on `(scientific_name, strain)`, `Publication`'s
`pmid`/`pmcid`/`doi`, `Gene`'s `sgd_id`/`ncbi_gene_id`/`kegg_gene_id`), it
remains a redundant concurrency backstop underneath the same recheck, not a
substitute for it -- a race between the recheck and the insert is still
possible in principle, and would surface as an `IntegrityError` this
package does not swallow (the caller's transaction handling is responsible
for that).

**Reaction `NEW` is not supported in this increment.**
`Reaction.internal_id` is `NOT NULL UNIQUE`, and normalization deliberately
never generates one (`app.normalization.reaction`'s own docstring: "a
persistence identifier, never incoming identity"). No allocator for values
like `FFA_R0001` exists anywhere in this repository -- the only precedent
found, `tests/database/test_group_c_models.py`'s `_internal_id()`, is an
`itertools.count()` helper explicitly scoped to that one test module, not a
production-safe, concurrency-correct allocator. Inventing a
"`MAX` + 1"-style allocator here would be exactly the concurrency-unsafe
shortcut this increment's instructions forbid. `persist_reaction` therefore
implements `MATCHED`/`AMBIGUOUS`/`CONFLICTED`/`UNRESOLVED` fully, and
returns a structured `FAILED` result for `NEW` explaining why, rather than
allocating an ID unsafely -- see this increment's completion report for the
recommended follow-up (a dedicated, transactional allocator, most likely a
database sequence, which is a schema change out of this increment's scope).

Evidence, claims, confidence scoring, and human review-state transitions
are explicitly out of scope -- this package only ever reads/writes the
canonical entity tables (`Organism`, `Publication`, `Gene`, `Protein`,
`Compound`, `Compartment`, `Reaction`/`ReactionParticipant`,
`ReactionEnzyme`) plus `SourceCrossReference`/`ExternalRecord` provenance.
"""

from __future__ import annotations
