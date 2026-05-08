# Iteration and Validation Plan

## Iteration Loop
1. Ingest manual and run extraction.
2. Build canonical snapshot.
3. Generate tool suite.
4. Run validation gates.
5. Review failures and improve prompts/parsers/schemas.
6. Re-run until quality thresholds are met.

## Validation Layers
1. Extraction Validation
- Section coverage ratio.
- Table extraction integrity.
- OCR confidence distribution.

2. Schema Validation
- Required field completion.
- Type correctness.
- Relationship graph integrity.

3. Retrieval Validation
- Hit-rate on known rule queries.
- Citation correctness under semantic retrieval.

4. Tool Behavior Validation
- Scenario-based expected output tests.
- Penalty and scoring consistency checks.

5. Regression Validation
- Compare current snapshot vs prior snapshot.
- Flag changed interpretations for manual review.

## Quality Gates
- Block release if:
	- Any critical rule lacks citation.
	- Schema validity drops below 95%.
	- Regression introduces high-severity behavior change.

## Review Cadence
- Daily during initial build phase.
- Per manual update once pipeline stabilizes.

## Metrics Dashboard (Minimum)
- Extraction completeness (%).
- Citation coverage (%).
- Rule confidence distribution.
- Tool test pass rate (%).
- End-to-end runtime (minutes).

## Improvement Backlog Template
- Issue id
- Stage
- Impact
- Root cause
- Proposed fix
- Owner
- Status

