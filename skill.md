# AI Skill Definitions

## Cross-Skill Standards
- Every skill output must include metadata:
	- `schema_version`
	- `generated_at`
	- `source_manual_hash`
- Every recommendation-producing skill must include source citations.
- Confidence labels are required for extracted or inferred content:
	- `high`: direct rule text match
	- `medium`: derived from tightly related context
	- `low`: ambiguous, conflicting, or OCR-uncertain

## Shared Acceptance Checks
- JSON outputs pass schema validation.
- No unresolved placeholders remain in generated code or markdown.
- Artifact paths are deterministic for reproducible reruns.
- Validation logs are written to `/artifacts/{game_year}/validation/`.

## Skill: Consolidated FRC Manual Insight Analysis
- **Intent:** Use a single worksheet-driven framework (derived from Team 2791/6328 and FIRST kickoff worksheets) to convert manual text into actionable game insights. Operates as the strategic reasoning layer above PDF Rule Extraction — prefer consuming `rules.json` when available to avoid redundant PDF parsing; fall back to raw PDF only when `rules.json` has not yet been produced.
- **Input:** `rules.json` (preferred, from PDF Rule Extraction) or game manual PDF (fallback). Team Updates and Q&A clarifications as supplemental inputs. Optional historical game analogs.
- **Output:**
	- `manual_insight_packet.json`
	- `strategy_hypotheses.md`
	- `risk_register.json`
	- `citation_index.json`
- **Primary Sections (in required order):**
	1. Match periods and phase changes (auto, teleop, endgame)
	2. Field zones, field elements, and protected areas
	3. Game pieces (types, possession limits, recycling loops)
	4. Scoring map (all scoring actions, values, constraints)
	5. Penalties and foul economics (offset cost to recover)
	6. Ranking points and tie-breaker mechanics
	7. Robot constraints (weight, frame, extension, height windows)
	8. Strategy synthesis (auto, teleop, endgame, tournament)
	9. Chokehold and counter-strategy analysis
	10. Theoretical maxima (single robot and full alliance by phase)
- **Question Matrix (must be answered with citations):**
	- What are the highest leverage scoring actions per phase?
	- Which actions are cooperative versus independent in 3v3 play?
	- Which field locations create defensive choke points or safe scoring lanes?
	- Which penalties are most likely and most expensive in expected points?
	- Which RP paths are solo-capable versus alliance-dependent?
	- Which minimum robot capabilities maximize pick probability?
- **Scoring Insight Rubric:**
	- `value_per_second`: expected points contribution divided by cycle time
	- `risk_weighted_value`: value adjusted by foul likelihood and execution variance
	- `alliance_dependency_score`: how much success depends on partners
	- `defense_sensitivity`: performance drop under moderate defense
	- `implementation_complexity`: mechanism + software + driver training effort
- **Output Contract (minimum keys):**
	- `metadata`: `game_year`, `manual_version`, `schema_version`, `generated_at`
	- `phase_model`: `auto`, `teleop`, `endgame`
	- `scoring_actions[]`: `id`, `phase`, `base_points`, `constraints`, `citations[]`
	- `penalty_model[]`: `rule_id`, `cost_estimate`, `common_trigger`, `avoidance_guidance`
	- `rp_model[]`: `rp_name`, `requirements`, `solo_feasibility`, `alliance_dependencies`
	- `strategy_candidates[]`: `name`, `assumptions`, `expected_value`, `key_risks`
	- `open_questions[]`: unresolved ambiguities requiring Q&A follow-up
- **Validation:**
	- No section may be left empty.
	- Every strategic claim must map to at least one citation.
	- Contradictions across rule sections must be flagged in `open_questions`.
	- At least three distinct strategy candidates must be generated (safe/balanced/high-upside).
