# AI Skill Definitions

## Scope Note

The core project focuses on understanding the game before robot implementation.

Core skills should support:
- cited manual extraction
- field drawing extraction and field modeling
- game mechanics modeling
- strategic analysis and team decision support
- rough game and strategy simulation
- validation and iteration

Robot code generation is a later, human-gated workflow after the team selects tasks and provides robot design inputs.

## Cross-Skill Standards

- Every skill output must include metadata:
  - `schema_version`
  - `generated_at`
  - `source_manual_hash`
  - `source_field_drawing_hash` when field data is used
  - input hashes for any non-manual artifacts it depends on
- Every recommendation-producing skill must include source citations.
- Confidence labels are required for extracted or inferred content:
  - `high`: direct rule or drawing match
  - `medium`: derived from nearby cited context
  - `low`: ambiguous, conflicting, OCR-uncertain, or drawing-uncertain

## Shared Acceptance Checks

- JSON outputs pass schema validation.
- No unresolved placeholders remain in generated Markdown or JSON.
- Artifact paths are deterministic for reproducible reruns.
- Validation logs are written under `/artifacts/{game_year}/validation/`.

## Skill: PDF Rule Extraction

- **Intent:** Convert the game manual into machine-readable cited rules. Extraction only; no strategy recommendations.
- **Input:** game manual PDF, optional Team Updates or Q&A addenda
- **Output:** `rules.json`, `extraction_report.md`
- **Output contract:**
  - `sections[]`: `id`, `title`, `text`, `page`
  - `scoring[]`: `action`, `points`, `phase`, `constraints`, `citations[]`
  - `penalties[]`: `rule_id`, `type`, `points`, `citations[]`
  - `field_rules`: field-related manual clauses
  - `timing`: `auto_s`, `teleop_s`, `endgame_s`
  - `equipment_limits`: `weight`, `frame`, `extension`, `height`
- **Validation:** all key rule sections present; every extracted rule carries page and section citation; low-confidence clauses are listed in `extraction_report.md`

## Skill: Field Drawing Extraction and Modeling

- **Intent:** Convert field drawings into traceable references and a normalized simulation-ready field model.
- **Input:** field-dimension drawing PDF, `rules.json`
- **Output:** `field_layout_reference.json`, `field_model.json`, `apriltag_field_layout.json` when applicable
- **Output contract:**
  - `field_layout_reference.json`: raw dimension tokens, page references, drawing labels, confidence notes
  - `field_model.json`: field dimensions, reference frames, zones, scoring locations, game-piece starts, obstacles
  - `apriltag_field_layout.json`: WPILib-compatible tag layout when source drawings define tags
- **Validation:** normalized coordinates are traceable to drawing references; unresolved geometry is flagged explicitly

## Skill: Game Mechanics Modeling

- **Intent:** Turn `rules.json` and `field_model.json` into a simulation-ready mechanics model.
- **Input:** `rules.json`, `field_model.json`
- **Output:** `mechanics.json`
- **Output contract:**
  - `states[]`: `id`, `phase`, `entry_conditions`, `exit_conditions`
  - `transitions[]`: `from_state`, `to_state`, `action`, `duration_s`, `point_delta`, `citations[]`
  - `resource_constraints[]`
  - `phase_limits`
  - `scoring_model`
  - `penalty_model`
- **Validation:** all scoring actions appear as transitions; time limits are enforced; contradictions are surfaced rather than guessed away

## Skill: Strategy Synthesis

- **Intent:** Produce a concise, cited explanation of the game, likely task priorities, and the decisions humans must make before robot design.
- **Input:** `rules.json`, `field_model.json`, `mechanics.json`
- **Output:** `strategy_packet.json`, `strategy_brief.md`, `team_decision_packet.md`
- **Output contract:**
  - `game_summary`
  - `task_candidates[]`
  - `role_candidates[]`
  - `scoring_priorities[]`
  - `cycle_assumptions[]`
  - `risk_notes[]`
  - `open_questions[]`
  - `team_decisions_needed[]`
- **Validation:** every recommendation has a citation; contradictions are listed in `open_questions`; at least a safe, balanced, and high-upside strategy set is considered

## Skill: Game and Strategy Simulation

- **Intent:** Compare rough strategy choices and task priorities without requiring a chosen team robot design.
- **Input:** `field_model.json`, `mechanics.json`, `strategy_packet.json`
- **Output:** `simulation_model.json`, `sim_params.json`, `sim_summary.json`, `simulation_report.md`
- **Approach:** discrete-event or simple top-down simulation with abstract capability profiles, fixed seeds, and clear assumptions
- **Validation:** outputs are reproducible under fixed seeds; assumptions and warnings are surfaced explicitly; relative rankings are more important than absolute score accuracy

## Skill: Validation and Iteration

- **Intent:** Decide whether an analysis run is usable and identify the next fix when it is not.
- **Input:** all generated core artifacts
- **Output:** `validation_report.json`, optional fix directives
- **Validation gates:** citation coverage, field-model consistency, schema health, mechanics validity, simulation reproducibility, strategy traceability

## Human-Gated Future Skills

These are intentionally outside the core analysis pipeline:
- WPILib Robot Code Generation
- Maple-Sim Robot Validation
- Power Usage Modeling
- Scouting App Development
- AdvantageScope Log Integration
- RL or self-play architecture search
- Release automation and artifact publishing

## Skill Dependency Graph

```
[anthropic/pdf]
    -> PDF Rule Extraction
    -> Field Drawing Extraction and Modeling
        -> Game Mechanics Modeling
            -> Strategy Synthesis
                -> Game and Strategy Simulation
                    -> Validation and Iteration
```

## External Skill Notes

- Load `anthropic/pdf` before reading or generating PDFs.
- Load `anthropic/mcp-builder` only when a local module is mature enough to extract into an MCP.
- Load `karpathy/claude` as a general implementation discipline layer.
- No robot-codegen skill is required to reach the core analysis MVP.
