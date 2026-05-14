import {
  FUEL_RADIUS_M,
  HUB_RADIUS_M,
  PRESET_STRATEGIES,
  ROBOT_RADIUS_M,
  TOWER_RADIUS_M,
  buildHeadlessSummary,
  createMatch,
  formatTime,
  getPresets,
  issueMoveCommand,
  loadGameSpec,
  resetMatch,
  setRobotCommand,
  setRobotController,
  setRobotProfile,
  setSelection,
  stepMatch
} from "./engine.js";

const canvas = document.getElementById("arenaCanvas");
const context = canvas.getContext("2d");
const matchHud = document.getElementById("matchHud");
const blueRoster = document.getElementById("blueRoster");
const redRoster = document.getElementById("redRoster");
const selectedRobot = document.getElementById("selectedRobot");
const selectedRobotCommands = document.getElementById("selectedRobotCommands");
const eventLog = document.getElementById("eventLog");
const startPauseButton = document.getElementById("startPauseButton");
const stepButton = document.getElementById("stepButton");
const resetButton = document.getElementById("resetButton");
const speedSelect = document.getElementById("speedSelect");
const bluePresetSelect = document.getElementById("bluePresetSelect");
const redPresetSelect = document.getElementById("redPresetSelect");

const COMMAND_BUTTONS = [
  { label: "Auto Cycle", mode: "auto_cycle", key: "q" },
  { label: "Collect", mode: "collect", key: "w" },
  { label: "Score", mode: "score", key: "e" },
  { label: "Defend", mode: "defend", key: "d" },
  { label: "Climb L1", mode: "climb", climbLevel: 1, key: "z" },
  { label: "Climb L2", mode: "climb", climbLevel: 2, key: "x" },
  { label: "Climb L3", mode: "climb", climbLevel: 3, key: "c" },
  { label: "Hold", mode: "hold" }
];

let game;
let state;
let simulationSpeed = 1;
let previousFrameTime = performance.now();

function getSelectedRobot() {
  return state.robots.find((robot) => robot.id === state.selection) ?? state.robots[0];
}

function populatePresetSelect(select, current) {
  select.innerHTML = "";
  for (const presetName of Object.keys(getPresets())) {
    const option = document.createElement("option");
    option.value = presetName;
    option.textContent = presetName.replaceAll("_", " ");
    option.selected = presetName === current;
    select.appendChild(option);
  }
}

function matchOptionsFromUi() {
  return {
    bluePreset: bluePresetSelect.value,
    redPreset: redPresetSelect.value,
    humanSlots: state
      ? state.robots.filter((robot) => robot.controllerType === "human").map((robot) => robot.id)
      : ["blue-1"]
  };
}

function rebuildMatch(options = {}) {
  state = resetMatch(game, {
    bluePreset: options.bluePreset ?? bluePresetSelect.value,
    redPreset: options.redPreset ?? redPresetSelect.value,
    humanSlots: options.humanSlots ?? ["blue-1"]
  });
  populatePresetSelect(bluePresetSelect, state.presets.blue);
  populatePresetSelect(redPresetSelect, state.presets.red);
  render();
}

function worldToScreen(x, y) {
  const padding = 38;
  const scale = Math.min((canvas.width - (padding * 2)) / game.field.length, (canvas.height - (padding * 2)) / game.field.width);
  return {
    x: padding + (x * scale),
    y: padding + (y * scale),
    scale
  };
}

function screenToWorld(x, y) {
  const origin = worldToScreen(0, 0);
  return {
    x: (x - origin.x) / origin.scale,
    y: (y - origin.y) / origin.scale
  };
}

