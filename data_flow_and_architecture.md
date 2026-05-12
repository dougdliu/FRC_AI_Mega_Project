# System Architecture

- **Inputs:**
  - `/inputs/{game_year}GameManual.pdf`
  - `/inputs/{game_year}-field-dimension-dwgs.pdf`
  - optional Team Updates and Q&A addenda
- **Context:** `/context/game_spec.json`
- **Artifacts:** `/artifacts/{game_year}/`
  - `rules.json`, `field_layout_reference.json`, `extraction_report.md`
  - `field_model.json`, `apriltag_field_layout.json`
  - `mechanics.json`, `strategy_packet.json`, `strategy_brief.md`, `team_decision_packet.md`
  - `simulation_model.json`, `sim_params.json`, `sim_summary.json`, `simulation_report.md`
  - `validation_report.json`
- **Orchestrator:** single local pipeline runner first; optional MCP extraction later
- **Storage:** JSON and Markdown artifacts first; no required app databases in the core MVP
- **Compute:** CPU-first for parsing, field modeling, strategic analysis, and coarse simulation sweeps

## Core Architecture Decisions

1. **Field drawings are first-class inputs**
   - Field dimensions, scoring locations, protected zones, and start locations must be traced to drawing references where possible.
   - The pipeline should preserve unresolved drawing ambiguities instead of silently choosing coordinates.

2. **Extraction and analysis are separate**
   - `pdf_extractor` captures cited facts.
   - `mechanic_analyst`, `strategy_architect`, and `sim_engineer` reason from those facts.

3. **Strategy simulation uses abstract capability profiles**
   - Simulations compare cycle assumptions, task priorities, field paths, and alliance-role mixes.
   - Simulations do not assume a specific team robot unless humans provide that later.

4. **Robot code generation is human-gated**
   - Codegen requires selected team objectives, robot design, mechanisms, sensors, and acceptance tests.
   - Those inputs are downstream of this project’s core analysis artifacts.

5. **Keep the MVP in-process**
   - Build the first version as normal modules behind one CLI.
   - Extract stable interfaces into MCP servers only when reuse or isolation clearly helps.

## Data Contracts

- `rules.json`
  - Required keys: `sections`, `scoring`, `penalties`, `field_rules`, `timing`, `equipment_limits`, `citations`
  - Required citation fields per clause: `section_id`, `page`, `clause_text`

- `field_layout_reference.json`
  - Required keys: `metadata`, `drawing_pages`, `dimension_tokens`, `raw_locations`, `confidence_notes`
  - Purpose: retain source-traceable drawing observations before coordinate normalization

- `field_model.json`
  - Required keys: `metadata`, `field_dimensions`, `reference_frames`, `zones`, `scoring_locations`, `game_piece_locations`, `obstacles`
  - Purpose: provide normalized geometry to strategy analysis and simulations

- `apriltag_field_layout.json`
  - Required keys when tags exist: `field`, `tags`, `metadata`
  - Purpose: preserve a WPILib-compatible field-layout artifact for later downstream consumers

- `mechanics.json`
  - Required keys: `states`, `transitions`, `resource_constraints`, `phase_limits`, `scoring_model`, `penalty_model`
  - Each transition must map to one or more rule citations

- `strategy_packet.json`
  - Required keys: `game_summary`, `task_candidates`, `role_candidates`, `scoring_priorities`, `cycle_assumptions`, `risk_notes`, `open_questions`

- `team_decision_packet.md`
  - Required sections: `Strategic Choices`, `Robot Capability Questions`, `Simulation Assumptions To Validate`, `Open Rule Questions`

- `simulation_model.json`
  - Required keys: `field_model_ref`, `mechanics_ref`, `entities`, `actions`, `timing`, `scoring`, `constraints`

- `sim_params.json`
  - Required keys: `seed_set`, `capability_profiles`, `cycle_time_ranges`, `strategy_scenarios`, `assumption_notes`

- `sim_summary.json`
  - Required keys: `seed_set`, `ranked_strategies`, `sensitivity_notes`, `robust_findings`, `warnings`

- `game_spec.json`
  - Merges normalized rules, field geometry, mechanics, and selected strategy assumptions
  - Contains `schema_version`, `generator_versions`, and `generated_at`

## Execution Topology

- Sequential core stages: ingest → field model → mechanics → strategy → simulation → validation
- Parallelism only inside stage-local work such as page extraction, table extraction, geometry checks, or seeded sim batches
- Validation is the only join gate that can mark a run as usable

## Provenance and Reproducibility

- Every generated artifact must include:
  - `source_manual_hash`
  - `source_field_drawing_hash`
  - `generator_name`
  - `generator_version`
  - `generated_at`
- Simulation outputs must include fixed seed values.
- Strategy claims must trace back to citations or explicitly labeled assumptions.

## Failure Boundaries

- Stage failures are isolated to stage-local output directories.
- Invalid cited extraction blocks downstream reasoning stages.
- Missing critical field geometry blocks simulation, but not the extraction report.
- Unstable simulation rankings must be reported as uncertainty, not promoted as recommendations.
