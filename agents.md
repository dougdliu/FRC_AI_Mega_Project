# FRC Game Analysis & Simulation Agents

## Core Agent Registry

| Agent | Role | Core Capabilities | Tools/Dependencies | I/O Contract |
|-------|------|-------------------|--------------------|--------------|
| `pdf_extractor` | Manual and drawing extractor | PDF text extraction, table parsing, OCR fallback, citation capture, raw field reference extraction | `pymupdf`, `pdfplumber`, table extractor | Input: game manual PDF + field drawing PDF → Output: `rules.json`, `field_layout_reference.json`, `extraction_report.md` |
| `field_modeler` | Field geometry modeler | Field coordinate normalization, zone modeling, scoring location extraction, alliance mirroring | geometry helpers, schema validators | Input: `field_layout_reference.json` + field drawing PDF → Output: `field_model.json`, `apriltag_field_layout.json` |
| `mechanic_analyst` | Game logic modeler | State modeling, scoring transitions, possession/resource rules, timing constraints | `networkx`, `sympy`, rule JSON | Input: `rules.json` + `field_model.json` → Output: `mechanics.json` |
| `strategy_architect` | Game analyst | Role decomposition, task prioritization, cycle assumptions, foul-risk analysis, human-readable strategy summary | `numpy`, strategy templates | Input: `rules.json` + `field_model.json` + `mechanics.json` → Output: `strategy_packet.json`, `strategy_brief.md`, `team_decision_packet.md` |
| `sim_engineer` | Game and strategy simulator | Seeded discrete-event or top-down sim, field-path sweeps, capability-profile comparison | `numpy`, `scipy`, lightweight sim code | Input: `field_model.json` + `mechanics.json` + `strategy_packet.json` → Output: `simulation_model.json`, `sim_params.json`, `sim_summary.json`, `simulation_report.md` |
| `qa_validator` | Validation gate | Schema checks, citation checks, field consistency checks, reproducibility checks | `pytest`, schema validators | Input: all core artifacts → Output: `validation_report.json` |

## Orchestration Protocol

- **Sequence:** `pdf_extractor` → `field_modeler` → `mechanic_analyst` → `strategy_architect` → `sim_engineer` → `qa_validator`
- **Feedback loop:** if simulation exposes impossible assumptions or unstable strategy rankings, rerun `strategy_architect` and then `sim_engineer`
- **State:** outputs are versioned in `/artifacts/{game_year}/`; shared normalized context lives in `/context/game_spec.json`
- **Fallback:** any failed stage returns a rework directive and stops downstream stages until fixed

## Human-Gated Future Agents

These are intentionally outside the core manual-analysis pipeline:
- `robot_codegen`, after the team provides robot design inputs and selected tasks
- `power_engineer`, after robot hardware choices exist
- `scout_dev`, after the analysis artifacts stabilize
- `advscope_integrator`, after real or simulated robot logs exist

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

- `pdf_extractor`: extraction completeness, confidence labels, and citation fidelity
- `field_modeler`: coordinate consistency, zone correctness, and field-drawing traceability
- `mechanic_analyst`: correctness of states, transitions, and hard constraints
- `strategy_architect`: usefulness and traceability of strategic recommendations
- `sim_engineer`: reproducible relative rankings and clearly stated assumptions
- `qa_validator`: final release gate authority for analysis artifacts

## Concurrency Rules

- Keep the core pipeline mostly sequential to reduce coordination overhead.
- Parallelize only internal work units that do not mutate shared state, such as page extraction or seeded sim batches.
- Artifact naming must remain deterministic to support caching and diffing.
