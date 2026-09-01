# Agent 1: Biochemical Evidence Curator
## Testing Specification

**Document:** `docs/05_testing.md`

**Version:** 0.1

**Status:** Implementation Specification

---

# Purpose

This document defines the testing requirements for Agent 1.

Testing must cover both:

1. conventional software correctness, and
2. scientific integrity.

Agent 1 is intended to support construction of mechanistic biological models. Therefore, a test suite that checks only code execution is insufficient.

The system must also demonstrate that it does not:

- invent scientific evidence,
- merge biologically distinct measurements,
- hide uncertainty,
- collapse conflicting evidence,
- promote unsupported claims,
- confuse LLM output with scientific evidence,
- bypass human review.

Scientific correctness constraints are first-class software requirements.

---

# Testing Framework

Use:

```text
pytest
```

for the main test framework.

Recommended supporting tools:

```text
pytest-asyncio
pytest-cov
httpx
respx
factory-boy or equivalent
freezegun when useful
```

Use PostgreSQL for database integration tests whenever practical.

Do not rely exclusively on SQLite for tests because PostgreSQL-specific behavior, JSONB, enums, constraints, and transactions are part of the production design.

---

# Test Organization

Tests should be organized approximately as:

```text
tests/
├── unit/
│   ├── scoring/
│   ├── normalization/
│   ├── validation/
│   ├── extraction/
│   └── utilities/
│
├── database/
│   ├── test_organism.py
│   ├── test_gene.py
│   ├── test_protein.py
│   ├── test_compound.py
│   ├── test_reaction.py
│   ├── test_claim.py
│   ├── test_evidence.py
│   ├── test_kinetics.py
│   └── test_review.py
│
├── api/
│   ├── test_health.py
│   ├── test_organisms.py
│   ├── test_reactions.py
│   ├── test_claims.py
│   ├── test_evidence.py
│   ├── test_reviews.py
│   └── test_exports.py
│
├── connectors/
│   ├── test_pubmed.py
│   ├── test_kegg.py
│   ├── test_brenda.py
│   ├── test_sgd.py
│   └── test_biocyc.py
│
├── workflows/
│   ├── test_curation_workflow.py
│   ├── test_conflict_workflow.py
│   ├── test_kinetics_workflow.py
│   └── test_export_workflow.py
│
├── scientific_integrity/
│   ├── test_no_hallucinated_evidence.py
│   ├── test_uncertainty_preservation.py
│   ├── test_conflict_preservation.py
│   ├── test_measurement_context.py
│   ├── test_human_review_boundary.py
│   └── test_agent2_export_safety.py
│
├── fixtures/
│   ├── publications/
│   ├── kegg/
│   ├── brenda/
│   ├── sgd/
│   └── models/
│
└── conftest.py
```

---

# Testing Principles

The following principles are mandatory.

## Principle 1: Test scientific failure modes directly

Do not assume that general unit tests will catch scientific integrity failures.

Specific tests must deliberately attempt to make the system behave incorrectly.

---

## Principle 2: Prefer deterministic fixtures

Most automated tests should use fixed local fixtures rather than live scientific APIs.

This allows:

- reproducibility,
- fast execution,
- protection against API outages,
- stable expected outputs.

---

## Principle 3: Separate live integration tests

Tests that call real external services must be explicitly marked.

Example:

```python
@pytest.mark.live
```

Live tests must not run by default.

---

## Principle 4: Never test by requiring invented biology

Tests must use either:

- synthetic clearly labeled fixture data, or
- verified real biological examples.

Do not introduce fictitious PMIDs or realistic-looking fake experimental values without clearly labeling them as test fixtures.

---

## Principle 5: Test negative cases

Every major validation rule should include at least one test that proves invalid input is rejected.

---

# Required Test Markers

Define pytest markers for:

```text
unit
database
api
connector
workflow
scientific_integrity
live
slow
```

Document these markers in `pyproject.toml`.

---

# Test Database

Use a separate test PostgreSQL database.

Tests must never connect to production or development databases.

Recommended environment variable:

```text
TEST_DATABASE_URL
```