- **Prompt Template:** `Analyze the FRC game manual using the consolidated kickoff worksheet framework. Extract rule-grounded facts first, then derive strategy insights for auto, teleop, endgame, ranking, and playoff outcomes. Return the output contract fields exactly, include citations for every non-trivial claim, and flag ambiguities for Q&A follow-up.`
- **Reference Basis:**
	- Team 6328 kickoff worksheet (Chief Delphi PDF)
	- Team 2791 kickoff worksheet (Chief Delphi PDF)
	- FIRST Kickoff Worksheet (rev Sep 2025 PDF)
	- FIRST Kickoff Breakdown Worksheet (PDF)

## Skill: PDF Rule Extraction
- **Role:** Structured extraction foundation. Produces the machine-readable `rules.json` that both **Consolidated FRC Manual Insight Analysis** (strategic reasoning) and **Game Mechanics Modeling** (computational modeling) consume. Does not perform strategic analysis — extraction only.
- **Input:** Game manual PDF (via `anthropic/pdf` skill). Also accepts Team Updates and Q&A addenda as supplemental PDFs to merge into output.
- **Output:** `rules.json` (sections, clauses, constraints, scoring, field dimensions, time limits, equipment limits)
- **Output Contract (minimum keys):** `sections[]` (id, title, text, page), `scoring[]` (action, points, phase, constraints), `penalties[]` (rule_id, type, points), `field` (dimensions, zones, elements), `timing` (auto_s, teleop_s, endgame_s), `equipment_limits` (weight, frame, extension, height), `citations[]` (page, section_id, raw_text)
- **Tools:** `anthropic/pdf` (primary extraction), `pymupdf`, regex clause parser, table extractor
- **Downstream consumers:** `Consolidated FRC Manual Insight Analysis` (preferred input over raw PDF), `Game Mechanics Modeling`
- **Validation:** All game objectives, time limits, penalties, and field measurements present. Every extracted clause must include page and section citation. Cross-check section count against PDF table of contents.
- **Prompt Template:** `Extract all game rules, constraints, scoring, penalties, field dimensions, and time limits from the PDF. Output rules.json with sections: {sections, scoring, penalties, field, timing, equipment_limits, citations}. Do not interpret or analyze — extract only.`

## Skill: Game Mechanics Modeling
- **Role:** Formal computational modeling layer. Converts `rules.json` into a machine-executable model (state machine + constraint graph) that the Monte Carlo simulator and 2D physics sim consume directly. Distinct from **Consolidated FRC Manual Insight Analysis**, which produces human-readable strategic outputs — this skill produces simulation-ready data structures.
- **Input:** `rules.json` (from PDF Rule Extraction). Also accepts `manual_insight_packet.json` as a supplemental cross-reference to flag modeling gaps.
- **Output:** `mechanics.json` (state machine, resource flow graph, win conditions, physics constraints)
- **Output Contract (minimum keys):** `states[]` (id, phase, entry_conditions, exit_conditions), `transitions[]` (from_state, to_state, action, duration_s, point_delta), `resource_constraints[]` (resource, limit, scope), `win_conditions[]` (condition, tiebreaker_order), `physics` (max_speed_mps, max_accel_mps2, robot_footprint_m)
- **Tools:** `networkx` (constraint graph), constraint solver, `sympy` (physics bounds)
- **Downstream consumers:** `Monte Carlo Strategy Simulation`, `2D Physics Simulation`, `WPILib Robot Code Generation`
- **Validation:** No circular state transitions. All scoring actions from `rules.json` must appear as transitions. All time limits enforced as state duration bounds. Resource constraints must be satisfiable (constraint solver passes). Cross-check `win_conditions` against `rp_model` in `manual_insight_packet.json` if available.
- **Prompt Template:** `Convert rules.json into a formal game mechanics model. Build a state machine with states for each game phase and transitions for each scoring action. Model resource constraints and win conditions as computable constraints. Output mechanics.json — do not include strategy recommendations; this is a simulation input, not a strategy document.`

