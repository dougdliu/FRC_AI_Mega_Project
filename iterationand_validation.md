# Iteration & Validation Protocol
- **Trigger:** New robot logs, sim drift, or manual override
- **Process:**
  1. Parse logs → identify bottleneck (e.g., PID overshoot, scoring miss)
  2. Cross-reference with sim bounds
  3. Generate targeted patch (PID tune, command refactor, strategy shift)
  4. Run regression sim (1k runs)
  5. If Δperformance > threshold → commit patch
  6. Log changes to `/artifacts/{game_year}/iteration_log.json`
- **Validation Gates:**
  - Rule compliance (FRC manual)
  - Electrical limits (12V, thermal)
  - Physics bounds (speed, acceleration, collision)
  - Simulation convergence (MC confidence >95%)
- **Fallback:** Any gate failure → `qa_validator` → rework → max 3 retries

## Quantitative Thresholds
- PID improvement candidate accepted only if:
  - settling time improves by >= 10%, or
  - overshoot decreases by >= 15%.
- Strategy patch accepted only if regression sim shows:
  - win-rate increase >= 2.0 points, or
  - equivalent win-rate with lower penalty risk.
- Monte Carlo confidence interval width must be <= 0.03 for reported top strategies.

## Defect Triage Levels
- Critical: rule compliance violations, unsafe electrical outputs, corrupted artifacts.
- Major: performance regression beyond threshold, invalid simulation assumptions.
- Minor: formatting, non-blocking UX issues, weak recommendation confidence.

## Validation Output Contract
- Required output file: `/artifacts/{game_year}/validation_report.json`
- Required fields:
  - `run_id`
  - `gates` (pass/fail per gate)
  - `defects` (with severity)
  - `recommended_actions`
  - `release_decision`
