# FRC Game Analysis & Dev Agents

## Agent Registry
| Agent | Role | Core Capabilities | Tools/Dependencies | I/O Contract |
|-------|------|-------------------|--------------------|--------------|
| `pdf_extractor` | Manual Parser | PDF→text, image/table extraction, OCR fallback, rule clause mapping | `pymupdf`, `tabula-py`, `layoutparser` | Input: PDF → Output: Structured JSON (rules, constraints, scoring, field layout) |
| `mechanic_analyst` | Game Logic Modeler | Constraint graph, state machine, resource flow, win condition modeling | `networkx`, `sympy`, rule JSON | Input: Rule JSON → Output: Game mechanics spec (physics, scoring, penalties, time limits) |
| `strategy_architect` | Tactic Designer | Alliance composition, resource allocation, risk/reward mapping, Monte Carlo prep | `numpy`, `scipy`, strategy templates | Input: Mechanics spec → Output: Strategy doc (markdown/PDF) + simulation parameters |
| `robot_codegen` | WPILib Generator | Command-based architecture, subsystem wiring, PID config, motor/sensor mapping | `wpilib-template`, `jinja2`, C++/Java | Input: Strategy spec → Output: WPILib project skeleton + core subsystems |
| `power_engineer` | Electrical Modeler | Current draw estimation, battery sag, duty cycle, thermal limits | `pandas`, `matplotlib`, motor curves | Input: Robot design → Output: Power usage app (web) + CSV/JSON budget |
| `scout_dev` | Scouting App Builder | Data schema, UI logic, export/import, API sync | `streamlit`/`flutter`, `sqlite`, `fastapi` | Input: Scoring/penalty rules → Output: Scouting app + DB schema |
| `sim_engineer` | 2D/Physics Simulator | Canvas rendering, collision detection, kinematics, parameter sliders | `pygame`/`p5.js`, `box2d` | Input: Robot params → Output: Interactive 2D sim + parameter JSON |
| `mc_simulator` | Monte Carlo Strategist | 3v3 alliance simulation, win-rate estimation, parameter sweeps | `ray`, `numpy`, `seaborn` | Input: Sim params + rules → Output: Win-rate heatmap + strategy recommendations |
| `advscope_integrator` | Logging Formatter | WPILib log→AdvantageScope CSV, trajectory export, replay sync | `wpilib-log`, `csvkit` | Input: Robot code → Output: `.csv` logs + AdvantageScope config |
| `qa_validator` | Rule & Physics Auditor | FRC rule compliance, constraint checking, simulation vs reality bounds | `pytest`, rule DB, physics bounds | Input: All outputs → Output: Validation report + fix directives |

## Orchestration Protocol
- **Sequence:** `pdf_extractor` → `mechanic_analyst` → `strategy_architect` → parallel `robot_codegen`, `power_engineer`, `scout_dev`, `sim_engineer`, `mc_simulator` → `advscope_integrator` → `qa_validator`
- **Fallback:** Any agent failing validation triggers `qa_validator` → rework directive → retry (max 3)
- **State:** All outputs versioned in `/artifacts/{game_year}/`. Shared context via `/context/game_spec.json`

## Agent Interface Standards
- All agent outputs must include:
	- `success` (bool)
	- `artifact_paths` (array)
	- `warnings` (array)
	- `citations` (array of `{section_id, page}` where applicable)
- All agent failures must include:
	- `error_code`
	- `error_message`
	- `retryable` (bool)

## Quality Ownership
- `pdf_extractor`: extraction completeness and citation fidelity.
- `mechanic_analyst`: constraint correctness and state-machine validity.
- `strategy_architect`: strategy assumptions and scenario coverage.
- `robot_codegen`: compile validity and command-based architecture conformance.
- `power_engineer`: electrical envelope conformance.
- `sim_engineer` and `mc_simulator`: reproducible simulation outputs.
- `qa_validator`: final release gate authority.

## Concurrency Rules
- Parallel agents must not write to shared files directly.
- Shared updates happen only through orchestrator-managed merge into `/context/game_spec.json`.
- Artifact naming must be deterministic to support caching and diffing.