## Skill: WPILib Robot Code Generation
- **Input:** `mechanics.json`, `strategy.md`
- **Output:** WPILib project (Java/C++), subsystems, commands, PID configs
- **Tools:** `jinja2`, wpilib-template, motor/sensor DB
- **WPILib Version:** WPILib 2026 (current year). Always target the latest 2026 release. Verify the exact version at https://github.com/wpilibsuite/allwpilib/releases before generating code; do not hardcode a patch version.
- **Vendor Dependencies (always include, always use latest stable release):**
  - **AdvantageKit** — Verify latest at https://github.com/Mechanical-Advantage/AdvantageKit/releases. Add the vendordep JSON from the AdvantageKit release assets. Use `@AutoLog` annotations on all subsystems.
  - **CTRE Phoenix v6** — Verify latest at https://maven.ctr-electronics.com/release/com/ctre/phoenix6/tools/. Use the v6 API (`TalonFX`, `CANcoder`, `Pigeon2`); never generate v5 (`com.ctre.phoenix`) imports.
  - **PathplannerLib** — Verify latest at https://github.com/mjansen4857/pathplanner/releases. Use `AutoBuilder.configure()` for auto routine wiring and `PathPlannerPath.fromPathFile()` for named paths.
  - **photonlib** — Verify latest at https://github.com/PhotonVision/photonvision/releases. Use `PhotonCamera` and `PhotonPoseEstimator` for vision-assisted odometry.
  - **WPILib New Commands** — Included in WPILib 2026 core (`edu.wpi.first.wpilibj2.command`). Do not add as a separate vendordep; confirm the `commands` artifact is present in `build.gradle` / `build.json`.
- **Vendordep fetch rule:** Before writing any vendordep JSON inline, fetch the `.json` URL from the library's official release page to get the exact artifact version string. Never construct version strings from memory.
- **Validation:** Compiles with WPILib 2026, uses command-based paradigm, includes all required subsystems. Reject output that imports deprecated or pre-2026 APIs. All five vendor libraries must be present in the generated project's vendordeps or `build.gradle` dependencies block.
- **Prompt Template:** `Generate a command-based WPILib project targeting WPILib 2026 with vendor dependencies: AdvantageKit (latest), CTRE Phoenix v6 (latest), PathplannerLib (latest), photonlib (latest), WPILib New Commands (2026 core). Include subsystems for {intake, drive, scoring, lifting}. Add PID configs, auto routines using PathplannerLib, and teleop commands. Follow WPILib 2026 standards and API conventions.`

## Skill: Power Usage Modeling
- **Input:** Robot design, motor specs, duty cycles
- **Output:** Web app (Streamlit) + `power_budget.csv`
- **Tools:** `pandas`, motor curve DB, thermal model
- **Validation:** Peak/avg current within 12V battery limits, thermal warnings at >80% duty, matches FRC electrical guidelines.
- **Prompt Template:** `Estimate current draw per subsystem under {scoring, intake, travel}. Generate power_budget.csv and a Streamlit dashboard showing real-time draw vs battery voltage.`

## Skill: Scouting App Development
- **Input:** Scoring/penalty rules, alliance structure
- **Output:** Web/mobile app, SQLite schema, export API
- **Tools:** `streamlit`/`flutter`, `fastapi`, `sqlite`
- **Validation:** Captures all scoring events, penalties, alliance roles, and match outcomes. Exportable to JSON/CSV.
- **Prompt Template:** `Build a scouting app that logs {scoring_types, penalties, alliance_role, match_number}. Include offline mode, auto-export, and validation rules.`

## Skill: 2D Physics Simulation
- **Input:** Robot params (size, speed, physics, intake width, scoring type)
- **Output:** Interactive canvas sim + `sim_params.json`
- **Tools:** `pygame`/`p5.js`, `box2d`, kinematics solver
- **Validation:** Collision detection accurate, speed/acceleration match specs, parameter sliders update in real-time.
- **Prompt Template:** `Create a 2D sim where robots match {size, speed, intake_width}. Implement scoring, collisions, and field boundaries. Output parameter JSON and interactive canvas.`

