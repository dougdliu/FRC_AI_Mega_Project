# FRC Tooling and MCP Plan

## Scope Decision

The core MVP does not need many separate MCP servers.

Start with one local pipeline and ordinary in-process modules. Extract MCP servers only after the extraction, field-model, strategy, and simulation contracts stabilize.

Use MCPs when:
- a capability must be shared across multiple tools or agents
- the interface is stable enough to version
- process isolation is clearly worth the added complexity

Avoid MCPs in the first pass for internal-only glue code such as prompt orchestration, simple strategy heuristics, and artifact merging.

## Core Candidate Services

| MCP Name | Purpose | Transport | Notes |
|----------|---------|-----------|-------|
| `frc-rulebook-mcp` | Manual extraction, clause lookup, citation search | `stdio` | First candidate because the interface is document-oriented |
| `frc-field-model-mcp` | Field drawing extraction and coordinate normalization | `stdio` | Useful once field schemas stabilize |
| `strategy-sim-mcp` | Coarse seeded strategy sweeps and capability-profile comparison | `stdio` | Only after the local sim contract stabilizes |
| `qa-validator-mcp` | Artifact validation and release gating | `stdio` | Good extraction target after schemas are stable |

## Candidate Specifications

### 1. `frc-rulebook-mcp`

**Purpose:** parse manuals, extract cited clauses, and expose clause lookup.

**Tools:**
- `extract_rules(manual_pdf_path, supplemental_pdf_paths)`
- `search_rules(query)`
- `validate_clause(clause_id, proposed_interpretation)`

**Resources:**
- `rules://{year}/manual.json`
- `rules://{year}/clauses/{id}`

### 2. `frc-field-model-mcp`

**Purpose:** extract field references and normalize them into a simulation-ready field model.

**Tools:**
- `extract_field_references(field_drawing_pdf_path)`
- `build_field_model(field_layout_reference_path)`
- `validate_field_model(field_model_path)`

**Resources:**
- `field://{year}/layout_reference.json`
- `field://{year}/field_model.json`

### 3. `strategy-sim-mcp`

**Purpose:** run coarse simulations for rough strategy ranking and sensitivity analysis.

**Tools:**
- `build_simulation_model(field_model_path, mechanics_path)`
- `run_coarse_sweep(config)`
- `export_sim_summary(run_id)`

**Resources:**
- `sim://configs/{year}.json`
- `sim://results/{run_id}.json`

### 4. `qa-validator-mcp`

**Purpose:** validate artifact schemas, citations, field consistency, and simulation reproducibility.

**Tools:**
- `validate_artifacts(manifest_path)`
- `validate_citations(rules_path, strategy_path)`
- `validate_simulation_results(result_path)`

**Resources:**
- `validation://schemas/{name}.json`
- `validation://reports/{run_id}.json`

## Human-Gated Future Services

These stay out of the core MVP unless a later robot-design workflow is started:
- `wpilib-codegen-mcp`
- `maplesim-robot-mcp`
- `electrical-power-mcp`
- `scouting-data-mcp`
- `advscope-log-mcp`

## Transport and Envelope Standard

- Transport: `stdio` by default
- Protocol: JSON-RPC 2.0
- Error envelope: `{success, data, error_code, error_message, duration_ms}`
- Versioning: semantic versioning only after the interface is stable

## Security Baseline

- Allowlist filesystem paths per MCP.
- Validate all path and command arguments at the boundary.
- Redact secrets from logs and tool responses.
- Deny network egress unless a specific MCP explicitly needs it.

## Implementation Priority

1. Keep everything in-process first.
2. Extract `frc-rulebook-mcp` if document tooling needs reuse.
3. Extract `frc-field-model-mcp` after the field model schema stabilizes.
4. Extract `strategy-sim-mcp` and `qa-validator-mcp` only when their schemas stop changing frequently.

## MCP Development Skill

If any of these services are extracted, use the vendored `mcp-builder` skill and keep the first implementation in Python plus FastMCP unless a Node-specific runtime need appears.
