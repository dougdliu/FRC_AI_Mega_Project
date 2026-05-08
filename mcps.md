# FRC AI MCP Registry & Specifications

## 📦 MCP Overview
MCPs are lightweight, stateless servers that expose tools, resources, and prompts to the agent orchestrator. All communicate via JSON-RPC 2.0 over stdio or HTTP. Each MCP is versioned, sandboxed, and returns structured JSON.

## 🗂️ MCP Inventory
| MCP Name | Purpose | Transport | Max Runtime | Memory Budget |
|----------|---------|-----------|-------------|---------------|
| `frc-rulebook-mcp` | Rule extraction & clause lookup | stdio | 5s | 256MB |
| `wpilib-codegen-mcp` | WPILib scaffolding & validation | stdio | 10s | 512MB |
| `electrical-power-mcp` | Current draw & battery modeling | stdio | 8s | 384MB |
| `scouting-data-mcp` | Event logging & schema export | HTTP | 3s | 128MB |
| `physics-sim-mcp` | 2D field & kinematics simulation | stdio | 15s | 1GB |
| `monte-carlo-strategy-mcp` | 3v3 alliance sweeps & win rates | stdio | 30s | 2GB |
| `advscope-log-mcp` | WPILib log parsing & CSV export | stdio | 10s | 384MB |
| `iterative-dev-mcp` | Log drift analysis & patch generation | stdio | 12s | 512MB |

---

## 🔧 Detailed MCP Specifications

### 1. `frc-rulebook-mcp`
**Purpose:** Parse game manuals, extract clauses, enforce FRC compliance.  
**Tools:**
- `extract_rules(manual_pdf_path: string, field_drawing_pdf_path: string) → {sections: [{id, title, text, constraints}], field: {dims, zones}, scoring: [{type, points, conditions}], field_layout_reference: object, apriltag_field_layout: object}`
- `search_rules(query: string) → [{clause_id, section, match_score}]`
- `validate_clause(clause_id: string, proposed_action: string) → {compliant: bool, reason: string}`
**Resources:**
- `rules://{year}/manual.json`
- `rules://{year}/clauses/{id}`
**Integration:** Feeds `pdf_extractor`, `mechanic_analyst`, `qa_validator`

---

### 2. `wpilib-codegen-mcp`
**Purpose:** Generate compliant WPILib projects, subsystems, and auto routines.  
**Tools:**
- `generate_subsystem(spec: {type, motors, sensors, pid}) → {java_path, cpp_path, compile_status}`
- `generate_auto_routine(params: {steps, scoring_type, field_zones}) → {auto_code.java}`
- `validate_wpilib(code: string) → {errors: [], warnings: [], compiles: bool}`
**Resources:**
- `templates://java/command-based/`
- `templates://cpp/command-based/`
- `field://apriltag_field_layout.json`
- `db://motors.json`
**Integration:** Powers `robot_codegen`, `qa_validator`

---

### 3. `electrical-power-mcp`
**Purpose:** Model current draw, battery sag, thermal limits.  
**Tools:**
- `estimate_current_draw(subsystems: [{name, duty_cycle, duration}]) → {peak_amps, avg_amps, voltage_sag, thermal_risk}`
- `simulate_battery_sag(duty_cycle: float, temp: float) → {voltage_curve: [float], recovery_time: float}`
- `generate_power_dashboard() → {html_report, csv_budget}`
**Resources:**
- `power://motor_curves/{model}.csv`
- `power://battery_models/{type}.json`
**Integration:** Powers `power_engineer`, `qa_validator`

---

### 4. `scouting-data-mcp`
**Purpose:** Scouting schema, event logging, export.  
**Tools:**
- `log_event(event: {match, alliance, role, scoring, penalty, notes}) → {event_id, status}`
- `get_alliance_stats(match_id: string) → {team_1: {}, team_2: {}, win_prob}`
- `export_data(format: "csv"|"json"|"sqlite") → {file_path, row_count}`
**Resources:**
- `schema://scouting_v2.json`
- `db://scouting.sqlite`
**Integration:** Powers `scout_dev`, `mc_simulator`

---

### 5. `physics-sim-mcp`
**Purpose:** 2D field simulation, kinematics, collision detection.  
**Tools:**
- `run_simulation(params: {robots: [{size, speed, intake_width, scoring}], field: {layout, zones}, duration: float}) → {log_path, collision_count, score_summary}`
- `check_collision(robots: [robot_params]) → {colliding: bool, contact_point: {x,y}, force: float}`
- `export_sim_log() → {csv_path, json_trace}`
**Resources:**
- `sim://field_layout.json`
- `sim://apriltag_field_layout.json`
- `sim://robots/{id}.json`
**Integration:** Powers `sim_engineer`, `mc_simulator`

---

### 6. `monte-carlo-strategy-mcp`
**Purpose:** 3v3 alliance simulation, win-rate heatmaps.  
**Tools:**
- `run_sweep(configs: [alliance_config], runs: int) → {win_rates: {config: float}, confidence: float, top_strategies: [string]}`
- `get_heatmap_data() → {x: [param], y: [param], z: [win_rate]}`
- `recommend_alliance(target: "qual"|"playoff") → {composition, rationale}`
**Resources:**
- `mc://configs/{year}.json`
- `mc://results/win_rates.json`
**Integration:** Powers `mc_simulator`, `strategy_architect`

---