## Skill: Monte Carlo Strategy Simulation
- **Input:** 3v3 alliance configs, robot params, game rules
- **Output:** Win-rate heatmap, strategy recommendations
- **Tools:** `ray`, `numpy`, `seaborn`
- **Validation:** 10k+ runs per config, confidence intervals reported, matches FRC alliance dynamics.
- **Prompt Template:** `Run 3v3 Monte Carlo simulations for {alliance_compositions}. Sweep robot parameters. Output win-rate heatmaps and top strategies.`

## Skill: AdvantageScope Log Integration
- **Input:** WPILib code, simulation outputs
- **Output:** `.csv` logs, AdvantageScope config
- **Tools:** `wpilib-log`, `csvkit`
- **Validation:** Matches WPILib log schema, replayable in AdvantageScope, includes trajectory/telemetry.
- **Prompt Template:** `Generate WPILib-compliant logs for {drive, scoring, lifting}. Output CSVs and AdvantageScope config for trajectory replay.`

## Skill: Iterative Log-Assisted Dev
- **Input:** Robot logs, simulation results, performance gaps
- **Output:** Code patches, PID retuning, strategy adjustments
- **Tools:** `pandas`, `scipy.optimize`, diff generator
- **Validation:** Fixes address root causes, regression tests pass, matches logged telemetry.
- **Prompt Template:** `Analyze logs for {drift, lag, missed scoring}. Generate targeted code patches and PID retuning. Validate against simulation bounds.`

## Skill Dependency Graph

### Core Pipeline Flow (internal skills)
```
[anthropic/pdf]
    └─> PDF Rule Extraction  ──────────────────────────────────┐
            │                                                   │
            ├─> Consolidated FRC Manual Insight Analysis        │  (strategic reasoning layer)
            │       ──[anthropic/doc-coauthoring]               │
            │       └─> strategy_hypotheses.md                  │
            │               └─> WPILib Robot Code Generation ◄──┤
            │                       ──[anthropic/claude-api]    │
            │                                                   │
            └─> Game Mechanics Modeling  ◄─────────────────────┘  (computational modeling layer)
                    ──[codex/jupyter-notebook]
                    └─> mechanics.json
                            ├─> Monte Carlo Strategy Simulation  ──[codex/jupyter-notebook]
                            │       └─> win-rate heatmaps + strategy recommendations
                            │               └─> WPILib Robot Code Generation
                            │                       └─> AdvantageScope Log Integration
                            │                               └─> Iterative Log-Assisted Dev
                            └─> 2D Physics Simulation  ──[codex/screenshot]
                                    └─> AdvantageScope Log Integration

[anthropic/xlsx]
    └─> Power Usage Modeling  (parallel with robot_codegen)

[anthropic/frontend-design] + [anthropic/webapp-testing] + [codex/playwright] + [codex/playwright-interactive]
    └─> Scouting App Development  (parallel with robot_codegen)

[codex/screenshot]
    └─> 2D Physics Simulation  ──> AdvantageScope Log Integration

[codex/security-best-practices] + [codex/security-threat-model]
    └─> qa_validator  (gate over all parallel outputs)

[codex/cli-creator]
    └─> Pipeline CLI  (wraps orchestrator entry point)

[anthropic/mcp-builder]  (used when implementing any of the 8 MCPs in mcps.md)
    └─> all pipeline stages that call MCP servers

[codex/gh-fix-ci]
    └─> CI automation  (post-pipeline)

[codex/yeet]
    └─> Artifact release  (post-qa_validator)
```