function drawField() {
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#08111e";
  context.fillRect(0, 0, canvas.width, canvas.height);

  const topLeft = worldToScreen(0, 0);
  const bottomRight = worldToScreen(game.field.length, game.field.width);
  const width = bottomRight.x - topLeft.x;
  const height = bottomRight.y - topLeft.y;

  context.save();
  context.translate(topLeft.x, topLeft.y);

  context.fillStyle = "#1d2f4f";
  context.fillRect(0, 0, width * 0.24, height);
  context.fillStyle = "#452131";
  context.fillRect(width * 0.76, 0, width * 0.24, height);
  context.fillStyle = "#102237";
  context.fillRect(width * 0.24, 0, width * 0.52, height);

  context.strokeStyle = "rgba(255,255,255,0.18)";
  context.lineWidth = 2;
  context.strokeRect(0, 0, width, height);

  context.strokeStyle = "rgba(255,255,255,0.22)";
  context.beginPath();
  context.moveTo(width / 2, 0);
  context.lineTo(width / 2, height);
  context.stroke();

  context.fillStyle = "rgba(255,255,255,0.08)";
  for (const rect of game.field.bumpRects) {
    const screen = worldToScreen(rect.x, rect.y);
    context.fillRect(screen.x - topLeft.x, screen.y - topLeft.y, rect.width * screen.scale, rect.height * screen.scale);
  }
  context.fillStyle = "rgba(244,211,94,0.08)";
  for (const rect of game.field.trenchRects) {
    const screen = worldToScreen(rect.x, rect.y);
    context.fillRect(screen.x - topLeft.x, screen.y - topLeft.y, rect.width * screen.scale, rect.height * screen.scale);
  }

  const hubState = state.activeHubs;
  for (const [alliance, hub] of [["blue", game.field.hubs.blue_hub], ["red", game.field.hubs.red_hub]]) {
    const screen = worldToScreen(hub.x, hub.y);
    context.beginPath();
    context.fillStyle = hubState[alliance]
      ? alliance === "blue" ? "rgba(77,141,255,0.75)" : "rgba(255,103,121,0.75)"
      : "rgba(120,120,120,0.42)";
    context.arc(screen.x - topLeft.x, screen.y - topLeft.y, HUB_RADIUS_M * screen.scale, 0, Math.PI * 2);
    context.fill();
  }

  for (const tower of [game.field.towers.blue_tower, game.field.towers.red_tower]) {
    const screen = worldToScreen(tower.x, tower.y);
    context.beginPath();
    context.fillStyle = "rgba(244,211,94,0.22)";
    context.arc(screen.x - topLeft.x, screen.y - topLeft.y, TOWER_RADIUS_M * screen.scale, 0, Math.PI * 2);
    context.fill();
  }

  context.restore();
}

function drawFuel() {
  for (const fuel of state.fuel) {
    if (!fuel.active) {
      continue;
    }
    const screen = worldToScreen(fuel.x, fuel.y);
    context.beginPath();
    context.fillStyle = "#f4d35e";
    context.arc(screen.x, screen.y, Math.max(2, FUEL_RADIUS_M * screen.scale), 0, Math.PI * 2);
    context.fill();
  }
}

function drawRobots() {
  for (const robot of state.robots) {
    const screen = worldToScreen(robot.position.x, robot.position.y);
    const radius = ROBOT_RADIUS_M * screen.scale;

    context.beginPath();
    context.fillStyle = robot.alliance === "blue" ? "#4d8dff" : "#ff6779";
    context.arc(screen.x, screen.y, radius, 0, Math.PI * 2);
    context.fill();

    context.save();
    context.translate(screen.x, screen.y);
    context.rotate(robot.heading);
    context.strokeStyle = "rgba(255,255,255,0.8)";
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(0, 0);
    context.lineTo(radius, 0);
    context.stroke();
    context.restore();

    if (robot.selected) {
      context.beginPath();
      context.strokeStyle = "rgba(79,209,197,0.95)";
      context.lineWidth = 3;
      context.arc(screen.x, screen.y, radius + 5, 0, Math.PI * 2);
      context.stroke();
    }

    if (robot.command.target) {
      const target = worldToScreen(robot.command.target.x, robot.command.target.y);
      context.strokeStyle = "rgba(255,255,255,0.16)";
      context.lineWidth = 1.5;
      context.beginPath();
      context.moveTo(screen.x, screen.y);
      context.lineTo(target.x, target.y);
      context.stroke();
    }

    context.fillStyle = "rgba(0,0,0,0.65)";
    context.fillRect(screen.x - radius, screen.y + radius + 3, radius * 2, 7);
    context.fillStyle = "#f4d35e";
    context.fillRect(screen.x - radius, screen.y + radius + 3, (radius * 2) * (robot.carriedFuel / 12), 7);

    context.fillStyle = "#ffffff";
    context.font = "12px Inter, sans-serif";
    context.textAlign = "center";
    context.fillText(String(robot.localId), screen.x, screen.y + 4);
  }
}

function drawOverlay() {
  context.fillStyle = "rgba(255,255,255,0.9)";
  context.font = "18px Inter, sans-serif";
  context.textAlign = "left";
  context.fillText(`Phase: ${state.phaseLabel}`, 18, 28);
  context.fillText(`Time: ${formatTime(state.remainingTime)}`, 18, 52);
  context.fillText(`Blue ${state.score.blue.fuel + state.score.blue.tower - state.score.blue.fouls} - Red ${state.score.red.fuel + state.score.red.tower - state.score.red.fouls}`, 18, 76);
}

