# FRC Megaproject

An AI-assisted pipeline for extracting FRC game information from manuals and field drawings, analyzing the game strategically, and building rough simulations from that extracted understanding.

The core project stops before robot code generation. Robot code requires human decisions about team goals, robot architecture, mechanisms, sensors, and implementation tradeoffs after strategic analysis is complete.

## Feasibility Summary

This project is feasible if the MVP focuses on information extraction, game modeling, strategic analysis, and simulation.

It is not realistic or desirable for the core pipeline to generate robot code from the manual alone because the manual does not specify:
- which tasks the team chooses to prioritize
- what robot architecture the team will build
- mechanism geometry, motor choices, sensors, and controls assumptions
- team-specific fabrication and driver constraints

The minimum viable path is:
- extract cited rules and field geometry
- normalize field zones, scoring locations, game pieces, and timing
- model scoring, constraints, and match flow
- produce strategic analysis and task-priority recommendations
- run coarse simulations that compare strategies and capability assumptions
- produce a human decision packet for later robot design and code work

## Core Pipeline

```
Game manual PDF + field drawing PDF + optional Team Updates/Q&A
    -> [1] Manual and Field Extraction
           outputs: rules.json, field_layout_reference.json, extraction_report.md

    -> [2] Field Model Construction
           outputs: field_model.json, apriltag_field_layout.json

    -> [3] Game Mechanics Modeling
           outputs: mechanics.json

    -> [4] Strategic Analysis
           outputs: strategy_packet.json, strategy_brief.md, team_decision_packet.md

    -> [5] Game and Strategy Simulation
           outputs: simulation_model.json, sim_params.json, sim_summary.json, simulation_report.md

    -> [6] Validation
           outputs: validation_report.json
```

The simulations use abstract capability profiles and field geometry to compare likely strategies. They are not a substitute for team-specific robot design.

## Core Agents

| Agent | Role |
|---|---|
| `pdf_extractor` | Extracts cited manual rules and raw field-drawing references |
| `field_modeler` | Converts field drawings and extracted references into a normalized field model |
| `mechanic_analyst` | Converts rules and field information into a mechanics model |
| `strategy_architect` | Produces strategic analysis, task priorities, and team decision prompts |
| `sim_engineer` | Builds and runs coarse simulations from mechanics, strategy assumptions, and field geometry |
| `qa_validator` | Verifies citations, schemas, field consistency, and simulation reproducibility |

See [agents.md](agents.md) for the agent registry and orchestration rules.

## Required Inputs

Each game year needs:
- a game manual PDF
- a field-dimension drawing PDF
- optional Team Updates and Q&A clarifications when available

Human inputs for robot code are intentionally not part of this core pipeline. They belong to a later phase after the team reviews the strategy and simulation outputs.

## Canonical Artifacts

All outputs are versioned under `/artifacts/{game_year}/`:

| Artifact | Produced by | Purpose |
|---|---|---|
| `rules.json` | `pdf_extractor` | Cited rule extraction |
| `field_layout_reference.json` | `pdf_extractor` | Raw field dimensions, drawing references, and page citations |
| `extraction_report.md` | `pdf_extractor` | Confidence notes, ambiguous clauses, and manual-review queue |
| `field_model.json` | `field_modeler` | Normalized zones, scoring locations, game-piece starts, and reference frames |
| `apriltag_field_layout.json` | `field_modeler` | WPILib-compatible AprilTag layout when tags are defined in source drawings |
| `mechanics.json` | `mechanic_analyst` | Simulation-ready game model |
| `strategy_packet.json` | `strategy_architect` | Structured priorities, role candidates, and assumptions |
| `strategy_brief.md` | `strategy_architect` | Human-readable game analysis |
| `team_decision_packet.md` | `strategy_architect` | Questions and tradeoffs for the team before robot design |
| `simulation_model.json` | `sim_engineer` | Game-specific simulation model derived from mechanics and field data |
| `sim_params.json` | `sim_engineer` | Simulation assumptions and capability profiles |
| `sim_summary.json` | `sim_engineer` | Ranked strategy outcomes and sensitivity notes |
| `simulation_report.md` | `sim_engineer` | Human-readable simulation interpretation |
| `validation_report.json` | `qa_validator` | Final go/no-go report for the current analysis run |

`/context/game_spec.json` remains the shared normalized view of extracted rules, field geometry, mechanics, and strategy assumptions.

## Human-Gated Robot Phase

Robot code generation can become a later phase only after humans provide:
- selected team objectives and priority tasks
- robot architecture and mechanism choices
- drivetrain, sensors, motors, and controls assumptions
- simulation fidelity requirements and acceptance tests

That later phase may consume the artifacts from this project, but it should not be treated as part of the manual-analysis MVP.

## Runtime Targets

These are planning targets for the core analysis MVP:
- extraction and field modeling: `<= 10 min` typical
- mechanics and strategy synthesis: `<= 10 min`
- coarse simulation sweeps: `<= 15 min`
- full analysis run: `<= 35 min` on a development machine

## Explicit Non-Goals for Core MVP

These are useful future extensions, but they should not block the first working version:
- robot code generation
- Maple-Sim robot implementation validation
- scouting app generation
- power budgeting and thermal dashboards
- AdvantageScope log pipelines
- RL self-play and full 3v3 high-fidelity physics search
- mandatory MCP microservice splits for every subsystem

## Documentation

| File | Contents |
|---|---|
| [agents.md](agents.md) | Core agent registry and orchestration rules |
| [skill.md](skill.md) | Skill definitions aligned to extraction, strategy, and simulation |
| [mcps.md](mcps.md) | Optional MCP extraction plan after the core pipeline stabilizes |
| [pipeline_overview.md](pipeline_overview.md) | Stage-by-stage core flow, success criteria, and runtime targets |
| [data_flow_and_architecture.md](data_flow_and_architecture.md) | Core data contracts and architecture decisions |
| [iterationand_validation.md](iterationand_validation.md) | Iteration loop and validation gates |