### 7. `advscope-log-mcp`
**Purpose:** WPILib log parsing, AdvantageScope CSV export.  
**Tools:**
- `parse_wpilib_log(log_path: string) → {channels: [{name, type, samples}], valid: bool}`
- `generate_advscope_csv() → {csv_path, channel_map}`
- `replay_trajectory(trajectory_id: string) → {replay_config.json}`
**Resources:**
- `logs://{year}/match_{id}.csv`
- `config://advscope.json`
**Integration:** Powers `advscope_integrator`, `iterative-dev-mcp`

---

### 8. `iterative-dev-mcp`
**Purpose:** Log-assisted debugging, PID tuning, patch generation.  
**Tools:**
- `analyze_drift(log_path: string, channel: string) → {overshoot: float, settling_time: float, recommendation: string}`
- `generate_pid_tune(pid: {p,i,d}, target: string) → {tuned_pid: {p,i,d}, expected_improvement: float}`
- `run_regression(patch: string) → {passes: int, failures: [], regression_summary}`
**Resources:**
- `dev://iteration_log.json`
- `dev://patches/{id}.diff`
**Integration:** Powers `skill: Iterative Log-Assisted Dev`, `qa_validator`

---

## 🔌 Transport & Orchestration
- **Transport:** `stdio` (default for local agents), `HTTP` (for scouting/power dashboards)
- **Protocol:** JSON-RPC 2.0, `tools/call`, `resources/read`, `prompts/list`
- **Sandboxing:** Each MCP runs in isolated container/process. Max memory/CPU enforced via `cgroups` or `systemd`.
- **Error Handling:** All tools return `{success: bool, data: any, error: string}`. Orchestrator retries on `success: false` (max 3).
- **Versioning:** MCPs versioned semantically (`v1.2.0`). Orchestrator locks to compatible versions via `mcps.lock`.

## Request/Response Envelope Standard
- Request metadata (required):
	- `trace_id`
	- `session_id`
	- `agent_id`
	- `timestamp_utc`
- Response metadata (required):
	- `success`
	- `duration_ms`
	- `error_code` (nullable)
	- `error_message` (nullable)

## Security Baseline
- Allowlist filesystem paths per MCP.
- Deny network egress unless explicitly needed (for HTTP MCPs).
- Redact secrets from logs and tool responses.
- Validate all path and command arguments before execution.

---

## 🔗 Wiring to Existing Pipeline
- Stage 1 Ingest:
	- `pdf_extractor` calls `frc-rulebook-mcp.extract_rules` and `search_rules`.
- Stage 2 Model:
	- `mechanic_analyst` consumes rule resources and emits mechanics artifacts.
- Stage 3 Plan:
	- `strategy_architect` pulls from `monte-carlo-strategy-mcp` for baseline sweeps.
- Stage 4 Build (parallel):
	- `robot_codegen` -> `wpilib-codegen-mcp`
	- `power_engineer` -> `electrical-power-mcp`
	- `scout_dev` -> `scouting-data-mcp`
	- `sim_engineer` -> `physics-sim-mcp`
	- `mc_simulator` -> `monte-carlo-strategy-mcp`
- Stage 5 Integrate:
	- `advscope_integrator` -> `advscope-log-mcp`
- Stage 6 Validate:
	- `qa_validator` re-checks compliance via `frc-rulebook-mcp` and regression checks via `iterative-dev-mcp`.

## Implementation Priority
1. `frc-rulebook-mcp`
2. `wpilib-codegen-mcp`
3. `physics-sim-mcp`
4. `monte-carlo-strategy-mcp`
5. `iterative-dev-mcp`

## MCP Development Skill

All MCP servers in this registry must be built using the vendored `mcp-builder` skill:

- **Skill file:** `skills/anthropic/mcp-builder/SKILL.md`
- **Best practices:** `skills/anthropic/mcp-builder/reference/mcp_best_practices.md`
- **Python guide:** `skills/anthropic/mcp-builder/reference/python_mcp_server.md` (FastMCP)
- **TypeScript guide:** `skills/anthropic/mcp-builder/reference/node_mcp_server.md`
- **Evaluation guide:** `skills/anthropic/mcp-builder/reference/evaluation.md`

**Stack decision:** Use Python + FastMCP for all stdio servers (consistent with the rest of the pipeline's Python stack). Use TypeScript + `@modelcontextprotocol/sdk` only if a specific MCP requires browser/Node.js-native capabilities.

**Four-phase build process for each MCP:**
1. **Research** — Review service API docs, study MCP protocol spec (`https://modelcontextprotocol.io`), load `mcp_best_practices.md`.
2. **Implement** — Set up project structure per language guide, implement tools with Pydantic (Python) or Zod (TypeScript) schemas, use async/await for all I/O.
3. **Review & Test** — Run `MCP Inspector` (`npx @modelcontextprotocol/inspector`), verify type coverage and error handling, confirm tool naming follows `{service}_{action}` convention.
4. **Evaluate** — Create 10 Q&A eval pairs per `evaluation.md`; eval questions must be independent, read-only, complex, and verifiable.

**Security requirements** (load `skills/codex/security-best-practices/SKILL.md` during review):
- Allowlist all filesystem paths before any file operation.
- Validate and sanitize all tool arguments at the MCP boundary.
- Never log secrets, API keys, or raw PDF content to stdout/stderr.
- Deny egress on MCPs that don't require network access.