### Dependency Rules
- `anthropic/pdf` must be loaded before any PDF is read or written.
- **PDF Rule Extraction runs first** and its `rules.json` is the shared input to both Consolidated FRC Manual Insight Analysis and Game Mechanics Modeling. Neither downstream skill should re-parse the raw PDF if `rules.json` exists.
- **Consolidated FRC Manual Insight Analysis** and **Game Mechanics Modeling** are parallel consumers of `rules.json` — they run concurrently after extraction completes. Consolidated produces strategic outputs (human-readable); Game Mechanics produces simulation inputs (machine-readable).
- `manual_insight_packet.json` (from Consolidated) may be passed to Game Mechanics Modeling as a cross-reference to catch modeling gaps, but is not required.
- `anthropic/mcp-builder` (+ relevant reference file) must be loaded before implementing any MCP server.
- `anthropic/claude-api` must be loaded before any agent writes code that calls Claude.
- `codex/security-best-practices` and `codex/security-threat-model` must both be loaded at the `qa_validator` gate.
- `codex/jupyter-notebook` is shared between `mechanic_analyst` and `mc_simulator`; both use `experiment` mode notebooks.
- External Tier 2 skills (`webapp-testing`, `playwright`, `playwright-interactive`, `screenshot`, `doc-coauthoring`, `frontend-design`) are loaded only when their owning agent is active.
- `codex/yeet` and `codex/gh-fix-ci` are post-pipeline; they do not block any upstream stage.

---

## External Skill Registry

External skills pulled from community repositories and vendored into `skills/`. Each entry lists the local path, source, pipeline stage, and activation instructions.

### Tier 1 — Core Pipeline Skills (always available)

#### `anthropic/pdf` — PDF Extraction & Generation
- **Local path:** `skills/anthropic/pdf/SKILL.md`
- **Source:** https://github.com/anthropics/skills/tree/main/skills/pdf
- **Pipeline stage:** Stage 1: Ingest / `pdf_extractor` agent / `frc-rulebook-mcp`
- **Activation:** Load `skills/anthropic/pdf/SKILL.md` when the agent reads or generates a PDF. Provides `pdfplumber`-based extraction, table detection, OCR fallback via `pytesseract`, and PDF report generation via `reportlab`.
- **Key tools:** `pdfplumber`, `pymupdf`, `pytesseract`, `reportlab`

#### `anthropic/mcp-builder` — MCP Server Development
- **Local path:** `skills/anthropic/mcp-builder/SKILL.md`
- **Reference files:** `skills/anthropic/mcp-builder/reference/` (`mcp_best_practices.md`, `python_mcp_server.md`, `node_mcp_server.md`, `evaluation.md`)
- **Source:** https://github.com/anthropics/skills/tree/main/skills/mcp-builder
- **Pipeline stage:** All MCP servers in `mcps.md`
- **Activation:** Load `skills/anthropic/mcp-builder/SKILL.md` + the relevant reference file when implementing any MCP server. Python: load `reference/python_mcp_server.md`. TypeScript: load `reference/node_mcp_server.md`. Always load `reference/mcp_best_practices.md`.
- **Four-phase workflow:** Research → Implement (FastMCP/TypeScript SDK) → Review & Test (MCP Inspector) → Evaluate (10 Q&A eval pairs)
- **Recommended stack:** Python + FastMCP or TypeScript + `@modelcontextprotocol/sdk`

#### `anthropic/claude-api` — Claude API & Managed Agents
- **Local path:** `skills/anthropic/claude-api/SKILL.md`
- **Source:** https://github.com/anthropics/skills/tree/main/skills/claude-api
- **Pipeline stage:** Orchestrator + all LLM-calling agents
- **Activation:** Load when writing any code that calls Claude. Covers tool use, managed agents, prompt caching, streaming, batch, compaction, and model selection.
- **Default model:** `claude-opus-4-7` with `thinking: {type: "adaptive"}` and streaming for long outputs.
- **Key patterns:** `managed-agents` for stateful orchestration, prompt caching for large game manuals, tool runner for agent loops.

