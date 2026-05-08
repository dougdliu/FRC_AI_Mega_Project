# System Architecture
- **Input:** `/inputs/{game_year}.pdf`
- **Context:** `/context/game_spec.json` (shared state)
- **Artifacts:** `/artifacts/{game_year}/`
  - `rules.json`, `mechanics.json`, `strategy.md`
  - `wpilib_project/`, `power_app/`, `scouting_app/`
  - `sim_2d/`, `mc_results/`, `logs/`
- **Orchestrator:** LangGraph/CrewAI state machine
- **Storage:** SQLite for scouting, CSV for logs, JSON for specs
- **Compute:** Local GPU for sim/MC, CPU for parsing/generation
- **APIs:** FastAPI for app serving, Streamlit for dashboards

## Data Contracts
- `rules.json`
  - Required keys: `sections`, `scoring`, `penalties`, `field`, `timing`.
  - Required citation fields per clause: `section_id`, `page`, `clause_text`.
- `mechanics.json`
  - Required keys: `states`, `transitions`, `resource_constraints`, `win_conditions`.
  - Each transition must map to one or more rule citations.
- `game_spec.json`
  - Merges normalized rules, mechanics, and strategy constraints.
  - Contains `schema_version`, `generator_versions`, and `created_at`.

## Execution Topology
- Sequential stages: ingest, model, plan.
- Parallel stages: robot codegen, power modeling, scouting app, physics sim, Monte Carlo.
- Join stage: integration plus validation.

## Provenance and Reproducibility
- Every generated artifact must include:
  - `source_manual_hash`
  - `generator_name`
  - `generator_version`
  - `timestamp_utc`
- Simulation and Monte Carlo outputs must include random seed values.

## Failure Boundaries
- Stage failures are isolated to stage-local output directories.
- Upstream artifacts remain immutable once marked validated.
- Retry count and failure reason are appended to `/artifacts/{game_year}/run_history.json`.