Each test should operate in an isolated transaction where practical.

Database state must not leak between tests.

---

# Fixtures

Create reusable fixtures for:

```text
organism
strain
gene
protein
compound
compartment
reaction
publication
claim
evidence
kinetic measurement
regulatory interaction
knowledge gap
review event
```

Fixtures should use minimal data necessary for the test.

Avoid enormous all-purpose fixtures.

---

# Synthetic Scientific Fixtures

Synthetic fixtures should use clearly recognizable identifiers such as:

```text
TEST_GENE_001
TEST_RXN_001
PMID_TEST_001
```

These must never be confused with real scientific identifiers.

If a field requires a syntactically valid numeric PMID, use a test-only namespace in application fixtures rather than silently representing it as a real PubMed record.

---

# Database Tests

---

# Organism Tests

Required:

```text
test_create_organism

test_create_multiple_strains_same_species

test_duplicate_species_strain_rejected

test_null_strain_allowed

test_ncbi_taxonomy_id_indexed
```

The system must permit:

```text
Saccharomyces cerevisiae / S288C
Saccharomyces cerevisiae / BY4741
Saccharomyces cerevisiae / CEN.PK
```

as separate strain records.

---

# Gene Tests

Required:

```text
test_create_gene

test_gene_requires_organism

test_gene_external_identifier_uniqueness

test_gene_aliases_preserved

test_gene_symbol_not_assumed_globally_unique

test_gene_not_silently_merged_across_strains
```

---

# Protein Tests

Required:

```text
test_create_protein

test_protein_can_exist_without_gene_when_necessary

test_multiple_proteins_can_reference_same_gene

test_ec_number_preserved

test_protein_localization_is_not_inferred_from_reaction
```

---

# Enzyme Complex Tests

Required:

```text
test_create_enzyme_complex

test_add_complex_member

test_complex_member_uniqueness

test_complex_member_stoichiometry_preserved

test_optional_subunit_supported
```

---

# Compound Tests

Required:

```text
test_create_compound

test_compound_synonym_uniqueness

test_compound_external_identifiers_preserved

test_similar_names_do_not_force_merge

test_different_charge_states_can_remain_distinct

test_formula_nullable

test_charge_nullable
```

---

# Reaction Tests

Required:

```text
test_create_reaction

test_internal_reaction_id_unique

test_reaction_participants_structural

test_reaction_participant_requires_positive_stoichiometry

test_reactant_and_product_roles_distinct

test_modifier_role_supported

test_reversibility_can_be_null

test_mass_balance_can_be_unknown

test_charge_balance_can_be_unknown

test_reaction_does_not_require_free_text_equation
```

---

# Reaction Enzyme Tests

Required:

```text
test_reaction_can_reference_protein

test_reaction_can_reference_complex

test_reaction_enzyme_rejects_both_null

test_reaction_enzyme_rejects_both_targets_when_disallowed

test_putative_catalyst_relationship_supported

test_enzyme_assignment_has_independent_confidence
```

---

# Publication Tests

Required:

```text
test_create_publication

test_pmid_unique_when_present

test_pmcid_unique_when_present

test_doi_unique_when_present

test_publication_without_pmid_allowed

test_open_access_nullable

test_full_text_available_nullable
```

---

# Claim Tests

Required:

```text
test_create_entity_claim

test_create_scalar_claim

test_claim_can_be_unknown

test_claim_can_be_conflicted

test_claim_confidence_nullable

test_supported_claim_requires_evidence

test_claim_without_evidence_cannot_be_promoted_to_supported

test_multiple_evidence_records_supported

test_multiple_claims_with_different_context_can_coexist
```

---

# Evidence Tests

Required:

```text
test_create_evidence

test_evidence_requires_claim

test_evidence_source_preserved

test_evidence_organism_preserved

test_evidence_strain_preserved

test_curator_summary_required

test_quoted_support_optional

test_database_evidence_distinct_from_direct_experiment

test_llm_not_allowed_as_source_type
```

If the code permits arbitrary source strings, the application layer must reject values such as:

```text
OPENAI
CHATGPT
LLM
CLAUDE
GEMINI
```

as scientific evidence source types.

---

# Kinetic Measurement Tests

This area requires especially strong testing.

Required:

```text
test_create_kinetic_measurement

test_original_value_preserved

test_original_unit_preserved

test_normalized_value_preserved_separately

test_normalization_does_not_overwrite_original

test_temperature_preserved

test_ph_preserved

test_strain_preserved

test_protein_construct_preserved

test_purification_state_preserved

test_assay_type_preserved

test_multiple_measurements_same_parameter_allowed

test_measurements_from_different_papers_not_merged

test_measurements_from_different_strains_not_merged

test_measurements_from_different_temperatures_not_merged

test_measurements_from_different_ph_not_merged

test_measurements_from_different_constructs_not_merged

test_missing_conditions_remain_null

test_kinetic_value_cannot_be_created_without_source_when_marked_supported
```

---

# Unit Conversion Tests

Required conversions should have deterministic unit tests.

Examples:

```text
uM -> mM
mM -> M
min^-1 -> s^-1
s^-1 -> min^-1
```

Required:

```text
test_um_to_mm

test_mm_to_m

test_per_minute_to_per_second

test_conversion_round_trip

test_unknown_unit_rejected_or_flagged

test_original_unit_not_modified

test_original_value_not_modified
```

Do not implement ambiguous biochemical activity-unit conversion unless sufficient metadata exist.

For example:

```text
nmol / min / mg protein
```

must not be converted to `s^-1` unless enzyme molecular amount is known.

Add:

```text
test_specific_activity_not_converted_to_kcat_without_required_metadata
```

---

# Regulatory Interaction Tests

Required:

```text
test_create_direct_regulation

test_create_indirect_regulation

test_direct_and_indirect_remain_distinct

test_transcriptional_and_catalytic_regulation_remain_distinct

test_regulation_requires_claim_when_evidence_backed

test_regulator_target_direction_preserved

test_opposite_regulatory_effects_can_coexist
```

---

# Modeling Assumption Tests

Required:

```text
test_create_modeling_assumption

test_modeling_assumption_not_evidence

test_modeling_assumption_not_claim_support

test_human_approval_defaults_false

test_machine_cannot_approve_assumption

test_human_can_approve_assumption
```

---

# Knowledge Gap Tests

Required:

```text
test_create_knowledge_gap

test_unknown_kcat_can_create_gap

test_unknown_compartment_can_create_gap

test_gap_priority_range

test_gap_can_reference_reaction

test_gap_can_reference_protein

test_suggested_experiment_not_treated_as_evidence
```

---

# External Record Tests

Required:

```text
test_external_record_preserves_source

test_external_record_preserves_retrieval_timestamp

test_external_record_preserves_hash

test_retrieval_history_append_only

test_same_external_id_can_have_multiple_retrieval_versions

test_previous_raw_record_not_silently_overwritten
```

---

# Review Tests

Required:

```text
test_create_review_event

test_review_event_preserves_previous_state

test_review_event_preserves_new_state

test_machine_can_set_machine_reviewed

test_machine_can_set_needs_review

test_machine_cannot_set_human_accepted

test_human_can_set_human_accepted

test_invalid_review_transition_rejected

test_rejected_state_preserved

test_review_history_ordered
```

---

# Confidence Scoring Tests

The confidence algorithm must be completely deterministic.

Required:

```text
test_direct_biochemical_base_score

test_direct_in_vivo_base_score

test_genetic_base_score

test_curated_database_base_score

test_homology_base_score

test_llm_inference_score_zero

test_same_strain_modifier

test_same_species_modifier

test_other_fungus_modifier

test_bacterium_modifier

test_in_vivo_experimental_modifier

test_recombinant_enzyme_modifier

test_replication_bonus

test_replication_bonus_capped

test_minor_conflict_penalty

test_major_conflict_penalty

test_score_clamped_at_zero

test_score_clamped_at_one_hundred

test_confidence_class_very_high

test_confidence_class_high

test_confidence_class_moderate

test_confidence_class_low

test_null_score_maps_unknown
```

