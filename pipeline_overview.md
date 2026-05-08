# FRC AI Development Pipeline
1. **Ingest:** PDF → `pdf_extractor` → `rules.json`
2. **Model:** `mechanic_analyst` → `mechanics.json`
3. **Plan:** `strategy_architect` → `strategy.md` + sim parameters
4. **Build (Parallel):**
   - `robot_codegen` → WPILib project
   - `power_engineer` → Power app + budget
   - `scout_dev` → Scouting app
   - `sim_engineer` → 2D sim + params
   - `mc_simulator` → Monte Carlo reports
5. **Integrate:** `advscope_integrator` → Logs + config
6. **Validate:** `qa_validator` → Compliance report
7. **Iterate:** Logs → `skill: Iterative Log-Assisted Dev` → Code patches → Rebuild

## Success Criteria (Release Gate)
- Rule extraction recall >= 98% for required sections (objectives, timing, scoring, penalties, field).
- Every downstream recommendation includes at least one source citation (`section_id`, `page`).
- Simulation and Monte Carlo tools produce reproducible outputs with pinned random seeds.
- Validation report has zero critical rule compliance failures.

## Runtime Targets
- Full pipeline runtime (single game manual) <= 20 minutes on development machine.
- Incremental rerun after small rule edit <= 5 minutes.
- MCP timeout and retry policy enforced per `mcps.md`.

## Canonical Artifacts
- `/context/game_spec.json` is the single source of truth for shared game semantics.
- `/artifacts/{game_year}/manifest.json` lists all generated artifacts, hashes, and generator versions.
- `/artifacts/{game_year}/validation_report.json` is required for release decisions.

## Operational Risks and Mitigation
- OCR ambiguity in scanned tables:
   - Mitigation: dual extraction path (`pymupdf` + table parser) and confidence-based review queue.
- Strategy overfitting to simulation assumptions:
   - Mitigation: enforce scenario diversity and log-grounded calibration in each iteration.
- Drift between docs and implementation:
   - Mitigation: nightly schema checks and artifact manifest validation.
