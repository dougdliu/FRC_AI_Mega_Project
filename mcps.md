# MCPs (Model Context Protocol) Design

## Purpose
Define a clean MCP layer so agents can invoke project tools through stable contracts.

## MCP Server Categories
1. Document MCP
- parse_pdf(path)
- extract_tables(path)
- segment_sections(path)

2. Canonical Model MCP
- upsert_entities(entities)
- link_entities(relationships)
- validate_schema(snapshot_id)

3. Retrieval MCP
- build_vector_index(snapshot_id)
- build_keyword_index(snapshot_id)
- retrieve(query, mode)

4. Analysis Tool MCP
- score_scenario(input)
- check_penalty_risk(actions)
- compare_strategy(option_a, option_b)

5. Validation MCP
- run_extraction_tests(snapshot_id)
- run_regression_suite(snapshot_id)
- generate_quality_report(snapshot_id)

## Contract Requirements
- Inputs and outputs must be JSON schema validated.
- Every response must include status, timestamp, and trace_id.
- Failures must include machine-readable error codes.

## Versioning Strategy
- Version each MCP endpoint with semantic versions.
- Keep backwards compatibility across minor versions.
- Deprecate endpoints with explicit end-of-life dates.

## Observability
- Log request id, agent id, latency, and result class.
- Emit counters for parse failures, confidence warnings, and test failures.

## Security and Safety
- Restrict filesystem access to approved project directories.
- Redact secrets and tokens from logs.
- Validate path inputs to prevent traversal.

## Initial Implementation Priority
1. Document MCP
2. Canonical Model MCP
3. Validation MCP
4. Retrieval MCP
5. Analysis Tool MCP