---

# Independent Source Tests

Agent 1 must not count derivative sources as independent replication.

Test a case containing:

```text
primary paper A
review B citing paper A
database C citing paper A
```

Required:

```text
test_derivative_sources_not_counted_as_three_independent_sources
```

Then test:

```text
primary paper A
independent primary paper D
```

Required:

```text
test_independent_primary_study_receives_replication_bonus
```

---

# Conflict Tests

Required:

```text
test_opposite_claims_can_coexist

test_conflict_flag_created

test_conflict_does_not_delete_claim

test_conflict_penalty_applied

test_context_can_resolve_apparent_conflict

test_strain_difference_prevents_false_conflict

test_growth_condition_difference_prevents_false_conflict

test_conflict_remains_unresolved_when_context_insufficient
```

---

# Unknown vs Negative Evidence Tests

Required:

```text
test_no_search_result_returns_unknown

test_api_failure_returns_unknown_or_incomplete

test_no_search_result_does_not_create_negative_claim

test_negative_claim_requires_supporting_evidence

test_absence_of_evidence_not_equal_evidence_of_absence
```

Example:

If PubMed search returns zero hits for:

```text
TEST_GENE_001 kinetics
```

the result must not become:

```text
TEST_GENE_001 has no kinetic activity
```

---

# LLM Hallucination Protection Tests

These tests are mandatory.

Mock the LLM to return fabricated information.

Example mock response:

```text
PMID: 99999999
Km = 0.42 mM
Acc1 is mitochondrial
```

when none of those facts are present in the supplied source.

Required:

```text
test_llm_generated_pmid_not_accepted_without_retrieval

test_llm_generated_doi_not_accepted_without_retrieval

test_llm_generated_database_id_not_accepted_without_validation

test_llm_generated_kinetic_parameter_not_stored_as_supported

test_llm_generated_localization_not_stored_as_supported

test_llm_generated_reaction_not_stored_as_supported

test_llm_generated_regulation_not_stored_as_supported
```

The system may preserve such output only as explicitly labeled:

```text
LLM_HYPOTHESIS
```

with zero evidence score.

Required:

```text
test_llm_hypothesis_has_zero_evidence_score
```

---

# Source Grounding Tests

When the Evidence Extractor receives a source containing only:

```text
Protein A may regulate enzyme B.
```

it must not produce:

```text
Protein A directly inhibits enzyme B.
```

Required:

```text
test_speculative_language_not_promoted_to_direct_fact
```

Also test:

```text
test_author_hypothesis_labeled

test_review_summary_not_mistaken_for_direct_experiment

test_database_annotation_not_mistaken_for_primary_experiment
```

---

# Organism Transfer Tests

These are mandatory.

If evidence is from:

```text
Schizosaccharomyces pombe
```

and the target model is:

```text
Saccharomyces cerevisiae
```

the system must retain the original organism.

Required:

```text
test_other_species_evidence_retains_original_species

test_other_species_evidence_not_silently_transferred

test_homology_inference_explicitly_labeled

test_other_species_kinetic_value_has_reduced_applicability
```

---

# Strain Context Tests

If two measurements are:

```text
S288C: Km = 0.4 mM
CEN.PK: Km = 0.8 mM
```

both must remain.

Required:

```text
test_strain_specific_measurements_remain_distinct
```

Agent 1 must not create:

```text
Km = 0.6 mM
```

unless a separate explicitly derived analysis requests it.

Required:

```text
test_agent_does_not_average_strain_specific_measurements
```

---

# Measurement Confidence vs Model Applicability Tests

Test a measurement that is experimentally strong but physiologically mismatched.

Example:

```text
high-quality purified recombinant enzyme measurement
other species
37 C
pH 8.5
```

Required:

```text
test_measurement_confidence_can_be_high_while_model_applicability_low
```

Also:

```text
test_measurement_confidence_and_applicability_stored_separately
```

---

# Reaction Validation Tests

Required:

```text
test_balanced_reaction_passes_mass_check

test_unbalanced_reaction_fails_mass_check

test_balanced_charge_passes

test_charge_mismatch_fails

test_missing_formula_returns_indeterminate_not_false

test_missing_charge_returns_indeterminate_not_false

test_validation_does_not_modify_reaction

test_missing_water_can_be_flagged

test_missing_proton_can_be_flagged
```

A validator must distinguish:

```text
PASS
FAIL
INDETERMINATE
```

where appropriate.

Do not classify missing chemical information as a definite chemical failure.

---

# Reversibility Tests

Required:

```text
test_reversibility_can_be_unknown

test_arrow_symbol_does_not_establish_reversibility

test_database_reversibility_annotation_stored_as_database_evidence

test_thermodynamic_evidence_can_support_reversibility

test_reversibility_assumption_remains_assumption
```

---

# Localization Tests

Required:

```text
test_pathway_location_not_used_as_protein_localization_evidence

test_reaction_location_not_automatically_protein_location

test_protein_location_and_reaction_location_distinct

test_multiple_localizations_can_coexist

test_condition_dependent_localization_supported
```

---

# Search Connector Tests

Each connector must be tested independently.

Tests must mock HTTP responses by default.

---

# PubMed Connector Tests

Required:

```text
test_pubmed_search_builds_valid_request

test_pubmed_fetch_parses_metadata

test_pubmed_pmid_preserved

test_pubmed_empty_result_valid

test_pubmed_timeout_handled

test_pubmed_429_retried

test_pubmed_500_retried

test_pubmed_rate_limit_enforced

test_pubmed_response_cached

test_pubmed_failed_request_not_treated_as_zero_results
```

---

# KEGG Connector Tests

Required:

```text
test_kegg_search

test_kegg_get

test_kegg_identifier_parsing

test_kegg_reaction_parsing

test_kegg_compound_parsing

test_kegg_rate_limit_enforced

test_kegg_error_handled

test_kegg_cache_used
```

---

# BRENDA Connector Tests

Required:

```text
test_brenda_authentication_required

test_brenda_kinetic_result_parsed

test_brenda_organism_preserved

test_brenda_parameter_units_preserved

test_brenda_multiple_measurements_preserved

test_brenda_rate_limit_enforced

test_brenda_failure_does_not_create_negative_result
```

---

# SGD Connector Tests

Required:

```text
test_sgd_gene_lookup

test_sgd_systematic_name_parsed

test_sgd_aliases_preserved

test_sgd_external_identifiers_preserved

test_sgd_localization_annotation_labeled_as_database_annotation
```

---

# Connector Cache Tests

Required:

```text
test_identical_request_uses_cache

test_cache_key_includes_request_parameters

test_cached_raw_response_hash_preserved

test_cache_does_not_hide_new_version_when_refresh_requested
```

---

# Retry and Backoff Tests

Required:

```text
test_transient_failure_retried

test_permanent_400_not_retried_excessively

test_429_uses_backoff

test_retry_count_configurable

test_final_failure_logged
```

Tests must not actually sleep for long periods.

Inject or mock the sleep/backoff function.

---

# Search Strategy Tests

Required:

```text
test_gene_symbol_search_generated

test_systematic_gene_name_search_generated

test_enzyme_name_search_generated

test_ec_number_search_generated

test_kinetics_queries_generated

test_regulation_queries_generated

test_conflict_queries_generated

test_review_used_for_discovery_not_automatic_primary_support
```

---

# Search Saturation Tests

The default heuristic is:

```text
three consecutive distinct query variants
with no new relevant primary sources
```

Required:

```text
test_search_saturation_after_three_empty_novel_queries

test_duplicate_sources_do_not_count_as_new_evidence

test_new_primary_source_resets_saturation_counter

test_search_saturation_threshold_configurable
```

---

# Workflow Tests

Full workflow tests should exercise realistic end-to-end behavior using mocked scientific sources.

---

# Basic Curation Workflow

Required:

```text
test_pathway_task_created

test_task_decomposes_into_subtasks

test_candidate_reaction_created

test_source_retrieved

test_claim_extracted

test_evidence_linked

test_confidence_calculated

test_critic_runs

test_deterministic_validation_runs

test_knowledge_gap_created

test_machine_review_state_assigned
```

---

# Conflict Workflow

Fixture:

```text
Paper A:
Protein X localizes to cytosol.

Paper B:
Protein X localizes to mitochondria.
```

Expected:

```text
both claims preserved
conflict detected
context examined
confidence adjusted
human review requested if unresolved
```

Required:

```text
test_conflict_workflow_preserves_both_claims
```

---

# Kinetics Workflow

Fixture:

```text
Paper A:
Km = 0.4 mM at 30 C, pH 7.0

Paper B:
Km = 1.2 mM at 25 C, pH 8.0
```

Expected:

```text
two measurements
no averaging
conditions preserved
model applicability evaluated separately
```

Required:

```text
test_kinetics_workflow_preserves_individual_measurements
```

---

# API Tests

Use FastAPI's supported testing approach with an isolated test database.

Required tests include:

```text
test_health

test_system_info

test_create_organism_api

test_create_gene_api

test_create_compound_api

test_create_reaction_api

test_add_reaction_participant_api

test_negative_stoichiometry_returns_422

test_create_claim_api

test_add_evidence_api

test_create_kinetic_measurement_api

test_normalize_measurement_api

test_create_knowledge_gap_api

test_review_transition_api

test_machine_human_accept_returns_error

test_human_accept_success

test_default_export_filters_state

test_pagination_api

test_external_failure_response

test_openapi_available
```

---

# Authentication and Authorization Tests

Even if initial authentication is lightweight, authorization boundaries must be tested.

Required:

```text
test_anonymous_cannot_human_accept_when_auth_enabled

test_machine_role_cannot_human_accept

test_human_reviewer_can_human_accept

test_machine_role_cannot_approve_modeling_assumption

test_human_reviewer_can_approve_modeling_assumption

test_client_supplied_reviewer_type_not_trusted_when_auth_enabled
```

---

# Export Tests

Exports are a critical safety boundary.

Required:

```text
test_default_export_human_accepted_only

test_proposed_record_excluded

test_machine_reviewed_record_excluded_by_default

test_needs_review_record_excluded_by_default

test_rejected_record_excluded

test_explicit_research_export_can_include_machine_reviewed

test_export_preserves_curation_state

test_export_preserves_internal_ids

test_export_preserves_reaction_relationships

test_export_preserves_evidence_links

test_export_preserves_assumptions

test_export_preserves_knowledge_gaps
```

---

# Agent 2 Safety Tests

These tests verify that downstream model-building software cannot accidentally treat uncertain material as accepted biology.

Required:

```text
test_agent2_default_export_contains_no_rejected_records

test_agent2_default_export_contains_no_llm_hypotheses

test_agent2_default_export_contains_no_unapproved_assumptions

test_agent2_default_export_contains_only_human_accepted_biology

test_exported_kinetic_measurements_retain_conditions

test_export_does_not_replace_missing_values_with_defaults
```

---

# Scientific Integrity Test Suite

Create a dedicated test marker:

```text
scientific_integrity
```

The following tests must always run in continuous integration.

```text
test_no_claim_without_evidence

test_llm_is_not_evidence_source

test_no_hallucinated_pmid

test_no_hallucinated_doi

test_no_hallucinated_kinetic_parameter

test_no_hallucinated_reaction

test_unknown_remains_unknown

test_absence_of_evidence_not_negative_evidence

test_conflicting_claims_preserved

test_different_strains_not_merged

test_different_conditions_not_merged

test_original_measurements_preserved

test_assumption_not_fact

test_human_acceptance_requires_human

test_rejected_record_not_exported

test_llm_hypothesis_not_exported_as_fact
```

Failure of any scientific-integrity test must fail the build.

---

# Regression Tests

Whenever a scientific-integrity bug is discovered:

1. reproduce the bug,
2. create a failing test,
3. fix the bug,
4. keep the test permanently.

Examples:

