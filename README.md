# FRC Megaproject

An AI-agent pipeline for FIRST Robotics Competition (FRC) game analysis, robot code generation, scouting, and simulation. Given a game manual PDF, the pipeline extracts rules, models game mechanics, synthesizes strategy, generates WPILib robot code, runs Monte Carlo simulations, and produces a scouting app — all in a single orchestrated run.

---

## Pipeline Overview

```
PDF Manual
    └─> [1] PDF Rule Extraction        → rules.json
            ├─> [2a] Consolidated FRC Manual Insight Analysis  → strategy_hypotheses.md
            │                                                     manual_insight_packet.json
            └─> [2b] Game Mechanics Modeling                   → mechanics.json
                        ├─> [3] Monte Carlo Strategy Simulation → win-rate heatmaps
                        └─> [3] 2D Physics Simulation           → sim_params.json

                        [3] WPILib Robot Code Generation        ← mechanics.json + strategy
                        [3] Power Usage Modeling                ← robot design + motor specs
                        [3] Scouting App Development            ← scoring/penalty rules

                        [4] AdvantageScope Log Integration      → .csv logs
                        [5] qa_validator gate
                        [6] Iterative Log-Assisted Dev          → code patches
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
| `sim_engineer` | Builds interactive 2D physics simulation canvas |
| `mc_simulator` | Runs 3v3 Monte Carlo alliance sweeps (10k+ runs) |
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
| `mechanics.json` | `mechanic_analyst` | `robot_codegen`, `sim_engineer`, `mc_simulator` |
| `manual_insight_packet.json` | `strategy_architect` | `robot_codegen`, `mc_simulator` |
| `strategy_hypotheses.md` | `strategy_architect` | `robot_codegen` |
| `power_budget.csv` | `power_engineer` | `qa_validator` |
| `sim_params.json` | `sim_engineer` | `mc_simulator` |
| `manifest.json` | Orchestrator | Release gate |
| `validation_report.json` | `qa_validator` | Release decision |

`/context/game_spec.json` is the single shared source of truth for game semantics across all parallel agents. Parallel agents must not write to it directly — all updates go through the orchestrator merge step.

---

## Release Gate (qa_validator)

A release requires:
- Rule extraction recall ≥ 98% for required sections (objectives, timing, scoring, penalties, field)
- Every downstream recommendation includes at least one source citation (`section_id`, `page`)
- Reproducible simulation outputs with pinned random seeds
- Zero critical rule compliance failures in `validation_report.json`
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