#### `anthropic/xlsx` — Spreadsheet Creation & Analysis
- **Local path:** `skills/anthropic/xlsx/SKILL.md`
- **Source:** https://github.com/anthropics/skills/tree/main/skills/xlsx
- **Pipeline stage:** `power_engineer` output / `scout_dev` data export
- **Activation:** Load when generating power budget workbooks or scouting data exports. Use `openpyxl` for formula-bearing workbooks; `pandas` for data exports. Always use Excel formulas (not hardcoded values); run `scripts/recalc.py` after writing.
- **Key tools:** `openpyxl`, `pandas`

#### `codex/jupyter-notebook` — Reproducible Notebooks
- **Local path:** `skills/codex/jupyter-notebook/SKILL.md`
- **Helper script:** `skills/codex/jupyter-notebook/scripts/new_notebook.py`
- **Source:** https://github.com/openai/skills/tree/main/skills/.curated/jupyter-notebook
- **Pipeline stage:** `mc_simulator` analysis reports / `mechanic_analyst` exploration
- **Activation:** Load when creating notebooks for Monte Carlo sweep results, scoring analysis, or game mechanics exploration. Use `experiment` kind for data analysis, `tutorial` kind for strategy walkthroughs. Scaffold with `new_notebook.py`.

#### `karpathy/claude` — LLM Agent Behavioral Guidelines
- **Local path:** `skills/karpathy/CLAUDE.md`
- **Source:** https://github.com/forrestchang/andrej-karpathy-skills/blob/main/CLAUDE.md
- **Pipeline stage:** All agents (foundational)
- **Activation:** Load for all agents to enforce four core behavioral principles: (1) Think before coding — surface assumptions and tradeoffs; (2) Simplicity First — minimum code, no speculative features; (3) Surgical Changes — touch only what is required; (4) Goal-Driven Execution — define verifiable success criteria before starting multi-step tasks.
- **Key principles:** No premature abstraction, no silent interpretation of ambiguous requirements, no "improvement" of adjacent code, every change traceable to the user's request.

---

### Tier 2 — Sub-Task Skills (load when stage is active)

#### `anthropic/webapp-testing` — Web App Test Automation
- **Local path:** `skills/anthropic/webapp-testing/SKILL.md`
- **Helper script:** `skills/anthropic/webapp-testing/scripts/with_server.py`
- **Source:** https://github.com/anthropics/skills/tree/main/skills/webapp-testing
- **Pipeline stage:** `scout_dev` QA / power dashboard QA
- **Activation:** Load when writing Playwright tests for the scouting app or power dashboard. Use `with_server.py` to manage multi-process server lifecycle (FastAPI backend + Streamlit frontend).

#### `anthropic/frontend-design` — UI Component Design
- **Local path:** `skills/anthropic/frontend-design/SKILL.md`
- **Source:** https://github.com/anthropics/skills/tree/main/skills/frontend-design
- **Pipeline stage:** `scout_dev` UI / power dashboard UI
- **Activation:** Load when building or refining the scouting app UI or power dashboard frontend components.

#### `anthropic/doc-coauthoring` — Document Co-Authoring
- **Local path:** `skills/anthropic/doc-coauthoring/SKILL.md`
- **Source:** https://github.com/anthropics/skills/tree/main/skills/doc-coauthoring
- **Pipeline stage:** `strategy_architect` output (`strategy.md`)
- **Activation:** Load when generating the strategy document output. Provides structured document drafting with human review checkpoints.

#### `codex/playwright` — Playwright Test Generation
- **Local path:** `skills/codex/playwright/SKILL.md`
- **Source:** https://github.com/openai/skills/tree/main/skills/.curated/playwright
- **Pipeline stage:** Scouting app / dashboard testing (complements `webapp-testing`)
- **Activation:** Load alongside `anthropic/webapp-testing` for additional Playwright test patterns.

#### `codex/playwright-interactive` — Interactive Playwright Sessions
- **Local path:** `skills/codex/playwright-interactive/SKILL.md`
- **Source:** https://github.com/openai/skills/tree/main/skills/.curated/playwright-interactive
- **Pipeline stage:** Field layout UI validation / live scouting session replay
- **Activation:** Load when doing interactive browser-based validation of the field visualization or scouting session replay.

