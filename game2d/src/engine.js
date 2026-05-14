const ROBOT_RADIUS_M = 0.34;
const FUEL_RADIUS_M = 0.12;
const HUB_RADIUS_M = 0.82;
const TOWER_RADIUS_M = 0.72;
const MAX_CARRY = 12;
const AUTO_PHASE_S = 20;
const TELEOP_PHASE_S = 140;
const ENDGAME_THRESHOLD_S = 30;
const TRANSITION_SHIFT_S = 10;
const SHIFT_DURATION_S = 25;
const TOTAL_MATCH_S = 160;
const CONTACT_DISTANCE_M = 0.8;
const PICKUP_RANGE_M = 0.55;
const SCORE_RANGE_M = 0.9;
const CLIMB_RANGE_M = 0.95;
const TOWER_PROTECTION_RANGE_M = 1.15;
const DEFENSE_DRAG_FACTOR = 0.45;
const PRESET_STRATEGIES = {
  safe_rp_foundation: ["balanced_climber", "balanced_climber", "tower_anchor"],
  shift_aware_fuel_pressure: ["fuel_sprinter", "fuel_sprinter", "support_disruptor"],
  tower_anchor_plus_fuel_support: ["tower_anchor", "balanced_climber", "fuel_sprinter"],
  balanced_reference: ["balanced_climber", "balanced_climber", "fuel_sprinter"]
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function randomBetween(rng, min, max) {
  return lerp(min, max, rng());
}

function distance(a, b) {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.hypot(dx, dy);
}

function normalizeVector(dx, dy) {
  const magnitude = Math.hypot(dx, dy);
  if (magnitude <= 1e-6) {
    return { x: 0, y: 0 };
  }
  return { x: dx / magnitude, y: dy / magnitude };
}

function advanceTowards(position, target, distanceStep) {
  const dx = target.x - position.x;
  const dy = target.y - position.y;
  const magnitude = Math.hypot(dx, dy);
  if (magnitude <= distanceStep || magnitude <= 1e-6) {
    return { x: target.x, y: target.y, reached: true };
  }
  const direction = normalizeVector(dx, dy);
  return {
    x: position.x + (direction.x * distanceStep),
    y: position.y + (direction.y * distanceStep),
    reached: false
  };
}

function makeRng(seed) {
  let state = seed >>> 0;
  return () => {
    state = (1664525 * state + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

function chooseWeightedOption(options, rng) {
  const totalWeight = options.reduce((sum, option) => sum + option.weight, 0);
  let threshold = rng() * totalWeight;
  for (const option of options) {
    threshold -= option.weight;
    if (threshold <= 0) {
      return option;
    }
  }
  return options[options.length - 1];
}

export function loadGameSpec(spec) {
  const length = spec.field_geometry.field_dimensions.length_m;
  const width = spec.field_geometry.field_dimensions.width_m;
  const hubs = Object.fromEntries(
    spec.field_geometry.scoring_locations
      .filter((item) => item.kind === "hub")
      .map((item) => [item.name, { x: item.pose.x_m, y: item.pose.y_m }])
  );
  const towers = Object.fromEntries(
    spec.field_geometry.scoring_locations
      .filter((item) => item.kind === "tower")
      .map((item) => [item.name, { x: item.pose.x_m, y: item.pose.y_m }])
  );
  const profiles = spec.selected_strategy_assumptions.cycle_assumptions.capability_profiles;
  const zoneData = Object.fromEntries(spec.field_geometry.zones.map((zone) => [zone.name, zone]));
  return {
    year: spec.game_year,
    gameName: spec.game_name,
    field: {
      length,
      width,
      hubs,
      towers,
      blueCollect: { x: length / 2 - 1.2, y: width * 0.33 },
      redCollect: { x: length / 2 + 1.2, y: width * 0.67 },
      blueDepot: { x: 1.3, y: width * 0.18 },
      redDepot: { x: length - 1.3, y: width * 0.82 },
      blueOutpost: { x: 0.65, y: width * 0.78 },
      redOutpost: { x: length - 0.65, y: width * 0.22 },
      zoneData,
      bumpRects: [
        { x: 2.8, y: 1.05, width: 0.55, height: 1.1 },
        { x: 2.8, y: width - 2.15, width: 0.55, height: 1.1 },
        { x: length - 3.35, y: 1.05, width: 0.55, height: 1.1 },
        { x: length - 3.35, y: width - 2.15, width: 0.55, height: 1.1 }
      ],
      trenchRects: [
        { x: 6.4, y: 0.35, width: 1.25, height: 1.0 },
        { x: 6.4, y: width - 1.35, width: 1.25, height: 1.0 },
        { x: length - 7.65, y: 0.35, width: 1.25, height: 1.0 },
        { x: length - 7.65, y: width - 1.35, width: 1.25, height: 1.0 }
      ]
    },
    rules: {
      totalMatchS: spec.normalized_rules.timing.total_s,
      autoS: spec.normalized_rules.timing.auto_s,
      teleopS: spec.normalized_rules.timing.teleop_s,
      endgameS: spec.normalized_rules.timing.endgame_s,
      transitionShiftS: spec.normalized_rules.timing.transition_shift_s,
      shiftDurationS: spec.normalized_rules.timing.alliance_shift_s,
      totalFuel: spec.normalized_rules.match_setup.total_fuel,
      fuelPerDepot: spec.normalized_rules.match_setup.fuel_per_depot,
      fuelPerOutpost: spec.normalized_rules.match_setup.fuel_per_outpost_chute,
      maxPreloadPerRobot: spec.normalized_rules.match_setup.max_preload_per_robot,
      autoFuelPoints: spec.normalized_rules.scoring.find((item) => item.phase === "AUTO" && item.action === "fuel_scored_active_hub")?.points ?? 1,
      teleopFuelPoints: spec.normalized_rules.scoring.find((item) => item.phase === "TELEOP" && item.action === "fuel_scored_active_hub")?.points ?? 1,
      towerPoints: {
        auto1: spec.normalized_rules.scoring.find((item) => item.action === "tower_level_1_auto")?.points ?? 15,
        tele1: spec.normalized_rules.scoring.find((item) => item.action === "tower_level_1_teleop")?.points ?? 10,
        tele2: spec.normalized_rules.scoring.find((item) => item.action === "tower_level_2_teleop")?.points ?? 20,
        tele3: spec.normalized_rules.scoring.find((item) => item.action === "tower_level_3_teleop")?.points ?? 30
      },
      rpThresholds: {
        energized: spec.normalized_rules.ranking_points.find((item) => item.name === "ENERGIZED RP")?.threshold ?? 100,
        supercharged: spec.normalized_rules.ranking_points.find((item) => item.name === "SUPERCHARGED RP")?.threshold ?? 360,
        traversal: spec.normalized_rules.ranking_points.find((item) => item.name === "TRAVERSAL RP")?.threshold ?? 50
      },
      foulPoints: {
        minor: spec.normalized_rules.penalties.find((item) => item.type === "MINOR FOUL")?.points ?? 5,
        major: spec.normalized_rules.penalties.find((item) => item.type === "MAJOR FOUL")?.points ?? 15
      },
      hubStatus: spec.normalized_rules.hub_status
    },
    profiles,
    recommendedStrategy: spec.selected_strategy_assumptions.recommended_strategy,
    rankedStrategies: spec.selected_strategy_assumptions.ranked_strategies
  };
}

function buildRobotProfile(profileName, profile, rng) {
  const cycleMid = (profile.cycle_time_s[0] + profile.cycle_time_s[1]) / 2;
  const autoFuelMid = (profile.auto_fuel_scored[0] + profile.auto_fuel_scored[1]) / 2;
  const accuracyMid = (profile.teleop_accuracy[0] + profile.teleop_accuracy[1]) / 2;
  const climbChoice = chooseWeightedOption(profile.teleop_climb_options, rng);
  return {
    profileName,
    label: profile.label,
    description: profile.description,
    driveSpeedMps: clamp(3.4 + (1.8 / cycleMid), 2.8, 4.9),
    pickupRatePerSecond: clamp(0.75 + (1.3 / cycleMid), 0.55, 1.8),
    scoreRatePerSecond: clamp(0.55 + (1.0 / cycleMid), 0.4, 1.45),
    autoFuelTarget: Math.round(autoFuelMid),
    teleopAccuracy: accuracyMid,
    climbTarget: climbChoice.level,
    climbSuccessBase: profile.climb_success_base,
    defensePressure: profile.defense_pressure,
    autoClimbProbability: profile.auto_l1_probability,
    cycleMid
  };
}

function initialRobotSpawn(index, alliance, game) {
  const rows = [0.22, 0.5, 0.78];
  const y = game.field.width * rows[index];
  if (alliance === "blue") {
    return { x: 0.9, y };
  }
  return { x: game.field.length - 0.9, y };
}

function makeFuelField(game) {
  const fuel = [];
  let idCounter = 0;
  for (let index = 0; index < game.rules.fuelPerDepot; index += 1) {
    fuel.push({ id: `fuel-${idCounter += 1}`, zone: "blue_depot", x: game.field.blueDepot.x + (index % 6) * 0.12, y: game.field.blueDepot.y + Math.floor(index / 6) * 0.12, active: true });
    fuel.push({ id: `fuel-${idCounter += 1}`, zone: "red_depot", x: game.field.redDepot.x - (index % 6) * 0.12, y: game.field.redDepot.y - Math.floor(index / 6) * 0.12, active: true });
  }
  for (let index = 0; index < game.rules.fuelPerOutpost; index += 1) {
    fuel.push({ id: `fuel-${idCounter += 1}`, zone: "blue_outpost", x: game.field.blueOutpost.x + ((index % 4) * 0.11), y: game.field.blueOutpost.y - Math.floor(index / 4) * 0.11, active: true });
    fuel.push({ id: `fuel-${idCounter += 1}`, zone: "red_outpost", x: game.field.redOutpost.x - ((index % 4) * 0.11), y: game.field.redOutpost.y + Math.floor(index / 4) * 0.11, active: true });
  }
  const midfieldFuel = Math.max(240, game.rules.totalFuel - fuel.length - 48);
  const columns = 24;
  const rows = Math.ceil(midfieldFuel / columns);
  for (let index = 0; index < midfieldFuel; index += 1) {
    const row = Math.floor(index / columns);
    const column = index % columns;
    fuel.push({
      id: `fuel-${idCounter += 1}`,
      zone: "neutral",
      x: lerp(game.field.length * 0.34, game.field.length * 0.66, columns <= 1 ? 0 : column / (columns - 1)),
      y: lerp(0.8, game.field.width - 0.8, rows <= 1 ? 0 : row / (rows - 1)),
      active: true
    });
  }
  return fuel;
}

function makeRobot(alliance, slotIndex, controllerType, profileName, game, rng) {
  const spec = buildRobotProfile(profileName, game.profiles[profileName], rng);
  const spawn = initialRobotSpawn(slotIndex, alliance, game);
  const localId = alliance === "blue" ? slotIndex + 1 : slotIndex + 4;
  return {
    id: `${alliance}-${slotIndex + 1}`,
    alliance,
    slotIndex,
    localId,
    name: `${alliance.toUpperCase()}-${slotIndex + 1}`,
    controllerType,
    profileName,
    profile: spec,
    position: { ...spawn },
    velocity: { x: 0, y: 0 },
    heading: alliance === "blue" ? 0 : Math.PI,
    carriedFuel: clamp(Math.round(spec.autoFuelTarget * 0.75), 2, game.rules.maxPreloadPerRobot),
    command: { mode: "auto_cycle", target: null, climbLevel: 0 },
    botMemory: { laneTarget: null, targetRobotId: null },
    score: { fuel: 0, tower: 0, foulsDrawn: 0, foulPointsGiven: 0 },
    autoFuelRemaining: spec.autoFuelTarget,
    defenseTimer: 0,
    climbCommitTimer: 0,
    climbAttemptedLevel: 0,
    climbLocked: false,
    autoClimbResolved: false,
    isClimbing: false,
    isProtected: false,
    disabled: false,
    cycleCooldown: 0,
    pickupBuffer: 0,
    scoreBuffer: 0,
    selected: false
  };
}

function resolveAllianceProfiles(presetOrList, game) {
  if (typeof presetOrList === "string" && presetOrList.includes(",")) {
    const parsed = presetOrList
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
      .slice(0, 3)
      .filter((profileName) => game.profiles[profileName]);
    if (parsed.length === 3) {
      return parsed;
    }
  }
  const profiles = PRESET_STRATEGIES[presetOrList] ?? PRESET_STRATEGIES.balanced_reference;
  return profiles.slice(0, 3);
}

function getActiveHubState(state) {
  const elapsed = state.elapsedTime;
  const remaining = clamp(TOTAL_MATCH_S - elapsed, 0, TOTAL_MATCH_S);
  if (elapsed < AUTO_PHASE_S) {
    return { blue: true, red: true, label: "AUTO" };
  }
  const teleElapsed = elapsed - AUTO_PHASE_S;
  if (remaining <= ENDGAME_THRESHOLD_S) {
    return { blue: true, red: true, label: "ENDGAME" };
  }
  if (teleElapsed < TRANSITION_SHIFT_S) {
    return { blue: true, red: true, label: "TRANSITION" };
  }
  const shiftIndex = clamp(Math.floor((teleElapsed - TRANSITION_SHIFT_S) / SHIFT_DURATION_S), 0, 3);
  const redWonAuto = state.autoFuel.red >= state.autoFuel.blue;
  const shiftTable = redWonAuto
    ? state.game.rules.hubStatus.alliance_shifts.if_red_auto_fuel_greater_or_selected
    : state.game.rules.hubStatus.alliance_shifts.if_blue_auto_fuel_greater_or_selected;
  const shift = shiftTable[shiftIndex] ?? shiftTable[shiftTable.length - 1];
  return {
    blue: shift.blue === "active",
    red: shift.red === "active",
    label: `SHIFT ${shiftIndex + 1}`
  };
}

function towerPositionForAlliance(game, alliance) {
  return alliance === "blue" ? game.field.towers.blue_tower : game.field.towers.red_tower;
}

function hubPositionForAlliance(game, alliance) {
  return alliance === "blue" ? game.field.hubs.blue_hub : game.field.hubs.red_hub;
}

function collectGoalForAlliance(game, alliance) {
  return alliance === "blue" ? game.field.blueCollect : game.field.redCollect;
}

function depotGoalForAlliance(game, alliance) {
  return alliance === "blue" ? game.field.blueDepot : game.field.redDepot;
}

function addEvent(state, text, severity = "info") {
  state.events.unshift({
    id: `${state.tick}-${state.events.length}`,
    timeLabel: formatTime(TOTAL_MATCH_S - state.elapsedTime),
    text,
    severity
  });
  state.events = state.events.slice(0, 60);
}

function formatTime(remainingSeconds) {
  const clamped = Math.max(0, remainingSeconds);
  const minutes = Math.floor(clamped / 60);
  const seconds = Math.floor(clamped % 60);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function scoreFuel(robot, state, dt) {
  if (robot.carriedFuel <= 0) {
    return;
  }
  const active = state.activeHubs[robot.alliance];
  if (!active) {
    if (state.tick % 30 === 0) {
      addEvent(state, `${robot.name} reached an inactive HUB and wasted a scoring attempt.`, "warn");
    }
    return;
  }
  robot.scoreBuffer += robot.profile.scoreRatePerSecond * dt;
  let scored = 0;
  while (robot.scoreBuffer >= 1 && robot.carriedFuel > 0) {
    robot.scoreBuffer -= 1;
    robot.carriedFuel -= 1;
    if (state.rng() <= robot.profile.teleopAccuracy) {
      scored += 1;
    }
  }
  if (scored > 0) {
    const pointsPerFuel = state.elapsedTime < AUTO_PHASE_S ? state.game.rules.autoFuelPoints : state.game.rules.teleopFuelPoints;
    state.score[robot.alliance].fuel += scored * pointsPerFuel;
    robot.score.fuel += scored * pointsPerFuel;
    if (state.elapsedTime < AUTO_PHASE_S) {
      state.autoFuel[robot.alliance] += scored;
    }
    state.recycledFuelBuffer[robot.alliance] += scored;
    addEvent(state, `${robot.name} scored ${scored} FUEL for ${scored * pointsPerFuel} points.`, "score");
  }
}

function attemptPickup(robot, state, dt) {
  if (robot.carriedFuel >= MAX_CARRY) {
    return;
  }
  robot.pickupBuffer += robot.profile.pickupRatePerSecond * dt;
  let grabbed = 0;
  while (robot.pickupBuffer >= 1 && robot.carriedFuel < MAX_CARRY) {
    const nearbyFuel = state.fuel.find((fuel) => fuel.active && distance(robot.position, fuel) <= PICKUP_RANGE_M);
    if (!nearbyFuel) {
      break;
    }
    nearbyFuel.active = false;
    robot.carriedFuel += 1;
    grabbed += 1;
    robot.pickupBuffer -= 1;
  }
  if (grabbed > 0 && (state.tick % 10 === 0 || grabbed >= 2)) {
    addEvent(state, `${robot.name} collected ${grabbed} FUEL.`, "pickup");
  }
}

function recycleFuel(state) {
  const recycledTotal = state.recycledFuelBuffer.blue + state.recycledFuelBuffer.red;
  if (recycledTotal <= 0) {
    return;
  }
  const neutralFuel = state.fuel.filter((fuel) => !fuel.active);
  let remainingBlue = state.recycledFuelBuffer.blue;
  let remainingRed = state.recycledFuelBuffer.red;
  for (const fuel of neutralFuel) {
    if (remainingBlue <= 0 && remainingRed <= 0) {
      break;
    }
    fuel.active = true;
    fuel.zone = "neutral_recycled";
    fuel.x = randomBetween(state.rng, state.game.field.length * 0.44, state.game.field.length * 0.56);
    fuel.y = randomBetween(state.rng, 1.1, state.game.field.width - 1.1);
    if (remainingBlue > 0) {
      remainingBlue -= 1;
    } else if (remainingRed > 0) {
      remainingRed -= 1;
    }
  }
  state.recycledFuelBuffer.blue = remainingBlue;
  state.recycledFuelBuffer.red = remainingRed;
}

function assignTowerPoints(robot, state, level, auto) {
  const points = auto
    ? state.game.rules.towerPoints.auto1
    : level === 3
      ? state.game.rules.towerPoints.tele3
      : level === 2
        ? state.game.rules.towerPoints.tele2
        : state.game.rules.towerPoints.tele1;
  state.score[robot.alliance].tower += points;
  robot.score.tower += points;
  robot.isClimbing = true;
  robot.climbLocked = true;
  robot.climbAttemptedLevel = level;
  addEvent(state, `${robot.name} completed a level ${level} tower climb for ${points} points.`, "score");
}

function attemptAutoClimb(robot, state) {
  if (robot.climbLocked || robot.autoClimbResolved || state.elapsedTime >= AUTO_PHASE_S - 1.5) {
    return;
  }
  if (robot.autoFuelRemaining > 0 || robot.carriedFuel > 4) {
    return;
  }
  robot.autoClimbResolved = true;
  if (state.rng() <= robot.profile.autoClimbProbability) {
    const tower = towerPositionForAlliance(state.game, robot.alliance);
    robot.position.x = tower.x;
    robot.position.y = tower.y;
    assignTowerPoints(robot, state, 1, true);
  } else {
    addEvent(state, `${robot.name} declined or missed an AUTO climb attempt.`, "info");
  }
}

function attemptTeleopClimb(robot, state, dt) {
  if (robot.climbLocked || !robot.command.climbLevel) {
    return;
  }
  const tower = towerPositionForAlliance(state.game, robot.alliance);
  if (distance(robot.position, tower) > CLIMB_RANGE_M) {
    return;
  }
  const targetLevel = robot.command.climbLevel;
  const baseTime = targetLevel === 3 ? 9 : targetLevel === 2 ? 6.5 : 4.5;
  robot.climbCommitTimer += dt;
  if (robot.climbCommitTimer < baseTime) {
    return;
  }
  const successModifier = targetLevel === robot.profile.climbTarget ? 0.0 : targetLevel > robot.profile.climbTarget ? -0.18 : 0.06;
  const success = state.rng() <= clamp(robot.profile.climbSuccessBase + successModifier, 0.32, 0.97);
  if (success) {
    assignTowerPoints(robot, state, targetLevel, false);
  } else {
    addEvent(state, `${robot.name} failed a level ${targetLevel} climb attempt.`, "warn");
    robot.climbLocked = true;
    robot.climbAttemptedLevel = targetLevel;
  }
}

function applyDefense(robot, state, dt) {
  const opponents = state.robots.filter((candidate) => candidate.alliance !== robot.alliance && !candidate.isClimbing);
  for (const opponent of opponents) {
    if (distance(robot.position, opponent.position) <= CONTACT_DISTANCE_M) {
      opponent.defenseTimer = clamp(opponent.defenseTimer + (dt * (0.5 + robot.profile.defensePressure)), 0, 3);
      if (state.remainingTime <= ENDGAME_THRESHOLD_S && distance(opponent.position, towerPositionForAlliance(state.game, opponent.alliance)) <= TOWER_PROTECTION_RANGE_M) {
        state.score[robot.alliance === "blue" ? "red" : "blue"].fouls += state.game.rules.foulPoints.major;
        robot.score.foulPointsGiven += state.game.rules.foulPoints.major;
        addEvent(state, `${robot.name} committed a tower-protection major foul on ${opponent.name}.`, "foul");
      }
    }
  }
}

function decayDefense(robot, dt) {
  robot.defenseTimer = Math.max(0, robot.defenseTimer - dt * 0.75);
}

function desiredTarget(robot, state) {
  const mode = robot.command.mode;
  if (robot.command.target) {
    return robot.command.target;
  }
  if (mode === "collect") {
    return collectGoalForAlliance(state.game, robot.alliance);
  }
  if (mode === "score") {
    return hubPositionForAlliance(state.game, robot.alliance);
  }
  if (mode === "defend") {
    const opposingHub = hubPositionForAlliance(state.game, robot.alliance === "blue" ? "red" : "blue");
    return { x: lerp(state.game.field.length / 2, opposingHub.x, 0.55), y: opposingHub.y };
  }
  if (mode === "climb") {
    return towerPositionForAlliance(state.game, robot.alliance);
  }
  if (mode === "auto_cycle") {
    if (robot.carriedFuel >= Math.max(3, MAX_CARRY - 2)) {
      return hubPositionForAlliance(state.game, robot.alliance);
    }
    if (state.remainingTime <= ENDGAME_THRESHOLD_S && robot.profile.climbTarget > 0) {
      return towerPositionForAlliance(state.game, robot.alliance);
    }
    return collectGoalForAlliance(state.game, robot.alliance);
  }
  return hubPositionForAlliance(state.game, robot.alliance);
}

function updateBotCommand(robot, state) {
  if (robot.controllerType !== "bot") {
    return;
  }
  const remaining = state.remainingTime;
  const active = state.activeHubs[robot.alliance];
  if (remaining <= ENDGAME_THRESHOLD_S && robot.profile.climbTarget > 0 && !robot.climbLocked) {
    robot.command.mode = "climb";
    robot.command.climbLevel = robot.profile.climbTarget;
    robot.command.target = towerPositionForAlliance(state.game, robot.alliance);
    return;
  }
  if (!active) {
    if (robot.profile.defensePressure >= 0.55) {
      robot.command.mode = "defend";
      robot.command.climbLevel = 0;
      robot.command.target = desiredTarget(robot, state);
      return;
    }
    robot.command.mode = robot.carriedFuel >= 8 ? "hold" : "collect";
    robot.command.climbLevel = 0;
    robot.command.target = robot.command.mode === "hold" ? depotGoalForAlliance(state.game, robot.alliance) : collectGoalForAlliance(state.game, robot.alliance);
    return;
  }
  if (robot.carriedFuel >= 4 || robot.autoFuelRemaining > 0) {
    robot.command.mode = "score";
  } else {
    robot.command.mode = "collect";
  }
  robot.command.climbLevel = 0;
  robot.command.target = desiredTarget(robot, state);
}

function moveRobot(robot, state, dt) {
  if (robot.disabled || robot.isClimbing) {
    return;
  }
  const target = desiredTarget(robot, state);
  const pressurePenalty = robot.defenseTimer * DEFENSE_DRAG_FACTOR;
  const speed = clamp(robot.profile.driveSpeedMps - pressurePenalty, 0.8, robot.profile.driveSpeedMps);
  const moved = advanceTowards(robot.position, target, speed * dt);
  robot.velocity.x = moved.x - robot.position.x;
  robot.velocity.y = moved.y - robot.position.y;
  robot.position.x = clamp(moved.x, ROBOT_RADIUS_M, state.game.field.length - ROBOT_RADIUS_M);
  robot.position.y = clamp(moved.y, ROBOT_RADIUS_M, state.game.field.width - ROBOT_RADIUS_M);
  if (Math.abs(robot.velocity.x) > 1e-4 || Math.abs(robot.velocity.y) > 1e-4) {
    robot.heading = Math.atan2(robot.velocity.y, robot.velocity.x);
  }
}

function updateHumanHoldState(robot, state) {
  if (robot.controllerType !== "human") {
    return;
  }
  if (robot.command.mode === "hold") {
    robot.command.target = depotGoalForAlliance(state.game, robot.alliance);
  }
}

function updateRobot(robot, state, dt) {
  updateHumanHoldState(robot, state);
  updateBotCommand(robot, state);
  decayDefense(robot, dt);
  robot.cycleCooldown = Math.max(0, robot.cycleCooldown - dt);
  moveRobot(robot, state, dt);

  if (state.elapsedTime < AUTO_PHASE_S) {
    if (robot.autoFuelRemaining > 0 && distance(robot.position, hubPositionForAlliance(state.game, robot.alliance)) <= SCORE_RANGE_M) {
      const take = Math.min(robot.autoFuelRemaining, Math.max(1, Math.floor(robot.profile.scoreRatePerSecond * dt * 3)));
      robot.autoFuelRemaining -= take;
      robot.carriedFuel = Math.max(0, robot.carriedFuel - take);
      state.autoFuel[robot.alliance] += take;
      state.score[robot.alliance].fuel += take * state.game.rules.autoFuelPoints;
      robot.score.fuel += take * state.game.rules.autoFuelPoints;
    }
    attemptPickup(robot, state, dt);
    attemptAutoClimb(robot, state);
    return;
  }

  if (robot.command.mode === "defend") {
    applyDefense(robot, state, dt);
  }
  if (distance(robot.position, collectGoalForAlliance(state.game, robot.alliance)) <= 1.0 || distance(robot.position, depotGoalForAlliance(state.game, robot.alliance)) <= 0.9) {
    attemptPickup(robot, state, dt);
  }
  if (distance(robot.position, hubPositionForAlliance(state.game, robot.alliance)) <= SCORE_RANGE_M && robot.command.mode !== "collect") {
    scoreFuel(robot, state, dt);
  }
  if (robot.command.mode === "climb") {
    attemptTeleopClimb(robot, state, dt);
  }
}

function computeRankingPoints(state) {
  const result = {
    blue: 0,
    red: 0,
    bonuses: {
      blue: [],
      red: []
    }
  };
  const blueTotal = state.score.blue.fuel + state.score.blue.tower - state.score.blue.fouls;
  const redTotal = state.score.red.fuel + state.score.red.tower - state.score.red.fouls;
  if (blueTotal > redTotal) {
    result.blue += 3;
  } else if (redTotal > blueTotal) {
    result.red += 3;
  } else {
    result.blue += 1;
    result.red += 1;
  }
  for (const alliance of ["blue", "red"]) {
    if (state.score[alliance].fuel >= state.game.rules.rpThresholds.energized) {
      result[alliance] += 1;
      result.bonuses[alliance].push("ENERGIZED RP");
    }
    if (state.score[alliance].fuel >= state.game.rules.rpThresholds.supercharged) {
      result[alliance] += 1;
      result.bonuses[alliance].push("SUPERCHARGED RP");
    }
    if (state.score[alliance].tower >= state.game.rules.rpThresholds.traversal) {
      result[alliance] += 1;
      result.bonuses[alliance].push("TRAVERSAL RP");
    }
  }
  return result;
}

function summarizeState(state) {
  const totalBlue = state.score.blue.fuel + state.score.blue.tower + state.score.blue.fouls;
  const totalRed = state.score.red.fuel + state.score.red.tower + state.score.red.fouls;
  return {
    score: {
      blue: { ...state.score.blue, total: totalBlue },
      red: { ...state.score.red, total: totalRed }
    },
    rankingPoints: computeRankingPoints(state),
    activeHubs: state.activeHubs,
    phaseLabel: state.phaseLabel,
    timeRemaining: state.remainingTime,
    autoFuel: { ...state.autoFuel }
  };
}

export function createMatch(game, options = {}) {
  const rng = makeRng(options.seed ?? 2026);
  const bluePreset = resolveAllianceProfiles(options.bluePreset ?? "safe_rp_foundation", game);
  const redPreset = resolveAllianceProfiles(options.redPreset ?? "balanced_reference", game);
  const humanSlots = new Set(options.humanSlots ?? ["blue-1"]);
  const robots = [];
  for (let index = 0; index < 3; index += 1) {
    robots.push(makeRobot("blue", index, humanSlots.has(`blue-${index + 1}`) ? "human" : "bot", bluePreset[index], game, rng));
  }
  for (let index = 0; index < 3; index += 1) {
    robots.push(makeRobot("red", index, humanSlots.has(`red-${index + 1}`) ? "human" : "bot", redPreset[index], game, rng));
  }
  const state = {
    game,
    tick: 0,
    elapsedTime: 0,
    remainingTime: game.rules.totalMatchS,
    running: false,
    completed: false,
    phaseLabel: "AUTO",
    activeHubs: { blue: true, red: true, label: "AUTO" },
    robots,
    fuel: makeFuelField(game),
    score: {
      blue: { fuel: 0, tower: 0, fouls: 0 },
      red: { fuel: 0, tower: 0, fouls: 0 }
    },
    rng,
    autoFuel: { blue: 0, red: 0 },
    recycledFuelBuffer: { blue: 0, red: 0 },
    events: [],
    selection: robots[0].id,
    presets: {
      blue: typeof options.bluePreset === "string" ? options.bluePreset : "safe_rp_foundation",
      red: typeof options.redPreset === "string" ? options.redPreset : "balanced_reference"
    }
  };
  state.robots[0].selected = true;
  addEvent(state, `Loaded ${game.gameName} 2D sandbox.`, "info");
  return state;
}

export function resetMatch(game, options = {}) {
  return createMatch(game, options);
}

export function setSelection(state, robotId) {
  state.selection = robotId;
  for (const robot of state.robots) {
    robot.selected = robot.id === robotId;
  }
}

export function setRobotController(state, robotId, controllerType) {
  const robot = state.robots.find((candidate) => candidate.id === robotId);
  if (!robot) {
    return;
  }
  robot.controllerType = controllerType;
}

export function setRobotProfile(state, robotId, profileName) {
  const robot = state.robots.find((candidate) => candidate.id === robotId);
  if (!robot || !state.game.profiles[profileName]) {
    return;
  }
  const rng = makeRng(state.tick + robot.localId + 1);
  robot.profileName = profileName;
  robot.profile = buildRobotProfile(profileName, state.game.profiles[profileName], rng);
  robot.autoFuelRemaining = robot.profile.autoFuelTarget;
  robot.carriedFuel = clamp(robot.carriedFuel, 0, MAX_CARRY);
}

export function setRobotCommand(state, robotId, partialCommand) {
  const robot = state.robots.find((candidate) => candidate.id === robotId);
  if (!robot) {
    return;
  }
  robot.command = {
    ...robot.command,
    ...partialCommand
  };
}

export function issueMoveCommand(state, robotId, target) {
  setRobotCommand(state, robotId, { target: { x: target.x, y: target.y } });
}

export function getRobotObservation(state, robotId) {
  const robot = state.robots.find((candidate) => candidate.id === robotId);
  if (!robot) {
    return null;
  }
  const opponents = state.robots.filter((candidate) => candidate.alliance !== robot.alliance);
  const closestOpponent = opponents.reduce((best, candidate) => {
    if (!best || distance(robot.position, candidate.position) < distance(robot.position, best.position)) {
      return candidate;
    }
    return best;
  }, null);
  return {
    robot: {
      id: robot.id,
      alliance: robot.alliance,
      position: { ...robot.position },
      carriedFuel: robot.carriedFuel,
      defenseTimer: robot.defenseTimer,
      command: { ...robot.command },
      climbLocked: robot.climbLocked,
      profileName: robot.profileName
    },
    match: {
      elapsedTime: state.elapsedTime,
      remainingTime: state.remainingTime,
      phaseLabel: state.phaseLabel,
      activeHubs: { ...state.activeHubs },
      score: summarizeState(state).score
    },
    targets: {
      allianceHub: hubPositionForAlliance(state.game, robot.alliance),
      allianceTower: towerPositionForAlliance(state.game, robot.alliance),
      collection: collectGoalForAlliance(state.game, robot.alliance),
      opponentHub: hubPositionForAlliance(state.game, robot.alliance === "blue" ? "red" : "blue")
    },
    closestOpponent: closestOpponent
      ? {
          id: closestOpponent.id,
          position: { ...closestOpponent.position },
          distance: distance(robot.position, closestOpponent.position),
          isClimbing: closestOpponent.isClimbing
        }
      : null
  };
}

export function stepMatch(state, dt) {
  if (state.completed) {
    return summarizeState(state);
  }
  state.tick += 1;
  state.elapsedTime = clamp(state.elapsedTime + dt, 0, state.game.rules.totalMatchS);
  state.remainingTime = clamp(state.game.rules.totalMatchS - state.elapsedTime, 0, state.game.rules.totalMatchS);
  state.activeHubs = getActiveHubState(state);
  state.phaseLabel = state.activeHubs.label;

  for (const robot of state.robots) {
    updateRobot(robot, state, dt);
  }
  recycleFuel(state);

  if (state.elapsedTime >= state.game.rules.totalMatchS) {
    state.completed = true;
    state.running = false;
      const summary = summarizeState(state);
      const winner = summary.score.blue.total === summary.score.red.total
      ? "Tie"
      : summary.score.blue.total > summary.score.red.total
        ? "Blue wins"
        : "Red wins";
    addEvent(state, `Match over: ${winner}.`, "info");
    return summary;
  }

  return summarizeState(state);
}

export function getPresets() {
  return { ...PRESET_STRATEGIES };
}

export function buildHeadlessSummary(state) {
  const summary = summarizeState(state);
  return {
    score: summary.score,
    rankingPoints: summary.rankingPoints,
    events: state.events.slice(0, 12),
    robotBreakdown: state.robots.map((robot) => ({
      id: robot.id,
      alliance: robot.alliance,
      profileName: robot.profileName,
      controllerType: robot.controllerType,
      fuelPoints: robot.score.fuel,
      towerPoints: robot.score.tower,
      foulPointsGiven: robot.score.foulPointsGiven,
      carriedFuelAtEnd: robot.carriedFuel,
      climbLocked: robot.climbLocked,
      climbAttemptedLevel: robot.climbAttemptedLevel
    }))
  };
}

export {
  AUTO_PHASE_S,
  TELEOP_PHASE_S,
  ENDGAME_THRESHOLD_S,
  TOTAL_MATCH_S,
  PRESET_STRATEGIES,
  ROBOT_RADIUS_M,
  HUB_RADIUS_M,
  TOWER_RADIUS_M,
  FUEL_RADIUS_M,
  formatTime
};
