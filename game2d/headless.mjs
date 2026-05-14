import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import {
  buildHeadlessSummary,
  createMatch,
  loadGameSpec,
  stepMatch
} from "./src/engine.js";

function parseArgs(argv) {
  const options = {
    matches: 10,
    dt: 0.2,
    bluePreset: "safe_rp_foundation",
    redPreset: "balanced_reference"
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (arg === "--matches" && next) {
      options.matches = Number(next);
      index += 1;
    } else if (arg === "--dt" && next) {
      options.dt = Number(next);
      index += 1;
    } else if (arg === "--blue" && next) {
      options.bluePreset = next;
      index += 1;
    } else if (arg === "--red" && next) {
      options.redPreset = next;
      index += 1;
    }
  }
  return options;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const specPath = fileURLToPath(new URL("../context/game_spec.json", import.meta.url));
  const spec = JSON.parse(await readFile(specPath, "utf8"));
  const game = loadGameSpec(spec);

  const results = [];
  for (let matchIndex = 0; matchIndex < options.matches; matchIndex += 1) {
    const state = createMatch(game, {
      seed: 2026 + matchIndex,
      bluePreset: options.bluePreset,
      redPreset: options.redPreset,
      humanSlots: []
    });
    state.running = true;
    while (!state.completed) {
      stepMatch(state, options.dt);
    }
    results.push(buildHeadlessSummary(state));
  }

  const aggregate = results.reduce((summary, result) => {
    summary.blueScore += result.score.blue.total;
    summary.redScore += result.score.red.total;
    summary.blueFuel += result.score.blue.fuel;
    summary.redFuel += result.score.red.fuel;
    summary.blueTower += result.score.blue.tower;
    summary.redTower += result.score.red.tower;
    summary.blueRp += result.rankingPoints.blue;
    summary.redRp += result.rankingPoints.red;
    if (result.score.blue.total > result.score.red.total) {
      summary.blueWins += 1;
    } else if (result.score.red.total > result.score.blue.total) {
      summary.redWins += 1;
    } else {
      summary.ties += 1;
    }
    return summary;
  }, {
    blueScore: 0,
    redScore: 0,
    blueFuel: 0,
    redFuel: 0,
    blueTower: 0,
    redTower: 0,
    blueRp: 0,
    redRp: 0,
    blueWins: 0,
    redWins: 0,
    ties: 0
  });

  const average = (value) => Number((value / results.length).toFixed(3));
  console.log(JSON.stringify({
    game: game.gameName,
    matches: results.length,
    bluePreset: options.bluePreset,
    redPreset: options.redPreset,
    aggregate: {
      avgBlueScore: average(aggregate.blueScore),
      avgRedScore: average(aggregate.redScore),
      avgBlueFuel: average(aggregate.blueFuel),
      avgRedFuel: average(aggregate.redFuel),
      avgBlueTower: average(aggregate.blueTower),
      avgRedTower: average(aggregate.redTower),
      avgBlueRp: average(aggregate.blueRp),
      avgRedRp: average(aggregate.redRp),
      blueWins: aggregate.blueWins,
      redWins: aggregate.redWins,
      ties: aggregate.ties
    },
    lastMatch: results[results.length - 1]
  }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
