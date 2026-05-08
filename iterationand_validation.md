# Iteration & Validation Protocol
- **Trigger:** New robot logs, sim drift, manual override, or new architecture-search output (`sim_arch_feedback.json`).
- **Process:**
  1. Parse logs → identify bottleneck (e.g., PID overshoot, scoring miss)
  2. Cross-reference with sim bounds
  3. Generate targeted patch (PID tune, command refactor, strategy shift)
  4. Run regression sim (1k runs)
  5. If Δperformance > threshold → commit patch
  6. Log changes to `/artifacts/{game_year}/iteration_log.json`
  7. Re-run manual insight analysis if architecture rankings changed materially
- **Validation Gates:**
  - Rule compliance (FRC manual)
  - Electrical limits (12V, thermal)
  - Physics bounds (speed, acceleration, collision)
  - Simulation convergence (MC confidence >95%)
  - Architecture feedback contract validity (required before reranking when non-template `sim_arch_feedback.json` is present)
- **Fallback:** Any gate failure → `qa_validator` → rework → max 3 retries

## Quantitative Thresholds
- PID improvement candidate accepted only if:
  - settling time improves by >= 10%, or
  - overshoot decreases by >= 15%.
- Strategy patch accepted only if regression sim shows:
  - win-rate increase >= 2.0 points, or
  - equivalent win-rate with lower penalty risk.
- Monte Carlo confidence interval width must be <= 0.03 for reported top strategies.
- Architecture promotion accepted only if candidate improves risk-weighted value by >= 5% across at least 3 seeds or >= 50 human-play matches.
- Invalid architecture feedback artifacts fail fast when enum values or KPI/robot-parameter numeric bounds are violated.

## Defect Triage Levels
- Critical: rule compliance violations, unsafe electrical outputs, corrupted artifacts.
- Critical: invalid `sim_arch_feedback.json` contract when a non-template artifact is supplied.
- Major: performance regression beyond threshold, invalid simulation assumptions.
- Major: architecture recommendation not reproducible across seeds or human-play cohorts.
- Minor: formatting, non-blocking UX issues, weak recommendation confidence.

## Validation Output Contract
- Required output file: `/artifacts/{game_year}/validation_report.json`
- Required fields:
  - `run_id`
  - `gates` (pass/fail per gate)
  - `defects` (with severity)
  - `recommended_actions`
  - `release_decision`
  - `architecture_feedback_status` (ingested/skipped/invalid)
  - `architecture_feedback_issues[]` (contract validation failures, if any)
