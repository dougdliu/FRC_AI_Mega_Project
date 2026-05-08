# FRC Megaproject

An AI-agent pipeline for FIRST Robotics Competition (FRC) game analysis, robot code generation, scouting, and simulation. Given a game manual PDF and a field-dimension drawing PDF, the pipeline extracts rules, builds field-layout references, models game mechanics, synthesizes strategy, generates WPILib robot code, runs Monte Carlo simulations, and produces a scouting app — all in a single orchestrated run.

The current strategy stack includes a TideSim-style 2D architecture search loop: human 3v3 playtests and RL/self-play sweeps can emit `sim_arch_feedback.json`, and that artifact feeds back into the manual insight analysis to rerank strategy candidates when the contract passes validation.

---

## Pipeline Overview

```
PDF Manual + Field Dimension Drawing
    └─> [1] PDF Rule Extraction        → rules.json
                                            field_layout_reference.json
                                            apriltag_field_layout.json
            ├─> [2a] Consolidated FRC Manual Insight Analysis  → strategy_hypotheses.md
            │                                                     manual_insight_packet.json
            │                                                     manual_insight_analysis.md
            └─> [2b] Game Mechanics Modeling                   → mechanics.json
                        ├─> [3] Monte Carlo Strategy Simulation → win-rate heatmaps
                        └─> [3] 2D Physics Simulation           → sim_params.json
                                                                  sim_arch_feedback.json

                        [3] WPILib Robot Code Generation        ← mechanics.json + strategy
                        [3] Power Usage Modeling                ← robot design + motor specs
                        [3] Scouting App Development            ← scoring/penalty rules

                        [4] AdvantageScope Log Integration      → .csv logs
                        [5] qa_validator gate
                        [6] Iterative Log-Assisted Dev          → code patches
                        [7] Strategy Feedback Loop              ← sim_arch_feedback.json
```

Full runtime target: ≤ 20 minutes (single game manual). Incremental rerun after a rule edit: ≤ 5 minutes.

---

## Agents

| Agent | Role |
|---|---|
| `pdf_extractor` | Parses game manual PDF → structured `rules.json` |
| `mechanic_analyst` | Models game as state machine + constraint graph → `mechanics.json` |
| `strategy_architect` | Synthesizes strategy candidates and simulation parameters |
| `robot_codegen` | Generates WPILib 2026 command-based project with vendor deps |
| `power_engineer` | Models current draw and battery sag → Streamlit dashboard + CSV |
| `scout_dev` | Builds scouting web/mobile app with SQLite backend and export API |
| `sim_engineer` | Builds a TideSim-style 2D simulation game for human and RL architecture search |
| `mc_simulator` | Runs 3v3 Monte Carlo alliance sweeps (10k+ runs) and architecture sensitivity cross-checks |
| `advscope_integrator` | Formats WPILib logs for AdvantageScope replay |
| `qa_validator` | Final release gate: rule compliance, security review, schema checks |

See [agents.md](agents.md) for full agent specifications, I/O contracts, and concurrency rules.

---

## MCP Servers

Eight MCP servers expose tools to the orchestrator over JSON-RPC 2.0 (stdio or HTTP):

| MCP | Purpose |
|---|---|
| `frc-rulebook-mcp` | Rule extraction and clause lookup |
| `wpilib-codegen-mcp` | WPILib scaffolding and validation |
| `electrical-power-mcp` | Current draw and battery modeling |
| `scouting-data-mcp` | Event logging and schema export |
| `physics-sim-mcp` | 2D field and kinematics simulation |
| `monte-carlo-strategy-mcp` | 3v3 alliance sweeps and win rates |
| `advscope-log-mcp` | WPILib log parsing and CSV export |
| `iterative-dev-mcp` | Log drift analysis and patch generation |

See [mcps.md](mcps.md) for full tool signatures, resource URIs, and transport specs.

---

## Skills

AI skills provide domain-specific instructions loaded at runtime by the relevant agent.

### Internal Skills
Defined in [skill.md](skill.md):
- PDF Rule Extraction
- Consolidated FRC Manual Insight Analysis
- Game Mechanics Modeling
- WPILib Robot Code Generation (WPILib 2026, AdvantageKit, CTRE Phoenix v6, PathplannerLib, photonlib)
- Power Usage Modeling
- Scouting App Development
- 2D Physics Simulation
- Monte Carlo Strategy Simulation
- AdvantageScope Log Integration
- Iterative Log-Assisted Dev

### Vendored External Skills
Downloaded and pinned in `skills/`:

| Skill | Path | Purpose |
|---|---|---|
| `anthropic/pdf` | `skills/anthropic/pdf/` | PDF extraction and generation |
| `anthropic/mcp-builder` | `skills/anthropic/mcp-builder/` | MCP server development workflow |
| `anthropic/claude-api` | `skills/anthropic/claude-api/` | Claude API, managed agents, model selection |
| `anthropic/xlsx` | `skills/anthropic/xlsx/` | Spreadsheet creation and analysis |
| `anthropic/webapp-testing` | `skills/anthropic/webapp-testing/` | Playwright-based web app testing |
| `anthropic/frontend-design` | `skills/anthropic/frontend-design/` | UI component design |
| `anthropic/doc-coauthoring` | `skills/anthropic/doc-coauthoring/` | Structured document drafting |
| `codex/jupyter-notebook` | `skills/codex/jupyter-notebook/` | Reproducible experiment notebooks |
| `codex/playwright` | `skills/codex/playwright/` | Playwright test generation |
| `codex/playwright-interactive` | `skills/codex/playwright-interactive/` | Interactive browser sessions |
| `codex/security-best-practices` | `skills/codex/security-best-practices/` | OWASP Top 10 review |
| `codex/security-threat-model` | `skills/codex/security-threat-model/` | Threat modeling |
| `codex/cli-creator` | `skills/codex/cli-creator/` | Pipeline CLI scaffolding |
| `codex/gh-fix-ci` | `skills/codex/gh-fix-ci/` | CI failure diagnosis |
| `codex/yeet` | `skills/codex/yeet/` | Artifact publishing to GitHub releases |
| `codex/screenshot` | `skills/codex/screenshot/` | UI screenshot capture |

