# Agent Design

## Agent Philosophy
Use specialized agents with narrow responsibilities and explicit handoff artifacts. Avoid one monolithic agent for all tasks.

## Proposed Agent Set
1. Orchestrator Agent
- Runs pipeline stages in order.
- Tracks run metadata and retries.

2. PDF Parsing Agent
- Calls parsing tools and emits structured extraction output.
- Marks uncertain blocks for review.

3. Rule Modeling Agent
- Maps extracted text to canonical schema objects.
- Resolves references between definitions, rules, and scoring.

4. Retrieval Agent
- Builds and validates vector and keyword indexes.
- Tunes chunking and retrieval settings.

5. Tool Synthesis Agent
- Generates utilities from canonical model.
- Ensures each utility cites source sections.

6. Validation Agent
- Runs quality checks and regression test suites.
- Fails pipeline on critical confidence drops.

7. Release Agent
- Produces changelog and publishes approved artifacts.

## Handoff Contracts
- Parsing Agent -> Rule Modeling Agent
	- extraction_bundle.json
	- extraction_warnings.json

- Rule Modeling Agent -> Retrieval Agent
	- canonical_entities.json
	- canonical_relationships.json

- Retrieval Agent -> Tool Synthesis Agent
	- vector_index_manifest.json
	- keyword_index_manifest.json

- Tool Synthesis Agent -> Validation Agent
	- tool_manifest.json
	- generated_reports.json

## Agent Guardrails
- No agent may drop source citations.
- Ambiguous text must be marked with confidence and reason.
- Schema-breaking output must be rejected immediately.

## Failure Policy
- Retry transient failures up to 2 times.
- Escalate deterministic parsing failures to manual review queue.
- Block release when validation severity is high.