```text
kinetic values accidentally averaged
strain field dropped
wrong PMID associated with evidence
review article treated as direct evidence
machine set HUMAN_ACCEPTED
reaction export omitted compartment
```

Every such issue requires a regression test.

---

# Golden Fixture Tests

Create a small, manually reviewed test dataset representing a few biochemical reactions.

The dataset should contain:

```text
a confirmed reaction
a reaction with uncertain reversibility
a reaction with conflicting localization
multiple kinetic measurements
a regulatory interaction
a knowledge gap
a modeling assumption
```

Store expected normalized output as version-controlled JSON.

Required:

```text
test_golden_dataset_normalization

test_golden_dataset_claims

test_golden_dataset_conflicts

test_golden_dataset_export
```

Golden fixtures should be intentionally small and manually auditable.

---

# LLM Output Contract Tests

LLM responses must use structured schemas.

Use Pydantic validation.

Required:

```text
test_evidence_extraction_schema_valid

test_kinetics_extraction_schema_valid

test_regulation_extraction_schema_valid

test_critic_schema_valid

test_completion_review_schema_valid

test_invalid_llm_json_rejected

test_missing_required_llm_field_rejected

test_unknown_enum_rejected
```

Do not attempt to salvage severely malformed structured output silently.

---

# Prompt Regression Tests

Prompts are part of the software system.

For important prompts, maintain fixed source fixtures and verify critical behavior.

Examples:

```text
source contains speculation only
source contains two Km values
source discusses another organism
source reports indirect regulation
```

Required:

```text
test_prompt_does_not_promote_speculation

test_prompt_extracts_multiple_kinetic_values

test_prompt_preserves_source_organism

test_prompt_preserves_indirect_regulation
```

These tests may use mocked LLM responses in normal CI.

Optional live prompt-evaluation tests may be run separately.

---

# LLM Provider Independence Tests

Application code should depend on an internal LLM interface rather than a provider-specific API throughout the codebase.

Required:

```text
test_mock_llm_provider_supported

test_llm_provider_interface

test_provider_specific_response_normalized
```

This allows future changes in model provider without rewriting scientific logic.

---

# Determinism Tests

Where deterministic behavior is expected:

```text
test_confidence_same_input_same_output

test_unit_conversion_same_input_same_output

test_identifier_normalization_same_input_same_output

test_export_order_stable_when_configured

test_hash_same_raw_record_same_hash
```

LLM output itself may not be perfectly deterministic across providers, but downstream validation rules must be deterministic.

---

# Reproducibility Metadata Tests

Required:

```text
test_task_records_prompt_version

test_task_records_model_name

test_task_records_provider

test_task_records_execution_timestamp

test_task_records_software_version

test_external_record_records_retrieval_time
```

---

# Logging Tests

Required:

```text
test_search_query_logged

test_external_failure_logged

test_claim_creation_logged

test_conflict_logged

test_review_transition_logged

test_export_logged

test_secrets_not_logged
```

Never assert exact log formatting unless necessary.

Test semantic content.

---

# Security Tests

Required:

```text
test_api_key_not_returned

test_database_password_not_returned

test_brenda_password_not_returned

test_ncbi_api_key_not_returned

test_llm_api_key_not_returned

test_env_secrets_not_in_system_info

test_secrets_not_in_error_response
```

---

# Migration Tests

Alembic migrations must be tested.

Required:

```text
test_upgrade_empty_database_to_head

test_downgrade_recent_migration_when_supported

test_models_match_migration_head

test_seed_compartments_successful
```

At minimum, CI should prove that a completely empty PostgreSQL database can be migrated to the current schema.

---

# Seed Data Tests

Required:

```text
test_seed_saccharomyces_compartments

test_seed_is_idempotent

test_seed_does_not_insert_biological_reactions

test_seed_does_not_insert_kinetic_parameters
```

---

# Performance Tests

Initial performance requirements should be modest.

At minimum test that:

```text
listing 10,000 claims remains paginated

retrieving one reaction does not load all publications

bulk evidence insertion does not produce obvious N+1 behavior

export of a moderate pathway completes without excessive memory use
```