---

## Canonical Artifacts

All outputs are versioned under `/artifacts/{game_year}/`:

| Artifact | Produced by | Consumed by |
|---|---|---|
| `rules.json` | `pdf_extractor` | `mechanic_analyst`, `strategy_architect` |
| `field_layout_reference.json` | `pdf_extractor` | `mechanic_analyst`, `sim_engineer`, `mc_simulator`, `robot_codegen` |
| `apriltag_field_layout.json` | `pdf_extractor` | `robot_codegen`, deployed WPILib project |
| `mechanics.json` | `mechanic_analyst` | `robot_codegen`, `sim_engineer`, `mc_simulator` |
| `manual_insight_packet.json` | `strategy_architect` | `robot_codegen`, `mc_simulator` |
| `manual_insight_analysis.md` | `strategy_architect` | Human review, design discussion, drive strategy |
| `strategy_hypotheses.md` | `strategy_architect` | `robot_codegen` |
| `sim_arch_feedback.json` | `sim_engineer`, RL/self-play runs | `strategy_architect`, `mc_simulator`, `qa_validator` |
| `power_budget.csv` | `power_engineer` | `qa_validator` |
| `sim_params.json` | `sim_engineer` | `mc_simulator` |
| `manifest.json` | Orchestrator | Release gate |
| `validation_report.json` | `qa_validator` | Release decision |

`/context/game_spec.json` is the single shared source of truth for game semantics across all parallel agents. Parallel agents must not write to it directly — all updates go through the orchestrator merge step.

## Required Inputs

Each game year requires both source PDFs under `inputs/`:
- game manual PDF
- field-dimension drawing PDF

For example, 2026 uses:
- `inputs/2026GameManual.pdf`
- `inputs/2026-field-dimension-dwgs.pdf`

The field-dimension drawing is treated as a required peer input because it feeds:
- 2D sim field geometry
- AprilTagFieldLayout JSON generation for WPILib deploy assets
- cycle-time distance analysis for intake, transit, and scoring paths

Artifact details:
- `apriltag_field_layout.json` now uses the WPILib `AprilTagFieldLayout` JSON schema: top-level `tags[]` plus `field.length` and `field.width`.
- `field_layout_reference.json` also records blue-side and red-side reference frames so downstream sim/codegen stages can mirror coordinates consistently.
- Generated WPILib projects now include `src/main/java/frc/robot/FieldConstants.java` to centralize deploy-first AprilTag layout loading and alliance mirroring helpers.

If `sim_arch_feedback.json` is missing, the pipeline bootstrap regenerates a template artifact automatically. If a non-template artifact is present, the pipeline validates allowed enums and numeric bounds before using it to rerank strategy candidates.

## Architecture Feedback Contract

`sim_arch_feedback.json` must contain:
- `run_id`, `generated_at`, `source`, `robot_params`, `kpis`, `recommended_strategy_updates`, `confidence`

Allowed enums:
- `source`: `human_playtest`, `rl_self_play`, `hybrid`
- `confidence`: `low`, `medium`, `high`

Bounded numeric fields:
- Robot params: `drive_free_speed_fps` 0-30, `drive_time_to_full_speed_s` 0-10, `intake_rate_pieces_per_s` 0-30, `storage_capacity_assumed` 0-100, `score_rate_pieces_per_s` 0-30
- KPIs: `match_points` 0-500, `value_per_second` 0-10, `foul_points_conceded` 0-200, `tower_success_rate` 0-1, `defense_sensitivity` 0-1, `alliance_dependency_score` 0-1
- Strategy update delta: `delta_expected_value` -100 to 100

---

## Release Gate (qa_validator)

A release requires:
- Rule extraction recall ≥ 98% for required sections (objectives, timing, scoring, penalties, field)
- Both required source PDFs are present and recorded in the manifest (`source_manual`, `source_field_drawing`)
- Every downstream recommendation includes at least one source citation (`section_id`, `page`)
- Reproducible simulation outputs with pinned random seeds
- Zero critical rule compliance failures in `validation_report.json`
- Invalid non-template `sim_arch_feedback.json` artifacts fail the `architecture_feedback_contract` gate and block release
- OWASP security review passes for MCP HTTP endpoints, scouting API, and PDF ingest pipeline

---

## Documentation

| File | Contents |
|---|---|
| [agents.md](agents.md) | Agent registry, I/O contracts, concurrency rules |
| [skill.md](skill.md) | All skill definitions, dependency graph, external skill registry |
| [mcps.md](mcps.md) | MCP server specifications, tool signatures, build workflow |
| [pipeline_overview.md](pipeline_overview.md) | Pipeline stages, success criteria, runtime targets |
| [data_flow_and_architecture.md](data_flow_and_architecture.md) | Data flow diagrams and architecture decisions |
| [iterationand_validation.md](iterationand_validation.md) | Iteration loop, validation protocol, retry policy |
