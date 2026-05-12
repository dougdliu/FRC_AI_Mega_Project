# Iteration & Validation Protocol

- **Trigger:** new manual version, new field drawing, Team Update, Q&A clarification, changed extraction confidence, changed strategy assumptions, or unstable simulation result
- **Process:**
  1. Re-extract changed rule or field sections.
  2. Regenerate `field_model.json` if drawing-derived geometry changed.
  3. Regenerate `mechanics.json` and `strategy_packet.json`.
  4. Re-run simulation scenarios affected by the changed assumptions.
  5. Run validation gates.
  6. Record the result in `/artifacts/{game_year}/run_history.json` and `validation_report.json`.
- **Fallback:** any failed gate returns a concrete rework directive and blocks downstream promotion

## Validation Gates

- Rule coverage for scoring, timing, penalties, field zones, and robot constraints
- Field geometry coverage for dimensions, zones, scoring locations, game-piece locations, and alliance reference frames where available
- Citation coverage for every extracted rule and every strategic recommendation
- Schema validation for all JSON artifacts
- Simulation reproducibility under fixed seeds
- Simulation assumption transparency in `simulation_report.md`

## Quantitative Thresholds

- Every scoring action, penalty, phase boundary, protected zone, and major robot constraint in `rules.json` must carry a citation.
- Field dimensions used in simulation must trace to `field_layout_reference.json` or be explicitly labeled as assumptions.
- A strategy recommendation is acceptable only if the top role ordering is stable across at least 3 fixed seeds or a deterministic sweep.
- A simulation finding should be labeled `low confidence` if sensitivity sweeps change the top recommendation under plausible cycle-time ranges.
- Validation must fail fast on missing citations, broken artifact schemas, or impossible mechanics constraints.

## Defect Triage Levels

- Critical: uncited or contradictory rule extraction, corrupted artifacts, impossible mechanics, unusable field model
- Major: unstable strategy ranking, missing simulation assumptions, important field geometry gaps
- Minor: formatting issues, weak confidence labels, non-blocking documentation drift

## Validation Output Contract

- Required output file: `/artifacts/{game_year}/validation_report.json`
- Required fields:
  - `run_id`
  - `gates`
  - `defects`
  - `recommended_actions`
  - `release_decision`
  - `extraction_status`
  - `field_model_status`
  - `strategy_status`
  - `simulation_status`

## Robot Handoff Boundary

If humans decide to proceed to robot implementation, the analysis pipeline should hand off `team_decision_packet.md`, `strategy_packet.json`, `field_model.json`, and `mechanics.json` to a separate robot-design workflow. That workflow must collect team-specific robot design inputs before any robot code is generated.
