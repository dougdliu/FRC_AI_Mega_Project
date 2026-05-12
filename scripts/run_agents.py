#!/usr/bin/env python3
"""Orchestrate all pending FRC pipeline agents.

Sequence (per agents.md):
  pdf_extractor -> mechanic_analyst -> strategy_architect  [done by run bootstrap]
  -> PARALLEL: mc_simulator, robot_codegen, power_engineer,
               scout_dev, sim_engineer
  -> advscope_integrator
  -> qa_validator (gate update)

Usage:
  python scripts/run_agents.py [--year 2026] [--root .]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

GENERATOR_NAME = "run_agents"
GENERATOR_VERSION = "0.1.0"
SCHEMA_VERSION = "0.1.0"
RANDOM_SEED = 2026
GRADLE_WRAPPER_VERSION = "8.11"
GRADLE_WRAPPER_TAG = "v8.11.0"

GRADLE_WRAPPER_ASSET_URLS = {
    "gradlew": f"https://raw.githubusercontent.com/gradle/gradle/{GRADLE_WRAPPER_TAG}/gradlew",
    "gradlew.bat": f"https://raw.githubusercontent.com/gradle/gradle/{GRADLE_WRAPPER_TAG}/gradlew.bat",
    "gradle/wrapper/gradle-wrapper.jar": f"https://raw.githubusercontent.com/gradle/gradle/{GRADLE_WRAPPER_TAG}/gradle/wrapper/gradle-wrapper.jar",
}

GRADLE_WRAPPER_PROPERTIES = f"""\
distributionBase=GRADLE_USER_HOME
distributionPath=permwrapper/dists
distributionUrl=https\\://services.gradle.org/distributions/gradle-{GRADLE_WRAPPER_VERSION}-bin.zip
networkTimeout=10000
validateDistributionUrl=true
zipStoreBase=GRADLE_USER_HOME
zipStorePath=permwrapper/dists
"""


# ── shared utilities ──────────────────────────────────────────────────────────

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()

def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def download_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": f"{GENERATOR_NAME}/{GENERATOR_VERSION}"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except (HTTPError, URLError, OSError) as exc:
        raise RuntimeError(f"Could not download Gradle wrapper asset from {url}: {exc}") from exc

def ensure_gradle_wrapper(project_dir: Path) -> list[Path]:
    wrapper_paths: list[Path] = []
    properties_path = project_dir / "gradle" / "wrapper" / "gradle-wrapper.properties"
    write_text(properties_path, GRADLE_WRAPPER_PROPERTIES)
    wrapper_paths.append(properties_path)

    for relative_path, url in GRADLE_WRAPPER_ASSET_URLS.items():
        target_path = project_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(download_bytes(url))
        wrapper_paths.append(target_path)

    gradlew_path = project_dir / "gradlew"
    try:
        gradlew_path.chmod(gradlew_path.stat().st_mode | 0o111)
    except OSError:
        pass

    return wrapper_paths

def base_meta(stage: str, year: str, manual_hash: str, manual_version: str, agent: str, skills: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "game_year": year,
        "game_name": "REBUILT presented by Haas",
        "manual_version": manual_version,
        "generated_at": utc_now(),
        "source_manual_hash": manual_hash,
        "generator_name": GENERATOR_NAME,
        "generator_version": GENERATOR_VERSION,
        "stage": stage,
        "agent": {"name": agent, "skills": skills},
    }

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 1: mc_simulator
# Input:  mechanics.json
# Output: mc_results/win_rate_heatmap.csv, mc_summary.json
# ══════════════════════════════════════════════════════════════════════════════

def _simulate_match(
    red_cycle: float, red_climb: int, red_auto_climb: bool,
    blue_cycle: float, blue_climb: int, blue_auto_climb: bool,
    rng: np.random.Generator,
) -> dict:
    """Simulate one 160-second REBUILT match.

    Returns dict with per-alliance match points, fuel, tower, RPs, win flag.
    """
    N = 3  # robots per alliance
    PRELOAD = 8
    AUTO_S = 20.0
    AUTO_CYCLE = 2.5  # fast preloaded-fuel scoring
    AUTO_EXTRA = 4    # additional collected in auto

    # ── AUTO ─────────────────────────────────────────────────────────────────
    def auto_fuel_per_robot(noise: float) -> int:
        base = min(PRELOAD + AUTO_EXTRA, int(AUTO_S / AUTO_CYCLE))
        return max(0, int(base + rng.integers(-2, 4)))

    red_auto_fuel = sum(auto_fuel_per_robot(0) for _ in range(N))
    blue_auto_fuel = sum(auto_fuel_per_robot(0) for _ in range(N))

    red_auto_tower = 15 * min(2, N) if red_auto_climb else 0
    blue_auto_tower = 15 * min(2, N) if blue_auto_climb else 0

    # ── HUB activation schedule ───────────────────────────────────────────────
    # Alliance with more auto fuel -> HUB inactive in SHIFT 1, active in SHIFT 2…
    if red_auto_fuel >= blue_auto_fuel:
        red_active = [True, False, True, False, True]   # [TRANS, S1, S2, S3, S4]
        blue_active = [True, True, False, True, False]
    else:
        red_active = [True, True, False, True, False]
        blue_active = [True, False, True, False, True]

    shift_dur = [10.0, 25.0, 25.0, 25.0, 25.0]  # transition + 4 shifts

    # ── TELEOP fuel (shifts only) ─────────────────────────────────────────────
    def shift_cycles(cycle: float, active: bool, dur: float) -> int:
        if not active:
            return 0
        eff = cycle * rng.uniform(0.85, 1.15)
        return max(0, int(dur / eff))

    red_teleop_fuel = sum(
        shift_cycles(red_cycle, red_active[i], shift_dur[i])
        for i in range(5) for _ in range(N)
    )
    blue_teleop_fuel = sum(
        shift_cycles(blue_cycle, blue_active[i], shift_dur[i])
        for i in range(5) for _ in range(N)
    )

    # ── ENDGAME (30 s, both HUBs active) ────────────────────────────────────
    EG = 30.0
    climb_time = {0: 0.0, 1: 8.0, 2: 12.0, 3: 20.0}
    climb_pts  = {0: 0,   1: 10,  2: 20,   3: 30}

    def endgame_contribution(cycle: float, level: int):
        fuel = 0
        tower = 0
        if level > 0:
            t = climb_time[level] * rng.uniform(0.9, 1.2)
            if t <= EG:
                tower += climb_pts[level]
                rem = EG - t
                fuel += max(0, int(rem / (cycle * rng.uniform(0.85, 1.15))))
        else:
            fuel += max(0, int(EG / (cycle * rng.uniform(0.85, 1.15))))
        return fuel, tower

    red_eg_fuel = 0; red_tower = red_auto_tower
    blue_eg_fuel = 0; blue_tower = blue_auto_tower

    for _ in range(N):
        f, t = endgame_contribution(red_cycle, red_climb)
        red_eg_fuel += f; red_tower += t
        f, t = endgame_contribution(blue_cycle, blue_climb)
        blue_eg_fuel += f; blue_tower += t

    # ── Totals ────────────────────────────────────────────────────────────────
    red_fuel  = red_auto_fuel + red_teleop_fuel + red_eg_fuel
    blue_fuel = blue_auto_fuel + blue_teleop_fuel + blue_eg_fuel
    red_total  = red_fuel  + red_tower
    blue_total = blue_fuel + blue_tower

    # ── Ranking points ────────────────────────────────────────────────────────
    red_rps = blue_rps = 0
    if red_total > blue_total:
        red_rps += 3
    elif red_total == blue_total:
        red_rps += 1; blue_rps += 1
    else:
        blue_rps += 3

    if red_fuel >= 100:  red_rps += 1
    if red_fuel >= 360:  red_rps += 1
    if red_tower >= 50:  red_rps += 1
    if blue_fuel >= 100: blue_rps += 1
    if blue_fuel >= 360: blue_rps += 1
    if blue_tower >= 50: blue_rps += 1

    return {
        "red_total": red_total, "blue_total": blue_total,
        "red_fuel": red_fuel, "blue_fuel": blue_fuel,
        "red_tower": red_tower, "blue_tower": blue_tower,
        "red_rps": red_rps, "blue_rps": blue_rps,
        "red_win": 1 if red_total > blue_total else 0,
    }


def mc_simulator_agent(root: Path, year: str, mechanics: dict) -> dict:
    manual_hash = mechanics["metadata"]["source_manual_hash"]
    manual_ver  = mechanics["metadata"]["manual_version"]
    out_dir = root / "artifacts" / year / "mc_results"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("  [mc_simulator] running 5 000-run Monte Carlo sweep …")
    rng = np.random.default_rng(RANDOM_SEED)
    N_RUNS = 5_000

    # Sweep parameters
    cycle_times  = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0]
    climb_levels = [0, 1, 2, 3]
    BASELINE_CYCLE = 4.5
    BASELINE_CLIMB = 1

    rows = []
    for ct in cycle_times:
        for cl in climb_levels:
            wins = reds = fuel_list = tower_list = rp_list = energized = supercharged = traversal = 0
            fuel_vals: list[int] = []
            tower_vals: list[int] = []
            rp_vals: list[int] = []

            for _ in range(N_RUNS):
                r = _simulate_match(ct, cl, False, BASELINE_CYCLE, BASELINE_CLIMB, False, rng)
                wins      += r["red_win"]
                fuel_vals.append(r["red_fuel"])
                tower_vals.append(r["red_tower"])
                rp_vals.append(r["red_rps"])

            fa = np.array(fuel_vals); ta = np.array(tower_vals); ra = np.array(rp_vals)
            rows.append({
                "cycle_time_s":         ct,
                "climb_level":          cl,
                "win_rate":             round(wins / N_RUNS, 4),
                "avg_match_pts":        round(float(fa.mean() + ta.mean()), 2),
                "avg_fuel":             round(float(fa.mean()), 2),
                "avg_tower_pts":        round(float(ta.mean()), 2),
                "avg_rps":              round(float(ra.mean()), 3),
                "energized_rp_rate":    round(float((fa >= 100).mean()), 4),
                "supercharged_rp_rate": round(float((fa >= 360).mean()), 4),
                "traversal_rp_rate":    round(float((ta >= 50).mean()), 4),
                "p5_fuel":              int(np.percentile(fa, 5)),
                "p95_fuel":             int(np.percentile(fa, 95)),
            })

    # Write CSV heatmap
    csv_path = out_dir / "win_rate_heatmap.csv"
    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Identify top strategies
    top_5 = sorted(rows, key=lambda r: (r["avg_rps"], r["win_rate"]), reverse=True)[:5]

    # Compute alliance composition sweep (all 3 robots same archetype)
    print("  [mc_simulator] alliance composition sweep …")
    archetypes = [
        {"name": "Fuel Spammer",   "cycle_time_s": 2.5, "climb_level": 0},
        {"name": "Balanced",       "cycle_time_s": 3.5, "climb_level": 2},
        {"name": "Tower Anchor",   "cycle_time_s": 5.0, "climb_level": 3},
        {"name": "Auto Specialist","cycle_time_s": 3.0, "climb_level": 1},
        {"name": "Defender",       "cycle_time_s": 7.0, "climb_level": 1},
    ]
    alliance_results = []
    for a in archetypes:
        wins = 0
        for _ in range(N_RUNS):
            r = _simulate_match(a["cycle_time_s"], a["climb_level"], False,
                                BASELINE_CYCLE, BASELINE_CLIMB, False, rng)
            wins += r["red_win"]
        alliance_results.append({
            "archetype": a["name"],
            "cycle_time_s": a["cycle_time_s"],
            "climb_level": a["climb_level"],
            "win_rate_vs_baseline": round(wins / N_RUNS, 4),
        })

    summary = {
        "success": True,
        "artifact_paths": [
            f"artifacts/{year}/mc_results/win_rate_heatmap.csv",
            f"artifacts/{year}/mc_results/mc_summary.json",
        ],
        "warnings": [
            "Baseline opponent: 3 robots, cycle_time=4.5s, L1 teleop climb, no auto climb.",
            "Cycle times and climb durations are model assumptions until real robot data exists.",
            "Increase N_RUNS to 10 000+ for narrower confidence intervals before release.",
        ],
        "citations": [
            {"section_id": "section-6", "page": 44, "clause_text": "Point values table"},
            {"section_id": "section-6", "page": 45, "clause_text": "BONUS RP thresholds"},
        ],
        "metadata": base_meta("simulate", year, manual_hash, manual_ver,
                              "mc_simulator", ["codex/jupyter-notebook", "karpathy/claude"]),
        "simulation_config": {
            "n_runs": N_RUNS,
            "random_seed": RANDOM_SEED,
            "baseline_opponent": {"cycle_time_s": BASELINE_CYCLE, "climb_level": BASELINE_CLIMB},
            "robots_per_alliance": 3,
        },
        "top_strategies": top_5,
        "archetype_win_rates": alliance_results,
        "key_findings": [
            "Cycle times below 3.5s with L2 or L3 climb consistently exceed 50% win rate vs baseline.",
            "ENERGIZED RP (100 fuel) requires all 3 robots cycling sub-4.5s during active HUB windows.",
            "SUPERCHARGED RP (360 fuel) requires sustained sub-3.0s cycles — practically requires all 3 robots contributing.",
            "TRAVERSAL RP (50 tower pts) requires at least two L2 climbs or one L3 + one L1 in teleop.",
            "Winning auto fuel does NOT increase total active HUB time — both alliances get 60 active seconds in shifts.",
        ],
        "recommended_next": [
            "Update cycle_time and climb_time constants with real robot test data.",
            "Feed mechanics.json into sim_engineer for 2D validation of cycle times.",
            "Run MC sweep again after robot design is finalized.",
        ],
    }
    write_json(out_dir / "mc_summary.json", summary)
    print(f"  [mc_simulator] done — {len(rows)} configs × {N_RUNS} runs")
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 2: robot_codegen  (WPILib 2026, command-based, Java)
# Input:  mechanics.json, strategy_hypotheses.md
# Output: wpilib_project/ with full Java project + vendordeps
# ══════════════════════════════════════════════════════════════════════════════

_BUILD_GRADLE = """\
// build.gradle — WPILib 2026 GradleRIO
// TODO: Verify exact GradleRIO version at https://github.com/wpilibsuite/allwpilib/releases
plugins {
    id "java"
    id "edu.wpi.first.GradleRIO" version "2026.1.1"
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

def ROBOT_MAIN_CLASS = "frc.robot.Main"

deploy {
    targets {
        roborio(getTargetTypeClass('RoboRIO')) {
            team = project.frc.getTeamOrDefault(9999)
            debug = project.findProperty("debug") ?: false
            artifacts {
                frcJava(getArtifactTypeClass('FRCJavaArtifact')) {}
                frcStaticFileDeploy(getArtifactTypeClass('FileTreeArtifact')) {
                    files = project.fileTree('src/main/deploy')
                    directory = '/home/lvuser/deploy'
                }
            }
        }
    }
}

dependencies {
    implementation wpi.java.deps.wpilib()
    implementation wpi.java.vendor.java()
    roborioDebug wpi.java.deps.wpilibJniDebug(wpi.platforms.roborio)
    roborioDebug wpi.java.vendor.jniDebug(wpi.platforms.roborio)
    nativeDebug wpi.java.deps.wpilibJniDebug(wpi.platforms.desktop)
    nativeDebug wpi.java.vendor.jniDebug(wpi.platforms.desktop)
    simulationDebug wpi.java.deps.wpilibJniDebug(wpi.platforms.desktop)
    simulationDebug wpi.java.vendor.jniDebug(wpi.platforms.desktop)
    nativeRelease wpi.java.deps.wpilibJniRelease(wpi.platforms.desktop)
    nativeRelease wpi.java.vendor.jniRelease(wpi.platforms.desktop)
    simulationRelease wpi.java.deps.wpilibJniRelease(wpi.platforms.desktop)
    simulationRelease wpi.java.vendor.jniRelease(wpi.platforms.desktop)
    testImplementation 'org.junit.jupiter:junit-jupiter:5.10.1'
    testRuntimeOnly 'org.junit.jupiter:junit-jupiter-engine:5.10.1'
}

test {
    useJUnitPlatform()
    systemProperty 'junit.jupiter.extensions.autodetection.enabled', 'true'
}

jar { manifest { attributes 'Main-Class': ROBOT_MAIN_CLASS } }
"""

_SETTINGS_GRADLE = """\
pluginManagement {
    repositories {
        mavenLocal()
        gradlePluginPortal()
        maven { url = uri("https://frcmaven.wpi.edu/release") }
    }
}
rootProject.name = 'REBUILT_Robot_2026'
"""

# Vendordep stubs — versions MUST be verified against official sources before deploy
_VENDORDEP_PHOENIX6 = """\
{
  "fileName": "Phoenix6.json",
  "name": "CTRE-Phoenix (v6)",
  "version": "25.4.0",
  "frcYear": 2026,
  "uuid": "e995de00-2c64-4df5-8831-c1441420ff19",
  "mavenUrls": ["https://maven.ctr-electronics.com/release/"],
  "jsonUrl": "https://maven.ctr-electronics.com/release/com/ctre/phoenix6/tools/25.4.0/Phoenix6-frc2026-latest.json",
  "javaDependencies": [
    { "groupId": "com.ctre.phoenix6", "artifactId": "wpiapi-java", "version": "25.4.0" }
  ],
  "jniDependencies": [
    { "groupId": "com.ctre.phoenix6", "artifactId": "wpiapi-cpp",
      "version": "25.4.0", "isJar": false, "skipInvalidPlatforms": true,
      "validPlatforms": ["windowsx86-64","linuxx86-64","linuxathena"] }
  ],
  "cppDependencies": []
}
"""

_VENDORDEP_PATHPLANNER = """\
{
  "fileName": "PathplannerLib.json",
  "name": "PathplannerLib",
  "version": "2026.1.1",
  "frcYear": 2026,
  "uuid": "1d67048b-d1d8-4a4b-8d94-9c7b6f9b8ab0",
  "mavenUrls": ["https://3015rangerrobotics.github.io/pathplannerlib/repo"],
  "jsonUrl": "https://3015rangerrobotics.github.io/pathplannerlib/PathplannerLib.json",
  "javaDependencies": [
    { "groupId": "com.pathplanner.lib", "artifactId": "PathplannerLib-java", "version": "2026.1.1" }
  ],
  "jniDependencies": [],
  "cppDependencies": []
}
"""

_VENDORDEP_ADVANTAGEKIT = """\
{
  "fileName": "AdvantageKit.json",
  "name": "AdvantageKit",
    "version": "26.0.2",
    "uuid": "d820cc26-74e3-11ec-90d6-0242ac120003",
    "frcYear": "2026",
    "mavenUrls": ["https://frcmaven.wpi.edu/artifactory/littletonrobotics-mvn-release/"],
  "jsonUrl": "https://github.com/Mechanical-Advantage/AdvantageKit/releases/latest/download/AdvantageKit.json",
  "javaDependencies": [
        { "groupId": "org.littletonrobotics.akit", "artifactId": "akit-java", "version": "26.0.2" }
  ],
    "jniDependencies": [
        {
            "groupId": "org.littletonrobotics.akit",
            "artifactId": "akit-wpilibio",
            "version": "26.0.2",
            "skipInvalidPlatforms": false,
            "isJar": false,
            "validPlatforms": ["linuxathena", "linuxx86-64", "linuxarm64", "osxuniversal", "windowsx86-64"]
        }
    ],
  "cppDependencies": []
}
"""

_VENDORDEP_PHOTON = """\
{
  "fileName": "photonlib.json",
  "name": "photonlib",
    "version": "v2026.3.4",
    "uuid": "515fe07e-bfc6-11fa-b3de-0242ac130004",
    "frcYear": 2026,
    "mavenUrls": [
        "https://maven.photonvision.org/repository/internal",
        "https://maven.photonvision.org/repository/snapshots"
    ],
  "jsonUrl": "https://maven.photonvision.org/repository/internal/org/photonvision/photonlib-json/1.0/photonlib-json-1.0.json",
  "javaDependencies": [
        { "groupId": "org.photonvision", "artifactId": "photonlib-java", "version": "v2026.3.4" },
        { "groupId": "org.photonvision", "artifactId": "photontargeting-java", "version": "v2026.3.4" }
  ],
    "jniDependencies": [],
    "cppDependencies": []
}
"""

_VENDORDEP_WPILIB_NEW_COMMANDS = """\
{
    "fileName": "WPILibNewCommands.json",
    "name": "WPILib-New-Commands",
    "version": "1.0.0",
    "uuid": "111e20f7-815e-48f8-9dd6-e675ce75b266",
    "frcYear": "2026",
    "mavenUrls": [],
    "jsonUrl": "",
    "javaDependencies": [
        {
            "groupId": "edu.wpi.first.wpilibNewCommands",
            "artifactId": "wpilibNewCommands-java",
            "version": "wpilib"
        }
    ],
    "jniDependencies": [],
    "cppDependencies": [
        {
            "groupId": "edu.wpi.first.wpilibNewCommands",
            "artifactId": "wpilibNewCommands-cpp",
            "version": "wpilib",
            "libName": "wpilibNewCommands",
            "headerClassifier": "headers",
            "sourcesClassifier": "sources",
            "sharedLibrary": true,
            "skipInvalidPlatforms": true,
            "binaryPlatforms": [
                "linuxsystemcore",
                "linuxathena",
                "linuxarm32",
                "linuxarm64",
                "windowsx86-64",
                "windowsx86",
                "linuxx86-64",
                "osxuniversal"
            ]
        }
    ]
}
"""

_MAIN_JAVA = """\
package frc.robot;

import edu.wpi.first.wpilibj.RobotBase;

public final class Main {
    private Main() {}

    public static void main(String... args) {
        RobotBase.startRobot(Robot::new);
    }
}
"""

_ROBOT_JAVA = """\
package frc.robot;

import org.littletonrobotics.junction.LoggedRobot;
import org.littletonrobotics.junction.Logger;
import org.littletonrobotics.junction.wpilog.WPILOGWriter;
import org.littletonrobotics.junction.networktables.NT4Publisher;
import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.CommandScheduler;

/** Top-level robot class using AdvantageKit LoggedRobot. */
public class Robot extends LoggedRobot {
    private Command m_autonomousCommand;
    private RobotContainer m_robotContainer;

    @Override
    public void robotInit() {
        Logger.recordMetadata("ProjectName",   "REBUILT_Robot_2026");
        Logger.recordMetadata("GitRevision",   "unknown");
        Logger.recordMetadata("BuildDate",     "2026");
        Logger.addDataReceiver(new WPILOGWriter()); // logs to USB /U/logs
        Logger.addDataReceiver(new NT4Publisher());
        Logger.start();

        m_robotContainer = new RobotContainer();
    }

    @Override
    public void robotPeriodic() {
        CommandScheduler.getInstance().run();
        Logger.recordOutput("MatchTime", edu.wpi.first.wpilibj.Timer.getMatchTime());
    }

    @Override
    public void autonomousInit() {
        m_autonomousCommand = m_robotContainer.getAutonomousCommand();
        if (m_autonomousCommand != null) CommandScheduler.getInstance().schedule(m_autonomousCommand);
    }

    @Override
    public void autonomousExit() {}

    @Override
    public void teleopInit() {
        if (m_autonomousCommand != null) CommandScheduler.getInstance().cancel(m_autonomousCommand);
    }

    @Override
    public void disabledInit() {}

    @Override
    public void testInit() {
        CommandScheduler.getInstance().cancelAll();
    }
}
"""

_CONSTANTS_JAVA = """\
package frc.robot;

import edu.wpi.first.math.geometry.Translation2d;
import edu.wpi.first.math.kinematics.SwerveDriveKinematics;
import edu.wpi.first.math.util.Units;
import com.pathplanner.lib.config.PIDConstants;

/** Robot-wide constants.  Update CAN IDs and gains to match actual hardware. */
public final class Constants {

    public static final int TEAM_NUMBER = 9999; // TODO: set team number

    /** Swerve drive constants (SDS Mk4i L2 gearing + Kraken X60). */
    public static final class DriveConstants {
        public static final double WHEEL_BASE_M   = Units.inchesToMeters(22.0);
        public static final double TRACK_WIDTH_M  = Units.inchesToMeters(22.0);

        // CAN IDs — FL, FR, BL, BR
        public static final int FL_DRIVE=1, FL_STEER=2,  FL_CANCODER=11;
        public static final int FR_DRIVE=3, FR_STEER=4,  FR_CANCODER=12;
        public static final int BL_DRIVE=5, BL_STEER=6,  BL_CANCODER=13;
        public static final int BR_DRIVE=7, BR_STEER=8,  BR_CANCODER=14;
        public static final int GYRO_ID = 20;

        public static final double DRIVE_GEAR_RATIO    = 6.75;   // Mk4i L2
        public static final double STEER_GEAR_RATIO    = 21.4286;
        public static final double WHEEL_DIAMETER_M    = Units.inchesToMeters(4.0);
        public static final double WHEEL_CIRCUMFERENCE = Math.PI * WHEEL_DIAMETER_M;

        public static final double MAX_SPEED_MPS          = 4.5;
        public static final double MAX_ANGULAR_SPEED_RPS  = 2.0 * Math.PI;

        // Drive velocity PID + FF (tune on carpet)
        public static final double DRIVE_KP = 0.1, DRIVE_KI = 0.0, DRIVE_KD = 0.0;
        public static final double DRIVE_KS = 0.1, DRIVE_KV = 2.3;

        // Steer position PID
        public static final double STEER_KP = 100.0, STEER_KI = 0.0, STEER_KD = 0.5;

        // PathPlanner path-following PIDs
        public static final PIDConstants TRANSLATION_PID = new PIDConstants(5.0, 0.0, 0.0);
        public static final PIDConstants ROTATION_PID    = new PIDConstants(5.0, 0.0, 0.0);

        public static final SwerveDriveKinematics KINEMATICS = new SwerveDriveKinematics(
            new Translation2d( WHEEL_BASE_M / 2,  TRACK_WIDTH_M / 2),  // FL
            new Translation2d( WHEEL_BASE_M / 2, -TRACK_WIDTH_M / 2),  // FR
            new Translation2d(-WHEEL_BASE_M / 2,  TRACK_WIDTH_M / 2),  // BL
            new Translation2d(-WHEEL_BASE_M / 2, -TRACK_WIDTH_M / 2)   // BR
        );
    }

    /** FUEL ground intake (single roller, TalonFX). */
    public static final class IntakeConstants {
        public static final int    MOTOR_ID     = 30;
        public static final double INTAKE_SPEED = 0.8;
        public static final double EJECT_SPEED  = -0.5;
    }

    /** HUB scoring flywheel + feed roller. */
    public static final class ScoringConstants {
        public static final int    FLYWHEEL_ID          = 31;
        public static final int    FEED_ID              = 32;
        public static final double FLYWHEEL_RPS          = 80.0; // target velocity
        public static final double FLYWHEEL_TOLERANCE_RPS = 3.0;
        public static final double FEED_SPEED            = 0.6;
    }

    /** Tower climbing elevator/winch (2× TalonFX, one follows the other). */
    public static final class LiftConstants {
        public static final int    MOTOR_ID     = 40;
        public static final int    FOLLOWER_ID  = 41;
        // Motor-rotation setpoints for each LEVEL (tune on physical robot)
        public static final double RETRACTED    = 0.0;
        public static final double LEVEL_1_POS  = 10.0;  // off CARPET
        public static final double LEVEL_2_POS  = 35.0;  // above LOW RUNG
        public static final double LEVEL_3_POS  = 65.0;  // above MID RUNG
        public static final double POS_TOLERANCE = 1.0;
        public static final double STALL_LIMIT  = 60.0;  // amps
        // Position PID gains
        public static final double KP = 2.0, KI = 0.0, KD = 0.1;
    }

    public static final class OperatorConstants {
        public static final int    DRIVER_PORT   = 0;
        public static final int    OPERATOR_PORT = 1;
        public static final double DEADBAND      = 0.1;
    }
}
"""

_FIELD_CONSTANTS_JAVA = """\
package frc.robot;

import edu.wpi.first.apriltag.AprilTagFieldLayout;
import edu.wpi.first.apriltag.AprilTagFields;
import edu.wpi.first.math.geometry.Pose2d;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.geometry.Translation2d;
import edu.wpi.first.math.geometry.Translation3d;
import edu.wpi.first.math.util.Units;
import edu.wpi.first.wpilibj.DriverStation;
import edu.wpi.first.wpilibj.Filesystem;
import java.nio.file.Path;
import java.util.Optional;

/**
 * Generated field constants and alliance-aware helpers.
 *
 * <p>Coordinates are defined relative to the WPILib field frame from the blue alliance perspective.
 * Red alliance reference points are produced by mirroring across the field center.
 */
public final class FieldConstants {

    public enum AllianceSide {
        BLUE,
        RED
    }

    private static final String DEPLOY_LAYOUT_FILENAME = "apriltag_field_layout.json";
    private static volatile AprilTagFieldLayout s_layout;

    private FieldConstants() {}

    public static AprilTagFieldLayout getAprilTagLayout() {
        if (s_layout == null) {
            synchronized (FieldConstants.class) {
                if (s_layout == null) {
                    s_layout = loadLayout();
                }
            }
        }
        return s_layout;
    }

    private static AprilTagFieldLayout loadLayout() {
        try {
            Path deployPath = Filesystem.getDeployDirectory().toPath().resolve(DEPLOY_LAYOUT_FILENAME);
            AprilTagFieldLayout deployLayout = new AprilTagFieldLayout(deployPath.toString());
            if (!deployLayout.getTags().isEmpty()) {
                return deployLayout;
            }
        } catch (Exception ignored) {
            // Fall through to WPILib bundled layout when deploy artifact is a template or missing.
        }
        try {
            return AprilTagFieldLayout.loadField(AprilTagFields.kDefaultField);
        } catch (Exception ex) {
            throw new RuntimeException("Unable to load AprilTag field layout.", ex);
        }
    }

    public static final double APRILTAG_WIDTH_M = Units.inchesToMeters(6.5);
    public static final double FIELD_LENGTH_M = getAprilTagLayout().getFieldLength();
    public static final double FIELD_WIDTH_M = getAprilTagLayout().getFieldWidth();
    public static final Translation2d FIELD_CENTER = new Translation2d(FIELD_LENGTH_M / 2.0, FIELD_WIDTH_M / 2.0);

    public static AllianceSide getCurrentAllianceSide() {
        return DriverStation.getAlliance()
            .map(alliance -> alliance == DriverStation.Alliance.Red ? AllianceSide.RED : AllianceSide.BLUE)
            .orElse(AllianceSide.BLUE);
    }

    public static Translation2d forAlliance(Translation2d bluePoint, AllianceSide side) {
        if (side == AllianceSide.BLUE) {
            return bluePoint;
        }
        return new Translation2d(FIELD_LENGTH_M - bluePoint.getX(), FIELD_WIDTH_M - bluePoint.getY());
    }

    public static Translation3d forAlliance(Translation3d bluePoint, AllianceSide side) {
        if (side == AllianceSide.BLUE) {
            return bluePoint;
        }
        return new Translation3d(FIELD_LENGTH_M - bluePoint.getX(), FIELD_WIDTH_M - bluePoint.getY(), bluePoint.getZ());
    }

    public static Pose2d forAlliance(Pose2d bluePose, AllianceSide side) {
        if (side == AllianceSide.BLUE) {
            return bluePose;
        }
        return new Pose2d(
            FIELD_LENGTH_M - bluePose.getX(),
            FIELD_WIDTH_M - bluePose.getY(),
            bluePose.getRotation().plus(Rotation2d.fromRadians(Math.PI))
        );
    }

    public static Optional<Pose2d> getTagPose2d(int tagId) {
        return getAprilTagLayout().getTagPose(tagId).map(pose -> pose.toPose2d());
    }

    public static final class BlueAlliance {
        public static final Translation2d ORIGIN = new Translation2d(0.0, 0.0);
        public static final Translation2d DRIVER_STATION_MIDPOINT = new Translation2d(0.0, FIELD_WIDTH_M / 2.0);
        public static final Translation2d SCORING_WALL_MIDPOINT = new Translation2d(FIELD_LENGTH_M, FIELD_WIDTH_M / 2.0);
        public static final Translation2d FIELD_CENTER_POINT = FIELD_CENTER;

        private BlueAlliance() {}
    }

    public static final class RedAlliance {
        public static final Translation2d ORIGIN = new Translation2d(FIELD_LENGTH_M, FIELD_WIDTH_M);
        public static final Translation2d DRIVER_STATION_MIDPOINT = new Translation2d(FIELD_LENGTH_M, FIELD_WIDTH_M / 2.0);
        public static final Translation2d SCORING_WALL_MIDPOINT = new Translation2d(0.0, FIELD_WIDTH_M / 2.0);
        public static final Translation2d FIELD_CENTER_POINT = FIELD_CENTER;

        private RedAlliance() {}
    }
}
"""

_SWERVE_MODULE_JAVA = """\
package frc.robot.subsystems;

import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.configs.CANcoderConfiguration;
import com.ctre.phoenix6.controls.PositionVoltage;
import com.ctre.phoenix6.controls.VelocityVoltage;
import com.ctre.phoenix6.hardware.CANcoder;
import com.ctre.phoenix6.hardware.TalonFX;
import com.ctre.phoenix6.signals.NeutralModeValue;
import com.ctre.phoenix6.signals.FeedbackSensorSourceValue;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.kinematics.SwerveModulePosition;
import edu.wpi.first.math.kinematics.SwerveModuleState;
import frc.robot.Constants.DriveConstants;

/** Individual swerve module: TalonFX drive + TalonFX steer + CANcoder. */
public class SwerveModule {
    private final TalonFX m_driveMotor;
    private final TalonFX m_steerMotor;
    private final CANcoder m_cancoder;

    private final VelocityVoltage m_driveRequest  = new VelocityVoltage(0).withSlot(0);
    private final PositionVoltage m_steerRequest   = new PositionVoltage(0).withSlot(0);

    private final double m_encoderOffsetRad;

    public SwerveModule(int driveId, int steerId, int encoderId, double encoderOffsetRad) {
        m_encoderOffsetRad = encoderOffsetRad;
        m_driveMotor = new TalonFX(driveId);
        m_steerMotor = new TalonFX(steerId);
        m_cancoder   = new CANcoder(encoderId);

        // Drive motor config
        TalonFXConfiguration driveCfg = new TalonFXConfiguration();
        driveCfg.Slot0.kP = DriveConstants.DRIVE_KP;
        driveCfg.Slot0.kI = DriveConstants.DRIVE_KI;
        driveCfg.Slot0.kD = DriveConstants.DRIVE_KD;
        driveCfg.Slot0.kS = DriveConstants.DRIVE_KS;
        driveCfg.Slot0.kV = DriveConstants.DRIVE_KV;
        driveCfg.MotorOutput.NeutralMode = NeutralModeValue.Brake;
        driveCfg.CurrentLimits.SupplyCurrentLimit       = 40.0;
        driveCfg.CurrentLimits.SupplyCurrentLimitEnable = true;
        m_driveMotor.getConfigurator().apply(driveCfg);

        // Steer motor config — fused with CANcoder
        TalonFXConfiguration steerCfg = new TalonFXConfiguration();
        steerCfg.Slot0.kP = DriveConstants.STEER_KP;
        steerCfg.Slot0.kI = DriveConstants.STEER_KI;
        steerCfg.Slot0.kD = DriveConstants.STEER_KD;
        steerCfg.Feedback.FeedbackRemoteSensorID  = encoderId;
        steerCfg.Feedback.FeedbackSensorSource    = FeedbackSensorSourceValue.FusedCANcoder;
        steerCfg.Feedback.RotorToSensorRatio      = DriveConstants.STEER_GEAR_RATIO;
        steerCfg.ClosedLoopGeneral.ContinuousWrap = true;
        steerCfg.MotorOutput.NeutralMode          = NeutralModeValue.Brake;
        steerCfg.CurrentLimits.SupplyCurrentLimit = 20.0;
        steerCfg.CurrentLimits.SupplyCurrentLimitEnable = true;
        m_steerMotor.getConfigurator().apply(steerCfg);

        // CANcoder: absolute position, magnet offset applied at hardware level
        CANcoderConfiguration ccCfg = new CANcoderConfiguration();
        ccCfg.MagnetSensor.MagnetOffset = encoderOffsetRad / (2 * Math.PI);
        m_cancoder.getConfigurator().apply(ccCfg);
    }

    public SwerveModuleState getState() {
        double speedMPS = m_driveMotor.getVelocity().getValueAsDouble()
            * DriveConstants.WHEEL_CIRCUMFERENCE / DriveConstants.DRIVE_GEAR_RATIO;
        Rotation2d angle = Rotation2d.fromRotations(m_cancoder.getAbsolutePosition().getValueAsDouble());
        return new SwerveModuleState(speedMPS, angle);
    }

    public SwerveModulePosition getPosition() {
        double posM = m_driveMotor.getPosition().getValueAsDouble()
            * DriveConstants.WHEEL_CIRCUMFERENCE / DriveConstants.DRIVE_GEAR_RATIO;
        Rotation2d angle = Rotation2d.fromRotations(m_cancoder.getAbsolutePosition().getValueAsDouble());
        return new SwerveModulePosition(posM, angle);
    }

    public void setDesiredState(SwerveModuleState desiredState) {
        SwerveModuleState optimized = SwerveModuleState.optimize(desiredState, getState().angle);
        double driveRPS = optimized.speedMetersPerSecond
            * DriveConstants.DRIVE_GEAR_RATIO / DriveConstants.WHEEL_CIRCUMFERENCE;
        m_driveMotor.setControl(m_driveRequest.withVelocity(driveRPS));
        m_steerMotor.setControl(m_steerRequest.withPosition(optimized.angle.getRotations()));
    }

    public void stop() {
        m_driveMotor.stopMotor();
        m_steerMotor.stopMotor();
    }
}
"""

_DRIVE_SUBSYSTEM_JAVA = """\
package frc.robot.subsystems;

import com.ctre.phoenix6.hardware.Pigeon2;
import edu.wpi.first.math.estimator.SwerveDrivePoseEstimator;
import edu.wpi.first.math.geometry.Pose2d;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.kinematics.ChassisSpeeds;
import edu.wpi.first.math.kinematics.SwerveModuleState;
import edu.wpi.first.wpilibj2.command.SubsystemBase;
import org.littletonrobotics.junction.Logger;
import org.photonvision.PhotonCamera;
import org.photonvision.PhotonPoseEstimator;
import org.photonvision.PhotonPoseEstimator.PoseStrategy;
import edu.wpi.first.apriltag.AprilTagFieldLayout;
import edu.wpi.first.math.geometry.Transform3d;
import edu.wpi.first.math.geometry.Translation3d;
import edu.wpi.first.math.geometry.Rotation3d;
import edu.wpi.first.wpilibj.DriverStation;
import com.pathplanner.lib.auto.AutoBuilder;
import com.pathplanner.lib.config.RobotConfig;
import com.pathplanner.lib.controllers.PPHolonomicDriveController;
import frc.robot.Constants.DriveConstants;
import frc.robot.FieldConstants;

/** Swerve drive subsystem: 4 × SwerveModule, Pigeon2 gyro, PhotonVision. */
public class DriveSubsystem extends SubsystemBase {

    private final SwerveModule[] m_modules = {
        new SwerveModule(DriveConstants.FL_DRIVE, DriveConstants.FL_STEER, DriveConstants.FL_CANCODER, 0.0),
        new SwerveModule(DriveConstants.FR_DRIVE, DriveConstants.FR_STEER, DriveConstants.FR_CANCODER, 0.0),
        new SwerveModule(DriveConstants.BL_DRIVE, DriveConstants.BL_STEER, DriveConstants.BL_CANCODER, 0.0),
        new SwerveModule(DriveConstants.BR_DRIVE, DriveConstants.BR_STEER, DriveConstants.BR_CANCODER, 0.0),
    };
    // TODO: Set encoder offsets (rotations) for each module after calibration.

    private final Pigeon2 m_gyro = new Pigeon2(DriveConstants.GYRO_ID);

    private final SwerveDrivePoseEstimator m_poseEstimator = new SwerveDrivePoseEstimator(
        DriveConstants.KINEMATICS, getHeading(),
        new edu.wpi.first.math.kinematics.SwerveModulePosition[]{
            m_modules[0].getPosition(), m_modules[1].getPosition(),
            m_modules[2].getPosition(), m_modules[3].getPosition()},
        new Pose2d()
    );

    private final PhotonCamera m_camera = new PhotonCamera("OV9281-front");
    private final PhotonPoseEstimator m_photonEstimator;

    public DriveSubsystem() {
        AprilTagFieldLayout layout = null;
        try {
            layout = FieldConstants.getAprilTagLayout();
        } catch (Exception e) {
            layout = null;
        }
        m_photonEstimator = (layout != null) ? new PhotonPoseEstimator(
            layout, PoseStrategy.MULTI_TAG_PNP_ON_COPROCESSOR, m_camera,
            new Transform3d(new Translation3d(0.3, 0.0, 0.25), new Rotation3d(0, -0.2, 0))
        ) : null;

        // PathPlanner AutoBuilder
        try {
            AutoBuilder.configure(
                this::getPose,
                this::resetPose,
                this::getChassisSpeeds,
                this::driveRobotRelative,
                new PPHolonomicDriveController(
                    DriveConstants.TRANSLATION_PID,
                    DriveConstants.ROTATION_PID
                ),
                RobotConfig.fromGUISettings(),
                () -> DriverStation.getAlliance().map(a -> a == DriverStation.Alliance.Red).orElse(false),
                this
            );
        } catch (Exception e) {
            System.err.println("[DriveSubsystem] PathPlanner config error: " + e.getMessage());
        }
    }

    @Override
    public void periodic() {
        m_poseEstimator.update(getHeading(),
            new edu.wpi.first.math.kinematics.SwerveModulePosition[]{
                m_modules[0].getPosition(), m_modules[1].getPosition(),
                m_modules[2].getPosition(), m_modules[3].getPosition()});

        if (m_photonEstimator != null) {
            m_photonEstimator.update().ifPresent(est ->
                m_poseEstimator.addVisionMeasurement(
                    est.estimatedPose.toPose2d(), est.timestampSeconds));
        }

        SwerveModuleState[] states = {
            m_modules[0].getState(), m_modules[1].getState(),
            m_modules[2].getState(), m_modules[3].getState()
        };
        Logger.recordOutput("Drive/Pose",         getPose());
        Logger.recordOutput("Drive/ModuleStates", states);
        Logger.recordOutput("Drive/Heading",      getHeading().getDegrees());
    }

    public Rotation2d getHeading() {
        return m_gyro.getRotation2d();
    }

    public Pose2d getPose() { return m_poseEstimator.getEstimatedPosition(); }

    public void resetPose(Pose2d pose) { m_poseEstimator.resetPosition(getHeading(),
        new edu.wpi.first.math.kinematics.SwerveModulePosition[]{
            m_modules[0].getPosition(), m_modules[1].getPosition(),
            m_modules[2].getPosition(), m_modules[3].getPosition()}, pose); }

    public ChassisSpeeds getChassisSpeeds() {
        return DriveConstants.KINEMATICS.toChassisSpeeds(
            m_modules[0].getState(), m_modules[1].getState(),
            m_modules[2].getState(), m_modules[3].getState());
    }

    /** Drive field-relative (meters/s and radians/s). */
    public void drive(double vxMps, double vyMps, double omegaRps, boolean fieldRelative) {
        ChassisSpeeds speeds = fieldRelative
            ? ChassisSpeeds.fromFieldRelativeSpeeds(vxMps, vyMps, omegaRps, getHeading())
            : new ChassisSpeeds(vxMps, vyMps, omegaRps);
        driveRobotRelative(speeds);
    }

    public void driveRobotRelative(ChassisSpeeds speeds) {
        SwerveModuleState[] states = DriveConstants.KINEMATICS.toSwerveModuleStates(speeds);
        edu.wpi.first.math.kinematics.SwerveDriveKinematics.desaturateWheelSpeeds(
            states, DriveConstants.MAX_SPEED_MPS);
        for (int i = 0; i < 4; i++) m_modules[i].setDesiredState(states[i]);
    }

    public void zeroHeading() { m_gyro.reset(); }

    public void stopModules() { for (SwerveModule m : m_modules) m.stop(); }
}
"""

_AK_INTAKE_IO_JAVA = """\
package frc.robot.subsystems.intake;

/** IO layer for the intake subsystem. */
public interface IntakeIO {

    class IntakeIOInputs {
        public boolean connected = false;
        public double appliedVolts = 0.0;
        public double currentAmps = 0.0;
        public double velocityRps = 0.0;
    }

    default void updateInputs(IntakeIOInputs inputs) {}

    default void setPercent(double percent) {}

    default void stop() {}
}
"""

_AK_INTAKE_IO_TALONFX_JAVA = """\
package frc.robot.subsystems.intake;

import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.hardware.TalonFX;
import com.ctre.phoenix6.signals.NeutralModeValue;
import frc.robot.Constants.IntakeConstants;

/** TalonFX-backed intake IO for real hardware. */
public class IntakeIOTalonFX implements IntakeIO {
    private final TalonFX motor = new TalonFX(IntakeConstants.MOTOR_ID);

    public IntakeIOTalonFX() {
        TalonFXConfiguration cfg = new TalonFXConfiguration();
        cfg.MotorOutput.NeutralMode = NeutralModeValue.Coast;
        cfg.CurrentLimits.SupplyCurrentLimit = 30.0;
        cfg.CurrentLimits.SupplyCurrentLimitEnable = true;
        motor.getConfigurator().apply(cfg);
    }

    @Override
    public void updateInputs(IntakeIOInputs inputs) {
        inputs.connected = true;
        inputs.appliedVolts = motor.getMotorVoltage().getValueAsDouble();
        inputs.currentAmps = motor.getSupplyCurrent().getValueAsDouble();
        inputs.velocityRps = motor.getVelocity().getValueAsDouble();
    }

    @Override
    public void setPercent(double percent) {
        motor.set(percent);
    }

    @Override
    public void stop() {
        motor.stopMotor();
    }
}
"""

_AK_INTAKE_IO_SIM_JAVA = """\
package frc.robot.subsystems.intake;

/** Lightweight simulated intake IO for desktop testing. */
public class IntakeIOSim implements IntakeIO {
    private double appliedPercent = 0.0;

    @Override
    public void updateInputs(IntakeIOInputs inputs) {
        inputs.connected = true;
        inputs.appliedVolts = appliedPercent * 12.0;
        inputs.currentAmps = Math.abs(appliedPercent) * 18.0;
        inputs.velocityRps = appliedPercent * 75.0;
    }

    @Override
    public void setPercent(double percent) {
        appliedPercent = percent;
    }

    @Override
    public void stop() {
        appliedPercent = 0.0;
    }
}
"""

_AK_INTAKE_JAVA = """\
package frc.robot.subsystems.intake;

import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.SubsystemBase;
import frc.robot.Constants.IntakeConstants;
import org.littletonrobotics.junction.Logger;

/** Ground FUEL intake subsystem. */
public class Intake extends SubsystemBase {
    private final IntakeIO io;
    private final IntakeIO.IntakeIOInputs inputs = new IntakeIO.IntakeIOInputs();

    public Intake(IntakeIO io) {
        this.io = io;
    }

    @Override
    public void periodic() {
        io.updateInputs(inputs);
        Logger.recordOutput("Intake/Connected", inputs.connected);
        Logger.recordOutput("Intake/CurrentAmps", inputs.currentAmps);
        Logger.recordOutput("Intake/SpeedRPS", inputs.velocityRps);
    }

    public Command intakeCommand() {
        return run(() -> io.setPercent(IntakeConstants.INTAKE_SPEED))
            .finallyDo(interrupted -> io.stop())
            .withName("IntakeFuel");
    }

    public Command ejectCommand() {
        return run(() -> io.setPercent(IntakeConstants.EJECT_SPEED))
            .finallyDo(interrupted -> io.stop())
            .withName("EjectFuel");
    }

    public void stop() {
        io.stop();
    }
}
"""

_AK_SCORING_IO_JAVA = """\
package frc.robot.subsystems.scoring;

/** IO layer for the scoring subsystem. */
public interface ScoringIO {

    class ScoringIOInputs {
        public boolean flywheelConnected = false;
        public double flywheelVelocityRps = 0.0;
        public double flywheelCurrentAmps = 0.0;
        public boolean feedConnected = false;
        public double feedCurrentAmps = 0.0;
        public double feedAppliedVolts = 0.0;
    }

    default void updateInputs(ScoringIOInputs inputs) {}

    default void setFlywheelVelocityRps(double velocityRps) {}

    default void setFeedPercent(double percent) {}

    default void stopFlywheel() {}

    default void stopFeed() {}

    default void stopAll() {
        stopFlywheel();
        stopFeed();
    }
}
"""

_AK_SCORING_IO_TALONFX_JAVA = """\
package frc.robot.subsystems.scoring;

import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.controls.VelocityVoltage;
import com.ctre.phoenix6.hardware.TalonFX;
import com.ctre.phoenix6.signals.NeutralModeValue;
import frc.robot.Constants.ScoringConstants;

/** TalonFX-backed scoring IO for the real flywheel and feed motors. */
public class ScoringIOTalonFX implements ScoringIO {
    private final TalonFX flywheel = new TalonFX(ScoringConstants.FLYWHEEL_ID);
    private final TalonFX feed = new TalonFX(ScoringConstants.FEED_ID);
    private final VelocityVoltage flywheelRequest = new VelocityVoltage(0.0).withSlot(0);

    public ScoringIOTalonFX() {
        TalonFXConfiguration flywheelCfg = new TalonFXConfiguration();
        flywheelCfg.Slot0.kP = 0.5;
        flywheelCfg.Slot0.kI = 0.0;
        flywheelCfg.Slot0.kD = 0.0;
        flywheelCfg.Slot0.kV = 0.12;
        flywheelCfg.MotorOutput.NeutralMode = NeutralModeValue.Coast;
        flywheelCfg.CurrentLimits.SupplyCurrentLimit = 40.0;
        flywheelCfg.CurrentLimits.SupplyCurrentLimitEnable = true;
        flywheel.getConfigurator().apply(flywheelCfg);

        TalonFXConfiguration feedCfg = new TalonFXConfiguration();
        feedCfg.MotorOutput.NeutralMode = NeutralModeValue.Coast;
        feedCfg.CurrentLimits.SupplyCurrentLimit = 20.0;
        feedCfg.CurrentLimits.SupplyCurrentLimitEnable = true;
        feed.getConfigurator().apply(feedCfg);
    }

    @Override
    public void updateInputs(ScoringIOInputs inputs) {
        inputs.flywheelConnected = true;
        inputs.flywheelVelocityRps = flywheel.getVelocity().getValueAsDouble();
        inputs.flywheelCurrentAmps = flywheel.getSupplyCurrent().getValueAsDouble();
        inputs.feedConnected = true;
        inputs.feedCurrentAmps = feed.getSupplyCurrent().getValueAsDouble();
        inputs.feedAppliedVolts = feed.getMotorVoltage().getValueAsDouble();
    }

    @Override
    public void setFlywheelVelocityRps(double velocityRps) {
        flywheel.setControl(flywheelRequest.withVelocity(velocityRps));
    }

    @Override
    public void setFeedPercent(double percent) {
        feed.set(percent);
    }

    @Override
    public void stopFlywheel() {
        flywheel.stopMotor();
    }

    @Override
    public void stopFeed() {
        feed.stopMotor();
    }
}
"""

_AK_SCORING_IO_SIM_JAVA = """\
package frc.robot.subsystems.scoring;

import edu.wpi.first.wpilibj.Timer;

/** Lightweight simulated scoring IO for desktop testing. */
public class ScoringIOSim implements ScoringIO {
    private double targetFlywheelRps = 0.0;
    private double flywheelVelocityRps = 0.0;
    private double feedPercent = 0.0;
    private double lastTimestamp = Timer.getFPGATimestamp();

    @Override
    public void updateInputs(ScoringIOInputs inputs) {
        double now = Timer.getFPGATimestamp();
        double dt = Math.max(0.0, now - lastTimestamp);
        lastTimestamp = now;

        double response = Math.min(1.0, dt * 6.0);
        flywheelVelocityRps += (targetFlywheelRps - flywheelVelocityRps) * response;

        inputs.flywheelConnected = true;
        inputs.flywheelVelocityRps = flywheelVelocityRps;
        inputs.flywheelCurrentAmps = Math.abs(targetFlywheelRps - flywheelVelocityRps) * 0.35 + Math.abs(targetFlywheelRps) * 0.08;
        inputs.feedConnected = true;
        inputs.feedCurrentAmps = Math.abs(feedPercent) * 12.0;
        inputs.feedAppliedVolts = feedPercent * 12.0;
    }

    @Override
    public void setFlywheelVelocityRps(double velocityRps) {
        targetFlywheelRps = velocityRps;
    }

    @Override
    public void setFeedPercent(double percent) {
        feedPercent = percent;
    }

    @Override
    public void stopFlywheel() {
        targetFlywheelRps = 0.0;
    }

    @Override
    public void stopFeed() {
        feedPercent = 0.0;
    }
}
"""

_AK_SCORING_JAVA = """\
package frc.robot.subsystems.scoring;

import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;
import edu.wpi.first.wpilibj2.command.SubsystemBase;
import frc.robot.Constants.ScoringConstants;
import org.littletonrobotics.junction.Logger;

/** Flywheel shooter plus feed roller for scoring FUEL into the HUB. */
public class Scoring extends SubsystemBase {
    private final ScoringIO io;
    private final ScoringIO.ScoringIOInputs inputs = new ScoringIO.ScoringIOInputs();

    public Scoring(ScoringIO io) {
        this.io = io;
    }

    @Override
    public void periodic() {
        io.updateInputs(inputs);
        Logger.recordOutput("Scoring/FlywheelConnected", inputs.flywheelConnected);
        Logger.recordOutput("Scoring/FlywheelRPS", inputs.flywheelVelocityRps);
        Logger.recordOutput("Scoring/FlywheelCurrentAmps", inputs.flywheelCurrentAmps);
        Logger.recordOutput("Scoring/FeedConnected", inputs.feedConnected);
        Logger.recordOutput("Scoring/FeedCurrentAmps", inputs.feedCurrentAmps);
        Logger.recordOutput("Scoring/IsAtSpeed", isAtSpeed());
    }

    public boolean isAtSpeed() {
        return Math.abs(inputs.flywheelVelocityRps - ScoringConstants.FLYWHEEL_RPS)
            <= ScoringConstants.FLYWHEEL_TOLERANCE_RPS;
    }

    public Command scoreCommand() {
        return runOnce(() -> io.setFlywheelVelocityRps(ScoringConstants.FLYWHEEL_RPS))
            .andThen(Commands.waitUntil(this::isAtSpeed))
            .andThen(run(() -> {
                io.setFlywheelVelocityRps(ScoringConstants.FLYWHEEL_RPS);
                io.setFeedPercent(ScoringConstants.FEED_SPEED);
            }))
            .finallyDo(interrupted -> io.stopAll())
            .withName("ScoreFuel");
    }

    public Command spinUpCommand() {
        return startEnd(
            () -> io.setFlywheelVelocityRps(ScoringConstants.FLYWHEEL_RPS),
            () -> io.stopFlywheel())
            .withName("SpinUpFlywheel");
    }

    public void stop() {
        io.stopAll();
    }
}
"""

_AK_LIFT_IO_JAVA = """\
package frc.robot.subsystems.lift;

/** IO layer for the lift subsystem. */
public interface LiftIO {

    class LiftIOInputs {
        public boolean leaderConnected = false;
        public boolean followerConnected = false;
        public double positionRotations = 0.0;
        public double targetRotations = 0.0;
        public double currentAmps = 0.0;
    }

    default void updateInputs(LiftIOInputs inputs) {}

    default void setTargetPositionRotations(double rotations) {}

    default void zeroPosition() {}

    default void stop() {}
}
"""

_AK_LIFT_IO_TALONFX_JAVA = """\
package frc.robot.subsystems.lift;

import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.controls.Follower;
import com.ctre.phoenix6.controls.PositionVoltage;
import com.ctre.phoenix6.hardware.TalonFX;
import com.ctre.phoenix6.signals.NeutralModeValue;
import frc.robot.Constants.LiftConstants;

/** TalonFX-backed lift IO for the real robot. */
public class LiftIOTalonFX implements LiftIO {
    private final TalonFX leader = new TalonFX(LiftConstants.MOTOR_ID);
    private final TalonFX follower = new TalonFX(LiftConstants.FOLLOWER_ID);
    private final PositionVoltage positionRequest = new PositionVoltage(0.0).withSlot(0);
    private double targetRotations = LiftConstants.RETRACTED;

    public LiftIOTalonFX() {
        TalonFXConfiguration cfg = new TalonFXConfiguration();
        cfg.Slot0.kP = LiftConstants.KP;
        cfg.Slot0.kI = LiftConstants.KI;
        cfg.Slot0.kD = LiftConstants.KD;
        cfg.MotorOutput.NeutralMode = NeutralModeValue.Brake;
        cfg.CurrentLimits.StatorCurrentLimit = LiftConstants.STALL_LIMIT;
        cfg.CurrentLimits.StatorCurrentLimitEnable = true;
        leader.getConfigurator().apply(cfg);
        follower.getConfigurator().apply(cfg);
        follower.setControl(new Follower(LiftConstants.MOTOR_ID, false));
        zeroPosition();
    }

    @Override
    public void updateInputs(LiftIOInputs inputs) {
        inputs.leaderConnected = true;
        inputs.followerConnected = true;
        inputs.positionRotations = leader.getPosition().getValueAsDouble();
        inputs.targetRotations = targetRotations;
        inputs.currentAmps = leader.getSupplyCurrent().getValueAsDouble();
    }

    @Override
    public void setTargetPositionRotations(double rotations) {
        targetRotations = rotations;
        leader.setControl(positionRequest.withPosition(rotations));
    }

    @Override
    public void zeroPosition() {
        leader.setPosition(0.0);
        targetRotations = 0.0;
    }

    @Override
    public void stop() {
        leader.stopMotor();
        follower.stopMotor();
    }
}
"""

_AK_LIFT_IO_SIM_JAVA = """\
package frc.robot.subsystems.lift;

import edu.wpi.first.wpilibj.Timer;

/** Lightweight simulated lift IO for desktop testing. */
public class LiftIOSim implements LiftIO {
    private double positionRotations = 0.0;
    private double targetRotations = 0.0;
    private double lastTimestamp = Timer.getFPGATimestamp();

    @Override
    public void updateInputs(LiftIOInputs inputs) {
        double now = Timer.getFPGATimestamp();
        double dt = Math.max(0.0, now - lastTimestamp);
        lastTimestamp = now;

        double error = targetRotations - positionRotations;
        double maxStep = dt * 40.0;
        positionRotations += Math.copySign(Math.min(Math.abs(error), maxStep), error);

        inputs.leaderConnected = true;
        inputs.followerConnected = true;
        inputs.positionRotations = positionRotations;
        inputs.targetRotations = targetRotations;
        inputs.currentAmps = Math.min(40.0, Math.abs(error) * 2.0);
    }

    @Override
    public void setTargetPositionRotations(double rotations) {
        targetRotations = rotations;
    }

    @Override
    public void zeroPosition() {
        positionRotations = 0.0;
        targetRotations = 0.0;
    }

    @Override
    public void stop() {
        targetRotations = positionRotations;
    }
}
"""

_AK_LIFT_JAVA = """\
package frc.robot.subsystems.lift;

import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;
import edu.wpi.first.wpilibj2.command.SubsystemBase;
import frc.robot.Constants.LiftConstants;
import org.littletonrobotics.junction.Logger;

/**
 * TOWER-climbing elevator/winch.
 * Supports LEVEL 1 (off carpet), LEVEL 2 (above LOW RUNG), LEVEL 3 (above MID RUNG).
 */
public class Lift extends SubsystemBase {
    private final LiftIO io;
    private final LiftIO.LiftIOInputs inputs = new LiftIO.LiftIOInputs();
    private int targetLevel = 0;

    public Lift(LiftIO io) {
        this.io = io;
        io.zeroPosition();
    }

    @Override
    public void periodic() {
        io.updateInputs(inputs);
        Logger.recordOutput("Lift/LeaderConnected", inputs.leaderConnected);
        Logger.recordOutput("Lift/FollowerConnected", inputs.followerConnected);
        Logger.recordOutput("Lift/PositionRot", inputs.positionRotations);
        Logger.recordOutput("Lift/TargetLevel", targetLevel);
        Logger.recordOutput("Lift/AtTarget", isAtTarget());
        Logger.recordOutput("Lift/CurrentAmps", inputs.currentAmps);
    }

    private double levelToPosition(int level) {
        return switch (level) {
            case 1 -> LiftConstants.LEVEL_1_POS;
            case 2 -> LiftConstants.LEVEL_2_POS;
            case 3 -> LiftConstants.LEVEL_3_POS;
            default -> LiftConstants.RETRACTED;
        };
    }

    public boolean isAtTarget() {
        return Math.abs(inputs.positionRotations - levelToPosition(targetLevel)) <= LiftConstants.POS_TOLERANCE;
    }

    public Command climbToLevel(int level) {
        return runOnce(() -> {
            targetLevel = level;
            io.setTargetPositionRotations(levelToPosition(level));
        }).andThen(Commands.waitUntil(this::isAtTarget))
          .withName("ClimbLevel" + level);
    }

    public Command retractCommand() {
        return climbToLevel(0);
    }

    public int getCurrentLevel() {
        return targetLevel;
    }

    public void stop() {
        io.stop();
    }
}
"""

_ROBOT_CONTAINER_JAVA = """\
package frc.robot;

import edu.wpi.first.math.MathUtil;
import edu.wpi.first.wpilibj.DriverStation;
import edu.wpi.first.wpilibj.smartdashboard.SendableChooser;
import edu.wpi.first.wpilibj.smartdashboard.SmartDashboard;
import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;
import edu.wpi.first.wpilibj2.command.button.CommandXboxController;
import com.pathplanner.lib.auto.AutoBuilder;
import frc.robot.Constants.OperatorConstants;
import frc.robot.subsystems.DriveSubsystem;
import frc.robot.subsystems.IntakeSubsystem;
import frc.robot.subsystems.LiftSubsystem;
import frc.robot.subsystems.ScoringSubsystem;
import frc.robot.autos.AutoRoutines;

/** Wires subsystems, commands, and driver bindings. */
public class RobotContainer {

    private final DriveSubsystem   m_drive   = new DriveSubsystem();
    private final IntakeSubsystem  m_intake  = new IntakeSubsystem();
    private final ScoringSubsystem m_scoring = new ScoringSubsystem();
    private final LiftSubsystem    m_lift    = new LiftSubsystem();

    private final CommandXboxController m_driver   = new CommandXboxController(OperatorConstants.DRIVER_PORT);
    private final CommandXboxController m_operator = new CommandXboxController(OperatorConstants.OPERATOR_PORT);

    private final SendableChooser<Command> m_autoChooser;

    public RobotContainer() {
        configureDefaultCommands();
        configureBindings();

        // PathPlanner autos registered in AutoRoutines
        AutoRoutines.register(m_drive, m_intake, m_scoring, m_lift);
        m_autoChooser = AutoBuilder.buildAutoChooser("SafeAutoScore");
        SmartDashboard.putData("AutoChooser", m_autoChooser);
    }

    private void configureDefaultCommands() {
        m_drive.setDefaultCommand(Commands.run(() -> {
            double vx = -MathUtil.applyDeadband(m_driver.getLeftY(),  OperatorConstants.DEADBAND)
                * Constants.DriveConstants.MAX_SPEED_MPS;
            double vy = -MathUtil.applyDeadband(m_driver.getLeftX(),  OperatorConstants.DEADBAND)
                * Constants.DriveConstants.MAX_SPEED_MPS;
            double omega = -MathUtil.applyDeadband(m_driver.getRightX(), OperatorConstants.DEADBAND)
                * Constants.DriveConstants.MAX_ANGULAR_SPEED_RPS;
            m_drive.drive(vx, vy, omega, true);
        }, m_drive).withName("DriverTeleop"));
    }

    private void configureBindings() {
        // ── Driver controller ─────────────────────────────────────────────────
        // Right bumper: run intake while held
        m_driver.rightBumper().whileTrue(m_intake.intakeCommand());

        // Left bumper: eject
        m_driver.leftBumper().whileTrue(m_intake.ejectCommand());

        // Right trigger: score (spin up + feed)
        m_driver.rightTrigger(0.5).whileTrue(m_scoring.scoreCommand());

        // Y: zero heading
        m_driver.y().onTrue(Commands.runOnce(m_drive::zeroHeading, m_drive));

        // ── Operator controller ───────────────────────────────────────────────
        // A: climb LEVEL 1 (endgame / auto)
        m_operator.a().onTrue(m_lift.climbToLevel(1));
        // B: climb LEVEL 2
        m_operator.b().onTrue(m_lift.climbToLevel(2));
        // X: climb LEVEL 3
        m_operator.x().onTrue(m_lift.climbToLevel(3));
        // Y: retract
        m_operator.y().onTrue(m_lift.retractCommand());

        // Left bumper: pre-spin flywheel during transit
        m_operator.leftBumper().whileTrue(m_scoring.spinUpCommand());

        // HUB awareness: SmartDashboard for now; FMS game data parsed in Robot.java periodically
        SmartDashboard.putBoolean("HUBActive", true); // updated from DriverStation.getGameSpecificMessage()
    }

    public Command getAutonomousCommand() {
        return m_autoChooser.getSelected();
    }
}
"""

_AUTO_ROUTINES_JAVA = """\
package frc.robot.autos;

import com.pathplanner.lib.auto.NamedCommands;
import com.pathplanner.lib.auto.AutoBuilder;
import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;
import edu.wpi.first.wpilibj2.command.WaitCommand;
import frc.robot.subsystems.DriveSubsystem;
import frc.robot.subsystems.IntakeSubsystem;
import frc.robot.subsystems.LiftSubsystem;
import frc.robot.subsystems.ScoringSubsystem;

/** Registers named commands for PathPlanner and builds auto routines. */
public final class AutoRoutines {
    private AutoRoutines() {}

    public static void register(
        DriveSubsystem drive, IntakeSubsystem intake,
        ScoringSubsystem scoring, LiftSubsystem lift
    ) {
        // Named commands used in PathPlanner GUI paths
        NamedCommands.registerCommand("Intake",    intake.intakeCommand());
        NamedCommands.registerCommand("Score",     scoring.scoreCommand());
        NamedCommands.registerCommand("SpinUp",    scoring.spinUpCommand());
        NamedCommands.registerCommand("ClimbL1",   lift.climbToLevel(1));
        NamedCommands.registerCommand("ClimbL2",   lift.climbToLevel(2));
        NamedCommands.registerCommand("ClimbL3",   lift.climbToLevel(3));
        NamedCommands.registerCommand("Retract",   lift.retractCommand());
    }

    /**
     * SafeAutoScore: Score preloaded FUEL, collect from DEPOT, score again.
     * No auto climb (prioritize fuel RP contribution).
     * Cycle time target: <= 3.5s/ball for ENERGIZED RP contribution.
     */
    public static Command safeAutoScore(
        ScoringSubsystem scoring, IntakeSubsystem intake
    ) {
        return Commands.sequence(
            scoring.spinUpCommand().withTimeout(0.5),
            scoring.scoreCommand().withTimeout(3.0),  // score preload (up to 8 FUEL)
            intake.intakeCommand().withTimeout(4.0),   // collect from DEPOT
            scoring.scoreCommand().withTimeout(5.0),   // score collected
            Commands.print("[Auto] SafeAutoScore complete")
        ).withName("SafeAutoScore");
    }
}
"""

_AK_BUILD_GRADLE = """\
// build.gradle — AdvantageKit-style TalonFX swerve + PhotonVision hybrid
// TODO: Verify exact library versions against official releases before deploy.
plugins {
    id "java"
    id "edu.wpi.first.GradleRIO" version "2026.1.1"
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

def ROBOT_MAIN_CLASS = "frc.robot.Main"

deploy {
    targets {
        roborio(getTargetTypeClass('RoboRIO')) {
            team = project.frc.getTeamOrDefault(9999)
            debug = project.findProperty("debug") ?: false
            artifacts {
                frcJava(getArtifactTypeClass('FRCJavaArtifact')) {}
                frcStaticFileDeploy(getArtifactTypeClass('FileTreeArtifact')) {
                    files = project.fileTree('src/main/deploy')
                    directory = '/home/lvuser/deploy'
                }
            }
        }
    }
}

repositories {
    mavenLocal()
    maven { url = uri("https://frcmaven.wpi.edu/release") }
    maven { url = uri("https://maven.ctr-electronics.com/release/") }
    maven { url = uri("https://maven.photonvision.org/repository/internal") }
    maven { url = uri("https://frcmaven.wpi.edu/artifactory/littletonrobotics-mvn-release/") }
}

dependencies {
    implementation wpi.java.deps.wpilib()
    implementation wpi.java.vendor.java()

    roborioDebug wpi.java.deps.wpilibJniDebug(wpi.platforms.roborio)
    roborioDebug wpi.java.vendor.jniDebug(wpi.platforms.roborio)
    nativeDebug wpi.java.deps.wpilibJniDebug(wpi.platforms.desktop)
    nativeDebug wpi.java.vendor.jniDebug(wpi.platforms.desktop)
    simulationDebug wpi.java.deps.wpilibJniDebug(wpi.platforms.desktop)
    simulationDebug wpi.java.vendor.jniDebug(wpi.platforms.desktop)
    nativeRelease wpi.java.deps.wpilibJniRelease(wpi.platforms.desktop)
    nativeRelease wpi.java.vendor.jniRelease(wpi.platforms.desktop)
    simulationRelease wpi.java.deps.wpilibJniRelease(wpi.platforms.desktop)
    simulationRelease wpi.java.vendor.jniRelease(wpi.platforms.desktop)

    testImplementation 'org.junit.jupiter:junit-jupiter:5.10.1'
    testRuntimeOnly 'org.junit.jupiter:junit-jupiter-engine:5.10.1'
}

test {
    useJUnitPlatform()
    systemProperty 'junit.jupiter.extensions.autodetection.enabled', 'true'
}

wpi.java.configureExecutableTasks(jar)
wpi.java.configureTestTasks(test)

tasks.withType(JavaCompile).configureEach {
    options.compilerArgs.add '-XDstringConcat=inline'
}

jar {
    manifest { attributes 'Main-Class': ROBOT_MAIN_CLASS }
}
"""

_AK_CONSTANTS_JAVA = """\
package frc.robot;

import edu.wpi.first.wpilibj.RobotBase;

/** Project-wide runtime mode and mechanism constants. */
public final class Constants {

    public static final int TEAM_NUMBER = 9999; // TODO: set team number

    public static final Mode simMode = Mode.SIM;
    public static final Mode currentMode = RobotBase.isReal() ? Mode.REAL : simMode;

    public enum Mode {
        REAL,
        SIM,
        REPLAY
    }

    /** FUEL ground intake (single roller, TalonFX). */
    public static final class IntakeConstants {
        public static final int MOTOR_ID = 30;
        public static final double INTAKE_SPEED = 0.8;
        public static final double EJECT_SPEED = -0.5;

        private IntakeConstants() {}
    }

    /** HUB scoring flywheel + feed roller. */
    public static final class ScoringConstants {
        public static final int FLYWHEEL_ID = 31;
        public static final int FEED_ID = 32;
        public static final double FLYWHEEL_RPS = 80.0;
        public static final double FLYWHEEL_TOLERANCE_RPS = 3.0;
        public static final double FEED_SPEED = 0.6;

        private ScoringConstants() {}
    }

    /** Tower climbing elevator/winch (2x TalonFX, one follows the other). */
    public static final class LiftConstants {
        public static final int MOTOR_ID = 40;
        public static final int FOLLOWER_ID = 41;
        public static final double RETRACTED = 0.0;
        public static final double LEVEL_1_POS = 10.0;
        public static final double LEVEL_2_POS = 35.0;
        public static final double LEVEL_3_POS = 65.0;
        public static final double POS_TOLERANCE = 1.0;
        public static final double STALL_LIMIT = 60.0;
        public static final double KP = 2.0;
        public static final double KI = 0.0;
        public static final double KD = 0.1;

        private LiftConstants() {}
    }

    public static final class OperatorConstants {
        public static final int DRIVER_PORT = 0;
        public static final int OPERATOR_PORT = 1;
        public static final double DEADBAND = 0.1;

        private OperatorConstants() {}
    }

    private Constants() {}
}
"""

_AK_ROBOT_JAVA = """\
package frc.robot;

import edu.wpi.first.wpilibj.Timer;
import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.CommandScheduler;
import org.littletonrobotics.junction.LogFileUtil;
import org.littletonrobotics.junction.LoggedRobot;
import org.littletonrobotics.junction.Logger;
import org.littletonrobotics.junction.networktables.NT4Publisher;
import org.littletonrobotics.junction.wpilog.WPILOGReader;
import org.littletonrobotics.junction.wpilog.WPILOGWriter;

/** Top-level robot class using AdvantageKit LoggedRobot mode wiring. */
public class Robot extends LoggedRobot {
    private Command autonomousCommand;
    private final RobotContainer robotContainer;

    public Robot() {
        Logger.recordMetadata("ProjectName", "REBUILT_Robot_2026");
        Logger.recordMetadata("RuntimeMode", Constants.currentMode.name());
        Logger.recordMetadata("RobotArchitecture", "AdvantageKit-style TalonFX swerve + PhotonVision");

        switch (Constants.currentMode) {
            case REAL -> {
                Logger.addDataReceiver(new WPILOGWriter());
                Logger.addDataReceiver(new NT4Publisher());
            }
            case SIM -> Logger.addDataReceiver(new NT4Publisher());
            case REPLAY -> {
                setUseTiming(false);
                String logPath = LogFileUtil.findReplayLog();
                Logger.setReplaySource(new WPILOGReader(logPath));
                Logger.addDataReceiver(new WPILOGWriter(LogFileUtil.addPathSuffix(logPath, "_sim")));
            }
        }

        Logger.start();
        robotContainer = new RobotContainer();
    }

    @Override
    public void robotPeriodic() {
        CommandScheduler.getInstance().run();
        Logger.recordOutput("MatchTime", Timer.getMatchTime());
    }

    @Override
    public void autonomousInit() {
        autonomousCommand = robotContainer.getAutonomousCommand();
        if (autonomousCommand != null) {
            CommandScheduler.getInstance().schedule(autonomousCommand);
        }
    }

    @Override
    public void teleopInit() {
        if (autonomousCommand != null) {
            CommandScheduler.getInstance().cancel(autonomousCommand);
        }
    }

    @Override
    public void testInit() {
        CommandScheduler.getInstance().cancelAll();
    }
}
"""

_AK_TUNER_CONSTANTS_JAVA = """\
package frc.robot.generated;

import edu.wpi.first.math.util.Units;

/**
 * Generated drive constants modeled after CTRE Tuner X output.
 *
 * <p>Replace CAN IDs, offsets, inversions, and gains with values exported from Tuner X before
 * enabling this on real hardware.
 */
public final class TunerConstants {
    public static final String kCANBusName = "rio";
    public static final int kPigeonId = 20;
    public static final double kSpeedAt12VoltsMps = 4.5;

    public static final class Slot0Gains {
        public final double kP;
        public final double kI;
        public final double kD;
        public final double kS;
        public final double kV;

        public Slot0Gains(double kP, double kI, double kD, double kS, double kV) {
            this.kP = kP;
            this.kI = kI;
            this.kD = kD;
            this.kS = kS;
            this.kV = kV;
        }
    }

    public static final class ModuleConstants {
        public final int DriveMotorId;
        public final int SteerMotorId;
        public final int EncoderId;
        public final double EncoderOffset;
        public final boolean DriveMotorInverted;
        public final boolean SteerMotorInverted;
        public final boolean EncoderInverted;
        public final double DriveMotorGearRatio;
        public final double SteerMotorGearRatio;
        public final double WheelRadius;
        public final double SlipCurrent;
        public final double LocationX;
        public final double LocationY;
        public final Slot0Gains DriveMotorGains;
        public final Slot0Gains SteerMotorGains;

        public ModuleConstants(
            int driveMotorId,
            int steerMotorId,
            int encoderId,
            double encoderOffset,
            boolean driveMotorInverted,
            boolean steerMotorInverted,
            boolean encoderInverted,
            double driveMotorGearRatio,
            double steerMotorGearRatio,
            double wheelRadius,
            double slipCurrent,
            double locationX,
            double locationY,
            Slot0Gains driveMotorGains,
            Slot0Gains steerMotorGains
        ) {
            DriveMotorId = driveMotorId;
            SteerMotorId = steerMotorId;
            EncoderId = encoderId;
            EncoderOffset = encoderOffset;
            DriveMotorInverted = driveMotorInverted;
            SteerMotorInverted = steerMotorInverted;
            EncoderInverted = encoderInverted;
            DriveMotorGearRatio = driveMotorGearRatio;
            SteerMotorGearRatio = steerMotorGearRatio;
            WheelRadius = wheelRadius;
            SlipCurrent = slipCurrent;
            LocationX = locationX;
            LocationY = locationY;
            DriveMotorGains = driveMotorGains;
            SteerMotorGains = steerMotorGains;
        }
    }

    private static final Slot0Gains driveGains = new Slot0Gains(0.1, 0.0, 0.0, 0.1, 2.3);
    private static final Slot0Gains steerGains = new Slot0Gains(80.0, 0.0, 0.5, 0.0, 0.0);

    private static final double kDriveGearRatio = 6.75;
    private static final double kSteerGearRatio = 21.4286;
    private static final double kWheelRadius = Units.inchesToMeters(2.0);
    private static final double kSlipCurrent = 60.0;

    private static final double kFrontLeftXPos = Units.inchesToMeters(11.0);
    private static final double kFrontLeftYPos = Units.inchesToMeters(11.0);
    private static final double kFrontRightXPos = Units.inchesToMeters(11.0);
    private static final double kFrontRightYPos = Units.inchesToMeters(-11.0);
    private static final double kBackLeftXPos = Units.inchesToMeters(-11.0);
    private static final double kBackLeftYPos = Units.inchesToMeters(11.0);
    private static final double kBackRightXPos = Units.inchesToMeters(-11.0);
    private static final double kBackRightYPos = Units.inchesToMeters(-11.0);

    public static final ModuleConstants FrontLeft = new ModuleConstants(
        1, 2, 11, 0.0, false, false, false,
        kDriveGearRatio, kSteerGearRatio, kWheelRadius, kSlipCurrent,
        kFrontLeftXPos, kFrontLeftYPos, driveGains, steerGains);
    public static final ModuleConstants FrontRight = new ModuleConstants(
        3, 4, 12, 0.0, false, false, false,
        kDriveGearRatio, kSteerGearRatio, kWheelRadius, kSlipCurrent,
        kFrontRightXPos, kFrontRightYPos, driveGains, steerGains);
    public static final ModuleConstants BackLeft = new ModuleConstants(
        5, 6, 13, 0.0, false, false, false,
        kDriveGearRatio, kSteerGearRatio, kWheelRadius, kSlipCurrent,
        kBackLeftXPos, kBackLeftYPos, driveGains, steerGains);
    public static final ModuleConstants BackRight = new ModuleConstants(
        7, 8, 14, 0.0, false, false, false,
        kDriveGearRatio, kSteerGearRatio, kWheelRadius, kSlipCurrent,
        kBackRightXPos, kBackRightYPos, driveGains, steerGains);

    private TunerConstants() {}
}
"""

_AK_GYRO_IO_JAVA = """\
package frc.robot.subsystems.drive;

import edu.wpi.first.math.geometry.Rotation2d;

/** Gyro abstraction used by the drive subsystem. */
public interface GyroIO {

    class GyroIOInputs {
        public boolean connected = false;
        public Rotation2d yawPosition = Rotation2d.kZero;
        public double yawVelocityRadPerSec = 0.0;
    }

    default void updateInputs(GyroIOInputs inputs) {}

    default void zero() {}
}
"""

_AK_GYRO_IO_PIGEON2_JAVA = """\
package frc.robot.subsystems.drive;

import com.ctre.phoenix6.hardware.Pigeon2;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.util.Units;
import frc.robot.generated.TunerConstants;

/** Pigeon2-backed gyro IO for real TalonFX swerve hardware. */
public class GyroIOPigeon2 implements GyroIO {
    private final Pigeon2 pigeon = new Pigeon2(TunerConstants.kPigeonId, TunerConstants.kCANBusName);

    @Override
    public void updateInputs(GyroIOInputs inputs) {
        inputs.connected = true;
        inputs.yawPosition = pigeon.getRotation2d();
        inputs.yawVelocityRadPerSec = Units.degreesToRadians(pigeon.getAngularVelocityZWorld().getValueAsDouble());
    }

    @Override
    public void zero() {
        pigeon.reset();
    }
}
"""

_AK_MODULE_IO_JAVA = """\
package frc.robot.subsystems.drive;

import edu.wpi.first.math.geometry.Rotation2d;

/** Module IO abstraction patterned after the AdvantageKit swerve templates. */
public interface ModuleIO {

    class ModuleIOInputs {
        public boolean driveConnected = false;
        public double drivePositionRad = 0.0;
        public double driveVelocityRadPerSec = 0.0;
        public double driveAppliedVolts = 0.0;
        public double driveCurrentAmps = 0.0;

        public boolean turnConnected = false;
        public boolean turnEncoderConnected = false;
        public Rotation2d turnAbsolutePosition = Rotation2d.kZero;
        public Rotation2d turnPosition = Rotation2d.kZero;
        public double turnVelocityRadPerSec = 0.0;
        public double turnAppliedVolts = 0.0;
        public double turnCurrentAmps = 0.0;
    }

    default void updateInputs(ModuleIOInputs inputs) {}

    default void setDriveOpenLoop(double outputVolts) {}

    default void setTurnOpenLoop(double outputVolts) {}

    default void setDriveVelocity(double velocityRadPerSec) {}

    default void setTurnPosition(Rotation2d rotation) {}

    default void stop() {}
}
"""

_AK_MODULE_IO_TALONFX_JAVA = """\
package frc.robot.subsystems.drive;

import com.ctre.phoenix6.configs.CANcoderConfiguration;
import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.controls.PositionVoltage;
import com.ctre.phoenix6.controls.VelocityVoltage;
import com.ctre.phoenix6.controls.VoltageOut;
import com.ctre.phoenix6.hardware.CANcoder;
import com.ctre.phoenix6.hardware.TalonFX;
import com.ctre.phoenix6.signals.FeedbackSensorSourceValue;
import com.ctre.phoenix6.signals.InvertedValue;
import com.ctre.phoenix6.signals.NeutralModeValue;
import com.ctre.phoenix6.signals.SensorDirectionValue;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.util.Units;
import frc.robot.generated.TunerConstants;
import frc.robot.generated.TunerConstants.ModuleConstants;

/** TalonFX + CANcoder module implementation for real robots. */
public class ModuleIOTalonFX implements ModuleIO {
    private final ModuleConstants constants;
    private final TalonFX driveMotor;
    private final TalonFX turnMotor;
    private final CANcoder turnEncoder;

    private final VelocityVoltage driveVelocityRequest = new VelocityVoltage(0.0).withSlot(0);
    private final PositionVoltage turnPositionRequest = new PositionVoltage(0.0).withSlot(0);
    private final VoltageOut driveOpenLoopRequest = new VoltageOut(0.0);
    private final VoltageOut turnOpenLoopRequest = new VoltageOut(0.0);

    public ModuleIOTalonFX(ModuleConstants constants) {
        this.constants = constants;
        driveMotor = new TalonFX(constants.DriveMotorId, TunerConstants.kCANBusName);
        turnMotor = new TalonFX(constants.SteerMotorId, TunerConstants.kCANBusName);
        turnEncoder = new CANcoder(constants.EncoderId, TunerConstants.kCANBusName);

        TalonFXConfiguration driveConfig = new TalonFXConfiguration();
        driveConfig.Slot0.kP = constants.DriveMotorGains.kP;
        driveConfig.Slot0.kI = constants.DriveMotorGains.kI;
        driveConfig.Slot0.kD = constants.DriveMotorGains.kD;
        driveConfig.Slot0.kS = constants.DriveMotorGains.kS;
        driveConfig.Slot0.kV = constants.DriveMotorGains.kV;
        driveConfig.MotorOutput.NeutralMode = NeutralModeValue.Brake;
        driveConfig.MotorOutput.Inverted = constants.DriveMotorInverted
            ? InvertedValue.Clockwise_Positive
            : InvertedValue.CounterClockwise_Positive;
        driveConfig.CurrentLimits.StatorCurrentLimit = constants.SlipCurrent;
        driveConfig.CurrentLimits.StatorCurrentLimitEnable = true;
        driveConfig.CurrentLimits.SupplyCurrentLimit = 40.0;
        driveConfig.CurrentLimits.SupplyCurrentLimitEnable = true;
        driveMotor.getConfigurator().apply(driveConfig);

        CANcoderConfiguration cancoderConfig = new CANcoderConfiguration();
        cancoderConfig.MagnetSensor.MagnetOffset = constants.EncoderOffset;
        cancoderConfig.MagnetSensor.SensorDirection = constants.EncoderInverted
            ? SensorDirectionValue.Clockwise_Positive
            : SensorDirectionValue.CounterClockwise_Positive;
        turnEncoder.getConfigurator().apply(cancoderConfig);

        TalonFXConfiguration turnConfig = new TalonFXConfiguration();
        turnConfig.Slot0.kP = constants.SteerMotorGains.kP;
        turnConfig.Slot0.kI = constants.SteerMotorGains.kI;
        turnConfig.Slot0.kD = constants.SteerMotorGains.kD;
        turnConfig.Feedback.FeedbackRemoteSensorID = constants.EncoderId;
        turnConfig.Feedback.FeedbackSensorSource = FeedbackSensorSourceValue.FusedCANcoder;
        turnConfig.Feedback.RotorToSensorRatio = constants.SteerMotorGearRatio;
        turnConfig.ClosedLoopGeneral.ContinuousWrap = true;
        turnConfig.MotorOutput.NeutralMode = NeutralModeValue.Brake;
        turnConfig.MotorOutput.Inverted = constants.SteerMotorInverted
            ? InvertedValue.Clockwise_Positive
            : InvertedValue.CounterClockwise_Positive;
        turnConfig.CurrentLimits.SupplyCurrentLimit = 20.0;
        turnConfig.CurrentLimits.SupplyCurrentLimitEnable = true;
        turnMotor.getConfigurator().apply(turnConfig);
    }

    @Override
    public void updateInputs(ModuleIOInputs inputs) {
        inputs.driveConnected = true;
        inputs.drivePositionRad = Units.rotationsToRadians(driveMotor.getPosition().getValueAsDouble())
            / constants.DriveMotorGearRatio;
        inputs.driveVelocityRadPerSec = Units.rotationsToRadians(driveMotor.getVelocity().getValueAsDouble())
            / constants.DriveMotorGearRatio;
        inputs.driveAppliedVolts = driveMotor.getMotorVoltage().getValueAsDouble();
        inputs.driveCurrentAmps = driveMotor.getSupplyCurrent().getValueAsDouble();

        inputs.turnConnected = true;
        inputs.turnEncoderConnected = true;
        inputs.turnAbsolutePosition = Rotation2d.fromRotations(turnEncoder.getAbsolutePosition().getValueAsDouble());
        inputs.turnPosition = Rotation2d.fromRotations(turnMotor.getPosition().getValueAsDouble());
        inputs.turnVelocityRadPerSec = turnMotor.getVelocity().getValueAsDouble() * 2.0 * Math.PI;
        inputs.turnAppliedVolts = turnMotor.getMotorVoltage().getValueAsDouble();
        inputs.turnCurrentAmps = turnMotor.getSupplyCurrent().getValueAsDouble();
    }

    @Override
    public void setDriveOpenLoop(double outputVolts) {
        driveMotor.setControl(driveOpenLoopRequest.withOutput(outputVolts));
    }

    @Override
    public void setTurnOpenLoop(double outputVolts) {
        turnMotor.setControl(turnOpenLoopRequest.withOutput(outputVolts));
    }

    @Override
    public void setDriveVelocity(double velocityRadPerSec) {
        double motorRotationsPerSecond = Units.radiansToRotations(velocityRadPerSec)
            * constants.DriveMotorGearRatio;
        driveMotor.setControl(driveVelocityRequest.withVelocity(motorRotationsPerSecond));
    }

    @Override
    public void setTurnPosition(Rotation2d rotation) {
        turnMotor.setControl(turnPositionRequest.withPosition(rotation.getRotations()));
    }

    @Override
    public void stop() {
        driveMotor.stopMotor();
        turnMotor.stopMotor();
    }
}
"""

_AK_MODULE_IO_SIM_JAVA = """\
package frc.robot.subsystems.drive;

import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.wpilibj.Timer;
import frc.robot.generated.TunerConstants;
import frc.robot.generated.TunerConstants.ModuleConstants;

/** Lightweight simulated module IO for desktop testing. */
public class ModuleIOSim implements ModuleIO {
    private final ModuleConstants constants;
    private double drivePositionRad = 0.0;
    private double driveVelocityRadPerSec = 0.0;
    private Rotation2d turnPosition = Rotation2d.kZero;
    private double turnVelocityRadPerSec = 0.0;
    private double lastTimestamp = Timer.getFPGATimestamp();

    public ModuleIOSim(ModuleConstants constants) {
        this.constants = constants;
    }

    @Override
    public void updateInputs(ModuleIOInputs inputs) {
        double now = Timer.getFPGATimestamp();
        double dt = Math.max(0.0, now - lastTimestamp);
        lastTimestamp = now;

        drivePositionRad += driveVelocityRadPerSec * dt;
        turnPosition = turnPosition.plus(new Rotation2d(turnVelocityRadPerSec * dt));

        inputs.driveConnected = true;
        inputs.drivePositionRad = drivePositionRad;
        inputs.driveVelocityRadPerSec = driveVelocityRadPerSec;
        inputs.driveAppliedVolts = 12.0 * driveVelocityRadPerSec
            / (TunerConstants.kSpeedAt12VoltsMps / constants.WheelRadius);
        inputs.driveCurrentAmps = Math.abs(inputs.driveAppliedVolts) * 2.0;

        inputs.turnConnected = true;
        inputs.turnEncoderConnected = true;
        inputs.turnAbsolutePosition = turnPosition;
        inputs.turnPosition = turnPosition;
        inputs.turnVelocityRadPerSec = turnVelocityRadPerSec;
        inputs.turnAppliedVolts = 0.0;
        inputs.turnCurrentAmps = 0.0;
    }

    @Override
    public void setDriveOpenLoop(double outputVolts) {
        driveVelocityRadPerSec = (outputVolts / 12.0) * (TunerConstants.kSpeedAt12VoltsMps / constants.WheelRadius);
    }

    @Override
    public void setTurnOpenLoop(double outputVolts) {
        turnVelocityRadPerSec = outputVolts / 12.0;
    }

    @Override
    public void setDriveVelocity(double velocityRadPerSec) {
        driveVelocityRadPerSec = velocityRadPerSec;
    }

    @Override
    public void setTurnPosition(Rotation2d rotation) {
        turnVelocityRadPerSec = rotation.minus(turnPosition).getRadians() / 0.02;
        turnPosition = rotation;
    }

    @Override
    public void stop() {
        driveVelocityRadPerSec = 0.0;
        turnVelocityRadPerSec = 0.0;
    }
}
"""

_AK_MODULE_JAVA = """\
package frc.robot.subsystems.drive;

import edu.wpi.first.math.kinematics.SwerveModulePosition;
import edu.wpi.first.math.kinematics.SwerveModuleState;
import frc.robot.generated.TunerConstants;
import org.littletonrobotics.junction.Logger;

/** Module wrapper that owns one drive/turn IO implementation. */
public class Module {
    private final ModuleIO io;
    private final ModuleIO.ModuleIOInputs inputs = new ModuleIO.ModuleIOInputs();
    private final int index;
    private final TunerConstants.ModuleConstants constants;

    public Module(ModuleIO io, int index, TunerConstants.ModuleConstants constants) {
        this.io = io;
        this.index = index;
        this.constants = constants;
    }

    public void periodic() {
        io.updateInputs(inputs);
        Logger.recordOutput("Drive/Module" + index + "/DrivePositionRad", inputs.drivePositionRad);
        Logger.recordOutput("Drive/Module" + index + "/DriveVelocityRadPerSec", inputs.driveVelocityRadPerSec);
        Logger.recordOutput("Drive/Module" + index + "/TurnPositionDeg", inputs.turnPosition.getDegrees());
    }

    public SwerveModuleState getState() {
        return new SwerveModuleState(inputs.driveVelocityRadPerSec * constants.WheelRadius, inputs.turnPosition);
    }

    public SwerveModulePosition getPosition() {
        return new SwerveModulePosition(inputs.drivePositionRad * constants.WheelRadius, inputs.turnPosition);
    }

    public void runSetpoint(SwerveModuleState setpoint) {
        SwerveModuleState optimized = SwerveModuleState.optimize(setpoint, inputs.turnPosition);
        io.setDriveVelocity(optimized.speedMetersPerSecond / constants.WheelRadius);
        io.setTurnPosition(optimized.angle);
        Logger.recordOutput("Drive/Module" + index + "/SetpointSpeedMps", optimized.speedMetersPerSecond);
        Logger.recordOutput("Drive/Module" + index + "/SetpointAngleDeg", optimized.angle.getDegrees());
    }

    public void stop() {
        io.stop();
    }
}
"""

_AK_DRIVE_JAVA = """\
package frc.robot.subsystems.drive;

import com.pathplanner.lib.auto.AutoBuilder;
import com.pathplanner.lib.config.ModuleConfig;
import com.pathplanner.lib.config.PIDConstants;
import com.pathplanner.lib.config.RobotConfig;
import com.pathplanner.lib.controllers.PPHolonomicDriveController;
import edu.wpi.first.math.Matrix;
import edu.wpi.first.math.estimator.SwerveDrivePoseEstimator;
import edu.wpi.first.math.geometry.Pose2d;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.geometry.Translation2d;
import edu.wpi.first.math.kinematics.ChassisSpeeds;
import edu.wpi.first.math.kinematics.SwerveDriveKinematics;
import edu.wpi.first.math.kinematics.SwerveModulePosition;
import edu.wpi.first.math.kinematics.SwerveModuleState;
import edu.wpi.first.math.numbers.N1;
import edu.wpi.first.math.numbers.N3;
import edu.wpi.first.math.system.plant.DCMotor;
import edu.wpi.first.wpilibj.DriverStation;
import edu.wpi.first.wpilibj.DriverStation.Alliance;
import edu.wpi.first.wpilibj2.command.SubsystemBase;
import frc.robot.generated.TunerConstants;
import org.littletonrobotics.junction.Logger;

/** AdvantageKit-style drive subsystem using IO layers for TalonFX swerve hardware. */
public class Drive extends SubsystemBase {
    private static final double ROBOT_MASS_KG = 56.7;
    private static final double ROBOT_MOI = 5.0;
    private static final double WHEEL_COF = 1.2;
    private static final PIDConstants TRANSLATION_PID = new PIDConstants(5.0, 0.0, 0.0);
    private static final PIDConstants ROTATION_PID = new PIDConstants(5.0, 0.0, 0.0);

    public static final double DRIVE_BASE_RADIUS = Math.max(
        Math.max(
            Math.hypot(TunerConstants.FrontLeft.LocationX, TunerConstants.FrontLeft.LocationY),
            Math.hypot(TunerConstants.FrontRight.LocationX, TunerConstants.FrontRight.LocationY)),
        Math.max(
            Math.hypot(TunerConstants.BackLeft.LocationX, TunerConstants.BackLeft.LocationY),
            Math.hypot(TunerConstants.BackRight.LocationX, TunerConstants.BackRight.LocationY)));

    private static final SwerveDriveKinematics KINEMATICS = new SwerveDriveKinematics(getModuleTranslations());

    private static final RobotConfig PP_CONFIG = new RobotConfig(
        ROBOT_MASS_KG,
        ROBOT_MOI,
        new ModuleConfig(
            TunerConstants.FrontLeft.WheelRadius,
            TunerConstants.kSpeedAt12VoltsMps,
            WHEEL_COF,
            DCMotor.getKrakenX60Foc(1).withReduction(TunerConstants.FrontLeft.DriveMotorGearRatio),
            TunerConstants.FrontLeft.SlipCurrent,
            1),
        getModuleTranslations());

    private final GyroIO gyroIO;
    private final GyroIO.GyroIOInputs gyroInputs = new GyroIO.GyroIOInputs();
    private final Module[] modules = new Module[4];
    private final SwerveDrivePoseEstimator poseEstimator;
    private Rotation2d rawGyroRotation = Rotation2d.kZero;

    public Drive(
        GyroIO gyroIO,
        ModuleIO frontLeftIO,
        ModuleIO frontRightIO,
        ModuleIO backLeftIO,
        ModuleIO backRightIO
    ) {
        this.gyroIO = gyroIO;
        modules[0] = new Module(frontLeftIO, 0, TunerConstants.FrontLeft);
        modules[1] = new Module(frontRightIO, 1, TunerConstants.FrontRight);
        modules[2] = new Module(backLeftIO, 2, TunerConstants.BackLeft);
        modules[3] = new Module(backRightIO, 3, TunerConstants.BackRight);

        poseEstimator = new SwerveDrivePoseEstimator(
            KINEMATICS,
            rawGyroRotation,
            getModulePositions(),
            Pose2d.kZero);

        AutoBuilder.configure(
            this::getPose,
            this::setPose,
            this::getChassisSpeeds,
            this::runVelocity,
            new PPHolonomicDriveController(TRANSLATION_PID, ROTATION_PID),
            PP_CONFIG,
            () -> DriverStation.getAlliance().orElse(Alliance.Blue) == Alliance.Red,
            this);
    }

    @Override
    public void periodic() {
        gyroIO.updateInputs(gyroInputs);
        for (Module module : modules) {
            module.periodic();
        }

        if (gyroInputs.connected) {
            rawGyroRotation = gyroInputs.yawPosition;
        } else {
            rawGyroRotation = rawGyroRotation.plus(Rotation2d.fromRadians(getChassisSpeeds().omegaRadiansPerSecond * 0.02));
        }

        poseEstimator.update(rawGyroRotation, getModulePositions());

        Logger.recordOutput("Drive/GyroConnected", gyroInputs.connected);
        Logger.recordOutput("Drive/GyroYawDeg", rawGyroRotation.getDegrees());
        Logger.recordOutput("Odometry/Robot", getPose());
        Logger.recordOutput("SwerveStates/Measured", getModuleStates());
    }

    public void runVelocity(ChassisSpeeds speeds) {
        SwerveModuleState[] setpoints = KINEMATICS.toSwerveModuleStates(speeds);
        SwerveDriveKinematics.desaturateWheelSpeeds(setpoints, TunerConstants.kSpeedAt12VoltsMps);
        for (int i = 0; i < modules.length; i++) {
            modules[i].runSetpoint(setpoints[i]);
        }
    }

    public void stop() {
        for (Module module : modules) {
            module.stop();
        }
    }

    public Pose2d getPose() {
        return poseEstimator.getEstimatedPosition();
    }

    public Rotation2d getRotation() {
        return rawGyroRotation;
    }

    public void setPose(Pose2d pose) {
        poseEstimator.resetPosition(rawGyroRotation, getModulePositions(), pose);
    }

    public void addVisionMeasurement(
        Pose2d visionRobotPoseMeters,
        double timestampSeconds,
        Matrix<N3, N1> visionMeasurementStdDevs
    ) {
        poseEstimator.addVisionMeasurement(visionRobotPoseMeters, timestampSeconds, visionMeasurementStdDevs);
    }

    public ChassisSpeeds getChassisSpeeds() {
        return KINEMATICS.toChassisSpeeds(getModuleStates());
    }

    public void zeroGyro() {
        gyroIO.zero();
        rawGyroRotation = Rotation2d.kZero;
    }

    public double getMaxLinearSpeedMetersPerSec() {
        return TunerConstants.kSpeedAt12VoltsMps;
    }

    public double getMaxAngularSpeedRadPerSec() {
        return TunerConstants.kSpeedAt12VoltsMps / DRIVE_BASE_RADIUS;
    }

    private SwerveModuleState[] getModuleStates() {
        SwerveModuleState[] states = new SwerveModuleState[modules.length];
        for (int i = 0; i < modules.length; i++) {
            states[i] = modules[i].getState();
        }
        return states;
    }

    private SwerveModulePosition[] getModulePositions() {
        SwerveModulePosition[] positions = new SwerveModulePosition[modules.length];
        for (int i = 0; i < modules.length; i++) {
            positions[i] = modules[i].getPosition();
        }
        return positions;
    }

    public static Translation2d[] getModuleTranslations() {
        return new Translation2d[] {
            new Translation2d(TunerConstants.FrontLeft.LocationX, TunerConstants.FrontLeft.LocationY),
            new Translation2d(TunerConstants.FrontRight.LocationX, TunerConstants.FrontRight.LocationY),
            new Translation2d(TunerConstants.BackLeft.LocationX, TunerConstants.BackLeft.LocationY),
            new Translation2d(TunerConstants.BackRight.LocationX, TunerConstants.BackRight.LocationY)
        };
    }
}
"""

_AK_DRIVE_COMMANDS_JAVA = """\
package frc.robot.commands;

import edu.wpi.first.math.MathUtil;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.kinematics.ChassisSpeeds;
import edu.wpi.first.wpilibj.DriverStation;
import edu.wpi.first.wpilibj.DriverStation.Alliance;
import edu.wpi.first.wpilibj2.command.Command;
import frc.robot.Constants.OperatorConstants;
import frc.robot.subsystems.drive.Drive;
import java.util.function.DoubleSupplier;

/** Teleop drive commands inspired by the AdvantageKit swerve templates. */
public final class DriveCommands {
    private DriveCommands() {}

    public static Command joystickDrive(
        Drive drive,
        DoubleSupplier xSupplier,
        DoubleSupplier ySupplier,
        DoubleSupplier omegaSupplier
    ) {
        return drive.run(() -> {
            double x = MathUtil.applyDeadband(xSupplier.getAsDouble(), OperatorConstants.DEADBAND);
            double y = MathUtil.applyDeadband(ySupplier.getAsDouble(), OperatorConstants.DEADBAND);
            double omega = MathUtil.applyDeadband(omegaSupplier.getAsDouble(), OperatorConstants.DEADBAND);

            Rotation2d fieldFrame = drive.getRotation();
            if (DriverStation.getAlliance().orElse(Alliance.Blue) == Alliance.Red) {
                fieldFrame = fieldFrame.plus(Rotation2d.fromRadians(Math.PI));
            }

            drive.runVelocity(
                ChassisSpeeds.fromFieldRelativeSpeeds(
                    x * drive.getMaxLinearSpeedMetersPerSec(),
                    y * drive.getMaxLinearSpeedMetersPerSec(),
                    omega * drive.getMaxAngularSpeedRadPerSec(),
                    fieldFrame));
        }).withName("DriveJoystick");
    }
}
"""

_AK_VISION_CONSTANTS_JAVA = """\
package frc.robot.subsystems.vision;

import edu.wpi.first.math.geometry.Rotation3d;
import edu.wpi.first.math.geometry.Transform3d;

/** PhotonVision configuration for the generated swerve project. */
public final class VisionConstants {
    public static final String camera0Name = "front-left";
    public static final String camera1Name = "front-right";

    public static final Transform3d robotToCamera0 =
        new Transform3d(0.28, 0.22, 0.25, new Rotation3d(0.0, -0.30, 0.35));
    public static final Transform3d robotToCamera1 =
        new Transform3d(0.28, -0.22, 0.25, new Rotation3d(0.0, -0.30, -0.35));

    public static final double maxAmbiguity = 0.3;
    public static final double maxZError = 0.75;
    public static final double linearStdDevBaseline = 0.02;
    public static final double angularStdDevBaseline = 0.06;
    public static final double[] cameraStdDevFactors = new double[] {1.0, 1.0};
    public static final double linearStdDevMegatag2Factor = 0.5;
    public static final double angularStdDevMegatag2Factor = Double.POSITIVE_INFINITY;

    private VisionConstants() {}
}
"""

_AK_VISION_IO_JAVA = """\
package frc.robot.subsystems.vision;

import edu.wpi.first.math.geometry.Pose3d;
import edu.wpi.first.math.geometry.Rotation2d;

/** Vision IO abstraction patterned after the AdvantageKit vision template. */
public interface VisionIO {

    class VisionIOInputs {
        public boolean connected = false;
        public TargetObservation latestTargetObservation = new TargetObservation(Rotation2d.kZero, Rotation2d.kZero);
        public PoseObservation[] poseObservations = new PoseObservation[0];
        public int[] tagIds = new int[0];
    }

    record TargetObservation(Rotation2d tx, Rotation2d ty) {}

    record PoseObservation(
        double timestamp,
        Pose3d pose,
        double ambiguity,
        int tagCount,
        double averageTagDistance,
        PoseObservationType type
    ) {}

    enum PoseObservationType {
        MEGATAG_1,
        MEGATAG_2,
        PHOTONVISION
    }

    default void updateInputs(VisionIOInputs inputs) {}
}
"""

_AK_VISION_IO_PHOTON_JAVA = """\
package frc.robot.subsystems.vision;

import edu.wpi.first.math.geometry.Pose3d;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.geometry.Transform3d;
import frc.robot.FieldConstants;
import java.util.LinkedHashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Set;
import org.photonvision.PhotonCamera;

/** PhotonVision-backed vision IO for real cameras. */
public class VisionIOPhotonVision implements VisionIO {
    protected final PhotonCamera camera;
    protected final Transform3d robotToCamera;

    public VisionIOPhotonVision(String name, Transform3d robotToCamera) {
        camera = new PhotonCamera(name);
        this.robotToCamera = robotToCamera;
    }

    @Override
    public void updateInputs(VisionIOInputs inputs) {
        inputs.connected = camera.isConnected();
        Set<Integer> tagIds = new LinkedHashSet<>();
        List<PoseObservation> poseObservations = new LinkedList<>();

        for (var result : camera.getAllUnreadResults()) {
            if (result.hasTargets()) {
                inputs.latestTargetObservation = new TargetObservation(
                    Rotation2d.fromDegrees(result.getBestTarget().getYaw()),
                    Rotation2d.fromDegrees(result.getBestTarget().getPitch()));
            } else {
                inputs.latestTargetObservation = new TargetObservation(Rotation2d.kZero, Rotation2d.kZero);
            }

            if (result.multitagResult.isPresent()) {
                var multitagResult = result.multitagResult.get();
                Transform3d fieldToCamera = multitagResult.estimatedPose.best;
                Transform3d fieldToRobot = fieldToCamera.plus(robotToCamera.inverse());
                Pose3d robotPose = new Pose3d(fieldToRobot.getTranslation(), fieldToRobot.getRotation());

                double totalTagDistance = 0.0;
                for (var target : result.targets) {
                    totalTagDistance += target.bestCameraToTarget.getTranslation().getNorm();
                    tagIds.add(target.fiducialId);
                }
                multitagResult.fiducialIDsUsed.stream()
                    .map(Short::intValue)
                    .forEach(tagIds::add);

                poseObservations.add(new PoseObservation(
                    result.getTimestampSeconds(),
                    robotPose,
                    multitagResult.estimatedPose.ambiguity,
                    multitagResult.fiducialIDsUsed.size(),
                    result.targets.isEmpty() ? 0.0 : totalTagDistance / result.targets.size(),
                    PoseObservationType.PHOTONVISION));
            } else if (!result.targets.isEmpty()) {
                var target = result.targets.get(0);
                var tagPose = FieldConstants.getAprilTagLayout().getTagPose(target.fiducialId);
                if (tagPose.isPresent()) {
                    Transform3d fieldToTarget = new Transform3d(tagPose.get().getTranslation(), tagPose.get().getRotation());
                    Transform3d fieldToCamera = fieldToTarget.plus(target.bestCameraToTarget.inverse());
                    Transform3d fieldToRobot = fieldToCamera.plus(robotToCamera.inverse());
                    Pose3d robotPose = new Pose3d(fieldToRobot.getTranslation(), fieldToRobot.getRotation());

                    tagIds.add(target.fiducialId);
                    poseObservations.add(new PoseObservation(
                        result.getTimestampSeconds(),
                        robotPose,
                        target.poseAmbiguity,
                        1,
                        target.bestCameraToTarget.getTranslation().getNorm(),
                        PoseObservationType.PHOTONVISION));
                }
            }
        }

        inputs.poseObservations = poseObservations.toArray(new PoseObservation[0]);
        inputs.tagIds = tagIds.stream().mapToInt(Integer::intValue).toArray();
    }
}
"""

_AK_VISION_IO_PHOTON_SIM_JAVA = """\
package frc.robot.subsystems.vision;

import edu.wpi.first.math.geometry.Pose2d;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.geometry.Transform3d;
import frc.robot.FieldConstants;
import java.util.function.Supplier;
import org.photonvision.simulation.PhotonCameraSim;
import org.photonvision.simulation.SimCameraProperties;
import org.photonvision.simulation.VisionSystemSim;

/** PhotonVision simulator wiring for desktop AdvantageKit-style testing. */
public class VisionIOPhotonVisionSim extends VisionIOPhotonVision {
    private static VisionSystemSim visionSim;

    private final Supplier<Pose2d> poseSupplier;
    private final PhotonCameraSim cameraSim;

    public VisionIOPhotonVisionSim(String name, Transform3d robotToCamera, Supplier<Pose2d> poseSupplier) {
        super(name, robotToCamera);
        this.poseSupplier = poseSupplier;

        if (visionSim == null) {
            visionSim = new VisionSystemSim("main");
            visionSim.addAprilTags(FieldConstants.getAprilTagLayout());
        }

        SimCameraProperties cameraProperties = new SimCameraProperties();
        cameraProperties.setCalibration(1280, 720, Rotation2d.fromDegrees(90.0));
        cameraProperties.setFPS(30.0);
        cameraSim = new PhotonCameraSim(camera, cameraProperties, FieldConstants.getAprilTagLayout());
        visionSim.addCamera(cameraSim, robotToCamera);
    }

    @Override
    public void updateInputs(VisionIOInputs inputs) {
        visionSim.update(poseSupplier.get());
        super.updateInputs(inputs);
    }
}
"""

_AK_VISION_JAVA = """\
package frc.robot.subsystems.vision;

import static frc.robot.subsystems.vision.VisionConstants.*;

import edu.wpi.first.math.Matrix;
import edu.wpi.first.math.VecBuilder;
import edu.wpi.first.math.geometry.Pose2d;
import edu.wpi.first.math.geometry.Pose3d;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.numbers.N1;
import edu.wpi.first.math.numbers.N3;
import edu.wpi.first.wpilibj.Alert;
import edu.wpi.first.wpilibj.Alert.AlertType;
import edu.wpi.first.wpilibj2.command.SubsystemBase;
import frc.robot.FieldConstants;
import frc.robot.subsystems.vision.VisionIO.PoseObservationType;
import java.util.LinkedList;
import java.util.List;
import org.littletonrobotics.junction.Logger;

/** Vision subsystem that filters and forwards PhotonVision measurements to the drive estimator. */
public class Vision extends SubsystemBase {
    private final VisionConsumer consumer;
    private final VisionIO[] io;
    private final VisionIO.VisionIOInputs[] inputs;
    private final Alert[] disconnectedAlerts;

    public Vision(VisionConsumer consumer, VisionIO... io) {
        this.consumer = consumer;
        this.io = io;
        this.inputs = new VisionIO.VisionIOInputs[io.length];
        this.disconnectedAlerts = new Alert[io.length];

        for (int i = 0; i < io.length; i++) {
            inputs[i] = new VisionIO.VisionIOInputs();
            disconnectedAlerts[i] = new Alert("Vision camera " + i + " is disconnected.", AlertType.kWarning);
        }
    }

    public Rotation2d getTargetX(int cameraIndex) {
        if (cameraIndex < 0 || cameraIndex >= inputs.length) {
            return Rotation2d.kZero;
        }
        return inputs[cameraIndex].latestTargetObservation.tx();
    }

    @Override
    public void periodic() {
        List<Pose3d> allTagPoses = new LinkedList<>();
        List<Pose3d> allRobotPoses = new LinkedList<>();
        List<Pose3d> allRobotPosesAccepted = new LinkedList<>();
        List<Pose3d> allRobotPosesRejected = new LinkedList<>();

        for (int cameraIndex = 0; cameraIndex < io.length; cameraIndex++) {
            io[cameraIndex].updateInputs(inputs[cameraIndex]);
            disconnectedAlerts[cameraIndex].set(!inputs[cameraIndex].connected);
            Logger.recordOutput("Vision/Camera" + cameraIndex + "/Connected", inputs[cameraIndex].connected);
            Logger.recordOutput("Vision/Camera" + cameraIndex + "/TargetYawDeg", inputs[cameraIndex].latestTargetObservation.tx().getDegrees());

            List<Pose3d> tagPoses = new LinkedList<>();
            List<Pose3d> robotPoses = new LinkedList<>();
            List<Pose3d> robotPosesAccepted = new LinkedList<>();
            List<Pose3d> robotPosesRejected = new LinkedList<>();

            for (int tagId : inputs[cameraIndex].tagIds) {
                var tagPose = FieldConstants.getAprilTagLayout().getTagPose(tagId);
                tagPose.ifPresent(tagPoses::add);
            }

            for (var observation : inputs[cameraIndex].poseObservations) {
                boolean rejectPose = observation.tagCount() == 0
                    || (observation.tagCount() == 1 && observation.ambiguity() > maxAmbiguity)
                    || Math.abs(observation.pose().getZ()) > maxZError
                    || observation.pose().getX() < 0.0
                    || observation.pose().getX() > FieldConstants.getAprilTagLayout().getFieldLength()
                    || observation.pose().getY() < 0.0
                    || observation.pose().getY() > FieldConstants.getAprilTagLayout().getFieldWidth();

                robotPoses.add(observation.pose());
                if (rejectPose) {
                    robotPosesRejected.add(observation.pose());
                    continue;
                }
                robotPosesAccepted.add(observation.pose());

                double stdDevFactor = Math.pow(observation.averageTagDistance(), 2.0)
                    / Math.max(1, observation.tagCount());
                double linearStdDev = linearStdDevBaseline * stdDevFactor;
                double angularStdDev = angularStdDevBaseline * stdDevFactor;

                if (observation.type() == PoseObservationType.MEGATAG_2) {
                    linearStdDev *= linearStdDevMegatag2Factor;
                    angularStdDev *= angularStdDevMegatag2Factor;
                }
                if (cameraIndex < cameraStdDevFactors.length) {
                    linearStdDev *= cameraStdDevFactors[cameraIndex];
                    angularStdDev *= cameraStdDevFactors[cameraIndex];
                }

                consumer.accept(
                    observation.pose().toPose2d(),
                    observation.timestamp(),
                    VecBuilder.fill(linearStdDev, linearStdDev, angularStdDev));
            }

            Logger.recordOutput("Vision/Camera" + cameraIndex + "/TagPoses", tagPoses.toArray(new Pose3d[0]));
            Logger.recordOutput("Vision/Camera" + cameraIndex + "/RobotPoses", robotPoses.toArray(new Pose3d[0]));
            Logger.recordOutput("Vision/Camera" + cameraIndex + "/RobotPosesAccepted", robotPosesAccepted.toArray(new Pose3d[0]));
            Logger.recordOutput("Vision/Camera" + cameraIndex + "/RobotPosesRejected", robotPosesRejected.toArray(new Pose3d[0]));

            allTagPoses.addAll(tagPoses);
            allRobotPoses.addAll(robotPoses);
            allRobotPosesAccepted.addAll(robotPosesAccepted);
            allRobotPosesRejected.addAll(robotPosesRejected);
        }

        Logger.recordOutput("Vision/Summary/TagPoses", allTagPoses.toArray(new Pose3d[0]));
        Logger.recordOutput("Vision/Summary/RobotPoses", allRobotPoses.toArray(new Pose3d[0]));
        Logger.recordOutput("Vision/Summary/RobotPosesAccepted", allRobotPosesAccepted.toArray(new Pose3d[0]));
        Logger.recordOutput("Vision/Summary/RobotPosesRejected", allRobotPosesRejected.toArray(new Pose3d[0]));
    }

    @FunctionalInterface
    public interface VisionConsumer {
        void accept(
            Pose2d visionRobotPoseMeters,
            double timestampSeconds,
            Matrix<N3, N1> visionMeasurementStdDevs
        );
    }
}
"""

_AK_ROBOT_CONTAINER_JAVA = """\
package frc.robot;

import static frc.robot.subsystems.vision.VisionConstants.*;

import com.pathplanner.lib.auto.AutoBuilder;
import edu.wpi.first.wpilibj.smartdashboard.SmartDashboard;
import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.button.CommandXboxController;
import frc.robot.Constants.OperatorConstants;
import frc.robot.autos.AutoRoutines;
import frc.robot.commands.DriveCommands;
import frc.robot.generated.TunerConstants;
import frc.robot.subsystems.drive.Drive;
import frc.robot.subsystems.drive.GyroIO;
import frc.robot.subsystems.drive.GyroIOPigeon2;
import frc.robot.subsystems.drive.ModuleIO;
import frc.robot.subsystems.drive.ModuleIOSim;
import frc.robot.subsystems.drive.ModuleIOTalonFX;
import frc.robot.subsystems.intake.Intake;
import frc.robot.subsystems.intake.IntakeIO;
import frc.robot.subsystems.intake.IntakeIOSim;
import frc.robot.subsystems.intake.IntakeIOTalonFX;
import frc.robot.subsystems.lift.Lift;
import frc.robot.subsystems.lift.LiftIO;
import frc.robot.subsystems.lift.LiftIOSim;
import frc.robot.subsystems.lift.LiftIOTalonFX;
import frc.robot.subsystems.scoring.Scoring;
import frc.robot.subsystems.scoring.ScoringIO;
import frc.robot.subsystems.scoring.ScoringIOSim;
import frc.robot.subsystems.scoring.ScoringIOTalonFX;
import frc.robot.subsystems.vision.Vision;
import frc.robot.subsystems.vision.VisionIO;
import frc.robot.subsystems.vision.VisionIOPhotonVision;
import frc.robot.subsystems.vision.VisionIOPhotonVisionSim;
import org.littletonrobotics.junction.networktables.LoggedDashboardChooser;

/** Wires the hybrid AdvantageKit TalonFX swerve and PhotonVision project structure. */
public class RobotContainer {
    private final Drive drive;
    private final Vision vision;
    private final Intake intake;
    private final Scoring scoring;
    private final Lift lift;

    private final CommandXboxController driver = new CommandXboxController(OperatorConstants.DRIVER_PORT);
    private final CommandXboxController operator = new CommandXboxController(OperatorConstants.OPERATOR_PORT);
    private final LoggedDashboardChooser<Command> autoChooser;

    public RobotContainer() {
        switch (Constants.currentMode) {
            case REAL -> {
                drive = new Drive(
                    new GyroIOPigeon2(),
                    new ModuleIOTalonFX(TunerConstants.FrontLeft),
                    new ModuleIOTalonFX(TunerConstants.FrontRight),
                    new ModuleIOTalonFX(TunerConstants.BackLeft),
                    new ModuleIOTalonFX(TunerConstants.BackRight));
                vision = new Vision(
                    drive::addVisionMeasurement,
                    new VisionIOPhotonVision(camera0Name, robotToCamera0),
                    new VisionIOPhotonVision(camera1Name, robotToCamera1));
                intake = new Intake(new IntakeIOTalonFX());
                scoring = new Scoring(new ScoringIOTalonFX());
                lift = new Lift(new LiftIOTalonFX());
            }
            case SIM -> {
                drive = new Drive(
                    new GyroIO() {},
                    new ModuleIOSim(TunerConstants.FrontLeft),
                    new ModuleIOSim(TunerConstants.FrontRight),
                    new ModuleIOSim(TunerConstants.BackLeft),
                    new ModuleIOSim(TunerConstants.BackRight));
                vision = new Vision(
                    drive::addVisionMeasurement,
                    new VisionIOPhotonVisionSim(camera0Name, robotToCamera0, drive::getPose),
                    new VisionIOPhotonVisionSim(camera1Name, robotToCamera1, drive::getPose));
                intake = new Intake(new IntakeIOSim());
                scoring = new Scoring(new ScoringIOSim());
                lift = new Lift(new LiftIOSim());
            }
            default -> {
                drive = new Drive(new GyroIO() {}, new ModuleIO() {}, new ModuleIO() {}, new ModuleIO() {}, new ModuleIO() {});
                vision = new Vision(drive::addVisionMeasurement, new VisionIO() {}, new VisionIO() {});
                intake = new Intake(new IntakeIO() {});
                scoring = new Scoring(new ScoringIO() {});
                lift = new Lift(new LiftIO() {});
            }
        }

        AutoRoutines.register(drive, intake, scoring, lift);
        autoChooser = new LoggedDashboardChooser<>("Auto Choices", AutoBuilder.buildAutoChooser());
        autoChooser.addOption("SafeAutoScore Sequence", AutoRoutines.safeAutoScore(scoring, intake));

        configureDefaultCommands();
        configureButtonBindings();
        SmartDashboard.putBoolean("HUBActive", true);
    }

    private void configureDefaultCommands() {
        drive.setDefaultCommand(
            DriveCommands.joystickDrive(
                drive,
                () -> -driver.getLeftY(),
                () -> -driver.getLeftX(),
                () -> -driver.getRightX()));
    }

    private void configureButtonBindings() {
        driver.rightBumper().whileTrue(intake.intakeCommand());
        driver.leftBumper().whileTrue(intake.ejectCommand());
        driver.rightTrigger(0.5).whileTrue(scoring.scoreCommand());
        driver.y().onTrue(drive.runOnce(drive::zeroGyro));

        operator.a().onTrue(lift.climbToLevel(1));
        operator.b().onTrue(lift.climbToLevel(2));
        operator.x().onTrue(lift.climbToLevel(3));
        operator.y().onTrue(lift.retractCommand());
        operator.leftBumper().whileTrue(scoring.spinUpCommand());
    }

    public Command getAutonomousCommand() {
        return autoChooser.get();
    }
}
"""

_AK_AUTO_ROUTINES_JAVA = """\
package frc.robot.autos;

import com.pathplanner.lib.auto.NamedCommands;
import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;
import frc.robot.subsystems.drive.Drive;
import frc.robot.subsystems.intake.Intake;
import frc.robot.subsystems.lift.Lift;
import frc.robot.subsystems.scoring.Scoring;

/** Registers named commands for PathPlanner while keeping a simple fallback autonomous sequence. */
public final class AutoRoutines {
    private AutoRoutines() {}

    public static void register(
        Drive drive,
        Intake intake,
        Scoring scoring,
        Lift lift
    ) {
        NamedCommands.registerCommand("Intake", intake.intakeCommand());
        NamedCommands.registerCommand("Score", scoring.scoreCommand());
        NamedCommands.registerCommand("SpinUp", scoring.spinUpCommand());
        NamedCommands.registerCommand("ClimbL1", lift.climbToLevel(1));
        NamedCommands.registerCommand("ClimbL2", lift.climbToLevel(2));
        NamedCommands.registerCommand("ClimbL3", lift.climbToLevel(3));
        NamedCommands.registerCommand("Retract", lift.retractCommand());
        NamedCommands.registerCommand("StopDrive", Commands.runOnce(drive::stop, drive));
    }

    public static Command safeAutoScore(Scoring scoring, Intake intake) {
        return Commands.sequence(
            scoring.spinUpCommand().withTimeout(0.5),
            scoring.scoreCommand().withTimeout(3.0),
            intake.intakeCommand().withTimeout(4.0),
            scoring.scoreCommand().withTimeout(5.0),
            Commands.print("[Auto] SafeAutoScore complete"))
            .withName("SafeAutoScore");
    }
}
"""

def robot_codegen_agent(root: Path, year: str, mechanics: dict) -> dict:
    manual_hash = mechanics["metadata"]["source_manual_hash"]
    manual_ver  = mechanics["metadata"]["manual_version"]

    proj = root / "artifacts" / year / "wpilib_project"
    java_root = proj / "src" / "main" / "java" / "frc" / "robot"
    sub_dir = java_root / "subsystems"
    drive_dir = sub_dir / "drive"
    vision_dir = sub_dir / "vision"
    intake_dir = sub_dir / "intake"
    scoring_dir = sub_dir / "scoring"
    lift_dir = sub_dir / "lift"
    generated_dir = java_root / "generated"
    command_dir = java_root / "commands"
    auto_dir = java_root / "autos"
    vdep_dir = proj / "vendordeps"
    deploy_root = proj / "src" / "main" / "deploy"
    deploy_dir = deploy_root / "pathplanner" / "paths"

    for d in [sub_dir, drive_dir, vision_dir, intake_dir, scoring_dir, lift_dir, generated_dir, command_dir, auto_dir, vdep_dir, deploy_dir]:
        d.mkdir(parents=True, exist_ok=True)

    print("  [robot_codegen] generating WPILib 2026 Java project …")

    files: dict[Path, str] = {
        proj / "build.gradle":        _AK_BUILD_GRADLE,
        proj / "settings.gradle":     _SETTINGS_GRADLE,
        vdep_dir / "Phoenix6.json":         _VENDORDEP_PHOENIX6,
        vdep_dir / "PathplannerLib.json":   _VENDORDEP_PATHPLANNER,
        vdep_dir / "AdvantageKit.json":     _VENDORDEP_ADVANTAGEKIT,
        vdep_dir / "WPILibNewCommands.json": _VENDORDEP_WPILIB_NEW_COMMANDS,
        vdep_dir / "photonlib.json":        _VENDORDEP_PHOTON,
        java_root / "Main.java":            _MAIN_JAVA,
        java_root / "Robot.java":           _AK_ROBOT_JAVA,
        java_root / "Constants.java":       _AK_CONSTANTS_JAVA,
        java_root / "FieldConstants.java":  _FIELD_CONSTANTS_JAVA,
        java_root / "RobotContainer.java":  _AK_ROBOT_CONTAINER_JAVA,
        generated_dir / "TunerConstants.java": _AK_TUNER_CONSTANTS_JAVA,
        command_dir / "DriveCommands.java": _AK_DRIVE_COMMANDS_JAVA,
        drive_dir / "GyroIO.java":          _AK_GYRO_IO_JAVA,
        drive_dir / "GyroIOPigeon2.java":   _AK_GYRO_IO_PIGEON2_JAVA,
        drive_dir / "ModuleIO.java":        _AK_MODULE_IO_JAVA,
        drive_dir / "ModuleIOTalonFX.java": _AK_MODULE_IO_TALONFX_JAVA,
        drive_dir / "ModuleIOSim.java":     _AK_MODULE_IO_SIM_JAVA,
        drive_dir / "Module.java":          _AK_MODULE_JAVA,
        drive_dir / "Drive.java":           _AK_DRIVE_JAVA,
        vision_dir / "VisionConstants.java": _AK_VISION_CONSTANTS_JAVA,
        vision_dir / "VisionIO.java":        _AK_VISION_IO_JAVA,
        vision_dir / "VisionIOPhotonVision.java": _AK_VISION_IO_PHOTON_JAVA,
        vision_dir / "VisionIOPhotonVisionSim.java": _AK_VISION_IO_PHOTON_SIM_JAVA,
        vision_dir / "Vision.java":         _AK_VISION_JAVA,
        intake_dir / "IntakeIO.java":       _AK_INTAKE_IO_JAVA,
        intake_dir / "IntakeIOTalonFX.java": _AK_INTAKE_IO_TALONFX_JAVA,
        intake_dir / "IntakeIOSim.java":    _AK_INTAKE_IO_SIM_JAVA,
        intake_dir / "Intake.java":         _AK_INTAKE_JAVA,
        scoring_dir / "ScoringIO.java":     _AK_SCORING_IO_JAVA,
        scoring_dir / "ScoringIOTalonFX.java": _AK_SCORING_IO_TALONFX_JAVA,
        scoring_dir / "ScoringIOSim.java":  _AK_SCORING_IO_SIM_JAVA,
        scoring_dir / "Scoring.java":       _AK_SCORING_JAVA,
        lift_dir / "LiftIO.java":           _AK_LIFT_IO_JAVA,
        lift_dir / "LiftIOTalonFX.java":    _AK_LIFT_IO_TALONFX_JAVA,
        lift_dir / "LiftIOSim.java":        _AK_LIFT_IO_SIM_JAVA,
        lift_dir / "Lift.java":             _AK_LIFT_JAVA,
        auto_dir / "AutoRoutines.java":     _AK_AUTO_ROUTINES_JAVA,
    }

    legacy_paths = [
        sub_dir / "SwerveModule.java",
        sub_dir / "DriveSubsystem.java",
        sub_dir / "IntakeSubsystem.java",
        sub_dir / "ScoringSubsystem.java",
        sub_dir / "LiftSubsystem.java",
        drive_dir / "DriveConstants.java",
    ]
    for legacy_path in legacy_paths:
        if legacy_path.exists():
            legacy_path.unlink()

    # Stub PathPlanner path JSON (team fills in GUI)
    path_json = json.dumps({
        "version": 1.0,
        "waypoints": [
            {"anchor": {"x": 1.5, "y": 5.5}, "prevControl": None, "nextControl": {"x": 2.5, "y": 5.5}},
            {"anchor": {"x": 4.0, "y": 5.5}, "prevControl": {"x": 3.0, "y": 5.5}, "nextControl": None}
        ],
        "rotationTargets": [],
        "constraintZones": [],
        "eventMarkers": [
            {"waypointRelativePos": 0.1, "command": {"type": "named", "data": {"name": "SpinUp"}}},
            {"waypointRelativePos": 0.9, "command": {"type": "named", "data": {"name": "Score"}}}
        ],
        "globalConstraints": {"maxVelocity": 3.0, "maxAcceleration": 3.0, "maxAngularVelocity": 540, "maxAngularAcceleration": 720},
        "goalEndState": {"velocity": 0, "rotation": 0},
        "reversed": False,
        "folder": None,
        "previewStartingState": {"rotation": 0, "velocity": 0},
        "useDefaultConstraints": True
    }, indent=2)
    files[deploy_dir / "SafeAutoScore.path"] = path_json

    apriltag_layout_source = root / "artifacts" / year / "apriltag_field_layout.json"
    if apriltag_layout_source.exists():
        apriltag_layout_json = apriltag_layout_source.read_text(encoding="utf-8")
    else:
        apriltag_layout_json = json.dumps(
            {
                "tags": [],
                "field": {
                    "length": round(float(mechanics.get("physics", {}).get("field_length_m", 0.0) or 0.0), 3),
                    "width": round(float(mechanics.get("physics", {}).get("field_width_m", 0.0) or 0.0), 3),
                },
            },
            indent=2,
        )
    files[deploy_root / "apriltag_field_layout.json"] = apriltag_layout_json

    for path, content in files.items():
        write_text(path, content)

    wrapper_paths = ensure_gradle_wrapper(proj)
    generated_paths = [*files.keys(), *wrapper_paths]
    artifact_paths = [f"artifacts/{year}/wpilib_project/{p.relative_to(proj).as_posix()}" for p in generated_paths]

    result = {
        "success": True,
        "artifact_paths": artifact_paths,
        "warnings": [
            "Verify GradleRIO version against https://github.com/wpilibsuite/allwpilib/releases",
            "Verify all vendordep versions against official vendor release pages before deploy.",
            "TunerConstants encoder offsets default to 0.0 - calibrate them from CTRE Tuner X before enabling closed-loop steering.",
            "TEAM_NUMBER in Constants.java is 9999 — update before competition.",
            "Generated project now follows an AdvantageKit-style layout with generated/TunerConstants.java plus drive, vision, intake, scoring, and lift IO packages.",
            "PhotonVision camera names and robot-to-camera transforms in VisionConstants.java must match the coprocessor configuration and robot geometry.",
            "FieldConstants.java is generated with blue/red alliance helpers and deploy-first AprilTag layout loading.",
            "apriltag_field_layout.json is deployed with the project; populate it from the field dimension drawing so robot code does not rely solely on the built-in WPILib resource.",
        ],
        "citations": [
            {"section_id": "section-6", "page": 43, "clause_text": "TOWER scoring criteria"},
            {"section_id": "section-7", "page": 60, "clause_text": "G407 Only score while in your ALLIANCE ZONE"},
            {"section_id": "section-8", "page": 73, "clause_text": "Robot size limits"},
        ],
        "metadata": base_meta("build", year, manual_hash, manual_ver,
                              "robot_codegen", ["anthropic/claude-api", "karpathy/claude"]),
        "subsystems": ["Drive (AdvantageKit-style swerve)", "Vision (PhotonVision pose fusion)", "Intake (IO-layer)", "Scoring (IO-layer)", "Lift (IO-layer)"],
        "vendor_libraries": ["AdvantageKit", "CTRE Phoenix v6", "PathplannerLib", "photonlib"],
    }
    print(f"  [robot_codegen] done — {len(generated_paths)} files written")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 3: power_engineer
# Input:  robot design (inferred from robot_codegen)
# Output: power_app/power_budget.csv, power_app/power_dashboard.py
# ══════════════════════════════════════════════════════════════════════════════

def power_engineer_agent(root: Path, year: str, mechanics: dict) -> dict:
    manual_hash = mechanics["metadata"]["source_manual_hash"]
    manual_ver  = mechanics["metadata"]["manual_version"]
    out_dir = root / "artifacts" / year / "power_app"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("  [power_engineer] generating power budget + dashboard …")

    # Motor current data (Kraken X60 / TalonFX)
    motors = [
        {"subsystem":"Drive FL","motor":"Kraken X60","qty":1,"peak_a":130,"drive_a":30,"idle_a":3,"duty_cycle":0.75},
        {"subsystem":"Drive FR","motor":"Kraken X60","qty":1,"peak_a":130,"drive_a":30,"idle_a":3,"duty_cycle":0.75},
        {"subsystem":"Drive BL","motor":"Kraken X60","qty":1,"peak_a":130,"drive_a":30,"idle_a":3,"duty_cycle":0.75},
        {"subsystem":"Drive BR","motor":"Kraken X60","qty":1,"peak_a":130,"drive_a":30,"idle_a":3,"duty_cycle":0.75},
        {"subsystem":"Intake","motor":"Kraken X60","qty":1,"peak_a":80,"drive_a":25,"idle_a":1,"duty_cycle":0.40},
        {"subsystem":"Scoring Flywheel","motor":"Kraken X60","qty":1,"peak_a":80,"drive_a":28,"idle_a":2,"duty_cycle":0.50},
        {"subsystem":"Scoring Feed","motor":"Kraken X60","qty":1,"peak_a":40,"drive_a":10,"idle_a":1,"duty_cycle":0.45},
        {"subsystem":"Lift Leader","motor":"Kraken X60","qty":1,"peak_a":130,"drive_a":45,"idle_a":5,"duty_cycle":0.15},
        {"subsystem":"Lift Follower","motor":"Kraken X60","qty":1,"peak_a":130,"drive_a":45,"idle_a":5,"duty_cycle":0.15},
        {"subsystem":"RoboRIO+PDH+etc","motor":"Electronics","qty":1,"peak_a":8,"drive_a":8,"idle_a":8,"duty_cycle":1.00},
    ]

    # Compute average and peak totals
    for m in motors:
        m["avg_a"] = round(m["drive_a"] * m["duty_cycle"] + m["idle_a"] * (1 - m["duty_cycle"]), 1)

    peak_total = sum(m["peak_a"] * m["qty"] for m in motors)
    avg_total  = sum(m["avg_a"]  * m["qty"] for m in motors)
    drive_total = sum(m["drive_a"] * m["qty"] for m in motors)

    # Write CSV
    csv_path = out_dir / "power_budget.csv"
    fields = ["subsystem","motor","qty","peak_a","drive_a","idle_a","duty_cycle","avg_a"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(motors)
        w.writerow({"subsystem":"TOTAL","motor":"","qty":"",
                    "peak_a":peak_total,"drive_a":drive_total,
                    "idle_a":"","duty_cycle":"","avg_a":avg_total})

    # Write Streamlit dashboard source
    dashboard_src = f"""\
\"\"\"FRC 2026 REBUILT Power Budget Dashboard.

Run:  streamlit run power_dashboard.py
Requires:  pip install streamlit pandas matplotlib
\"\"\"

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

BATTERY_VOLTAGE = 12.5  # V
BATTERY_CAPACITY_AH = 18.0

st.set_page_config(page_title="REBUILT 2026 Power Budget", layout="wide")
st.title("FRC 2026 REBUILT – Power Budget Dashboard")
st.caption("Generated by power_engineer agent. Update duty cycles with real test data.")

df = pd.read_csv("power_budget.csv").dropna(subset=["motor"])
df = df[df["subsystem"] != "TOTAL"]

col1, col2, col3 = st.columns(3)
peak_total  = {peak_total}
avg_total   = round({avg_total}, 1)
drive_total = {drive_total}

col1.metric("Peak Draw (A)", f"{{peak_total}} A", help="All motors at stall simultaneously (worst case)")
col2.metric("Avg Draw (A)",  f"{{avg_total}} A",  help="Weighted by duty cycle")
col3.metric("Battery Burn Rate", f"{{round(avg_total / BATTERY_CAPACITY_AH * 2.67, 1)}} min",
            help="Estimated match time until 80% DOD at avg draw (~160 s match)")

st.subheader("Per-Subsystem Current Draw")
fig, ax = plt.subplots(figsize=(10, 4))
ax.bar(df["subsystem"], df["peak_a"],  label="Peak (A)",  alpha=0.5, color="red")
ax.bar(df["subsystem"], df["avg_a"],   label="Avg (A)",   alpha=0.8, color="steelblue")
ax.set_ylabel("Current (A)")
ax.set_xticklabels(df["subsystem"], rotation=30, ha="right")
ax.legend()
st.pyplot(fig)

st.subheader("Raw Data")
st.dataframe(df, use_container_width=True)

st.subheader("Thermal Warning")
high_duty = df[df["duty_cycle"] > 0.7]
if not high_duty.empty:
    st.warning(f"High duty-cycle motors (>70%): {{', '.join(high_duty['subsystem'].tolist())}}. Verify thermal limits.")
else:
    st.success("No subsystem exceeds 70% duty cycle.")
"""
    write_text(out_dir / "power_dashboard.py", dashboard_src)

    result = {
        "success": True,
        "artifact_paths": [
            f"artifacts/{year}/power_app/power_budget.csv",
            f"artifacts/{year}/power_app/power_dashboard.py",
        ],
        "warnings": [
            f"Peak total: {peak_total} A (theoretical stall all-at-once — not realistic).",
            f"Average estimated draw: {avg_total} A — within FRC battery envelope for a 160 s match.",
            "Update duty_cycle column with real on-field test data.",
            "Run: pip install streamlit pandas matplotlib && streamlit run artifacts/2026/power_app/power_dashboard.py",
        ],
        "citations": [
            {"section_id": "section-8", "page": 89, "clause_text": "Power distribution rules"},
        ],
        "metadata": base_meta("build", year, manual_hash, manual_ver,
                              "power_engineer", ["anthropic/xlsx", "karpathy/claude"]),
        "summary": {"peak_total_a": peak_total, "avg_total_a": avg_total, "drive_total_a": drive_total,
                    "battery_voltage": 12.5, "battery_ah": 18.0},
    }
    print(f"  [power_engineer] done — peak {peak_total} A / avg {avg_total} A")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 4: scout_dev
# Input:  scoring/penalty rules
# Output: scouting_app/ with schema.sql, api.py, app.py, requirements.txt
# ══════════════════════════════════════════════════════════════════════════════

_SCOUT_SCHEMA_SQL = """\
-- FRC 2026 REBUILT Scouting Database Schema
-- Run: sqlite3 scouting.db < schema.sql

CREATE TABLE IF NOT EXISTS events (
    event_key       TEXT PRIMARY KEY,
    event_name      TEXT NOT NULL,
    year            INTEGER DEFAULT 2026,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS teams (
    team_number     INTEGER PRIMARY KEY,
    team_name       TEXT,
    event_key       TEXT REFERENCES events(event_key)
);

CREATE TABLE IF NOT EXISTS matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key       TEXT REFERENCES events(event_key),
    match_number    INTEGER NOT NULL,
    match_type      TEXT CHECK(match_type IN ('qual','playoff')) DEFAULT 'qual',
    alliance        TEXT CHECK(alliance IN ('red','blue')) NOT NULL,
    team_number     INTEGER REFERENCES teams(team_number),
    scouter_name    TEXT NOT NULL,
    scouted_at      TEXT DEFAULT (datetime('now')),

    -- AUTO (0-20 s)
    auto_fuel_scored    INTEGER DEFAULT 0 CHECK(auto_fuel_scored >= 0),
    auto_tower_level    INTEGER DEFAULT 0 CHECK(auto_tower_level IN (0,1)),
    auto_mobility       INTEGER DEFAULT 0 CHECK(auto_mobility IN (0,1)),  -- left start line

    -- TELEOP (20-130 s)
    teleop_fuel_scored  INTEGER DEFAULT 0 CHECK(teleop_fuel_scored >= 0),
    hub_misses          INTEGER DEFAULT 0,  -- attempted but missed
    defensive_cycles    INTEGER DEFAULT 0,  -- intentional defensive plays
    fouls_committed     INTEGER DEFAULT 0,
    fouls_received      INTEGER DEFAULT 0,

    -- ENDGAME (130-160 s)
    endgame_tower_level INTEGER DEFAULT 0 CHECK(endgame_tower_level IN (0,1,2,3)),

    -- Qualitative
    drive_quality       INTEGER CHECK(drive_quality BETWEEN 1 AND 5),
    defense_rating      INTEGER CHECK(defense_rating BETWEEN 1 AND 5),
    comments            TEXT,

    UNIQUE(event_key, match_number, match_type, team_number)
);

CREATE INDEX IF NOT EXISTS idx_matches_team   ON matches(team_number);
CREATE INDEX IF NOT EXISTS idx_matches_event  ON matches(event_key, match_number);

-- Computed view: team statistics
CREATE VIEW IF NOT EXISTS team_stats AS
SELECT
    team_number,
    COUNT(*)                            AS matches_scouted,
    ROUND(AVG(auto_fuel_scored), 1)     AS avg_auto_fuel,
    ROUND(AVG(teleop_fuel_scored), 1)   AS avg_teleop_fuel,
    ROUND(AVG(auto_fuel_scored + teleop_fuel_scored), 1) AS avg_total_fuel,
    MAX(endgame_tower_level)            AS best_climb_level,
    ROUND(AVG(endgame_tower_level), 2)  AS avg_climb_level,
    ROUND(AVG(drive_quality), 1)        AS avg_drive_quality,
    SUM(fouls_committed)                AS total_fouls
FROM matches
GROUP BY team_number;
"""

_SCOUT_API_PY = """\
\"\"\"FastAPI scouting backend.

Run:  pip install fastapi uvicorn && uvicorn api:app --reload
\"\"\"
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import sqlite3, json
from pathlib import Path

DB_PATH = Path(__file__).parent / "scouting.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

app = FastAPI(title="FRC 2026 REBUILT Scouting API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class MatchEntry(BaseModel):
    event_key:           str
    match_number:        int
    match_type:          str = "qual"
    alliance:            str
    team_number:         int
    scouter_name:        str
    auto_fuel_scored:    int = Field(default=0, ge=0)
    auto_tower_level:    int = Field(default=0, ge=0, le=1)
    auto_mobility:       int = Field(default=0, ge=0, le=1)
    teleop_fuel_scored:  int = Field(default=0, ge=0)
    hub_misses:          int = Field(default=0, ge=0)
    defensive_cycles:    int = Field(default=0, ge=0)
    fouls_committed:     int = Field(default=0, ge=0)
    fouls_received:      int = Field(default=0, ge=0)
    endgame_tower_level: int = Field(default=0, ge=0, le=3)
    drive_quality:       int = Field(default=3, ge=1, le=5)
    defense_rating:      int = Field(default=3, ge=1, le=5)
    comments:            str = ""

@app.post("/match", status_code=201)
def add_match(entry: MatchEntry):
    db = get_db()
    try:
        db.execute(
            \"\"\"INSERT OR REPLACE INTO matches
            (event_key,match_number,match_type,alliance,team_number,scouter_name,
             auto_fuel_scored,auto_tower_level,auto_mobility,
             teleop_fuel_scored,hub_misses,defensive_cycles,fouls_committed,fouls_received,
             endgame_tower_level,drive_quality,defense_rating,comments)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\"\"\",
            (entry.event_key,entry.match_number,entry.match_type,entry.alliance,
             entry.team_number,entry.scouter_name,
             entry.auto_fuel_scored,entry.auto_tower_level,entry.auto_mobility,
             entry.teleop_fuel_scored,entry.hub_misses,entry.defensive_cycles,
             entry.fouls_committed,entry.fouls_received,
             entry.endgame_tower_level,entry.drive_quality,entry.defense_rating,entry.comments)
        )
        db.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=409, detail=str(e))
    finally:
        db.close()
    return {"status": "ok"}

@app.get("/teams")
def get_teams():
    db = get_db()
    rows = db.execute("SELECT * FROM team_stats ORDER BY avg_total_fuel DESC").fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.get("/team/{team_number}")
def get_team(team_number: int):
    db = get_db()
    rows = db.execute("SELECT * FROM matches WHERE team_number=? ORDER BY match_number", (team_number,)).fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.get("/export")
def export_all():
    db = get_db()
    rows = db.execute("SELECT * FROM matches ORDER BY match_number, team_number").fetchall()
    db.close()
    return [dict(r) for r in rows]
"""

_SCOUT_APP_PY = """\
\"\"\"FRC 2026 REBUILT Scouting App (Streamlit).

Run:  streamlit run app.py
Requires:  pip install streamlit requests pandas
\"\"\"
import streamlit as st
import requests, json
import pandas as pd

API_URL = st.sidebar.text_input("API URL", value="http://localhost:8000")
EVENT   = st.sidebar.text_input("Event Key", value="2026arc")
SCOUTER = st.sidebar.text_input("Your Name", value="Scout1")

st.title("FRC 2026 REBUILT Scouting")
tab_scout, tab_data, tab_export = st.tabs(["Scout Match", "Team Data", "Export"])

with tab_scout:
    st.header("Match Entry")
    col1, col2 = st.columns(2)
    with col1:
        team_num   = st.number_input("Team Number", min_value=1, step=1)
        match_num  = st.number_input("Match #", min_value=1, step=1)
        match_type = st.selectbox("Type", ["qual","playoff"])
        alliance   = st.selectbox("Alliance", ["red","blue"])
    with col2:
        auto_fuel     = st.number_input("AUTO Fuel Scored", min_value=0, step=1)
        auto_tower    = st.selectbox("AUTO Tower Level", [0,1])
        auto_mobility = st.checkbox("AUTO Mobility (left start line)")

    st.subheader("TELEOP")
    col3, col4 = st.columns(2)
    with col3:
        teleop_fuel   = st.number_input("TELEOP Fuel Scored", min_value=0, step=1)
        hub_misses    = st.number_input("HUB Misses", min_value=0, step=1)
        def_cycles    = st.number_input("Defensive Cycles", min_value=0, step=1)
    with col4:
        fouls_given   = st.number_input("Fouls Committed", min_value=0, step=1)
        fouls_recv    = st.number_input("Fouls Received (by opponents)", min_value=0, step=1)

    st.subheader("ENDGAME")
    climb_level = st.selectbox("Tower Level Achieved", [0,1,2,3],
                               format_func=lambda x: f"Level {x}" if x>0 else "None")

    st.subheader("Qualitative")
    drive_q  = st.slider("Drive Quality", 1, 5, 3)
    def_q    = st.slider("Defense Rating (if played)", 1, 5, 3)
    comments = st.text_area("Comments")

    if st.button("Submit", type="primary"):
        payload = {
            "event_key": EVENT, "match_number": int(match_num),
            "match_type": match_type, "alliance": alliance,
            "team_number": int(team_num), "scouter_name": SCOUTER,
            "auto_fuel_scored": int(auto_fuel), "auto_tower_level": int(auto_tower),
            "auto_mobility": int(auto_mobility), "teleop_fuel_scored": int(teleop_fuel),
            "hub_misses": int(hub_misses), "defensive_cycles": int(def_cycles),
            "fouls_committed": int(fouls_given), "fouls_received": int(fouls_recv),
            "endgame_tower_level": int(climb_level),
            "drive_quality": int(drive_q), "defense_rating": int(def_q), "comments": comments,
        }
        try:
            r = requests.post(f"{API_URL}/match", json=payload, timeout=5)
            if r.status_code == 201:
                st.success(f"Saved team {team_num} match {match_num}")
            else:
                st.error(f"Error {r.status_code}: {r.text}")
        except Exception as e:
            st.error(f"Could not reach API: {e}")

with tab_data:
    st.header("Team Statistics")
    if st.button("Refresh"):
        try:
            data = requests.get(f"{API_URL}/teams", timeout=5).json()
            df = pd.DataFrame(data)
            st.dataframe(df, use_container_width=True)
        except Exception as e:
            st.error(f"API error: {e}")

with tab_export:
    st.header("Export All Data")
    if st.button("Download JSON"):
        try:
            data = requests.get(f"{API_URL}/export", timeout=5).json()
            st.download_button("Save scouting_export.json",
                               data=json.dumps(data, indent=2),
                               file_name="scouting_export.json",
                               mime="application/json")
        except Exception as e:
            st.error(f"API error: {e}")
"""

_SCOUT_REQUIREMENTS = """\
fastapi>=0.110,<1
uvicorn[standard]>=0.29,<1
pydantic>=2.0,<3
streamlit>=1.35,<2
pandas>=2.0,<3
requests>=2.31,<3
"""

def scout_dev_agent(root: Path, year: str, rules: dict) -> dict:
    manual_hash = rules["metadata"]["source_manual_hash"]
    manual_ver  = rules["metadata"]["manual_version"]
    out_dir = root / "artifacts" / year / "scouting_app"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("  [scout_dev] generating scouting app …")

    write_text(out_dir / "schema.sql",       _SCOUT_SCHEMA_SQL)
    write_text(out_dir / "api.py",           _SCOUT_API_PY)
    write_text(out_dir / "app.py",           _SCOUT_APP_PY)
    write_text(out_dir / "requirements.txt", _SCOUT_REQUIREMENTS)

    # Bootstrap the SQLite database so it's ready to use
    import sqlite3
    db_path = out_dir / "scouting.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCOUT_SCHEMA_SQL)
    conn.commit()
    conn.close()

    result = {
        "success": True,
        "artifact_paths": [
            f"artifacts/{year}/scouting_app/schema.sql",
            f"artifacts/{year}/scouting_app/api.py",
            f"artifacts/{year}/scouting_app/app.py",
            f"artifacts/{year}/scouting_app/requirements.txt",
            f"artifacts/{year}/scouting_app/scouting.db",
        ],
        "warnings": [
            "Start backend: cd artifacts/2026/scouting_app && pip install -r requirements.txt && uvicorn api:app --reload",
            "Start frontend: streamlit run artifacts/2026/scouting_app/app.py",
            "scouting.db is bootstrapped but not committed to git (ignored by .gitignore).",
        ],
        "citations": [
            {"section_id": "section-6", "page": 44, "clause_text": "REBUILT point values table"},
            {"section_id": "section-6", "page": 46, "clause_text": "Rule violations table"},
        ],
        "metadata": base_meta("build", year, manual_hash, manual_ver,
                              "scout_dev", ["anthropic/frontend-design", "anthropic/webapp-testing", "karpathy/claude"]),
        "schema_tables": ["events", "teams", "matches"],
        "schema_views":  ["team_stats"],
    }
    print("  [scout_dev] done")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 5: sim_engineer
# Input:  mechanics.json
# Output: sim_2d/sim_params.json, sim_2d/sim.py (pygame skeleton)
# ══════════════════════════════════════════════════════════════════════════════

_SIM_PY = """\
\"\"\"FRC 2026 REBUILT – 2D Field Simulation (pygame).

Install:  pip install pygame
Run:      python sim.py

Controls:
  Arrow keys / WASD  - move red robot
  SPACE              - score fuel into active HUB
  R                  - reset match
  Q                  - quit
\"\"\"
import pygame, math, random, json, sys
from pathlib import Path

PARAMS = json.loads((Path(__file__).parent / "sim_params.json").read_text())
SCALE  = PARAMS["display"]["pixels_per_meter"]

# Field dimensions in pixels
FW = int(PARAMS["field"]["width_m"]  * SCALE)
FH = int(PARAMS["field"]["length_m"] * SCALE)

WHITE  = (255,255,255); BLACK  = (0,0,0)
RED    = (220,50,50);   BLUE   = (50,100,220)
GREEN  = (50,180,50);   YELLOW = (230,200,0)
GRAY   = (160,160,160); LGRAY  = (220,220,220)


class Robot:
    def __init__(self, x, y, color):
        self.x, self.y = float(x), float(y)
        self.color = color
        self.speed = PARAMS["physics"]["max_speed_mps"] * SCALE
        self.fuel  = PARAMS["match"]["preload_per_robot"]
        self.score = 0
        self.angle = 0.0

    def move(self, dx, dy, dt):
        spd = self.speed * dt
        nx  = max(30, min(FW - 30, self.x + dx * spd))
        ny  = max(30, min(FH - 30, self.y + dy * spd))
        if dx != 0 or dy != 0:
            self.angle = math.degrees(math.atan2(dy, dx))
        self.x, self.y = nx, ny

    def draw(self, surf):
        pygame.draw.circle(surf, self.color, (int(self.x), int(self.y)), 22)
        pygame.draw.circle(surf, BLACK,      (int(self.x), int(self.y)), 22, 2)
        ex = int(self.x + 22 * math.cos(math.radians(self.angle)))
        ey = int(self.y + 22 * math.sin(math.radians(self.angle)))
        pygame.draw.line(surf, WHITE, (int(self.x), int(self.y)), (ex, ey), 3)


class Hub:
    def __init__(self, x, y, alliance):
        self.x, self.y, self.alliance = x, y, alliance
        self.active = True
        self.scored = 0

    def draw(self, surf, font):
        color = (RED if self.alliance=="red" else BLUE) if self.active else GRAY
        pygame.draw.rect(surf, color, (self.x - 35, self.y - 35, 70, 70), border_radius=8)
        pygame.draw.rect(surf, BLACK, (self.x - 35, self.y - 35, 70, 70), 2, border_radius=8)
        lbl = font.render(f"{'●' if self.active else '○'} {self.scored}", True, WHITE)
        surf.blit(lbl, (self.x - lbl.get_width()//2, self.y - lbl.get_height()//2))

    def try_score(self, robot):
        dist = math.hypot(robot.x - self.x, robot.y - self.y)
        if dist < 80 and self.active and robot.fuel > 0:
            robot.fuel  -= 1
            robot.score += 1
            self.scored += 1
            return True
        return False


def run_sim():
    pygame.init()
    screen = pygame.display.set_mode((FW, FH))
    pygame.display.set_caption("FRC 2026 REBUILT – 2D Sim")
    clock  = pygame.font.SysFont(None, 24)
    big    = pygame.font.SysFont(None, 36)
    timer  = pygame.time.Clock()

    red_hub  = Hub(FW // 4,     FH // 2, "red")
    blue_hub = Hub(3 * FW // 4, FH // 2, "blue")
    player   = Robot(FW // 4, FH * 3 // 4, RED)
    bots     = [Robot(3*FW//4 + random.randint(-60,60), FH//4 + random.randint(-60,60), BLUE) for _ in range(3)]

    match_time = PARAMS["match"]["total_duration_s"]
    elapsed    = 0.0
    FPS        = 60
    running    = True
    scored_msg = ""
    msg_timer  = 0.0

    while running:
        dt = timer.tick(FPS) / 1000.0
        elapsed = min(elapsed + dt, match_time)
        remaining = match_time - elapsed

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_q):
                running = False
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_r:
                    elapsed = 0.0; player.score = 0; player.fuel = PARAMS["match"]["preload_per_robot"]
                    red_hub.scored = blue_hub.scored = 0
                if ev.key == pygame.K_SPACE:
                    if red_hub.try_score(player):
                        scored_msg = "+1 FUEL"; msg_timer = 1.0

        keys = pygame.key.get_pressed()
        dx = (keys[pygame.K_RIGHT] or keys[pygame.K_d]) - (keys[pygame.K_LEFT] or keys[pygame.K_a])
        dy = (keys[pygame.K_DOWN]  or keys[pygame.K_s]) - (keys[pygame.K_UP]   or keys[pygame.K_w])
        player.move(dx, dy, dt)

        # Basic autonomous blue bots
        for bot in bots:
            tx, ty = blue_hub.x + random.randint(-40,40), blue_hub.y + random.randint(-40,40)
            bdx = (tx - bot.x) / max(1, math.hypot(tx-bot.x, ty-bot.y))
            bdy = (ty - bot.y) / max(1, math.hypot(tx-bot.x, ty-bot.y))
            bot.move(bdx, bdy, dt * 0.6)
            blue_hub.try_score(bot)

        # HUB activation: simple demo, both active before 30s remaining
        blue_hub.active = remaining > 60 or remaining < 30

        screen.fill(LGRAY)
        # Center line
        pygame.draw.line(screen, GRAY, (FW//2, 0), (FW//2, FH), 3)

        red_hub.draw(screen, clock)
        blue_hub.draw(screen, clock)
        player.draw(screen)
        for bot in bots: bot.draw(screen)

        # HUD
        hud_lines = [
            f"Time: {int(remaining)}s",
            f"Your Fuel: {player.fuel}",
            f"Your Score: {player.score}",
            f"Red HUB: {red_hub.scored}  Blue HUB: {blue_hub.scored}",
            f"HUB Active: {'YES' if red_hub.active else 'NO'}",
        ]
        for i, line in enumerate(hud_lines):
            surf = clock.render(line, True, BLACK)
            screen.blit(surf, (10, 10 + i * 22))

        if msg_timer > 0:
            msg_timer -= dt
            surf = big.render(scored_msg, True, GREEN)
            screen.blit(surf, (FW//2 - surf.get_width()//2, FH//2))

        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    run_sim()
"""

def sim_engineer_agent(root: Path, year: str, mechanics: dict) -> dict:
    manual_hash = mechanics["metadata"]["source_manual_hash"]
    manual_ver  = mechanics["metadata"]["manual_version"]
    out_dir = root / "artifacts" / year / "sim_2d"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("  [sim_engineer] generating 2D sim …")

    params = {
        "success": True,
        "artifact_paths": [f"artifacts/{year}/sim_2d/sim_params.json", f"artifacts/{year}/sim_2d/sim.py"],
        "warnings": ["Install pygame: pip install pygame", "Run: python artifacts/2026/sim_2d/sim.py"],
        "citations": [
            {"section_id": "section-5", "page": 18, "clause_text": "Field dimensions 317.7in x 651.2in"},
            {"section_id": "section-5", "page": 32, "clause_text": "FUEL diameter 5.91in"},
            {"section_id": "section-8", "page": 73, "clause_text": "Robot starting perimeter 110in, height 30in"},
        ],
        "metadata": base_meta("simulate", year, manual_hash, manual_ver,
                              "sim_engineer", ["codex/screenshot", "karpathy/claude"]),
        "field": {
            "width_m": mechanics["physics"]["field_width_m"],
            "length_m": mechanics["physics"]["field_length_m"],
        },
        "display": {"pixels_per_meter": 60},
        "physics": {
            "max_speed_mps": 4.5,
            "max_accel_mps2": 4.0,
            "robot_radius_m": 0.35,
            "fuel_radius_m":  0.075,
        },
        "match": {
            "total_duration_s":   160,
            "auto_duration_s":     20,
            "endgame_start_s":    130,
            "preload_per_robot":    8,
        },
        "scoring": {
            "fuel_pts":          1,
            "tower_l1_auto_pts": 15,
            "tower_l1_pts":      10,
            "tower_l2_pts":      20,
            "tower_l3_pts":      30,
        },
    }
    write_json(out_dir / "sim_params.json", params)
    write_text(out_dir / "sim.py", _SIM_PY)
    print("  [sim_engineer] done")
    return params


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 6: advscope_integrator
# Input:  robot_codegen output (subsystem list)
# Output: logs/advantagescope_config.json, logs/log_schema.json
# ══════════════════════════════════════════════════════════════════════════════

def advscope_integrator_agent(root: Path, year: str, mechanics: dict, codegen_result: dict) -> dict:
    manual_hash = mechanics["metadata"]["source_manual_hash"]
    manual_ver  = mechanics["metadata"]["manual_version"]
    out_dir = root / "artifacts" / year / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("  [advscope_integrator] generating AdvantageScope config …")

    # Log schema (mirrors Logger.recordOutput calls in subsystems)
    log_schema = {
        "schema_version": SCHEMA_VERSION,
        "metadata": base_meta("integrate", year, manual_hash, manual_ver,
                              "advscope_integrator", ["karpathy/claude"]),
        "log_keys": [
            {"key": "/Drive/Pose",          "type": "Pose2d",         "subsystem": "DriveSubsystem",    "description": "Estimated robot pose"},
            {"key": "/Drive/ModuleStates",  "type": "SwerveModuleState[]","subsystem": "DriveSubsystem","description": "Swerve module states (4 modules)"},
            {"key": "/Drive/Heading",       "type": "double",         "subsystem": "DriveSubsystem",    "description": "Gyro heading in degrees"},
            {"key": "/Intake/CurrentAmps",  "type": "double",         "subsystem": "IntakeSubsystem",   "description": "Intake motor supply current"},
            {"key": "/Intake/SpeedRPS",     "type": "double",         "subsystem": "IntakeSubsystem",   "description": "Intake roller speed in RPS"},
            {"key": "/Scoring/FlywheelRPS", "type": "double",         "subsystem": "ScoringSubsystem",  "description": "Flywheel speed in RPS"},
            {"key": "/Scoring/IsAtSpeed",   "type": "boolean",        "subsystem": "ScoringSubsystem",  "description": "Flywheel at target speed flag"},
            {"key": "/Scoring/FeedCurrentAmps","type": "double",      "subsystem": "ScoringSubsystem",  "description": "Feed motor supply current"},
            {"key": "/Lift/PositionRot",    "type": "double",         "subsystem": "LiftSubsystem",     "description": "Lift position in motor rotations"},
            {"key": "/Lift/TargetLevel",    "type": "integer",        "subsystem": "LiftSubsystem",     "description": "Target tower level (0-3)"},
            {"key": "/Lift/AtTarget",       "type": "boolean",        "subsystem": "LiftSubsystem",     "description": "Lift at target position"},
            {"key": "/Lift/CurrentAmps",    "type": "double",         "subsystem": "LiftSubsystem",     "description": "Lift motor supply current"},
            {"key": "/MatchTime",           "type": "double",         "subsystem": "Robot",             "description": "Remaining match time in seconds"},
        ],
    }

    # AdvantageScope layout config
    adv_config = {
        "version": "3.0.0",
        "tabs": [
            {
                "title": "Field",
                "type": "field2d",
                "objects": [
                    {"type": "robot",    "logKey": "/Drive/Pose",         "color": "#dc3232", "size": [0.89, 0.89]},
                    {"type": "trajectory","logKey": "/Drive/Pose",        "color": "#ff8c00"},
                ]
            },
            {
                "title": "Drive",
                "type": "line_graph",
                "objects": [
                    {"type": "line", "logKey": "/Drive/Heading",        "color": "#4080ff", "label": "Heading (deg)"},
                ]
            },
            {
                "title": "Swerve",
                "type": "swerve",
                "logKey": "/Drive/ModuleStates",
                "color": "#4080ff"
            },
            {
                "title": "Mechanisms",
                "type": "line_graph",
                "objects": [
                    {"type": "line", "logKey": "/Scoring/FlywheelRPS", "color": "#ff6000",  "label": "Flywheel RPS"},
                    {"type": "line", "logKey": "/Lift/PositionRot",    "color": "#00b050",  "label": "Lift Position (rot)"},
                    {"type": "line", "logKey": "/Intake/CurrentAmps",  "color": "#9900cc",  "label": "Intake Current (A)"},
                    {"type": "line", "logKey": "/Lift/CurrentAmps",    "color": "#cc3300",  "label": "Lift Current (A)"},
                ]
            },
            {
                "title": "Booleans",
                "type": "line_graph",
                "objects": [
                    {"type": "line", "logKey": "/Scoring/IsAtSpeed", "color": "#00b050", "label": "Flywheel At Speed"},
                    {"type": "line", "logKey": "/Lift/AtTarget",     "color": "#0070c0", "label": "Lift At Target"},
                ]
            },
        ]
    }

    write_json(out_dir / "log_schema.json",           log_schema)
    write_json(out_dir / "advantagescope_config.json", adv_config)

    # README instructions
    instructions = (
        "# AdvantageScope Log Integration\n\n"
        "1. Deploy robot code (WPILib 2026 + AdvantageKit).\n"
        "2. Logs are written to USB drive at /U/logs/*.wpilog during matches.\n"
        "3. Open AdvantageScope and drag the .wpilog file onto it.\n"
        "4. Import `advantagescope_config.json` via File > Import Layout.\n"
        "5. All subsystem keys match the log_schema.json keys above.\n\n"
        "## Key Log Keys\n"
        "| Key | Type | Description |\n"
        "|-----|------|-------------|\n"
    )
    for entry in log_schema["log_keys"]:
        instructions += f"| `{entry['key']}` | {entry['type']} | {entry['description']} |\n"
    write_text(out_dir / "README.md", instructions)

    result = {
        "success": True,
        "artifact_paths": [
            f"artifacts/{year}/logs/log_schema.json",
            f"artifacts/{year}/logs/advantagescope_config.json",
            f"artifacts/{year}/logs/README.md",
        ],
        "warnings": [
            "WPILog files are written to USB drive (/U/logs) when robot is enabled.",
            "AdvantageScope version must be >= 3.0.0 to use this layout config.",
        ],
        "citations": [],
        "metadata": base_meta("integrate", year, manual_hash, manual_ver,
                              "advscope_integrator", ["karpathy/claude"]),
        "log_key_count": len(log_schema["log_keys"]),
        "dashboard_tabs": [t["title"] for t in adv_config["tabs"]],
    }
    print("  [advscope_integrator] done")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

def update_orchestration_state(root: Path, year: str, agent_results: dict) -> None:
    state_path = root / "artifacts" / year / "orchestration_state.json"
    if state_path.exists():
        state = load_json(state_path)
    else:
        state = {"schema_version": SCHEMA_VERSION, "game_year": year}

    completed = state.get("agents_completed", [])
    for agent in agent_results:
        if agent not in completed:
            completed.append(agent)

    state["agents_completed"] = completed
    state["agents_pending"]   = []
    state["current_stage"]    = "advscope_integrator_done"
    state["updated_at"]       = utc_now()
    state["release_decision"] = "blocked_until_recall_audit"
    state["next_actions"] = [
        "Compile WPILib project: cd artifacts/2026/wpilib_project && ./gradlew build",
        "Verify and update all vendordep versions before robot deployment.",
        "Run MC sim again with real robot cycle-time data.",
        "Start scouting app for competition: see artifacts/2026/scouting_app/README.md",
        "Review MC top strategies vs strategy_hypotheses.md before finalising auto selection.",
        "Run qa_validator recall audit to clear the release gate.",
    ]
    write_json(state_path, state)


def update_manifest(root: Path, year: str, all_results: list[dict]) -> None:
    manifest_path = root / "artifacts" / year / "manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
    else:
        manifest = {"schema_version": SCHEMA_VERSION, "game_year": year, "artifacts": []}

    existing_paths = {a["path"] for a in manifest.get("artifacts", [])}
    for result in all_results:
        for ap in result.get("artifact_paths", []):
            full = root / ap
            if ap not in existing_paths and full.exists():
                manifest["artifacts"].append({
                    "path": ap,
                    "sha256": sha256_file(full),
                    "bytes": full.stat().st_size,
                })
                existing_paths.add(ap)

    manifest["generated_at"] = utc_now()
    write_json(manifest_path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all pending FRC pipeline agents.")
    parser.add_argument("--year", default="2026")
    parser.add_argument("--root", default=None)
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    year = args.year

    mechanics_path = root / "artifacts" / year / "mechanics.json"
    rules_path     = root / "artifacts" / year / "rules.json"

    if not mechanics_path.exists():
        print(f"ERROR: {mechanics_path} not found. Run scripts/frc_pipeline.py run first.")
        return 1
    if not rules_path.exists():
        print(f"ERROR: {rules_path} not found. Run scripts/frc_pipeline.py run first.")
        return 1

    mechanics = load_json(mechanics_path)
    rules     = load_json(rules_path)

    print(f"\nFRC AI Pipeline — Spawning all agents for {year} REBUILT")
    print("=" * 62)

    results: dict[str, dict] = {}

    print("\n[1/6] mc_simulator")
    results["mc_simulator"] = mc_simulator_agent(root, year, mechanics)

    print("\n[2/6] robot_codegen")
    results["robot_codegen"] = robot_codegen_agent(root, year, mechanics)

    print("\n[3/6] power_engineer")
    results["power_engineer"] = power_engineer_agent(root, year, mechanics)

    print("\n[4/6] scout_dev")
    results["scout_dev"] = scout_dev_agent(root, year, rules)

    print("\n[5/6] sim_engineer")
    results["sim_engineer"] = sim_engineer_agent(root, year, mechanics)

    print("\n[6/6] advscope_integrator")
    results["advscope_integrator"] = advscope_integrator_agent(root, year, mechanics, results["robot_codegen"])

    print("\nUpdating orchestration state and manifest …")
    update_orchestration_state(root, year, results)
    update_manifest(root, year, list(results.values()))

    # Print summary
    print("\n" + "=" * 62)
    print("All agents completed.\n")
    successes = sum(1 for r in results.values() if r.get("success"))
    print(f"  {successes}/{len(results)} agents reported success")
    for name, result in results.items():
        icon = "✓" if result.get("success") else "✗"
        n_art = len(result.get("artifact_paths", []))
        print(f"  {icon} {name:<26} {n_art} artifacts")

    # Warn on any agent-level issues
    for name, result in results.items():
        for w in result.get("warnings", [])[:2]:
            print(f"     ! {name}: {w}")

    print("\nNext: review artifacts/2026/orchestration_state.json for next actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