function renderHud() {
  const blueTotal = state.score.blue.fuel + state.score.blue.tower - state.score.blue.fouls;
  const redTotal = state.score.red.fuel + state.score.red.tower - state.score.red.fouls;
  matchHud.innerHTML = "";
  const cards = [
    { title: "Time Remaining", value: formatTime(state.remainingTime) },
    { title: "Phase", value: state.phaseLabel },
    { title: "Blue Score", value: `${blueTotal}` },
    { title: "Red Score", value: `${redTotal}` },
    { title: "Blue Hubs", value: state.activeHubs.blue ? "Active" : "Inactive" },
    { title: "Red Hubs", value: state.activeHubs.red ? "Active" : "Inactive" },
    { title: "Blue Auto Fuel", value: `${state.autoFuel.blue}` },
    { title: "Red Auto Fuel", value: `${state.autoFuel.red}` }
  ];
  for (const card of cards) {
    const element = document.createElement("div");
    element.className = "hud-card";
    element.innerHTML = `<strong>${card.value}</strong><span>${card.title}</span>`;
    matchHud.appendChild(element);
  }
}

function rosterCard(robot) {
  const card = document.createElement("div");
  card.className = `robot-card ${robot.alliance}${robot.selected ? " selected" : ""}`;
  card.addEventListener("click", () => {
    setSelection(state, robot.id);
    render();
  });

  const header = document.createElement("div");
  header.className = "robot-card-header";
  header.innerHTML = `<strong>${robot.name}</strong><small>${robot.profile.label}</small>`;
  card.appendChild(header);

  const status = document.createElement("div");
  status.className = "muted";
  status.textContent = `${robot.controllerType.toUpperCase()} | ${robot.command.mode}${robot.command.climbLevel ? ` L${robot.command.climbLevel}` : ""} | carry ${robot.carriedFuel}`;
  card.appendChild(status);

  const controllerSelect = document.createElement("select");
  for (const optionValue of ["human", "bot"]) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionValue;
    option.selected = robot.controllerType === optionValue;
    controllerSelect.appendChild(option);
  }
  controllerSelect.addEventListener("change", (event) => {
    setRobotController(state, robot.id, event.target.value);
    render();
  });
  card.appendChild(controllerSelect);

  const profileSelect = document.createElement("select");
  for (const profileName of Object.keys(game.profiles)) {
    const option = document.createElement("option");
    option.value = profileName;
    option.textContent = game.profiles[profileName].label;
    option.selected = robot.profileName === profileName;
    profileSelect.appendChild(option);
  }
  profileSelect.addEventListener("change", (event) => {
    setRobotProfile(state, robot.id, event.target.value);
    render();
  });
  card.appendChild(profileSelect);
  return card;
}

function renderRosters() {
  blueRoster.innerHTML = "";
  redRoster.innerHTML = "";
  for (const robot of state.robots.filter((item) => item.alliance === "blue")) {
    blueRoster.appendChild(rosterCard(robot));
  }
  for (const robot of state.robots.filter((item) => item.alliance === "red")) {
    redRoster.appendChild(rosterCard(robot));
  }
}

function renderSelectedRobot() {
  const robot = getSelectedRobot();
  selectedRobot.innerHTML = `
    <div class="selected-summary">
      <div><strong>${robot.name}</strong> <span class="value">${robot.controllerType.toUpperCase()}</span></div>
      <div><span class="muted">Profile</span><div>${robot.profile.label}</div></div>
      <div><span class="muted">Carried fuel</span><div>${robot.carriedFuel}</div></div>
      <div><span class="muted">Command</span><div>${robot.command.mode}${robot.command.climbLevel ? ` (L${robot.command.climbLevel})` : ""}</div></div>
      <div><span class="muted">Fuel points</span><div>${robot.score.fuel}</div></div>
      <div><span class="muted">Tower points</span><div>${robot.score.tower}</div></div>
    </div>
  `;

  selectedRobotCommands.innerHTML = "";
  for (const command of COMMAND_BUTTONS) {
    const button = document.createElement("button");
    button.textContent = command.label;
    const active = robot.command.mode === command.mode && ((command.climbLevel ?? 0) === (robot.command.climbLevel ?? 0));
    if (active) {
      button.classList.add("active");
    }
    button.addEventListener("click", () => {
      setRobotCommand(state, robot.id, {
        mode: command.mode,
        climbLevel: command.climbLevel ?? 0,
        target: null
      });
      render();
    });
    selectedRobotCommands.appendChild(button);
  }
}

