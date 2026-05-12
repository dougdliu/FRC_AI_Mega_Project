# FRC AI Development Pipeline

## Stages

1. **Ingest Manual and Field Sources**
   - Inputs: game manual PDF, field drawing PDF, optional Team Updates and Q&A
   - Agent: `pdf_extractor`
   - Outputs: `rules.json`, `field_layout_reference.json`, `extraction_report.md`

2. **Build Field Model**
   - Inputs: `field_layout_reference.json`, field drawing PDF
   - Agent: `field_modeler`
   - Outputs: `field_model.json`, `apriltag_field_layout.json`

3. **Model Game Mechanics**
   - Inputs: `rules.json`, `field_model.json`
   - Agent: `mechanic_analyst`
   - Output: `mechanics.json`

4. **Analyze Strategy**
   - Inputs: `rules.json`, `field_model.json`, `mechanics.json`
   - Agent: `strategy_architect`
   - Outputs: `strategy_packet.json`, `strategy_brief.md`, `team_decision_packet.md`

5. **Simulate Game and Strategy Assumptions**
   - Inputs: `field_model.json`, `mechanics.json`, `strategy_packet.json`
   - Agent: `sim_engineer`
   - Outputs: `simulation_model.json`, `sim_params.json`, `sim_summary.json`, `simulation_report.md`
   - Notes: simulations use abstract capability profiles, not a chosen team robot design

6. **Validate Analysis Run**
   - Inputs: all artifacts from stages 1-5
   - Agent: `qa_validator`
   - Output: `validation_report.json`

## Key Design Decisions

- Treat field-drawing extraction as a first-class stage, not a side effect of manual parsing.
- Separate strategic analysis from robot implementation.
- Use abstract capability profiles to test strategy sensitivity before the team chooses a robot design.
- Keep simulations focused on game flow, scoring paths, field geometry, timing, and rough interaction constraints.
- Keep robot code generation out of the core pipeline until humans provide team objectives and robot design inputs.

## Success Criteria

- Extraction covers the major manual sections: objectives, timing, scoring, penalties, field, and robot constraints.
- Field model includes cited dimensions, zones, scoring locations, game-piece start locations, and alliance reference frames when available.
- Every non-trivial strategy recommendation carries at least one source citation.
- Simulation assumptions are explicit and reproducible under fixed seeds.
- Simulation results identify robust strategy priorities and sensitivity to cycle-time assumptions.
- Validation report clearly separates extraction defects, strategy defects, and simulation defects.

## Runtime Targets

- extraction and field modeling: `<= 10 min` typical
- mechanics and strategy synthesis: `<= 10 min`
- coarse simulation sweeps: `<= 15 min`
- full core run: `<= 35 min`

## Canonical Artifacts

- `/context/game_spec.json` is the shared normalized game view.
- `/artifacts/{game_year}/manifest.json` records generated artifacts, hashes, and generator versions.
- `/artifacts/{game_year}/field_model.json` stores normalized field geometry and interaction locations.
- `/artifacts/{game_year}/strategy_packet.json` stores structured strategic assumptions.
- `/artifacts/{game_year}/simulation_model.json` stores the game-specific simulation model.
- `/artifacts/{game_year}/sim_summary.json` stores ranked strategy results from simulation sweeps.
- `/artifacts/{game_year}/team_decision_packet.md` stores the human questions needed before robot design.
- `/artifacts/{game_year}/validation_report.json` is required before treating a run as usable.

## Operational Risks and Mitigation

- OCR ambiguity in scanned manuals:
  Mitigation: dual extraction path plus confidence flags and a manual review queue for low-confidence clauses.
- Field drawing ambiguity:
  Mitigation: preserve raw dimension tokens, page references, and unresolved geometry questions in `field_layout_reference.json`.
- Strategy overconfidence from rough simulation:
  Mitigation: report sensitivity ranges and avoid treating simulated scores as predictions.
- Premature robot implementation:
  Mitigation: stop the core pipeline at `team_decision_packet.md` and require human design inputs before any codegen phase.

## Post-Analysis Robot Handoff

A later robot implementation phase may consume these artifacts, but only after the team supplies selected tasks, robot architecture, mechanisms, sensors, and acceptance tests.