#### `codex/security-best-practices` — OWASP Security Review
- **Local path:** `skills/codex/security-best-practices/SKILL.md`
- **Source:** https://github.com/openai/skills/tree/main/skills/.curated/security-best-practices
- **Pipeline stage:** `qa_validator` / MCP server review
- **Activation:** Load during `qa_validator` gate for scouting app, MCP HTTP endpoints, and PDF ingest pipeline. Covers OWASP Top 10: path injection from untrusted PDF filenames, secret leakage in MCP logs, insecure API key handling.

#### `codex/security-threat-model` — Threat Modeling
- **Local path:** `skills/codex/security-threat-model/SKILL.md`
- **Source:** https://github.com/openai/skills/tree/main/skills/.curated/security-threat-model
- **Pipeline stage:** Architecture / MCP design review
- **Activation:** Load when designing MCP server boundaries or the scouting data API. Threat surfaces: untrusted PDF input, MCP stdio/HTTP transport, SQLite scouting database.

#### `codex/cli-creator` — Pipeline CLI Scaffolding
- **Local path:** `skills/codex/cli-creator/SKILL.md`
- **Source:** https://github.com/openai/skills/tree/main/skills/.curated/cli-creator
- **Pipeline stage:** Pipeline orchestrator entry point
- **Activation:** Load when building the one-command pipeline runner (`frc-pipeline run`, `frc-pipeline validate`, etc.). Handles argument parsing, help text, subcommand structure.

#### `codex/gh-fix-ci` — CI Failure Diagnosis
- **Local path:** `skills/codex/gh-fix-ci/SKILL.md`
- **Source:** https://github.com/openai/skills/tree/main/skills/.curated/gh-fix-ci
- **Pipeline stage:** GitHub Actions / CI automation
- **Activation:** Load when setting up or debugging GitHub Actions workflows for the pipeline automation repo.

#### `codex/yeet` — Artifact Publishing
- **Local path:** `skills/codex/yeet/SKILL.md`
- **Source:** https://github.com/openai/skills/tree/main/skills/.curated/yeet
- **Pipeline stage:** Stage 7: Iterate / artifact release
- **Activation:** Load when publishing validated artifacts (`manifest.json`, `validation_report.json`, strategy docs) to GitHub releases.

#### `codex/screenshot` — UI Screenshot Capture
- **Local path:** `skills/codex/screenshot/SKILL.md`
- **Source:** https://github.com/openai/skills/tree/main/skills/.curated/screenshot
- **Pipeline stage:** `sim_engineer` / strategy output
- **Activation:** Load when capturing screenshots of the 2D simulation canvas or field layout for inclusion in strategy documents and validation reports.

---

### Skill-to-Agent Activation Map

| Agent | Skills to activate |
|---|---|
| `pdf_extractor` | `anthropic/pdf`, `karpathy/claude` |
| `mechanic_analyst` | `codex/jupyter-notebook`, `karpathy/claude` |
| `strategy_architect` | `anthropic/doc-coauthoring`, `karpathy/claude` |
| `robot_codegen` | `anthropic/claude-api`, `karpathy/claude` |
| `power_engineer` | `anthropic/xlsx`, `karpathy/claude` |
| `scout_dev` | `anthropic/frontend-design`, `anthropic/webapp-testing`, `codex/playwright`, `karpathy/claude` |
| `sim_engineer` | `codex/screenshot`, `karpathy/claude` |
| `mc_simulator` | `codex/jupyter-notebook`, `karpathy/claude` |
| `advscope_integrator` | `karpathy/claude` |
| `qa_validator` | `codex/security-best-practices`, `codex/security-threat-model`, `karpathy/claude` |
| Orchestrator | `anthropic/claude-api`, `anthropic/mcp-builder`, `karpathy/claude` |
| Pipeline CLI | `codex/cli-creator`, `karpathy/claude` |
| CI / Release | `codex/gh-fix-ci`, `codex/yeet` |