function renderEventLog() {
  eventLog.innerHTML = "";
  for (const event of state.events) {
    const element = document.createElement("div");
    element.className = "event-item";
    element.innerHTML = `<span class="time">${event.timeLabel}</span>${event.text}`;
    eventLog.appendChild(element);
  }
}

function render() {
  drawField();
  drawFuel();
  drawRobots();
  drawOverlay();
  renderHud();
  renderRosters();
  renderSelectedRobot();
  renderEventLog();
  startPauseButton.textContent = state.running ? "Pause" : (state.completed ? "Replay" : "Start");
}

function tick(now) {
  const deltaSeconds = Math.min(0.05, (now - previousFrameTime) / 1000);
  previousFrameTime = now;
  if (state.running && !state.completed) {
    const simDt = deltaSeconds * simulationSpeed;
    stepMatch(state, simDt);
  }
  render();
  requestAnimationFrame(tick);
}

function setupCanvasInteractions() {
  canvas.addEventListener("click", (event) => {
    const bounds = canvas.getBoundingClientRect();
    const scaleX = canvas.width / bounds.width;
    const scaleY = canvas.height / bounds.height;
    const localX = (event.clientX - bounds.left) * scaleX;
    const localY = (event.clientY - bounds.top) * scaleY;
    const target = screenToWorld(localX, localY);
    const robot = getSelectedRobot();
    issueMoveCommand(state, robot.id, {
      x: Math.max(0.4, Math.min(game.field.length - 0.4, target.x)),
      y: Math.max(0.4, Math.min(game.field.width - 0.4, target.y))
    });
    render();
  });
}

function setupControls() {
  startPauseButton.addEventListener("click", () => {
    if (state.completed) {
      rebuildMatch(matchOptionsFromUi());
      state.running = true;
      return;
    }
    state.running = !state.running;
  });
  stepButton.addEventListener("click", () => {
    if (!state.completed) {
      stepMatch(state, 1.0);
      render();
    }
  });
  resetButton.addEventListener("click", () => rebuildMatch(matchOptionsFromUi()));
  speedSelect.addEventListener("change", () => {
    simulationSpeed = Number(speedSelect.value);
  });
  bluePresetSelect.addEventListener("change", () => rebuildMatch({ ...matchOptionsFromUi(), bluePreset: bluePresetSelect.value }));
  redPresetSelect.addEventListener("change", () => rebuildMatch({ ...matchOptionsFromUi(), redPreset: redPresetSelect.value }));

  window.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLSelectElement || event.target instanceof HTMLButtonElement) {
      return;
    }
    const key = event.key.toLowerCase();
    if (key === " ") {
      event.preventDefault();
      state.running = !state.running;
      return;
    }
    if (key === "r") {
      rebuildMatch(matchOptionsFromUi());
      return;
    }
    if (/^[1-6]$/.test(key)) {
      const index = Number(key) - 1;
      const robot = state.robots[index];
      if (robot) {
        setSelection(state, robot.id);
      }
      return;
    }
    const selected = getSelectedRobot();
    const command = COMMAND_BUTTONS.find((item) => item.key === key);
    if (command) {
      setRobotCommand(state, selected.id, {
        mode: command.mode,
        climbLevel: command.climbLevel ?? 0,
        target: null
      });
    }
  });
}

async function bootstrap() {
  const response = await fetch("../context/game_spec.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load game_spec.json: ${response.status}`);
  }
  const spec = await response.json();
  game = loadGameSpec(spec);
  state = createMatch(game, {
    bluePreset: "safe_rp_foundation",
    redPreset: "balanced_reference",
    humanSlots: ["blue-1"]
  });
  populatePresetSelect(bluePresetSelect, state.presets.blue);
  populatePresetSelect(redPresetSelect, state.presets.red);
  setupControls();
  setupCanvasInteractions();
  render();
  requestAnimationFrame((time) => {
    previousFrameTime = time;
    requestAnimationFrame(tick);
  });
  window.__rebuilt2d = {
    game,
    state,
    buildHeadlessSummary: () => buildHeadlessSummary(state)
  };
}

bootstrap().catch((error) => {
  console.error(error);
  document.body.innerHTML = `<pre style="padding:24px;color:#fff;background:#111">${error.stack}</pre>`;
});
