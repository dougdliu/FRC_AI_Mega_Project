# REBUILT 2026 2D Sandbox

This is a local browser-based 2D 3v3 sandbox built from the extracted `context/game_spec.json` output.

It serves two jobs:
- give humans a playable top-down match sandbox
- use the same match engine headlessly so bots and future AI agents can self-play the game

## What It Includes

- one browser canvas app with six robot slots
- per-slot human or bot control
- extracted 2026 REBUILT timing and hub-shift rules
- FUEL pickup, scoring, recycling, and tower climbing
- simple defensive contact and endgame tower-protection fouls
- a headless Node runner that uses the same engine for repeated bot-vs-bot matches

## Run The Browser Game

From the `game2d/` directory:

```bash
npm run serve
```

Then open:

```text
http://localhost:4173/game2d/
```

The browser app expects `../context/game_spec.json` to exist, so run the pipeline first.

## Run Headless Simulation

From the `game2d/` directory:

```bash
npm run simulate -- --matches 20
```

Example custom alliance mixes:

```bash
npm run simulate -- --matches 50 --blue fuel_sprinter,balanced_climber,tower_anchor --red balanced_climber,balanced_climber,fuel_sprinter
```

## Local Controls

- `Space`: start or pause
- `R`: reset match
- `1-6`: select robot
- `Q`: auto-cycle mode
- `W`: collect mode
- `E`: score mode
- `D`: defend mode
- `Z`, `X`, `C`: climb level 1, 2, 3 mode
- click the field: set the selected human robot's target point

Human-controlled robots are command-driven rather than direct-drive. That keeps the control model aligned with future bot and AI controllers.

## AI Hook Direction

The engine exports deterministic step functions plus robot observations and high-level command modes. The intended next step is to replace the scripted bot planner with trainable policies that emit the same command objects.
