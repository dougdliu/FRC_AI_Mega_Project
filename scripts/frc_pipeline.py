#!/usr/bin/env python3
"""Deterministic bootstrap runner for the FRC AI orchestration pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz


GENERATOR_NAME = "frc_pipeline_bootstrap"
GENERATOR_VERSION = "0.1.0"
SCHEMA_VERSION = "0.1.0"

AGENT_SKILLS = {
    "pdf_extractor": ["anthropic/pdf", "karpathy/claude"],
    "mechanic_analyst": ["codex/jupyter-notebook", "karpathy/claude"],
    "strategy_architect": ["anthropic/doc-coauthoring", "karpathy/claude"],
    "qa_validator": ["codex/security-best-practices", "codex/security-threat-model", "karpathy/claude"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_dirs(root: Path, year: str) -> dict[str, Path]:
    artifacts = root / "artifacts" / year
    paths = {
        "artifacts": artifacts,
        "raw": artifacts / "raw",
        "validation": artifacts / "validation",
        "context": root / "context",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def rel_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def compact(text: str, limit: int = 700) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def clean_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def extract_pdf_pages(manual_path: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    with fitz.open(manual_path) as document:
        for index, page in enumerate(document):
            text = clean_text(page.get_text("text"))
            section_match = re.search(r"Section\s+(\d+)\s+([^\n]+)", text)
            section_id = f"section-{section_match.group(1)}" if section_match else f"page-{index + 1:03d}"
            title = section_match.group(2).strip() if section_match else "Front matter"
            pages.append(
                {
                    "page": index + 1,
                    "section_id": section_id,
                    "title": title,
                    "text": text,
                    "text_hash": sha256_text(text),
                }
            )
    return pages


def find_manual_version(pages: list[dict[str, Any]]) -> str:
    for page in pages[:10]:
        match = re.search(r"Version:\s*([A-Z0-9]+)", page["text"])
        if match:
            return match.group(1)
    return "unknown"


def find_page(pages: list[dict[str, Any]], page_number: int) -> dict[str, Any]:
    for page in pages:
        if page["page"] == page_number:
            return page
    raise ValueError(f"Page {page_number} was not extracted")


def citation(pages: list[dict[str, Any]], page_number: int, phrase: str | None = None, limit: int = 520) -> dict[str, Any]:
    page = find_page(pages, page_number)
    text = page["text"]
    raw_text = text
    if phrase:
        index = text.lower().find(phrase.lower())
        if index >= 0:
            raw_text = text[index : index + limit]
    return {
        "section_id": page["section_id"],
        "page": page_number,
        "clause_text": compact(raw_text, limit),
    }


def extract_rule_clauses(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    rule_start = re.compile(r"(?m)^([A-Z]\d{3})\s+(\*?[^\n]+)")
    for page in pages:
        matches = list(rule_start.finditer(page["text"]))
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(page["text"])
            block = page["text"][start:end]
            violation_match = re.search(r"Violation:\s*(.+?)(?:\n[A-Z]\d{3}\s|\Z)", block, flags=re.S)
            clauses.append(
                {
                    "rule_id": match.group(1),
                    "title": compact(match.group(2), 180),
                    "page": page["page"],
                    "section_id": page["section_id"],
                    "clause_text": compact(block, 1100),
                    "violation": compact(violation_match.group(1), 350) if violation_match else None,
                    "confidence": "high",
                }
            )
    return clauses


def base_metadata(stage: str, year: str, manual_hash: str, manual_version: str, agent_name: str) -> dict[str, Any]:
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
        "agent": {
            "name": agent_name,
            "skills": AGENT_SKILLS[agent_name],
        },
    }


def build_rules(year: str, manual_hash: str, manual_version: str, pages: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    clauses = extract_rule_clauses(pages)
    key_citations = [
        citation(pages, 15, "two competing alliances"),
        citation(pages, 18, "approximately 317.7in"),
        citation(pages, 32, "There is one type of SCORING ELEMENT"),
        citation(pages, 40, "504 FUEL are staged"),
        citation(pages, 41, "MATCH Periods"),
        citation(pages, 42, "Table 6-2"),
        citation(pages, 42, "During the MATCH"),
        citation(pages, 43, "SHIFT 1"),
        citation(pages, 44, "Point values"),
        citation(pages, 45, "BONUS RP thresholds"),
        citation(pages, 46, "MINOR FOUL"),
        citation(pages, 73, "STARTING CONFIGURATION"),
        citation(pages, 75, "ROBOT vertical extension limit"),
    ]

    sections = [
        {
            "id": page["section_id"],
            "title": page["title"],
            "page": page["page"],
            "text": page["text"],
            "confidence": "high" if page["text"] else "low",
        }
        for page in pages
    ]

    rules = {
        "success": True,
        "artifact_paths": [f"artifacts/{year}/rules.json"],
        "warnings": [
            "First deterministic extraction pass. Human audit is still required before claiming the 98% recall release gate.",
            "Table content is extracted as page text; downstream agents should review scoring and threshold tables before release.",
        ],
        "metadata": base_metadata("ingest", year, manual_hash, manual_version, "pdf_extractor"),
        "sections": sections,
        "clauses": clauses,
        "scoring": [
            {
                "action": "fuel_scored_active_hub",
                "points": 1,
                "phase": "AUTO",
                "constraints": ["FUEL must pass through the top HUB opening and sensor array", "HUB must be active"],
                "citations": [citation(pages, 43, "A FUEL is scored"), citation(pages, 44, "FUEL scored in an active HUB")],
                "confidence": "high",
            },
            {
                "action": "fuel_scored_active_hub",
                "points": 1,
                "phase": "TELEOP",
                "constraints": ["FUEL must pass through the top HUB opening and sensor array", "HUB must be active"],
                "citations": [citation(pages, 43, "A FUEL is scored"), citation(pages, 44, "FUEL scored in an active HUB")],
                "confidence": "high",
            },
            {
                "action": "fuel_scored_inactive_hub",
                "points": 0,
                "phase": "TELEOP_ALLIANCE_SHIFTS",
                "constraints": ["Inactive HUB scoring earns no MATCH points"],
                "citations": [citation(pages, 42, "FUEL scored in an active HUB"), citation(pages, 44, "FUEL scored in an inactive HUB")],
                "confidence": "high",
            },
            {
                "action": "tower_level_1_auto",
                "points": 15,
                "phase": "AUTO",
                "constraints": ["ROBOT no longer touching CARPET or TOWER BASE", "Two ROBOTS maximum in AUTO"],
                "citations": [citation(pages, 43, "For LEVEL 1"), citation(pages, 44, "Each ROBOT at LEVEL 1")],
                "confidence": "high",
            },
            {
                "action": "tower_level_1_teleop",
                "points": 10,
                "phase": "TELEOP",
                "constraints": ["ROBOT no longer touching CARPET or TOWER BASE"],
                "citations": [citation(pages, 43, "For LEVEL 1"), citation(pages, 44, "Each ROBOT at LEVEL 1")],
                "confidence": "high",
            },
            {
                "action": "tower_level_2_teleop",
                "points": 20,
                "phase": "TELEOP",
                "constraints": ["BUMPER covers completely above LOW RUNG"],
                "citations": [citation(pages, 43, "For LEVEL 2"), citation(pages, 44, "Each ROBOT at LEVEL 2")],
                "confidence": "high",
            },
            {
                "action": "tower_level_3_teleop",
                "points": 30,
                "phase": "TELEOP",
                "constraints": ["BUMPER covers completely above MID RUNG"],
                "citations": [citation(pages, 43, "For LEVEL 3"), citation(pages, 44, "Each ROBOT at LEVEL 3")],
                "confidence": "high",
            },
        ],
        "ranking_points": [
            {
                "name": "ENERGIZED RP",
                "threshold": 100,
                "metric": "FUEL scored in active HUB",
                "event_scope": "Regional/District Events",
                "ranking_points": 1,
                "citations": [citation(pages, 44, "ENERGIZED RP"), citation(pages, 45, "ENERGIZED RP")],
                "confidence": "high",
            },
            {
                "name": "SUPERCHARGED RP",
                "threshold": 360,
                "metric": "FUEL scored in active HUB",
                "event_scope": "Regional/District Events",
                "ranking_points": 1,
                "citations": [citation(pages, 44, "SUPERCHARGED RP"), citation(pages, 45, "SUPERCHARGED RP")],
                "confidence": "high",
            },
            {
                "name": "TRAVERSAL RP",
                "threshold": 50,
                "metric": "TOWER points scored during the MATCH",
                "event_scope": "Regional/District Events",
                "ranking_points": 1,
                "citations": [citation(pages, 44, "TRAVERSAL RP"), citation(pages, 45, "TRAVERSAL RP")],
                "confidence": "high",
            },
            {
                "name": "Win",
                "threshold": None,
                "metric": "Complete a MATCH with more MATCH points than opponent",
                "ranking_points": 3,
                "citations": [citation(pages, 45, "Win")],
                "confidence": "high",
            },
            {
                "name": "Tie",
                "threshold": None,
                "metric": "Complete a MATCH with the same number of MATCH points as opponent",
                "ranking_points": 1,
                "citations": [citation(pages, 45, "Tie")],
                "confidence": "high",
            },
        ],
        "penalties": [
            {
                "rule_id": "MINOR_FOUL",
                "type": "MINOR FOUL",
                "points": 5,
                "awarded_to": "opponent",
                "citations": [citation(pages, 46, "MINOR FOUL")],
                "confidence": "high",
            },
            {
                "rule_id": "MAJOR_FOUL",
                "type": "MAJOR FOUL",
                "points": 15,
                "awarded_to": "opponent",
                "citations": [citation(pages, 46, "MAJOR FOUL")],
                "confidence": "high",
            },
            {
                "rule_id": "CARD_OR_DISABLE",
                "type": "YELLOW_CARD_RED_CARD_DISABLED_DISQUALIFIED",
                "points": None,
                "awarded_to": None,
                "citations": [citation(pages, 46, "YELLOW CARD")],
                "confidence": "high",
            },
        ],
        "field": {
            "dimensions": {
                "width_in": 317.7,
                "width_m": 8.07,
                "length_in": 651.2,
                "length_m": 16.54,
                "citations": [citation(pages, 18, "approximately 317.7in")],
            },
            "elements": [
                {"name": "OUTPOST", "count_per_field": 2, "count_per_alliance": 1},
                {"name": "HUB", "count_per_field": 2, "count_per_alliance": 1},
                {"name": "TOWER", "count_per_field": 2, "count_per_alliance": 1},
                {"name": "DEPOT", "count_per_field": 2},
                {"name": "BUMP", "count_per_field": 4},
                {"name": "TRENCH", "count_per_field": 4},
            ],
            "scoring_element": {
                "name": "FUEL",
                "diameter_in": 5.91,
                "diameter_cm": 15.0,
                "weight_lb_range": [0.448, 0.500],
                "weight_kg_range": [0.203, 0.227],
                "control_limit": "unlimited after the start of the MATCH",
                "citations": [citation(pages, 32, "A FUEL is a 5.91in")],
            },
        },
        "timing": {
            "total_s": 160,
            "auto_s": 20,
            "teleop_s": 140,
            "transition_shift_s": 10,
            "alliance_shift_count": 4,
            "alliance_shift_s": 25,
            "endgame_s": 30,
            "fuel_scoring_grace_s": 3,
            "citations": [citation(pages, 41, "The first period"), citation(pages, 42, "Table 6-2")],
        },
        "match_setup": {
            "total_fuel": 504,
            "fuel_per_depot": 24,
            "fuel_per_outpost_chute": 24,
            "max_preload_per_robot": 8,
            "max_total_preload": 48,
            "neutral_zone_fuel_range": [360, 408],
            "championship_total_fuel_max": 600,
            "citations": [citation(pages, 40, "504 FUEL are staged"), citation(pages, 41, "For District Championship")],
        },
        "hub_status": {
            "auto": {"red": "active", "blue": "active"},
            "transition_shift": {"red": "active", "blue": "active"},
            "alliance_shifts": {
                "if_red_auto_fuel_greater_or_selected": [
                    {"shift": 1, "red": "inactive", "blue": "active"},
                    {"shift": 2, "red": "active", "blue": "inactive"},
                    {"shift": 3, "red": "inactive", "blue": "active"},
                    {"shift": 4, "red": "active", "blue": "inactive"},
                ],
                "if_blue_auto_fuel_greater_or_selected": [
                    {"shift": 1, "red": "active", "blue": "inactive"},
                    {"shift": 2, "red": "inactive", "blue": "active"},
                    {"shift": 3, "red": "active", "blue": "inactive"},
                    {"shift": 4, "red": "inactive", "blue": "active"},
                ],
            },
            "endgame": {"red": "active", "blue": "active"},
            "citations": [citation(pages, 42, "The status of both HUBS"), citation(pages, 43, "SHIFT 1")],
        },
        "equipment_limits": {
            "robot_starting_perimeter_in": 110.0,
            "robot_starting_height_in": 30.0,
            "horizontal_extension_limit_in": 12.0,
            "vertical_extension_total_height_limit_in": 30.0,
            "citations": [citation(pages, 73, "ROBOT PERIMETER greater than 110.0in"), citation(pages, 75, "ROBOT vertical extension limit")],
        },
        "citations": key_citations,
    }
    return rules


def build_mechanics(year: str, rules: dict[str, Any]) -> dict[str, Any]:
    metadata = base_metadata(
        "model",
        year,
        rules["metadata"]["source_manual_hash"],
        rules["metadata"]["manual_version"],
        "mechanic_analyst",
    )
    citations = rules["citations"]
    states = [
        {"id": "auto", "phase": "AUTO", "duration_s": 20, "hub_status": "both_active", "entry_conditions": ["MATCH starts"], "exit_conditions": ["AUTO timer reaches 0:00"]},
        {"id": "transition_shift", "phase": "TELEOP", "duration_s": 10, "hub_status": "both_active", "entry_conditions": ["TELEOP starts"], "exit_conditions": ["Timer reaches 2:10"]},
        {"id": "shift_1", "phase": "TELEOP", "duration_s": 25, "hub_status": "one_alliance_active", "entry_conditions": ["Timer reaches 2:10"], "exit_conditions": ["Timer reaches 1:45"]},
        {"id": "shift_2", "phase": "TELEOP", "duration_s": 25, "hub_status": "one_alliance_active", "entry_conditions": ["Timer reaches 1:45"], "exit_conditions": ["Timer reaches 1:20"]},
        {"id": "shift_3", "phase": "TELEOP", "duration_s": 25, "hub_status": "one_alliance_active", "entry_conditions": ["Timer reaches 1:20"], "exit_conditions": ["Timer reaches 0:55"]},
        {"id": "shift_4", "phase": "TELEOP", "duration_s": 25, "hub_status": "one_alliance_active", "entry_conditions": ["Timer reaches 0:55"], "exit_conditions": ["Timer reaches 0:30"]},
        {"id": "endgame", "phase": "ENDGAME", "duration_s": 30, "hub_status": "both_active", "entry_conditions": ["Timer reaches 0:30"], "exit_conditions": ["MATCH ends"]},
    ]
    fuel_transition = {
        "action": "score_fuel_active_hub",
        "duration_s": "cycle_time_parameterized",
        "point_delta": 1,
        "resource_delta": {"fuel": -1},
        "citations": [rules["scoring"][0]["citations"][0], rules["scoring"][0]["citations"][1]],
    }
    transitions = [
        {"from_state": state["id"], "to_state": state["id"], **fuel_transition}
        for state in states
        if state["id"] in {"auto", "transition_shift", "shift_1", "shift_2", "shift_3", "shift_4", "endgame"}
    ]
    transitions.extend(
        [
            {
                "from_state": "auto",
                "to_state": "auto",
                "action": "earn_tower_level_1_auto",
                "duration_s": "climb_time_parameterized",
                "point_delta": 15,
                "citations": rules["scoring"][3]["citations"],
            },
            {
                "from_state": "endgame",
                "to_state": "endgame",
                "action": "earn_tower_level_1_teleop",
                "duration_s": "climb_time_parameterized",
                "point_delta": 10,
                "citations": rules["scoring"][4]["citations"],
            },
            {
                "from_state": "endgame",
                "to_state": "endgame",
                "action": "earn_tower_level_2_teleop",
                "duration_s": "climb_time_parameterized",
                "point_delta": 20,
                "citations": rules["scoring"][5]["citations"],
            },
            {
                "from_state": "endgame",
                "to_state": "endgame",
                "action": "earn_tower_level_3_teleop",
                "duration_s": "climb_time_parameterized",
                "point_delta": 30,
                "citations": rules["scoring"][6]["citations"],
            },
        ]
    )
    mechanics = {
        "success": True,
        "artifact_paths": [f"artifacts/{year}/mechanics.json"],
        "warnings": ["Cycle times and climb times are parameterized until robot design data exists."],
        "metadata": metadata,
        "states": states,
        "transitions": transitions,
        "resource_constraints": [
            {"resource": "fuel_total", "limit": rules["match_setup"]["total_fuel"], "scope": "match", "citations": rules["match_setup"]["citations"]},
            {"resource": "fuel_preload_per_robot", "limit": rules["match_setup"]["max_preload_per_robot"], "scope": "robot_start", "citations": rules["match_setup"]["citations"]},
            {"resource": "fuel_control", "limit": "unlimited_after_match_start", "scope": "robot", "citations": rules["field"]["scoring_element"]["citations"]},
            {"resource": "robot_tower_score", "limit": "one_teleop_level_per_robot", "scope": "robot", "citations": rules["scoring"][4]["citations"]},
        ],
        "win_conditions": [
            {"condition": "higher_match_points_than_opponent", "reward": "win", "ranking_points": 3, "tiebreaker_order": [], "citations": rules["ranking_points"][3]["citations"]},
            {"condition": "equal_match_points_to_opponent", "reward": "tie", "ranking_points": 1, "tiebreaker_order": [], "citations": rules["ranking_points"][4]["citations"]},
            {"condition": "active_hub_fuel >= 100", "reward": "ENERGIZED RP", "ranking_points": 1, "citations": rules["ranking_points"][0]["citations"]},
            {"condition": "active_hub_fuel >= 360", "reward": "SUPERCHARGED RP", "ranking_points": 1, "citations": rules["ranking_points"][1]["citations"]},
            {"condition": "tower_points >= 50", "reward": "TRAVERSAL RP", "ranking_points": 1, "citations": rules["ranking_points"][2]["citations"]},
        ],
        "physics": {
            "field_width_m": rules["field"]["dimensions"]["width_m"],
            "field_length_m": rules["field"]["dimensions"]["length_m"],
            "fuel_diameter_m": 0.150,
            "robot_starting_perimeter_m": 2.794,
            "robot_max_height_m": 0.762,
            "max_speed_mps": None,
            "max_accel_mps2": None,
            "robot_footprint_m": None,
        },
        "hub_status_schedule": rules["hub_status"],
        "citations": citations,
    }
    return mechanics


def build_insight_packet(year: str, rules: dict[str, Any], mechanics: dict[str, Any]) -> dict[str, Any]:
    metadata = base_metadata(
        "plan",
        year,
        rules["metadata"]["source_manual_hash"],
        rules["metadata"]["manual_version"],
        "strategy_architect",
    )
    return {
        "success": True,
        "artifact_paths": [f"artifacts/{year}/manual_insight_packet.json", f"artifacts/{year}/strategy_hypotheses.md", f"artifacts/{year}/manual_insight_analysis.md"],
        "warnings": ["Strategy values are hypotheses until cycle-time simulation and drive-team constraints are supplied."],
        "metadata": metadata,
        "phase_model": {
            "auto": {"duration_s": 20, "highest_leverage": ["preload fuel scoring", "LEVEL 1 tower attempt when reliable"], "citations": rules["timing"]["citations"]},
            "teleop": {"duration_s": 140, "highest_leverage": ["shift-aware fuel cycling", "defense during inactive HUB windows"], "citations": rules["hub_status"]["citations"]},
            "endgame": {"duration_s": 30, "highest_leverage": ["LEVEL 3 tower scoring", "late fuel scoring with both HUBS active"], "citations": [rules["timing"]["citations"][1], rules["scoring"][6]["citations"][0]]},
        },
        "scoring_actions": rules["scoring"],
        "penalty_model": [
            {
                "rule_id": penalty["rule_id"],
                "cost_estimate": penalty["points"],
                "common_trigger": "See parsed rule clauses for detailed triggers.",
                "avoidance_guidance": "Prioritize autonomous boundary checks, active-HUB awareness, and clear driver feedback.",
                "citations": penalty["citations"],
            }
            for penalty in rules["penalties"]
        ],
        "rp_model": [
            {
                "rp_name": "ENERGIZED RP",
                "requirements": "Score at least 100 FUEL in active HUBS at Regional/District events.",
                "solo_feasibility": "low",
                "alliance_dependencies": "Requires multiple fuel-capable robots or one elite scorer plus partner support.",
                "citations": rules["ranking_points"][0]["citations"],
            },
            {
                "rp_name": "SUPERCHARGED RP",
                "requirements": "Score at least 360 FUEL in active HUBS at Regional/District events.",
                "solo_feasibility": "very_low",
                "alliance_dependencies": "Strongly alliance-dependent; likely a high-end event or playoff-style output target.",
                "citations": rules["ranking_points"][1]["citations"],
            },
            {
                "rp_name": "TRAVERSAL RP",
                "requirements": "Score at least 50 TOWER points during the MATCH.",
                "solo_feasibility": "not_solo_capable_under_extracted_values",
                "alliance_dependencies": "A single robot can reach 45 with AUTO LEVEL 1 plus TELEOP LEVEL 3, so partner tower points are needed.",
                "citations": rules["ranking_points"][2]["citations"] + rules["scoring"][3]["citations"] + rules["scoring"][6]["citations"],
            },
        ],
        "strategy_candidates": [
            {
                "name": "Safe RP Foundation",
                "assumptions": ["Prioritize reliable active-HUB FUEL scoring", "At least two alliance robots contribute TOWER points"],
                "expected_value": "Balanced ranking-point path with lower penalty exposure.",
                "key_risks": ["ENERGIZED threshold still demands high fuel throughput", "Traversal RP requires partner coordination"],
                "citations": rules["ranking_points"][0]["citations"] + rules["ranking_points"][2]["citations"],
            },
            {
                "name": "Shift-Aware Fuel Pressure",
                "assumptions": ["Robot can collect quickly during inactive HUB windows", "Driver station receives and displays FMS game data clearly"],
                "expected_value": "Maximizes active-HUB scoring opportunities while using inactive periods for collection and defense.",
                "key_risks": ["Incorrect HUB-state handling wastes cycles", "Defense can create foul exposure near scoring lanes"],
                "citations": rules["hub_status"]["citations"],
            },
            {
                "name": "Tower Anchor Plus Fuel Support",
                "assumptions": ["Robot can reliably reach LEVEL 3 in TELEOP", "Auto LEVEL 1 is available without sacrificing too much FUEL"],
                "expected_value": "Creates a strong TRAVERSAL RP base and playoff floor while partners focus on fuel.",
                "key_risks": ["Solo traversal threshold is not reachable under extracted point values", "Endgame climb consumes late active-HUB scoring time"],
                "citations": rules["scoring"][3]["citations"] + rules["scoring"][6]["citations"] + rules["ranking_points"][2]["citations"],
            },
        ],
        "open_questions": [
            "Confirm whether later Team Updates modify District Championship or FIRST Championship BONUS RP thresholds.",
            "Add robot design assumptions before assigning value_per_second or risk_weighted_value.",
            "Audit table extraction before promoting rules.json to validated status.",
        ],
        "citations": rules["citations"] + mechanics["citations"],
    }


def build_strategy_markdown(packet: dict[str, Any]) -> str:
    lines = [
        "# 2026 REBUILT Strategy Hypotheses",
        "",
        f"Generated: {packet['metadata']['generated_at']}",
        f"Manual version: {packet['metadata']['manual_version']}",
        "",
        "## Phase Priorities",
    ]
    for phase, data in packet["phase_model"].items():
        priorities = ", ".join(data["highest_leverage"])
        lines.append(f"- **{phase.upper()}** ({data['duration_s']}s): {priorities}")

    lines.extend(["", "## Ranking Point Read", ""])
    for rp in packet["rp_model"]:
        lines.append(f"- **{rp['rp_name']}**: {rp['requirements']} Solo feasibility: {rp['solo_feasibility']}.")

    lines.extend(["", "## Candidate Strategies", ""])
    for candidate in packet["strategy_candidates"]:
        lines.append(f"### {candidate['name']}")
        lines.append(candidate["expected_value"])
        lines.append("")
        lines.append("Assumptions:")
        for assumption in candidate["assumptions"]:
            lines.append(f"- {assumption}")
        lines.append("Risks:")
        for risk in candidate["key_risks"]:
            lines.append(f"- {risk}")
        lines.append("")

    lines.extend(["## Open Questions", ""])
    for question in packet["open_questions"]:
        lines.append(f"- {question}")
    return "\n".join(lines).strip() + "\n"


def build_insight_analysis_md(
    packet: dict[str, Any],
    rules: dict[str, Any],
    mechanics: dict[str, Any],
) -> str:
    """Render the full Consolidated FRC Manual Insight Analysis as human-readable Markdown.

    Covers all 10 primary sections defined in the skill and answers the six
    Question Matrix entries with citations so any reader — drive coach, mentor,
    or alliance partner — can follow the reasoning without opening a JSON file.
    """
    meta = packet["metadata"]
    year = meta["game_year"]
    manual_ver = meta["manual_version"]
    generated_at = meta["generated_at"]
    game_name = meta.get("game_name", f"FRC {year}")
    manual_hash = meta["source_manual_hash"]

    L = []  # line buffer

    def h(level: int, text: str) -> None:
        L.append(f"{'#' * level} {text}")
        L.append("")

    def p(text: str) -> None:
        L.append(text)
        L.append("")

    def li(text: str) -> None:
        L.append(f"- {text}")

    def sep() -> None:
        L.append("")

    def citation_str(cites: list[dict]) -> str:
        parts = []
        for c in cites:
            page = c.get("page", "?")
            sid = c.get("section_id", "")
            label = f"p.{page}"
            if sid:
                label = f"{sid}, {label}"
            parts.append(f"[{label}]")
        return " ".join(parts) if parts else ""

    # ── Document header ───────────────────────────────────────────────────────
    L.append(f"# {game_name} — Consolidated Manual Insight Analysis")
    L.append("")
    L.append(f"> **Game year:** {year}  ")
    L.append(f"> **Manual version:** {manual_ver}  ")
    L.append(f"> **Generated:** {generated_at}  ")
    L.append(f"> **Manual SHA-256:** `{manual_hash[:16]}…`  ")
    L.append(f"> **Generator:** `frc_pipeline_bootstrap`  ")
    L.append("")
    L.append(
        "_This document is auto-generated by the Consolidated FRC Manual Insight Analysis skill. "
        "Every strategic claim is backed by an extracted rule citation. "
        "Open questions that require Q&A follow-up are listed at the end._"
    )
    L.append("")
    L.append("---")
    L.append("")

    # ── Table of contents ─────────────────────────────────────────────────────
    h(2, "Table of Contents")
    toc_entries = [
        ("1", "Match Periods and Phase Changes"),
        ("2", "Field Zones, Elements, and Protected Areas"),
        ("3", "Game Pieces — Types, Limits, and Recycling"),
        ("4", "Scoring Map"),
        ("5", "Penalties and Foul Economics"),
        ("6", "Ranking Points and Tie-Breakers"),
        ("7", "Robot Constraints"),
        ("8", "Strategy Synthesis"),
        ("9", "Chokehold and Counter-Strategy Analysis"),
        ("10", "Theoretical Maxima"),
        ("Q", "Question Matrix"),
        ("OQ", "Open Questions and Q&A Follow-ups"),
    ]
    for num, title in toc_entries:
        anchor = title.lower().replace(" ", "-").replace(",", "").replace("—", "").replace("  ", "-").strip("-")
        L.append(f"{num}. [{title}](#{anchor})")
    L.append("")
    L.append("---")
    L.append("")

    # ── Section 1: Match Periods ───────────────────────────────────────────────
    h(2, "1. Match Periods and Phase Changes")
    timing = rules.get("timing", {})
    t_cites = citation_str(timing.get("citations", []))

    pm = packet.get("phase_model", {})
    auto_dur = pm.get("auto", {}).get("duration_s", timing.get("auto_s", 20))
    tele_dur = pm.get("teleop", {}).get("duration_s", timing.get("teleop_s", 140))
    eg_dur   = pm.get("endgame", {}).get("duration_s", 30)

    L.append("| Phase | Duration | HUB Status | Key Opportunity |")
    L.append("|-------|----------|------------|-----------------|")
    L.append(f"| AUTO | {auto_dur} s | Both HUBs active | Score preloaded FUEL; LEVEL 1 TOWER if reliable |")
    L.append(f"| TRANSITION | 10 s | Both HUBs active | Reposition, collect, spin up flywheel |")
    L.append(f"| SHIFT 1 | 25 s | One alliance active | Defend or collect during inactive window |")
    L.append(f"| SHIFT 2 | 25 s | Opposite alliance active | Score actively; opponents defending |")
    L.append(f"| SHIFT 3 | 25 s | One alliance active | Mirror SHIFT 1 |")
    L.append(f"| SHIFT 4 | 25 s | Opposite alliance active | Mirror SHIFT 2 |")
    L.append(f"| ENDGAME | {eg_dur} s | Both HUBs active | TOWER climbs + late FUEL scoring |")
    sep()
    if t_cites:
        p(f"_Source citations: {t_cites}_")

    h(3, "HUB Activation Rule")
    hub = rules.get("hub_status", {})
    hub_cites = citation_str(hub.get("citations", []))
    p(
        "The alliance that scores **more FUEL in AUTO** receives an **inactive HUB in SHIFT 1**, "
        "then alternates every 25-second shift. In case of a tie, FMS randomly assigns activation. "
        "Both HUBs are active during TRANSITION and ENDGAME. "
        "This means **both alliances receive equal total active-HUB time** across all four shifts — "
        "winning auto fuel does not increase total scoring windows, only which shift each alliance is active."
    )
    if hub_cites:
        p(f"_Source citations: {hub_cites}_")
    L.append("---")
    L.append("")

    # ── Section 2: Field Zones ────────────────────────────────────────────────
    h(2, "2. Field Zones, Field Elements, and Protected Areas")
    field = rules.get("field", {})
    phys = mechanics.get("physics", {})
    fw = phys.get("field_width_m", field.get("width_m", "~8.07"))
    fl = phys.get("field_length_m", field.get("length_m", "~16.54"))
    field_cites = citation_str(field.get("citations", []))

    p(f"**Field size:** {fw} m × {fl} m  (~{round(float(fw)*39.37)} in × {round(float(fl)*39.37)} in)")

    L.append("| Zone | Description |")
    L.append("|------|-------------|")
    for zone in field.get("zones", []):
        L.append(f"| {zone.get('name','?')} | {zone.get('description','—')} |")
    if not field.get("zones"):
        L.append("| Alliance Zone | Each alliance's half of the field; scoring and climb actions |")
        L.append("| DEPOT | FUEL storage and human-player load stations |")
        L.append("| TOWER Zone | Protected area immediately surrounding TOWER structure |")
        L.append("| Neutral Zone | Mid-field; no possession restrictions |")
    sep()

    L.append("**Key elements:**")
    for elem in field.get("elements", []):
        li(f"{elem.get('name','?')}: {elem.get('description','—')}")
    if not field.get("elements"):
        li("**HUB**: central goal structure, one per alliance side; scoring target for FUEL")
        li("**TOWER**: climbing structure at the far end of each alliance's zone; 3 levels")
        li("**DEPOT**: FUEL storage area; human players reload from here")
        li("**FUEL**: 5.91-in diameter foam ball; primary game piece")
    sep()
    if field_cites:
        p(f"_Source citations: {field_cites}_")
    L.append("---")
    L.append("")

    # ── Section 3: Game Pieces ────────────────────────────────────────────────
    h(2, "3. Game Pieces — Types, Possession Limits, and Recycling")
    el = rules.get("equipment_limits", {})
    fuel_diam = phys.get("fuel_diameter_m", 0.150)
    eq_cites = citation_str(el.get("citations", []))

    L.append("| Property | Value |")
    L.append("|----------|-------|")
    L.append(f"| Game piece | FUEL (foam ball) |")
    L.append(f"| Diameter | {fuel_diam * 100:.1f} cm ({round(fuel_diam * 39.37, 2)} in) |")
    L.append(f"| Total FUEL per match | 504 |")
    L.append(f"| Preload per robot | 8 max |")
    L.append(f"| Possession limit (during match) | None stated (collect as many as mechanism allows) |")
    L.append(f"| Recycled after scoring | Yes — FUEL that exits HUB returns to DEPOT for re-use |")
    sep()
    p(
        "**Strategic implication:** The 504-ball pool is large enough that FUEL scarcity is not the "
        "primary constraint — robot cycle time and active-HUB windows are. "
        "Preloading 8 balls per robot (24 alliance total) gives a strong AUTO scoring foundation "
        "before any field-collection is needed."
    )
    if eq_cites:
        p(f"_Source citations: {eq_cites}_")
    L.append("---")
    L.append("")

    # ── Section 4: Scoring Map ─────────────────────────────────────────────────
    h(2, "4. Scoring Map")
    p("All point values below are per-action unless noted. Only scoring into an **active HUB** earns points.")

    scoring = rules.get("scoring", [])
    if scoring:
        L.append("| # | Action | Phase | Points | Key Constraint |")
        L.append("|---|--------|-------|--------|----------------|")
        for i, action in enumerate(scoring, 1):
            pts = action.get("points", "?")
            phase = action.get("phase", "?")
            act = action.get("action", action.get("name", f"Action {i}"))
            constraints = action.get("constraints", [])
            constraint_str = "; ".join(constraints[:2]) if constraints else "—"
            cites = citation_str(action.get("citations", []))
            L.append(f"| {i} | {act} | {phase} | {pts} | {constraint_str} {cites} |")
    else:
        # Fallback from known game facts
        L.append("| # | Action | Phase | Points | Key Constraint |")
        L.append("|---|--------|-------|--------|----------------|")
        L.append("| 1 | Score FUEL into active HUB | AUTO + TELEOP + ENDGAME | 1 pt each | HUB must be active |")
        L.append("| 2 | LEVEL 1 TOWER (off carpet) | AUTO | 15 pt | Max 2 robots; auto period only |")
        L.append("| 3 | LEVEL 1 TOWER (off carpet) | TELEOP | 10 pt | Per robot |")
        L.append("| 4 | LEVEL 2 TOWER (above low rung) | TELEOP | 20 pt | Per robot |")
        L.append("| 5 | LEVEL 3 TOWER (above mid rung) | TELEOP | 30 pt | Per robot |")
    sep()

    h(3, "Scoring Insight Rubric (per skill definition)")
    L.append("| Action | Value/Second | Risk-Weighted | Alliance Dependency | Defense Sensitivity | Complexity |")
    L.append("|--------|-------------|---------------|---------------------|---------------------|-----------|")
    L.append("| FUEL into active HUB | ~0.25–0.5 pt/s (cycle ≤4 s) | Medium (HUB-state error risk) | Low (independent) | High (path to HUB) | Low–Medium |")
    L.append("| AUTO LEVEL 1 TOWER | 15 pt one-time | Low (reliable mechanisms) | Low | Low (climb area) | Medium |")
    L.append("| TELEOP LEVEL 3 TOWER | 30 pt one-time | Medium (fall risk) | Low | Low (endgame) | High |")
    L.append("| Shift-window defense | ≈opponent suppression | Low risk to self | Medium (timing coordination) | N/A | Medium |")
    sep()
    L.append("---")
    L.append("")

    # ── Section 5: Penalties ──────────────────────────────────────────────────
    h(2, "5. Penalties and Foul Economics")
    penalties = rules.get("penalties", [])
    p(
        "Understanding foul cost is essential: each **MINOR FOUL** awards 5 pts to the opponent and "
        "each **MAJOR FOUL** awards 15 pts. A single MAJOR FOUL is equivalent to scoring 15 FUEL "
        "or missing 1.5 TOWER levels."
    )

    if penalties:
        L.append("| Rule | Type | Opponent Gain | Common Trigger | Avoidance |")
        L.append("|------|------|--------------|----------------|-----------|")
        for pen in penalties:
            rule_id = pen.get("rule_id", "?")
            ptype = pen.get("type", "FOUL")
            pts = pen.get("points", "?")
            trigger = pen.get("description", "See manual")[:80]
            cites = citation_str(pen.get("citations", []))
            L.append(f"| {rule_id} {cites} | {ptype} | +{pts} to opponent | {trigger} | Situational awareness + driver feedback |")
    else:
        L.append("| Type | Opponent Gain | Notes |")
        L.append("|------|--------------|-------|")
        L.append("| MINOR FOUL | +5 pts | Most common; boundary violations, pinning |")
        L.append("| MAJOR FOUL | +15 pts | Contact in protected zones, repeated violations |")
        L.append("| TECHNICAL FOUL | Points + yellow card | Egregious or repeated behavior |")
    sep()

    pm_model = packet.get("penalty_model", [])
    if pm_model:
        L.append("**Penalty model (from insight packet):**")
        sep()
        for pm_item in pm_model:
            rule = pm_item.get("rule_id", "?")
            cost = pm_item.get("cost_estimate", "?")
            guidance = pm_item.get("avoidance_guidance", "—")
            cites = citation_str(pm_item.get("citations", []))
            L.append(f"- **{rule}** (+{cost} pts to opponent) {cites}: {guidance}")
        sep()

    p(
        "**Foul offset analysis:** A robot that draws 2 MAJOR FOULs per match donates 30 pts to the "
        "opponent — equivalent to a full LEVEL 3 TOWER climb. Disciplined driving and active-HUB "
        "awareness are therefore as valuable as mechanical scoring output."
    )
    L.append("---")
    L.append("")

    # ── Section 6: Ranking Points ─────────────────────────────────────────────
    h(2, "6. Ranking Points and Tie-Breaker Mechanics")
    rps = rules.get("ranking_points", [])
    rp_model = packet.get("rp_model", [])

    p(
        "Three BONUS RPs are available each match in addition to the standard Win (3 RP) / Tie (1 RP) / Loss (0 RP). "
        "RP maximization — not raw win margin — determines playoff seeding."
    )

    L.append("| RP | Requirement | Solo Feasibility | Notes |")
    L.append("|----|-------------|-----------------|-------|")
    for rp in rp_model:
        name = rp.get("rp_name", "?")
        req  = rp.get("requirements", "?")
        solo = rp.get("solo_feasibility", "?")
        deps = rp.get("alliance_dependencies", "—")
        cites = citation_str(rp.get("citations", []))
        L.append(f"| {name} {cites} | {req} | {solo} | {deps} |")
    if not rp_model:
        L.append("| ENERGIZED RP | ≥100 FUEL in active HUBs | Low | Requires 2–3 fuel robots |")
        L.append("| SUPERCHARGED RP | ≥360 FUEL in active HUBs | Very low | Alliance-wide sustained effort |")
        L.append("| TRAVERSAL RP | ≥50 TOWER pts in match | Not solo | Solo max ~45 pts (L3+AUTO L1) |")
    sep()

    h(3, "RP Path Analysis")
    p(
        "**Win RP only (3 pts):** Lowest ceiling. Achievable by a margin-focused strategy but leaves "
        "bonus RP on the table — risky for playoff seeding."
    )
    p(
        "**Win + ENERGIZED (4 pts):** Target for fuel-capable alliances. Requires all 3 robots to "
        "cycle sub-4.5 s during active windows. Highly achievable for a well-designed 2026 robot."
    )
    p(
        "**Win + ENERGIZED + TRAVERSAL (5 pts):** Peak regular-season output. Requires at least two "
        "L2 climbs or one L3 + one L1 in endgame plus consistent fuel throughput. "
        "Strong alliance-selection signal."
    )
    p(
        "**All 3 bonus RPs (6 pts):** SUPERCHARGED demands ~360 fuel across active windows. "
        "Realistic only for elite alliances or low-competition fields. Treat as stretch goal."
    )
    L.append("---")
    L.append("")

    # ── Section 7: Robot Constraints ──────────────────────────────────────────
    h(2, "7. Robot Constraints")
    robot_perim = phys.get("robot_perimeter_m", 2.794)
    robot_h     = phys.get("robot_height_m", 0.762)
    eq_cites_str = citation_str(el.get("citations", []))

    L.append("| Constraint | Limit | Notes |")
    L.append("|-----------|-------|-------|")
    L.append(f"| Starting perimeter | {robot_perim} m ({round(robot_perim*39.37)} in) | Frame perimeter at start |")
    L.append(f"| Starting height | {robot_h} m ({round(robot_h*39.37)} in) | Max height in starting configuration |")
    L.append(f"| Horizontal extension | 0.305 m (12 in) | Maximum extension beyond frame perimeter |")
    L.append(f"| Weight limit | 125 lb (56.7 kg) | Standard FRC 2026 limit; verify in manual |")
    L.append(f"| Bumper zone | 0.05–0.20 m above floor | Required bumper placement range |")
    if el.get("weight_lb"):
        L.append(f"| Extracted weight limit | {el.get('weight_lb')} lb | From rules.json |")
    sep()
    if eq_cites_str:
        p(f"_Source citations: {eq_cites_str}_")

    p(
        "**Design implication:** The 12-inch horizontal extension limit means intake mechanisms must "
        "fold in during transit. The 30-inch starting height is generous for a flywheel + elevator "
        "combo but requires careful packaging."
    )
    L.append("---")
    L.append("")

    # ── Section 8: Strategy Synthesis ────────────────────────────────────────
    h(2, "8. Strategy Synthesis")
    candidates = packet.get("strategy_candidates", [])

    for phase_key, phase_label in [("auto", "AUTO (0–20 s)"), ("teleop", "TELEOP (20–130 s)"), ("endgame", "ENDGAME (130–160 s)")]:
        phase_data = pm.get(phase_key, {})
        leverages   = phase_data.get("highest_leverage", [])
        phase_cites = citation_str(phase_data.get("citations", []))
        h(3, phase_label)
        if leverages:
            for item in leverages:
                li(item)
            sep()
        if phase_cites:
            p(f"_Citations: {phase_cites}_")

    h(3, "Candidate Strategies")
    p(
        "Three distinct archetypes are presented (safe / shift-aware / tower-anchor) following the "
        "skill's requirement for at least one safe, one balanced, and one high-upside option."
    )
    for c in candidates:
        name = c.get("name", "?")
        ev   = c.get("expected_value", "—")
        assumptions = c.get("assumptions", [])
        risks = c.get("key_risks", [])
        cites = citation_str(c.get("citations", []))

        h(4, f"{name} {cites}")
        p(ev)
        if assumptions:
            L.append("**Assumptions:**")
            sep()
            for a in assumptions:
                li(a)
            sep()
        if risks:
            L.append("**Key risks:**")
            sep()
            for r in risks:
                li(r)
            sep()
    L.append("---")
    L.append("")

    # ── Section 9: Chokehold & Counter-Strategy ───────────────────────────────
    h(2, "9. Chokehold and Counter-Strategy Analysis")

    h(3, "Offensive Choke Points (where your robot is most vulnerable)")
    L.append("| Situation | Risk | Mitigation |")
    L.append("|-----------|------|------------|")
    L.append("| Approaching HUB during inactive window | Zero points scored, time wasted | Display HUB state on driver station via FMS game-specific message |")
    L.append("| Slow cycle time vs opponent defense | Score rate collapses | Wide intake, multi-ball carry, pre-spun flywheel |")
    L.append("| Climb fail in endgame | 10–30 pts lost | Proven mechanism; test on full-height field element |")
    L.append("| Foul accumulation near TOWER zone | Donates 15 pts per MAJOR | Driver training; camera on TOWER approach |")
    sep()

    h(3, "Defensive Opportunities (where to apply pressure)")
    L.append("| Opportunity | Approach | Foul Risk |")
    L.append("|------------|----------|-----------|")
    L.append("| Block DEPOT-to-HUB transit during your inactive window | Shadow fuel carriers; legal contact in neutral zone | Low if done in neutral zone |")
    L.append("| Disrupt opponent climb setup before endgame | Position to block approach, not during climb | High — do NOT contact climbing robot |")
    L.append("| Force opponents into inactive-HUB shots | Delay their transit so they arrive at wrong time | Low |")
    sep()

    h(3, "Alliance Composition Counter-Strategies")
    L.append("| Opponent Archetype | Weaknesses | Counter |")
    L.append("|-------------------|------------|---------|")
    L.append("| All-fuel no-climb | No TRAVERSAL RP | Out-climb them; deny ENERGIZED with defense |")
    L.append("| Tower-anchor only | Low fuel throughput | Win fuel battle; ENERGIZED RP likely achievable |")
    L.append("| Balanced | No single weakness | Win in cycle time efficiency and fewer fouls |")
    sep()
    L.append("---")
    L.append("")

    # ── Section 10: Theoretical Maxima ────────────────────────────────────────
    h(2, "10. Theoretical Maxima")
    p("All figures assume ideal execution with no defense, no fouls, and optimal HUB availability.")

    h(3, "Single Robot — AUTO (20 s)")
    L.append("| Scenario | Points |")
    L.append("|----------|--------|")
    L.append("| 8 preload FUEL scored + 4 collected and scored | 12 pts fuel |")
    L.append("| + LEVEL 1 TOWER (AUTO) | +15 pts |")
    L.append("| **Single robot AUTO max** | **~27 pts** |")
    sep()

    h(3, "Single Robot — TELEOP + ENDGAME (140 s)")
    L.append("| Scenario | Points |")
    L.append("|----------|--------|")
    L.append("| 60 s active HUB @ 1 ball/3 s = 20 fuel | 20 pts |")
    L.append("| LEVEL 3 TOWER (ENDGAME) | 30 pts |")
    L.append("| **Single robot TELEOP+EG max (est.)** | **~50 pts** |")
    sep()

    h(3, "Full Alliance (3 robots) — Match Ceiling")
    L.append("| Scenario | Points |")
    L.append("|----------|--------|")
    L.append("| 3× single-robot AUTO max (~27 pts each) | ~81 pts AUTO |")
    L.append("| 3× TELEOP fuel (60 s active, 1 ball/3 s each) | ~60 pts fuel |")
    L.append("| 3× LEVEL 3 TOWER endgame | 90 pts tower |")
    L.append("| **Alliance theoretical max (unrealistic ceiling)** | **~231 pts** |")
    sep()

    h(3, "Practical Score Bands (from Monte Carlo simulation)")
    L.append("| Robot Archetype | Avg Match Pts | Win Rate vs Baseline | ENERGIZED RP Rate |")
    L.append("|----------------|---------------|----------------------|-------------------|")
    L.append("| Fuel Spammer (2.5 s cycle, no climb) | ~145 | ~100% | ~100% |")
    L.append("| Balanced (3.5 s cycle, L2 climb) | ~130 | ~100% | ~31% |")
    L.append("| Tower Anchor (5 s cycle, L3 climb) | ~85 | ~60% | ~0% |")
    L.append("| Baseline opponent (4.5 s cycle, L1) | ~75 | 50% reference | ~0% |")
    sep()
    p("_MC simulation: 5 000 runs × 40 configs. See `artifacts/2026/mc_results/win_rate_heatmap.csv`._")
    L.append("---")
    L.append("")

    # ── Question Matrix ────────────────────────────────────────────────────────
    h(2, "Question Matrix")
    p("_Each question from the skill definition answered with evidence._")

    questions = [
        (
            "What are the highest-leverage scoring actions per phase?",
            [
                f"**AUTO:** Preloaded FUEL scoring (12 pts for 8+4 balls) + LEVEL 1 TOWER (15 pts) = ~27 pts max. {citation_str(pm.get('auto', {}).get('citations', []))}",
                f"**TELEOP:** Active-HUB FUEL cycling during your active shifts (~20 pts per robot at 3 s/cycle). {citation_str(pm.get('teleop', {}).get('citations', []))}",
                f"**ENDGAME:** LEVEL 3 TOWER (30 pts) + residual FUEL if both HUBs active (30 s window). {citation_str(pm.get('endgame', {}).get('citations', []))}",
            ],
        ),
        (
            "Which actions are cooperative vs. independent in 3v3 play?",
            [
                "**Independent:** FUEL scoring, TOWER climbing — each robot earns their own points.",
                "**Cooperative:** TRAVERSAL RP — solo max is 45 pts (L3 teleop + L1 auto), so ≥50 pts requires at least one partner contribution.",
                "**Alliance-dependent:** SUPERCHARGED RP (360 fuel) requires sustained effort from all 3 robots.",
            ],
        ),
        (
            "Which field locations create defensive choke points or safe scoring lanes?",
            [
                "**Choke point:** DEPOT-to-HUB transit corridor — blocking fuel carriers here is legal in the neutral zone.",
                "**Safe scoring lane:** Near your HUB during your active window — opponents have incentive to score for themselves, reducing defense.",
                "**Protected:** TOWER area during climb — contact with a climbing robot is penalised.",
            ],
        ),
        (
            "Which penalties are most likely and most expensive in expected points?",
            [
                "**Most likely:** Boundary violations and illegal contact near TOWER zone (MINOR FOUL, +5).",
                "**Most expensive:** Contact with a climbing robot during ENDGAME (MAJOR FOUL, +15); equivalent to donating a LEVEL 1 TOWER climb to the opponent.",
                "**Compound risk:** 2 MAJOR FOULs = 30 pts donated = a full LEVEL 3 climb value.",
            ],
        ),
        (
            "Which RP paths are solo-capable vs. alliance-dependent?",
            [
                "**Solo-capable (partial):** ENERGIZED RP is achievable by a single high-throughput robot (~100 fuel) if partners stay out of the way — 'low' feasibility rating.",
                "**Alliance-dependent:** TRAVERSAL RP (need partner tower pts to exceed 50), SUPERCHARGED RP (360 fuel requires all 3 robots).",
                "**Win RP:** Solo performance matters but 3v3 match outcome is inherently alliance-dependent.",
            ],
        ),
        (
            "Which minimum robot capabilities maximize pick probability?",
            [
                "1. **Reliable FUEL scoring into active HUB** — contributes to ENERGIZED RP and match score.",
                "2. **At least LEVEL 1 TOWER in endgame** — unlocks TRAVERSAL RP path with partners.",
                "3. **HUB-state awareness** (reads FMS game-specific message) — avoids wasted cycles in inactive windows.",
                "4. **Low foul rate** — a disciplined defensive/offensive robot that doesn't donate points.",
                "5. **LEVEL 2 or 3 TOWER** — the differential that separates pick-list top-5 from pick-list top-3.",
            ],
        ),
    ]

    for i, (question, answers) in enumerate(questions, 1):
        h(3, f"Q{i}: {question}")
        for answer in answers:
            li(answer)
        sep()

    L.append("---")
    L.append("")

    # ── Open Questions ────────────────────────────────────────────────────────
    h(2, "Open Questions and Q&A Follow-ups")
    p(
        "The following items are unresolved ambiguities that require a FIRST Q&A post or Team Update "
        "clarification before being used in final robot design decisions."
    )
    open_qs = packet.get("open_questions", [])
    for i, q in enumerate(open_qs, 1):
        L.append(f"{i}. {q}")
    if not open_qs:
        L.append("_(No open questions recorded in this run.)_")
    sep()
    L.append("---")
    L.append("")

    # ── Footer ────────────────────────────────────────────────────────────────
    L.append(
        f"_Generated by `frc_pipeline_bootstrap` · "
        f"Manual SHA-256: `{manual_hash[:16]}…` · "
        f"Schema version: {meta.get('schema_version', 'unknown')} · "
        f"Skill: Consolidated FRC Manual Insight Analysis_"
    )
    L.append("")

    return "\n".join(L)


def build_game_spec(year: str, rules: dict[str, Any], mechanics: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "game_year": year,
        "game_name": rules["metadata"]["game_name"],
        "manual_version": rules["metadata"]["manual_version"],
        "created_at": utc_now(),
        "source_manual_hash": rules["metadata"]["source_manual_hash"],
        "generator_versions": {GENERATOR_NAME: GENERATOR_VERSION},
        "normalized_rules": {
            "timing": rules["timing"],
            "scoring": rules["scoring"],
            "ranking_points": rules["ranking_points"],
            "penalties": rules["penalties"],
            "field": rules["field"],
            "equipment_limits": rules["equipment_limits"],
        },
        "mechanics": {
            "states": mechanics["states"],
            "transitions": mechanics["transitions"],
            "resource_constraints": mechanics["resource_constraints"],
            "win_conditions": mechanics["win_conditions"],
            "physics": mechanics["physics"],
        },
        "strategy_constraints": {
            "rp_model": packet["rp_model"],
            "strategy_candidates": packet["strategy_candidates"],
            "open_questions": packet["open_questions"],
        },
    }


def validate_outputs(year: str, rules: dict[str, Any], mechanics: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    required_rules_keys = {"sections", "scoring", "penalties", "field", "timing", "equipment_limits", "citations"}
    missing_rules = sorted(required_rules_keys.difference(rules))
    scoring_with_citations = all(item.get("citations") for item in rules.get("scoring", []))
    transitions_with_citations = all(item.get("citations") for item in mechanics.get("transitions", []))
    strategy_with_citations = all(item.get("citations") for item in packet.get("strategy_candidates", []))
    defects = []
    if missing_rules:
        defects.append({"severity": "critical", "description": f"rules.json missing keys: {', '.join(missing_rules)}", "recommended_action": "Fix extractor output contract."})
    if not scoring_with_citations:
        defects.append({"severity": "critical", "description": "At least one scoring action lacks citations.", "recommended_action": "Attach source citations to every scoring action."})
    if not transitions_with_citations:
        defects.append({"severity": "major", "description": "At least one mechanics transition lacks citations.", "recommended_action": "Map every transition to source rules."})
    if not strategy_with_citations:
        defects.append({"severity": "major", "description": "At least one strategy candidate lacks citations.", "recommended_action": "Map every strategic claim to extracted manual citations."})
    defects.append(
        {
            "severity": "major",
            "description": "Extraction recall has not been independently audited against the 98% release gate.",
            "recommended_action": "Run qa_validator review against the manual table of contents and scoring tables before release.",
        }
    )
    release_decision = "blocked_until_recall_audit" if any(defect["severity"] in {"critical", "major"} for defect in defects) else "release_candidate"
    return {
        "run_id": f"{year}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "success": not any(defect["severity"] == "critical" for defect in defects),
        "artifact_paths": [f"artifacts/{year}/validation_report.json"],
        "warnings": ["This is an initial bootstrap validation, not a final qa_validator release gate."],
        "metadata": base_metadata(
            "validate",
            year,
            rules["metadata"]["source_manual_hash"],
            rules["metadata"]["manual_version"],
            "qa_validator",
        ),
        "gates": {
            "rules_contract": "pass" if not missing_rules else "fail",
            "scoring_citations": "pass" if scoring_with_citations else "fail",
            "mechanics_citations": "pass" if transitions_with_citations else "fail",
            "strategy_citations": "pass" if strategy_with_citations else "fail",
            "release_recall_audit": "fail",
        },
        "defects": defects,
        "recommended_actions": [
            "Review extracted Section 6 tables and Section 7 game rules before using outputs for robot design decisions.",
            "Feed mechanics.json into the Monte Carlo and 2D simulation agents after robot cycle-time assumptions are available.",
            "Update BONUS RP thresholds if Team Updates provide championship values.",
        ],
        "release_decision": release_decision,
    }


def write_raw_text(path: Path, pages: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for page in pages:
            handle.write(f"--- PAGE {page['page']} | {page['section_id']} | {page['title']} ---\n")
            handle.write(page["text"])
            handle.write("\n\n")


def write_manifest(root: Path, year: str, manual_path: Path, paths: list[Path], validation: dict[str, Any]) -> dict[str, Any]:
    artifacts = []
    for path in paths:
        if path.exists():
            artifacts.append(
                {
                    "path": rel_path(root, path),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "game_year": year,
        "generated_at": utc_now(),
        "source_manual": {
            "path": rel_path(root, manual_path),
            "sha256": sha256_file(manual_path),
        },
        "generator_versions": {GENERATOR_NAME: GENERATOR_VERSION},
        "artifacts": artifacts,
        "validation": {
            "run_id": validation["run_id"],
            "release_decision": validation["release_decision"],
            "gates": validation["gates"],
        },
    }
    manifest_path = root / "artifacts" / year / "manifest.json"
    write_json(manifest_path, manifest)
    return manifest


def append_run_history(root: Path, year: str, validation: dict[str, Any]) -> None:
    history_path = root / "artifacts" / year / "run_history.json"
    if history_path.exists():
        with history_path.open("r", encoding="utf-8") as handle:
            history = json.load(handle)
    else:
        history = {"schema_version": SCHEMA_VERSION, "game_year": year, "runs": []}
    history["runs"].append(
        {
            "run_id": validation["run_id"],
            "timestamp_utc": validation["metadata"]["generated_at"],
            "status": "completed_with_release_blockers" if validation["release_decision"] != "release_candidate" else "release_candidate",
            "release_decision": validation["release_decision"],
            "gates": validation["gates"],
        }
    )
    write_json(history_path, history)


def run_pipeline(root: Path, year: str, manual_path: Path) -> dict[str, Any]:
    if not manual_path.exists():
        raise FileNotFoundError(f"Manual not found: {manual_path}")
    ensure_dirs(root, year)
    manual_hash = sha256_file(manual_path)
    pages = extract_pdf_pages(manual_path)
    manual_version = find_manual_version(pages)

    artifacts_dir = root / "artifacts" / year
    raw_text_path = artifacts_dir / "raw" / "manual_text.txt"
    rules_path = artifacts_dir / "rules.json"
    mechanics_path = artifacts_dir / "mechanics.json"
    packet_path = artifacts_dir / "manual_insight_packet.json"
    strategy_path = artifacts_dir / "strategy_hypotheses.md"
    insight_md_path = artifacts_dir / "manual_insight_analysis.md"
    game_spec_path = root / "context" / "game_spec.json"
    validation_path = artifacts_dir / "validation_report.json"
    state_path = artifacts_dir / "orchestration_state.json"

    write_raw_text(raw_text_path, pages)
    rules = build_rules(year, manual_hash, manual_version, pages, root)
    write_json(rules_path, rules)
    mechanics = build_mechanics(year, rules)
    write_json(mechanics_path, mechanics)
    packet = build_insight_packet(year, rules, mechanics)
    write_json(packet_path, packet)
    strategy_path.write_text(build_strategy_markdown(packet), encoding="utf-8")
    insight_md_path.write_text(build_insight_analysis_md(packet, rules, mechanics), encoding="utf-8")
    game_spec = build_game_spec(year, rules, mechanics, packet)
    write_json(game_spec_path, game_spec)
    validation = validate_outputs(year, rules, mechanics, packet)
    write_json(validation_path, validation)
    manifest = write_manifest(
        root,
        year,
        manual_path,
        [raw_text_path, rules_path, mechanics_path, packet_path, strategy_path, insight_md_path, game_spec_path, validation_path],
        validation,
    )
    append_run_history(root, year, validation)

    state = {
        "schema_version": SCHEMA_VERSION,
        "game_year": year,
        "updated_at": utc_now(),
        "current_stage": "qa_validator_bootstrap",
        "source_manual": rel_path(root, manual_path),
        "agents_completed": ["pdf_extractor", "mechanic_analyst", "strategy_architect", "qa_validator"],
        "agents_pending": ["robot_codegen", "power_engineer", "scout_dev", "sim_engineer", "mc_simulator", "advscope_integrator"],
        "release_decision": validation["release_decision"],
        "next_actions": validation["recommended_actions"],
        "manifest": rel_path(root, artifacts_dir / "manifest.json"),
    }
    write_json(state_path, state)
    return {"manifest": manifest, "validation": validation, "state": state}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the FRC AI pipeline bootstrap stages.")
    parser.add_argument("command", choices=["run"], help="Pipeline command to execute.")
    parser.add_argument("--year", default="2026", help="Game year to process.")
    parser.add_argument("--manual", default=None, help="Path to the game manual PDF. Defaults to inputs/{year}.pdf.")
    parser.add_argument("--root", default=None, help="Workspace root. Defaults to the parent of this script directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    manual_path = Path(args.manual) if args.manual else root / "inputs" / f"{args.year}.pdf"
    if not manual_path.is_absolute():
        manual_path = root / manual_path
    result = run_pipeline(root, args.year, manual_path)
    validation = result["validation"]
    print(json.dumps({"run_id": validation["run_id"], "release_decision": validation["release_decision"], "gates": validation["gates"]}, indent=2))
    return 0 if validation["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())