Mark larger performance tests:

```text
slow
```

and do not necessarily run them on every local test invocation.

---

# N+1 Query Tests

For frequently used endpoints, monitor query counts where practical.

Particularly:

```text
GET /reactions/{id}

GET /claims/{id}

GET /kinetic-measurements

POST /exports
```

Avoid accidental query explosions from ORM relationships.

---

# Coverage Requirements

Minimum line coverage target:

```text
90%
```

for core application code.

The following modules should target:

```text
95% or greater
```

where practical:

```text
confidence scoring
unit conversion
scientific validation
review-state transitions
export filtering
```

Coverage alone is not a sufficient quality metric.

A scientifically unsafe system with 100% line coverage is still unsafe.

---

# Continuous Integration

CI must run:

```text
ruff
pytest
pytest scientific_integrity
migration test
```

Recommended sequence:

```text
1. install dependencies
2. start PostgreSQL service
3. run Alembic migrations
4. run ruff
5. run unit tests
6. run database tests
7. run API tests
8. run workflow tests
9. run scientific-integrity tests
10. generate coverage report
```

---

# Required CI Failure Conditions

The build must fail if:

```text
any test fails

any scientific-integrity test fails

Alembic migration fails

ruff reports an error

coverage falls below configured threshold

schema validation fails
```

---

# Live External API Tests

Live tests may verify:

```text
PubMed connectivity
KEGG connectivity
BRENDA connectivity
SGD connectivity
```

They must be excluded from normal CI unless credentials and network access are explicitly configured.

Use:

```text
pytest -m live
```

Live tests must tolerate legitimate external-service changes.

Do not assert exact result counts from public scientific databases unless absolutely necessary.

---

# Manual Scientific Acceptance Tests

Before declaring Version 0.1 scientifically usable, perform a small manual curation exercise.

Suggested target:

```text
Saccharomyces cerevisiae
free fatty acid metabolism
```

Manually inspect at least:

```text
5 reactions

5 gene/protein associations

5 localization claims

10 kinetic measurements

5 regulatory claims

3 knowledge gaps

2 conflicting evidence cases
```

For each item verify:

```text
source exists

identifier is correct

organism is correct

strain is preserved

claim matches source

conditions are preserved

confidence is plausible

conflicts are visible

no unsupported inference is represented as fact
```

Document results in:

```text
docs/manual_validation_v0.1.md
```

---

# Release Gate

Version 0.1 must not be considered ready for scientific use unless:

1. all required tests pass,

2. scientific-integrity tests pass,

3. database migrations pass from an empty PostgreSQL database,

4. export filtering tests pass,

5. machine-to-human review boundary tests pass,

6. no known high-severity provenance bug remains,

7. the manually reviewed pilot dataset passes scientific inspection.

---

# Definition of Done

The testing layer is complete when:

1. Unit tests exist for core scientific logic.

2. Database tests cover schema constraints.

3. API tests cover public behavior.

4. Connector tests use mocked external responses.

5. Workflow tests cover complete curation paths.

6. Scientific-integrity tests directly attack known failure modes.

7. LLM hallucination protection is tested.

8. Kinetic measurements from different conditions cannot be silently merged.

9. Conflicting evidence remains preserved.

10. Unknown information remains unknown.

11. Machine processes cannot set `HUMAN_ACCEPTED`.

12. Default Agent 2 exports contain only permitted scientific records.

13. Alembic migrations are tested.

14. CI runs automatically.

15. Coverage thresholds are enforced.

16. Regression tests are added for every discovered scientific-integrity bug.

---

# Final Testing Principle

The test suite must answer more than:

```text
Does the software run?
```

It must also answer:

```text
Can the software be trusted not to silently distort the biology?
```

The highest-priority tests are those that prove the system preserves:

```text
provenance

experimental context

uncertainty

conflicting evidence

original measurements

human review boundaries
```

When a choice must be made between convenience and scientific integrity, tests must enforce scientific integrity.

A failure that produces an explicit:

```text
UNKNOWN
```

is preferable to a successful execution that produces an unsupported scientific claim.
