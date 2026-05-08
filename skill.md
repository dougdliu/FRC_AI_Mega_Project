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
- **Intent:** Use a single worksheet-driven framework (derived from Team 2791/6328 and FIRST kickoff worksheets) to convert manual text into actionable game insights.
- **Input:** Game manual PDF, Team Updates, Q&A clarifications, optional historical game analogs.
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
- **Input:** Game manual PDF
- **Output:** `rules.json` (sections, clauses, constraints, scoring, field dimensions)
- **Tools:** `pymupdf`, regex clause parser, table extractor
- **Validation:** All game objectives, time limits, penalties, and field measurements present. Cross-check against official FRC rulebook PDF if available.
- **Prompt Template:** `Extract all game rules, constraints, scoring, and field dimensions from the PDF. Output structured JSON with sections: {objectives, time, scoring, penalties, field, alliances, equipment_limits}.`

## Skill: Game Mechanics Modeling
- **Input:** `rules.json`
- **Output:** `mechanics.json` (state machine, resource flow, win conditions, physics constraints)
- **Tools:** `networkx`, constraint solver
- **Validation:** No circular dependencies, all scoring paths mapped, time/resource limits enforced.
- **Prompt Template:** `Model the game as a state machine. Map scoring paths, resource constraints, alliance interactions, and win conditions. Output mechanics.json with states, transitions, and constraints.`

## Skill: WPILib Robot Code Generation
- **Input:** `mechanics.json`, `strategy.md`
- **Output:** WPILib project (Java/C++), subsystems, commands, PID configs
- **Tools:** `jinja2`, wpilib-template, motor/sensor DB
- **WPILib Version:** WPILib 2026 (current year). Always target the latest 2026 release. Verify the exact version at https://github.com/wpilibsuite/allwpilib/releases before generating code; do not hardcode a patch version.
- **Validation:** Compiles with WPILib 2026, uses command-based paradigm, includes all required subsystems. Reject output that imports deprecated or pre-2026 APIs.
- **Prompt Template:** `Generate a command-based WPILib project targeting WPILib 2026. Include subsystems for {intake, drive, scoring, lifting}. Add PID configs, auto routines, and teleop commands. Follow WPILib 2026 standards and API conventions.`

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
    └─> PDF Rule Extraction
            └─> Game Mechanics Modeling  ──[codex/jupyter-notebook]
                    └─> Monte Carlo Strategy Simulation  ──[codex/jupyter-notebook]
                            └─> WPILib Robot Code Generation  ──[anthropic/claude-api]
                                    └─> AdvantageScope Log Integration
                                            └─> Iterative Log-Assisted Dev
                            │
                            └─> Strategy Synthesis  ──[anthropic/doc-coauthoring]
                                    └─> WPILib Robot Code Generation

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
| `pdf_extractor` | `anthropic/pdf` |
| `mechanic_analyst` | `codex/jupyter-notebook` |
| `strategy_architect` | `anthropic/doc-coauthoring` |
| `robot_codegen` | `anthropic/claude-api` |
| `power_engineer` | `anthropic/xlsx` |
| `scout_dev` | `anthropic/frontend-design`, `anthropic/webapp-testing`, `codex/playwright` |
| `sim_engineer` | `codex/screenshot` |
| `mc_simulator` | `codex/jupyter-notebook` |
| `advscope_integrator` | *(no external skill)* |
| `qa_validator` | `codex/security-best-practices`, `codex/security-threat-model` |
| Orchestrator | `anthropic/claude-api`, `anthropic/mcp-builder` |
| Pipeline CLI | `codex/cli-creator` |
| CI / Release | `codex/gh-fix-ci`, `codex/yeet` |
