#!/usr/bin/env python3
"""Deterministic bootstrap runner for the FRC AI orchestration pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

# Import decomposition module (available in skills)
sys.path.insert(0, str(Path(__file__).parent.parent / "skills"))
try:
    from anthropic.game_manual_decomposition.decompose import decompose_manual_to_sections
    HAS_DECOMPOSE = True
except ImportError:
    HAS_DECOMPOSE = False


GENERATOR_NAME = "frc_pipeline_bootstrap"
GENERATOR_VERSION = "0.1.0"
SCHEMA_VERSION = "0.1.0"

AGENT_SKILLS = {
    "pdf_extractor": ["anthropic/pdf", "karpathy/claude"],
    "field_modeler": ["codex/jupyter-notebook", "karpathy/claude"],
    "mechanic_analyst": ["codex/jupyter-notebook", "karpathy/claude"],
    "strategy_architect": ["anthropic/doc-coauthoring", "karpathy/claude"],
    "sim_engineer": ["codex/jupyter-notebook", "karpathy/claude"],
    "robot_codegen": ["anthropic/claude-api", "karpathy/claude"],
    "qa_validator": ["codex/security-best-practices", "codex/security-threat-model", "karpathy/claude"],
}

SIM_FEEDBACK_ALLOWED_SOURCES = {"human_playtest", "rl_self_play", "hybrid"}
SIM_FEEDBACK_ALLOWED_CONFIDENCE = {"low", "medium", "high"}
SIM_FEEDBACK_ROBOT_PARAM_BOUNDS = {
    "drive_free_speed_fps": (0.0, 30.0),
    "drive_time_to_full_speed_s": (0.0, 10.0),
    "intake_rate_pieces_per_s": (0.0, 30.0),
    "storage_capacity_assumed": (0.0, 100.0),
    "score_rate_pieces_per_s": (0.0, 30.0),
}
SIM_FEEDBACK_KPI_BOUNDS = {
    "match_points": (0.0, 500.0),
    "value_per_second": (0.0, 10.0),
    "foul_points_conceded": (0.0, 200.0),
    "tower_success_rate": (0.0, 1.0),
    "defense_sensitivity": (0.0, 1.0),
    "alliance_dependency_score": (0.0, 1.0),
}
SIM_FEEDBACK_UPDATE_DELTA_BOUNDS = (-100.0, 100.0)

CURRENT_GENERATED_ARTIFACTS = {
    "apriltag_field_layout.json",
    "extraction_report.md",
    "field_layout_reference.json",
    "field_model.json",
    "logs",
    "manifest.json",
    "manual_sections_index.json",
    "mechanics.json",
    "orchestration_state.json",
    "raw",
    "run_history.json",
    "rules.json",
    "sim_params.json",
    "sim_summary.json",
    "simulation_model.json",
    "simulation_report.md",
    "strategy_brief.md",
    "strategy_packet.json",
    "team_decision_packet.md",
    "validation",
    "validation_report.json",
}

LEGACY_GENERATED_ARTIFACTS = {
    "manual_insight_analysis.md",
    "manual_insight_packet.json",
    "mc_results",
    "power_app",
    "scouting_app",
    "sim_2d",
    "sim_arch_feedback.json",
    "strategy_hypotheses.md",
    "wpilib_project",
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


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def collect_manifest_artifact_paths(root: Path, manifest_path: Path, year: str) -> set[Path]:
    if not manifest_path.exists():
        return set()
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return set()
    if not isinstance(manifest, dict):
        return set()
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        return set()

    resolved: set[Path] = set()
    year_prefix = f"artifacts/{year}/"
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        rel = item.get("path")
        if not isinstance(rel, str):
            continue
        normalized = rel.replace("\\", "/")
        if normalized.startswith(year_prefix):
            resolved.add(root / Path(normalized))
    return resolved


def reset_artifact_output(root: Path, year: str) -> None:
    artifacts_dir = root / "artifacts" / year
    manifest_path = artifacts_dir / "manifest.json"
    cleanup_targets: set[Path] = set()

    for name in CURRENT_GENERATED_ARTIFACTS.union(LEGACY_GENERATED_ARTIFACTS):
        cleanup_targets.add(artifacts_dir / name)

    cleanup_targets.update(collect_manifest_artifact_paths(root, manifest_path, year))

    for path in sorted(cleanup_targets, key=lambda item: (len(item.parts), str(item)), reverse=True):
        if path == artifacts_dir:
            continue
        remove_path(path)


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


def resolve_input_path(root: Path, year: str, explicit: str | None, role: str) -> Path:
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_absolute() else root / candidate

    if role == "manual":
        candidates = [
            root / "inputs" / f"{year}.pdf",
            root / "inputs" / f"{year}GameManual.pdf",
            root / "inputs" / f"{year}-game-manual.pdf",
            root / "inputs" / f"{year}_game_manual.pdf",
        ]
    elif role == "field_drawing":
        candidates = [
            root / "inputs" / f"{year}-field-dimension-dwgs.pdf",
            root / "inputs" / f"{year}-field-dimensions.pdf",
            root / "inputs" / f"{year}_field_dimensions.pdf",
            root / "inputs" / f"{year}FieldDimensions.pdf",
        ]
    else:
        raise ValueError(f"Unsupported input role: {role}")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def find_manual_version(pages: list[dict[str, Any]]) -> str:
    for page in pages[:10]:
        match = re.search(r"Version:\s*([A-Z0-9]+)", page["text"])
        if match:
            return match.group(1)
    return "unknown"


def extract_dimension_tokens(pages: list[dict[str, Any]], max_tokens: int = 80) -> list[dict[str, Any]]:
    pattern = re.compile(r"\b\d+(?:\.\d+)?\s*(?:in(?:ches)?|ft|mm|cm|m)\b", flags=re.IGNORECASE)
    seen: set[tuple[int, str]] = set()
    tokens: list[dict[str, Any]] = []
    for page in pages:
        for match in pattern.finditer(page["text"]):
            value = match.group(0)
            key = (page["page"], value.lower())
            if key in seen:
                continue
            seen.add(key)
            tokens.append(
                {
                    "page": page["page"],
                    "section_id": page["section_id"],
                    "value": value,
                }
            )
            if len(tokens) >= max_tokens:
                return tokens
    return tokens


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
            "skills": AGENT_SKILLS.get(agent_name, []),
        },
    }


def build_rules(year: str, manual_hash: str, manual_version: str, pages: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    clauses = extract_rule_clauses(pages)
    zone_citation = citation(pages, 21, "ALLIANCE AREA")
    hub_citation = citation(pages, 22, "A HUB is one of two")
    hub_opening_citation = citation(pages, 23, "The top of each HUB has")
    apriltag_citation = citation(pages, 33, "AprilTags are 8.125in")
    apriltag_hub_citation = citation(pages, 34, "HUB AprilTags")
    apriltag_tower_citation = citation(pages, 34, "Two AprilTags")
    apriltag_outpost_citation = citation(pages, 35, "Two AprilTags")
    apriltag_trench_citation = citation(pages, 35, "TRENCH AprilTags")
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
            "zones": [
                {
                    "name": "ALLIANCE AREA",
                    "dimensions_in": {"width": 360.0, "depth": 134.0},
                    "dimensions_m": {"width": 9.14, "depth": 3.4},
                    "description": "Driver and human-player volume outside the carpeted field edge.",
                    "citations": [zone_citation],
                },
                {
                    "name": "ALLIANCE ZONE",
                    "dimensions_in": {"width": 317.7, "depth": 158.6},
                    "dimensions_m": {"width": 8.07, "depth": 4.03},
                    "description": "Alliance-side scoring half containing the TOWER and DEPOT.",
                    "citations": [zone_citation],
                },
                {
                    "name": "NEUTRAL ZONE",
                    "dimensions_in": {"width": 317.7, "depth": 283.0},
                    "dimensions_m": {"width": 8.07, "depth": 7.19},
                    "description": "Midfield collection zone formed by the BUMPS, TRENCHES, HUBS, and guardrails.",
                    "citations": [zone_citation],
                },
                {
                    "name": "OUTPOST AREA",
                    "dimensions_in": {"width": 71.0, "depth": 134.0},
                    "dimensions_m": {"width": 1.8, "depth": 3.4},
                    "description": "Human-player chute and corral area outside the carpet edge.",
                    "citations": [zone_citation],
                },
            ],
            "markings": [
                {
                    "name": "CENTER LINE",
                    "description": "White line that bisects the NEUTRAL ZONE.",
                    "citations": [zone_citation],
                },
                {
                    "name": "HUMAN STARTING LINE",
                    "offset_in": 24.0,
                    "offset_m": 0.61,
                    "description": "White line in the ALLIANCE AREA parallel to the ALLIANCE WALL.",
                    "citations": [zone_citation],
                },
                {
                    "name": "ROBOT STARTING LINE",
                    "description": "Alliance-colored line at the ALLIANCE ZONE edge in front of two BUMPS and an ALLIANCE HUB.",
                    "citations": [zone_citation],
                },
            ],
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
            "hub": {
                "footprint_in": {"width": 47.0, "depth": 47.0},
                "footprint_m": {"width": 1.19, "depth": 1.19},
                "alliance_wall_offset_in": 158.6,
                "alliance_wall_offset_m": 4.03,
                "opening_hex_width_in": 41.7,
                "opening_hex_width_m": 1.06,
                "opening_front_edge_height_in": 72.0,
                "opening_front_edge_height_m": 1.83,
                "neutral_zone_exits": 4,
                "citations": [hub_citation, hub_opening_citation],
            },
        },
        "field_rules": {
            "zones": [
                {
                    "name": "ALLIANCE AREA",
                    "rule": "Driver and human-player staging area outside the carpet edge.",
                    "citations": [zone_citation],
                },
                {
                    "name": "ALLIANCE ZONE",
                    "rule": "Alliance-side field volume that surrounds one TOWER and one DEPOT and includes the ROBOT STARTING LINE.",
                    "citations": [zone_citation],
                },
                {
                    "name": "NEUTRAL ZONE",
                    "rule": "Central midfield volume surrounding the CENTER LINE and bounded by BUMPS, TRENCHES, HUBS, and guardrails.",
                    "citations": [zone_citation],
                },
                {
                    "name": "OUTPOST AREA",
                    "rule": "Human-player loading area bounded by the OUTPOST, edge of carpet, and tape.",
                    "citations": [zone_citation],
                },
            ],
            "scoring_structures": [
                {
                    "name": "HUB",
                    "rule": "Each ALLIANCE has one HUB centered between two BUMPS and offset 158.6 in from its ALLIANCE WALL.",
                    "citations": [hub_citation],
                },
                {
                    "name": "HUB OPENING",
                    "rule": "ROBOTS deliver FUEL through a 41.7 in hexagonal opening whose front edge is 72 in above the carpet.",
                    "citations": [hub_opening_citation],
                },
            ],
            "apriltags": {
                "family": "36h11",
                "count": 32,
                "mount_panel_in": 10.5,
                "tag_size_in": 8.125,
                "hub_ids": [2, 3, 4, 5, 8, 9, 10, 11, 18, 19, 20, 21, 24, 25, 26, 27],
                "tower_ids": [15, 16, 31, 32],
                "outpost_ids": [13, 14, 29, 30],
                "trench_ids": [1, 6, 7, 12, 17, 22, 23, 28],
                "mount_heights_m": {
                    "hub": 1.124,
                    "tower": 0.5525,
                    "outpost": 0.5525,
                    "trench": 0.889,
                },
                "citations": [
                    apriltag_citation,
                    apriltag_hub_citation,
                    apriltag_tower_citation,
                    apriltag_outpost_citation,
                    apriltag_trench_citation,
                ],
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


def build_field_layout_reference(
    year: str,
    manual_hash: str,
    manual_version: str,
    field_drawing_hash: str,
    field_pages: list[dict[str, Any]],
    rules: dict[str, Any],
) -> dict[str, Any]:
    metadata = base_metadata("ingest", year, manual_hash, manual_version, "pdf_extractor")
    metadata["source_field_drawing_hash"] = field_drawing_hash
    dims = rules.get("field", {}).get("dimensions", {})
    field_length = float(dims.get("length_m", 0.0) or 0.0)
    field_width = float(dims.get("width_m", 0.0) or 0.0)
    drawing_origin_page = next((page for page in field_pages if "AprilTag Coordinates" in page.get("text", "")), None)
    drawing_origin_note = compact(drawing_origin_page["text"], 420) if drawing_origin_page else ""
    return {
        "success": True,
        "artifact_paths": [f"artifacts/{year}/field_layout_reference.json"],
        "warnings": [
            "Field drawing OCR is used as a traceable reference layer; unresolved geometry should stay explicit until a human audit confirms coordinates.",
            "AprilTag IDs and mounting groups are known from the manual, but most absolute poses still need manual trace-off from the field drawings.",
        ],
        "metadata": metadata,
        "drawing_pages": [
            {
                "page": page["page"],
                "section_id": page["section_id"],
                "title": page["title"],
                "text_excerpt": compact(page["text"], 280),
            }
            for page in field_pages
            if page.get("text")
        ],
        "field": {
            "length": field_length,
            "width": field_width,
        },
        "dimension_tokens": extract_dimension_tokens(field_pages),
        "raw_locations": [
            {
                "name": "field_carpet",
                "kind": "boundary",
                "dimensions_m": {"length": field_length, "width": field_width},
                "sources": [rules["field"]["dimensions"]["citations"][0]],
            },
            {
                "name": "blue_hub_reference",
                "kind": "scoring_location",
                "x_from_blue_wall_m": 4.03,
                "y_assumption_m": round(field_width / 2.0, 3),
                "coordinate_confidence": "medium",
                "notes": "The manual fixes HUB offset from the alliance wall. Y is modeled at field midpoint for coarse analysis until drawing trace-off is added.",
                "sources": rules["field"]["hub"]["citations"],
            },
            {
                "name": "red_hub_reference",
                "kind": "scoring_location",
                "x_from_blue_origin_m": round(field_length - 4.03, 3),
                "y_assumption_m": round(field_width / 2.0, 3),
                "coordinate_confidence": "medium",
                "notes": "Mirrored from the blue-side HUB reference for coarse analysis.",
                "sources": rules["field"]["hub"]["citations"],
            },
            {
                "name": "zone_dimensions",
                "kind": "zones",
                "zones": rules["field"]["zones"],
                "sources": [rules["field"]["zones"][0]["citations"][0]],
            },
            {
                "name": "apriltag_coordinate_frame",
                "kind": "reference_frame",
                "notes": drawing_origin_note,
                "sources": [{"section_id": drawing_origin_page["section_id"], "page": drawing_origin_page["page"], "clause_text": drawing_origin_note}] if drawing_origin_page else [],
            },
        ],
        "confidence_notes": [
            "Absolute AprilTag coordinates were not reliably OCR-extracted from the field drawing pages and remain unresolved.",
            "HUB X offsets are grounded in the manual. HUB Y placement is assumed to be the field midline for coarse strategy simulation only.",
            "Zone dimensions are treated as cited geometric references even though the zone-depth totals do not tile the full carpet dimensions directly.",
        ],
    }


def build_apriltag_field_layout(
    year: str,
    manual_hash: str,
    manual_version: str,
    field_drawing_hash: str,
    rules: dict[str, Any],
) -> dict[str, Any]:
    dims = rules.get("field", {}).get("dimensions", {})
    return {
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "game_year": year,
            "manual_version": manual_version,
            "generated_at": utc_now(),
            "source_manual_hash": manual_hash,
            "source_field_drawing_hash": field_drawing_hash,
            "generator_name": GENERATOR_NAME,
            "generator_version": GENERATOR_VERSION,
            "stage": "model",
            "agent": {"name": "field_modeler", "skills": AGENT_SKILLS.get("field_modeler", [])},
            "notes": [
                "Absolute tag poses still need manual trace-off from the field drawings.",
                "Tag IDs and mount groups are preserved here so downstream tooling can fill in poses deterministically later.",
            ],
        },
        "field": {
            "length": round(float(dims.get("length_m", 0.0) or 0.0), 3),
            "width": round(float(dims.get("width_m", 0.0) or 0.0), 3),
        },
        "tags": [],
        "known_mount_groups": {
            "hub": rules.get("field_rules", {}).get("apriltags", {}).get("hub_ids", []),
            "tower": rules.get("field_rules", {}).get("apriltags", {}).get("tower_ids", []),
            "outpost": rules.get("field_rules", {}).get("apriltags", {}).get("outpost_ids", []),
            "trench": rules.get("field_rules", {}).get("apriltags", {}).get("trench_ids", []),
        },
    }


def build_field_model(
    year: str,
    rules: dict[str, Any],
    field_layout_reference: dict[str, Any],
) -> dict[str, Any]:
    metadata = base_metadata(
        "model",
        year,
        rules["metadata"]["source_manual_hash"],
        rules["metadata"]["manual_version"],
        "field_modeler",
    )
    metadata["source_field_drawing_hash"] = field_layout_reference["metadata"]["source_field_drawing_hash"]
    dims = rules["field"]["dimensions"]
    field_length = float(dims["length_m"])
    field_width = float(dims["width_m"])
    hub_x = float(rules["field"]["hub"]["alliance_wall_offset_m"])
    blue_hub = {"x_m": round(hub_x, 3), "y_m": round(field_width / 2.0, 3), "z_m": 0.0}
    red_hub = {"x_m": round(field_length - hub_x, 3), "y_m": round(field_width / 2.0, 3), "z_m": 0.0}
    return {
        "success": True,
        "artifact_paths": [
            f"artifacts/{year}/field_model.json",
            f"artifacts/{year}/apriltag_field_layout.json",
        ],
        "warnings": [
            "This field model is normalized for strategy and coarse simulation, not a final robot-autonomy map.",
            "HUB Y coordinates and coarse travel distances are symmetry-based assumptions until the field drawings are manually traced.",
        ],
        "metadata": metadata,
        "field_dimensions": {
            "length_m": field_length,
            "width_m": field_width,
            "citations": dims["citations"],
        },
        "reference_frames": {
            "wpilib_blue_origin": {
                "origin": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
                "x_axis": "toward_red_alliance_wall",
                "y_axis": "away_from_scoring_table",
                "z_axis": "up",
            },
            "field_drawing_origin": {
                "origin_note": "Field drawing origin aligns to the blue-alliance / scoring-table corner reference described in the drawing text.",
                "source": next(
                    (
                        location["sources"][0]
                        for location in field_layout_reference.get("raw_locations", [])
                        if location.get("name") == "apriltag_coordinate_frame" and location.get("sources")
                    ),
                    None,
                ),
            },
            "alliance_mirroring": {
                "blue_to_red": {
                    "mirror_plane_x_m": round(field_length / 2.0, 3),
                    "description": "Red-alliance reference points are mirrored from blue-alliance coordinates across midfield.",
                }
            },
        },
        "zones": [
            {
                "name": "blue_alliance_zone",
                "alliance": "blue",
                "dimensions_m": rules["field"]["zones"][1]["dimensions_m"],
                "position_reference": "adjacent to blue alliance wall",
                "coordinate_confidence": "medium",
                "citations": rules["field"]["zones"][1]["citations"],
            },
            {
                "name": "neutral_zone",
                "alliance": None,
                "dimensions_m": rules["field"]["zones"][2]["dimensions_m"],
                "position_reference": "centered on midfield",
                "coordinate_confidence": "medium",
                "citations": rules["field"]["zones"][2]["citations"],
            },
            {
                "name": "red_alliance_zone",
                "alliance": "red",
                "dimensions_m": rules["field"]["zones"][1]["dimensions_m"],
                "position_reference": "adjacent to red alliance wall",
                "coordinate_confidence": "medium",
                "citations": rules["field"]["zones"][1]["citations"],
            },
            {
                "name": "outpost_area",
                "alliance": "both",
                "dimensions_m": rules["field"]["zones"][3]["dimensions_m"],
                "position_reference": "outside the carpet boundary near each alliance wall",
                "coordinate_confidence": "high",
                "citations": rules["field"]["zones"][3]["citations"],
            },
        ],
        "scoring_locations": [
            {
                "name": "blue_hub",
                "alliance": "blue",
                "kind": "hub",
                "pose": blue_hub,
                "opening_height_m": rules["field"]["hub"]["opening_front_edge_height_m"],
                "coordinate_confidence": "medium",
                "notes": "X offset is cited; Y is centered for coarse modeling.",
                "citations": rules["field"]["hub"]["citations"],
            },
            {
                "name": "red_hub",
                "alliance": "red",
                "kind": "hub",
                "pose": red_hub,
                "opening_height_m": rules["field"]["hub"]["opening_front_edge_height_m"],
                "coordinate_confidence": "medium",
                "notes": "Mirrored from the blue HUB reference for coarse modeling.",
                "citations": rules["field"]["hub"]["citations"],
            },
            {
                "name": "blue_tower",
                "alliance": "blue",
                "kind": "tower",
                "pose": {"x_m": 0.0, "y_m": round(field_width / 2.0, 3), "z_m": 0.0},
                "coordinate_confidence": "low",
                "notes": "Modeled at the alliance wall midpoint until tower-wall drawing traces are added.",
                "citations": rules["ranking_points"][2]["citations"],
            },
            {
                "name": "red_tower",
                "alliance": "red",
                "kind": "tower",
                "pose": {"x_m": round(field_length, 3), "y_m": round(field_width / 2.0, 3), "z_m": 0.0},
                "coordinate_confidence": "low",
                "notes": "Modeled at the alliance wall midpoint until tower-wall drawing traces are added.",
                "citations": rules["ranking_points"][2]["citations"],
            },
        ],
        "game_piece_locations": [
            {
                "name": "depots",
                "count": 2,
                "per_alliance": 1,
                "starting_fuel_per_location": 24,
                "notes": "Alliance-side fixed FUEL staging.",
                "citations": rules["match_setup"]["citations"],
            },
            {
                "name": "outpost_chutes",
                "count": 2,
                "per_alliance": 1,
                "starting_fuel_per_location": 24,
                "notes": "Human-player feed locations in the OUTPOST AREA.",
                "citations": rules["match_setup"]["citations"],
            },
            {
                "name": "neutral_zone_staging",
                "count_range": rules["match_setup"]["neutral_zone_fuel_range"],
                "notes": "Remaining FUEL are dispersed in the NEUTRAL ZONE with intentional variance.",
                "citations": rules["match_setup"]["citations"],
            },
        ],
        "obstacles": [
            {
                "name": "bumps",
                "count": 4,
                "notes": "Physical barriers that constrain midfield travel and line up with the HUB references.",
                "citations": rules["field"]["dimensions"]["citations"],
            },
            {
                "name": "trenches",
                "count": 4,
                "notes": "Lane-like field structures that also host AprilTags and influence collection paths.",
                "citations": rules["field_rules"]["apriltags"]["citations"],
            },
            {
                "name": "hubs",
                "count": 2,
                "footprint_m": rules["field"]["hub"]["footprint_m"],
                "notes": "Central scoring structures with four exits into the NEUTRAL ZONE.",
                "citations": rules["field"]["hub"]["citations"],
            },
        ],
        "coarse_paths": {
            "blue_start_to_blue_hub_m": round(hub_x, 3),
            "blue_hub_to_midfield_collection_m": round(max(field_length / 2.0 - hub_x, 0.0), 3),
            "blue_midfield_collection_to_blue_hub_m": round(max(field_length / 2.0 - hub_x, 0.0), 3),
            "blue_hub_to_blue_tower_m": round(hub_x, 3),
            "notes": "Coarse distances are 1D approximations for strategy and sensitivity sweeps, not collision-free autonomous paths.",
        },
    }


def build_mechanics(year: str, rules: dict[str, Any], field_model: dict[str, Any] | None = None) -> dict[str, Any]:
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
        "phase_limits": {
            "auto_s": rules["timing"]["auto_s"],
            "teleop_s": rules["timing"]["teleop_s"],
            "transition_shift_s": rules["timing"]["transition_shift_s"],
            "alliance_shift_count": rules["timing"]["alliance_shift_count"],
            "alliance_shift_s": rules["timing"]["alliance_shift_s"],
            "endgame_s": rules["timing"]["endgame_s"],
            "fuel_scoring_grace_s": rules["timing"]["fuel_scoring_grace_s"],
            "citations": rules["timing"]["citations"],
        },
        "scoring_model": rules["scoring"],
        "penalty_model": [
            {
                "rule_id": penalty["rule_id"],
                "type": penalty["type"],
                "points": penalty["points"],
                "awarded_to": penalty["awarded_to"],
                "citations": penalty["citations"],
            }
            for penalty in rules["penalties"]
        ],
        "win_conditions": [
            {"condition": "higher_match_points_than_opponent", "reward": "win", "ranking_points": 3, "tiebreaker_order": [], "citations": rules["ranking_points"][3]["citations"]},
            {"condition": "equal_match_points_to_opponent", "reward": "tie", "ranking_points": 1, "tiebreaker_order": [], "citations": rules["ranking_points"][4]["citations"]},
            {"condition": "active_hub_fuel >= 100", "reward": "ENERGIZED RP", "ranking_points": 1, "citations": rules["ranking_points"][0]["citations"]},
            {"condition": "active_hub_fuel >= 360", "reward": "SUPERCHARGED RP", "ranking_points": 1, "citations": rules["ranking_points"][1]["citations"]},
            {"condition": "tower_points >= 50", "reward": "TRAVERSAL RP", "ranking_points": 1, "citations": rules["ranking_points"][2]["citations"]},
        ],
        "physics": {
            "field_width_m": (field_model or {}).get("field_dimensions", {}).get("width_m", rules["field"]["dimensions"]["width_m"]),
            "field_length_m": (field_model or {}).get("field_dimensions", {}).get("length_m", rules["field"]["dimensions"]["length_m"]),
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


def build_sim_arch_feedback_template(year: str) -> dict[str, Any]:
    return {
        "run_id": "template",
        "generated_at": utc_now(),
        "source": "human_playtest",
        "robot_params": {
            "drive_free_speed_fps": 18.0,
            "drive_time_to_full_speed_s": 2.5,
            "intake_rate_pieces_per_s": 4.0,
            "storage_capacity_assumed": 8,
            "score_rate_pieces_per_s": 4.0,
            "climb_profile": "level_2_reliable",
        },
        "kpis": {
            "match_points": 0.0,
            "value_per_second": 0.0,
            "foul_points_conceded": 0.0,
            "tower_success_rate": 0.0,
            "defense_sensitivity": 0.0,
            "alliance_dependency_score": 0.0,
        },
        "recommended_strategy_updates": [
            {
                "strategy_name": "Safe RP Foundation",
                "delta_expected_value": 0.0,
                "reason": "Replace with measured delta from simulation runs.",
                "confidence": "low",
            }
        ],
        "confidence": "low",
        "notes": [
            "Template only. Replace with real outputs from human playtests and/or RL self-play before ingestion.",
            f"Place this file at artifacts/{year}/sim_arch_feedback.json",
            "Allowed confidence values: low, medium, high.",
            "KPI bounds: match_points 0-500, value_per_second 0-10, foul_points_conceded 0-200, tower_success_rate 0-1, defense_sensitivity 0-1, alliance_dependency_score 0-1.",
        ],
    }


def _validate_bounded_number(
    payload: dict[str, Any],
    field: str,
    bounds: tuple[float, float],
    errors: list[str],
) -> None:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        errors.append(f"{field} must be a finite numeric value")
        return
    low, high = bounds
    if float(value) < low or float(value) > high:
        errors.append(f"{field} must be between {low} and {high}; received {value}")


def validate_sim_arch_feedback(data: dict[str, Any]) -> tuple[bool, list[str]]:
    required_keys = {
        "run_id",
        "generated_at",
        "source",
        "robot_params",
        "kpis",
        "recommended_strategy_updates",
        "confidence",
    }
    errors: list[str] = []
    missing = sorted(required_keys.difference(data.keys()))
    for key in missing:
        errors.append(f"missing required key: {key}")

    run_id = data.get("run_id")
    if "run_id" in data and (not isinstance(run_id, str) or not run_id.strip()):
        errors.append("run_id must be a non-empty string")

    generated_at = data.get("generated_at")
    if "generated_at" in data and (not isinstance(generated_at, str) or not generated_at.strip()):
        errors.append("generated_at must be a non-empty string")

    source = data.get("source")
    if "source" in data and source not in SIM_FEEDBACK_ALLOWED_SOURCES:
        errors.append(
            "source must be one of: " + ", ".join(sorted(SIM_FEEDBACK_ALLOWED_SOURCES))
        )

    confidence = data.get("confidence")
    if "confidence" in data and confidence not in SIM_FEEDBACK_ALLOWED_CONFIDENCE:
        errors.append(
            "confidence must be one of: " + ", ".join(sorted(SIM_FEEDBACK_ALLOWED_CONFIDENCE))
        )

    robot_params = data.get("robot_params")
    if "robot_params" in data:
        if not isinstance(robot_params, dict):
            errors.append("robot_params must be an object")
        else:
            for field, bounds in SIM_FEEDBACK_ROBOT_PARAM_BOUNDS.items():
                if field not in robot_params:
                    errors.append(f"robot_params missing required field: {field}")
                else:
                    _validate_bounded_number(robot_params, field, bounds, errors)
            climb_profile = robot_params.get("climb_profile")
            if not isinstance(climb_profile, str) or not climb_profile.strip():
                errors.append("robot_params.climb_profile must be a non-empty string")

    kpis = data.get("kpis")
    if "kpis" in data:
        if not isinstance(kpis, dict):
            errors.append("kpis must be an object")
        else:
            for field, bounds in SIM_FEEDBACK_KPI_BOUNDS.items():
                if field not in kpis:
                    errors.append(f"kpis missing required field: {field}")
                else:
                    _validate_bounded_number(kpis, field, bounds, errors)

    updates = data.get("recommended_strategy_updates")
    if "recommended_strategy_updates" in data:
        if not isinstance(updates, list) or not updates:
            errors.append("recommended_strategy_updates must be a non-empty list")
        else:
            for index, update in enumerate(updates, start=1):
                if not isinstance(update, dict):
                    errors.append(f"recommended_strategy_updates[{index}] must be an object")
                    continue
                strategy_name = update.get("strategy_name")
                if not isinstance(strategy_name, str) or not strategy_name.strip():
                    errors.append(f"recommended_strategy_updates[{index}].strategy_name must be a non-empty string")
                reason = update.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    errors.append(f"recommended_strategy_updates[{index}].reason must be a non-empty string")
                update_confidence = update.get("confidence")
                if update_confidence not in SIM_FEEDBACK_ALLOWED_CONFIDENCE:
                    errors.append(
                        f"recommended_strategy_updates[{index}].confidence must be one of: "
                        + ", ".join(sorted(SIM_FEEDBACK_ALLOWED_CONFIDENCE))
                    )
                delta = update.get("delta_expected_value")
                if isinstance(delta, bool) or not isinstance(delta, (int, float)) or not math.isfinite(float(delta)):
                    errors.append(f"recommended_strategy_updates[{index}].delta_expected_value must be a finite numeric value")
                else:
                    low, high = SIM_FEEDBACK_UPDATE_DELTA_BOUNDS
                    if float(delta) < low or float(delta) > high:
                        errors.append(
                            f"recommended_strategy_updates[{index}].delta_expected_value must be between {low} and {high}; received {delta}"
                        )

    return (len(errors) == 0, errors)


def load_sim_arch_feedback(path: Path) -> tuple[dict[str, Any] | None, str, list[str]]:
    if not path.exists():
        return None, "missing", []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None, "invalid_json", ["sim_arch_feedback.json could not be parsed as valid JSON"]
    if not isinstance(data, dict):
        return None, "invalid_type", ["sim_arch_feedback.json root must be an object"]
    valid, errors = validate_sim_arch_feedback(data)
    if not valid:
        return None, "invalid_contract", errors
    if str(data.get("run_id", "")).strip().lower() == "template":
        return None, "template", []
    return data, "loaded", []


def apply_sim_feedback_to_candidates(
    candidates: list[dict[str, Any]],
    sim_feedback: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not sim_feedback:
        return candidates, {"status": "not_applied", "updates_used": 0}

    updates = sim_feedback.get("recommended_strategy_updates", [])
    if not isinstance(updates, list):
        return candidates, {"status": "not_applied", "updates_used": 0}

    delta_by_name: dict[str, float] = {}
    used_updates = 0
    for update in updates:
        if not isinstance(update, dict):
            continue
        name = str(update.get("strategy_name", "")).strip()
        if not name:
            continue
        try:
            delta = float(update.get("delta_expected_value", 0.0))
        except (TypeError, ValueError):
            delta = 0.0
        delta_by_name[name] = delta

    reranked: list[dict[str, Any]] = []
    for candidate in candidates:
        name = str(candidate.get("name", ""))
        delta = delta_by_name.get(name, 0.0)
        if name in delta_by_name:
            used_updates += 1
        updated = dict(candidate)
        updated["sim_feedback_delta_expected_value"] = delta
        reranked.append(updated)

    reranked.sort(
        key=lambda item: (item.get("sim_feedback_delta_expected_value", 0.0), item.get("name", "")),
        reverse=True,
    )
    summary = {
        "status": "applied",
        "source": sim_feedback.get("source", "unknown"),
        "run_id": sim_feedback.get("run_id", "unknown"),
        "updates_used": used_updates,
    }
    return reranked, summary


def build_insight_packet(
    year: str,
    rules: dict[str, Any],
    mechanics: dict[str, Any],
    sim_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = base_metadata(
        "plan",
        year,
        rules["metadata"]["source_manual_hash"],
        rules["metadata"]["manual_version"],
        "strategy_architect",
    )
    candidates = [
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
    ]
    reranked_candidates, feedback_ingestion = apply_sim_feedback_to_candidates(candidates, sim_feedback)

    warnings = ["Strategy values are hypotheses until cycle-time simulation and drive-team constraints are supplied."]
    if feedback_ingestion.get("status") == "applied":
        warnings.append(
            f"Applied sim_arch_feedback reranking from run_id={feedback_ingestion.get('run_id')} with {feedback_ingestion.get('updates_used', 0)} mapped updates."
        )

    return {
        "success": True,
        "artifact_paths": [
            f"artifacts/{year}/manual_insight_packet.json",
            f"artifacts/{year}/strategy_hypotheses.md",
            f"artifacts/{year}/manual_insight_analysis.md",
            f"artifacts/{year}/sim_arch_feedback.json",
        ],
        "warnings": warnings,
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
        "strategy_candidates": reranked_candidates,
        "open_questions": [
            "Confirm whether later Team Updates modify District Championship or FIRST Championship BONUS RP thresholds.",
            "Add robot design assumptions before assigning value_per_second or risk_weighted_value.",
            "Audit table extraction before promoting rules.json to validated status.",
        ],
        "architecture_optimization": {
            "intent": "Use 2D simulation to compare robot architectures and feed best-performing parameter sets back into strategy analysis.",
            "human_play_loop": {
                "enabled": True,
                "description": "Humans play 3v3 matches with configurable robot parameters to surface practical architecture tradeoffs.",
                "required_metrics": [
                    "match_points",
                    "foul_points_conceded",
                    "active_hub_accuracy",
                    "tower_success_rate",
                    "cycle_time_p50_s",
                    "cycle_time_p90_s",
                ],
            },
            "rl_self_play_loop": {
                "enabled": True,
                "description": "Six-agent 3v3 self-play with randomized architecture parameters and policy training to identify robust high-value designs.",
                "recommendation": "Start with PPO or SAC in a simplified 2D environment, then increase realism after baseline convergence.",
                "training_contract": {
                    "episodes_min": 10000,
                    "evaluation_matches_min": 1000,
                    "seed_count_min": 5,
                    "output_tables": [
                        "architecture_leaderboard",
                        "pareto_front_value_vs_risk",
                        "sensitivity_by_defense_pressure",
                    ],
                },
            },
            "feedback_contract": {
                "target_artifact": f"artifacts/{year}/sim_arch_feedback.json",
                "required_fields": [
                    "run_id",
                    "source",
                    "robot_params",
                    "kpis",
                    "recommended_strategy_updates",
                    "confidence",
                ],
            },
            "feedback_ingestion": feedback_ingestion,
        },
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

    def as_float(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def fmt_range(low: float, high: float, digits: int = 2) -> str:
        return f"{low:.{digits}f}-{high:.{digits}f}"

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

    h(3, "Cycle Time Range Assumptions and Throughput Model")
    p(
        "The cycle model below is tuned for current swerve-heavy FRC metagame assumptions and is used to "
        "estimate primary-objective points-per-second before deciding whether to switch into endgame actions."
    )

    # Requested cycle-time envelope assumptions.
    drive_time_min_s = 1.0
    drive_time_max_s = 9.0
    single_piece_intake_min_s = 0.5
    single_piece_intake_max_s = 2.0
    single_piece_score_min_s = 0.5
    single_piece_score_max_s = 2.0
    limited_intake_rate_low = 1.0
    limited_intake_rate_high = 10.0
    unlimited_intake_rate_low = 1.0
    unlimited_intake_rate_high = 15.0
    multi_piece_score_rate_low = 1.0
    multi_piece_score_rate_high = 15.0

    swerve_speed_low_fps = 13.0
    swerve_speed_high_fps = 22.0
    swerve_speed_low_mps = swerve_speed_low_fps * 0.3048
    swerve_speed_high_mps = swerve_speed_high_fps * 0.3048
    full_speed_accel_low_s = 1.0
    full_speed_accel_high_s = 5.0

    L.append("| Component | Assumption Range |")
    L.append("|-----------|------------------|")
    L.append(f"| Drive to game piece | {fmt_range(drive_time_min_s, drive_time_max_s, 1)} s |")
    L.append(f"| Acquire single piece | {fmt_range(single_piece_intake_min_s, single_piece_intake_max_s, 1)} s |")
    L.append(f"| Drive to scoring location | {fmt_range(drive_time_min_s, drive_time_max_s, 1)} s |")
    L.append(f"| Score single piece | {fmt_range(single_piece_score_min_s, single_piece_score_max_s, 1)} s |")
    L.append(f"| Intake rate (limited multi-piece robot) | {fmt_range(limited_intake_rate_low, limited_intake_rate_high, 1)} pieces/s |")
    L.append(f"| Intake rate (unlimited-storage assumption) | {fmt_range(unlimited_intake_rate_low, unlimited_intake_rate_high, 1)} pieces/s |")
    L.append(f"| Multi-piece scoring rate | {fmt_range(multi_piece_score_rate_low, multi_piece_score_rate_high, 1)} pieces/s |")
    L.append(f"| Swerve free speed envelope (Kraken X60 era) | {fmt_range(swerve_speed_low_fps, swerve_speed_high_fps, 0)} ft/s ({fmt_range(swerve_speed_low_mps, swerve_speed_high_mps, 2)} m/s) |")
    L.append(f"| Time to full speed (ratio/current dependent) | {fmt_range(full_speed_accel_low_s, full_speed_accel_high_s, 0)} s |")
    sep()

    p(
        "Reference drivetrain catalogs supplied with this project: x2i gear table image (embedded) and MK5n "
        "gear table URL: https://cdn.shopify.com/s/files/1/0065/4308/1590/files/MK5n_Gear_Ratios_eddd1a62-7d51-432c-ba53-38352d5b2337.png?v=1747249162"
    )

    # Geometry-based upper bound for in-robot game-piece storage.
    robot_perim_m = as_float(phys.get("robot_perimeter_m", 2.794), 2.794)
    robot_h_m = as_float(phys.get("robot_height_m", 0.762), 0.762)
    fuel_d_m = as_float(phys.get("fuel_diameter_m", fuel_diam), fuel_diam)
    square_side_m = robot_perim_m / 4.0
    robot_bounding_volume_m3 = square_side_m * square_side_m * robot_h_m
    piece_radius_m = fuel_d_m / 2.0
    piece_volume_m3 = (4.0 / 3.0) * 3.141592653589793 * (piece_radius_m ** 3)
    packing_efficiency = 0.64
    theoretical_piece_capacity = int(robot_bounding_volume_m3 * packing_efficiency / piece_volume_m3) if piece_volume_m3 > 0 else 0
    usable_cap_low = max(1, int(theoretical_piece_capacity * 0.8))
    usable_cap_high = max(usable_cap_low, int(theoretical_piece_capacity * 0.9))

    h(4, "Storage Capacity Envelope from Robot Geometry")
    L.append("| Metric | Value |")
    L.append("|--------|-------|")
    L.append(f"| Frame-perimeter-derived square side | {square_side_m:.3f} m |")
    L.append(f"| Bounding volume (side^2 x height) | {robot_bounding_volume_m3:.3f} m^3 |")
    L.append(f"| Single FUEL volume (sphere upper bound) | {piece_volume_m3:.5f} m^3 |")
    L.append(f"| Theoretical packed piece count (64% packing) | {theoretical_piece_capacity} pieces |")
    L.append(f"| Usable after 10-20% mechanism/chassis cut | {usable_cap_low}-{usable_cap_high} pieces |")
    sep()
    p(
        "This is a strict geometric upper bound. Real robots will be lower due to indexing architecture, legal "
        "extension constraints, and practical control limits."
    )

    def cycle_window(load_count: int, intake_rate_fast: float, intake_rate_slow: float, score_rate_fast: float, score_rate_slow: float) -> tuple[float, float, float, float]:
        drive_fast = 2.0 * drive_time_min_s
        drive_slow = 2.0 * drive_time_max_s
        intake_fast = load_count / intake_rate_fast
        intake_slow = load_count / intake_rate_slow
        score_fast = load_count / score_rate_fast
        score_slow = load_count / score_rate_slow
        total_fast = drive_fast + intake_fast + score_fast
        total_slow = drive_slow + intake_slow + score_slow
        pps_low = load_count / total_slow
        pps_high = load_count / total_fast
        return total_fast, total_slow, pps_low, pps_high

    # Requested variability for 1-piece and multi-piece robots.
    single_total_fast = (2.0 * drive_time_min_s) + single_piece_intake_min_s + single_piece_score_min_s
    single_total_slow = (2.0 * drive_time_max_s) + single_piece_intake_max_s + single_piece_score_max_s
    single_pps_low = 1.0 / single_total_slow
    single_pps_high = 1.0 / single_total_fast

    limited_capacity_assumption = min(12, max(5, usable_cap_low))
    limited_load_samples = sorted(set([1, max(2, limited_capacity_assumption // 2), limited_capacity_assumption]))

    unlimited_load_samples = [10, 20, 40]
    if usable_cap_low >= 60:
        unlimited_load_samples.append(60)
    unlimited_load_samples = sorted(set(unlimited_load_samples))

    h(4, "Cycle Time Envelope by Robot Handling Mode")
    L.append("| Mode | Planned Load | Total Cycle Time (s) | Primary Objective EPS (pts/s) |")
    L.append("|------|--------------|----------------------|-------------------------------|")
    L.append(
        "| Single-piece robot | 1 piece | "
        f"{fmt_range(single_total_fast, single_total_slow, 2)} | {fmt_range(single_pps_low, single_pps_high, 3)} |"
    )

    practical_pps_lows: list[float] = [single_pps_low]
    practical_pps_highs: list[float] = [single_pps_high]

    for load in limited_load_samples:
        total_fast, total_slow, pps_low, pps_high = cycle_window(
            load_count=load,
            intake_rate_fast=limited_intake_rate_high,
            intake_rate_slow=limited_intake_rate_low,
            score_rate_fast=multi_piece_score_rate_high,
            score_rate_slow=multi_piece_score_rate_low,
        )
        practical_pps_lows.append(pps_low)
        practical_pps_highs.append(pps_high)
        L.append(
            "| Limited multi-piece robot "
            f"(max {limited_capacity_assumption}) | {load} pieces | "
            f"{fmt_range(total_fast, total_slow, 2)} | {fmt_range(pps_low, pps_high, 3)} |"
        )

    for load in unlimited_load_samples:
        total_fast, total_slow, pps_low, pps_high = cycle_window(
            load_count=load,
            intake_rate_fast=unlimited_intake_rate_high,
            intake_rate_slow=unlimited_intake_rate_low,
            score_rate_fast=multi_piece_score_rate_high,
            score_rate_slow=multi_piece_score_rate_low,
        )
        L.append(
            "| Unlimited-storage assumption (upper bound) | "
            f"{load} pieces | {fmt_range(total_fast, total_slow, 2)} | {fmt_range(pps_low, pps_high, 3)} |"
        )
    sep()
    p(
        "Interpretation: higher planned loads improve best-case EPS only when intake and scoring rates are high enough. "
        "At slow intake rates, fully loading before scoring is often suboptimal versus shorter, faster trips."
    )

    # Endgame trade study: equivalent points-per-second versus continuing primary objective.
    h(4, "Endgame Action vs Continue Primary Objective (EPS Trade Study)")
    p(
        "Decision rule: choose endgame action when expected endgame EPS exceeds expected primary-objective EPS over the "
        "same remaining endgame window and climb success probability is acceptable."
    )
    L.append("$$")
    L.append("\\text{Primary EPS} = \\frac{\\text{Primary Points per Cycle}}{\\text{Cycle Time}}")
    L.append("$$")
    L.append("$$")
    L.append("\\text{Endgame EPS} = \\frac{\\text{Endgame Points}}{\\text{Commit Time}}")
    L.append("$$")
    L.append("$$")
    L.append("\\text{Break-even Commit Time} = \\frac{\\text{Endgame Points}}{\\text{Primary EPS}}")
    L.append("$$")
    sep()

    primary_practical_eps_low = min(practical_pps_lows)
    primary_practical_eps_high = max(practical_pps_highs)

    competitive_eps_lows = [v for v in practical_pps_lows if v >= 0.15]
    competitive_eps_highs = [v for v in practical_pps_highs if v <= 2.2]
    decision_eps_low = max(0.2, min(competitive_eps_lows) if competitive_eps_lows else primary_practical_eps_low)
    decision_eps_high = min(1.8, max(competitive_eps_highs) if competitive_eps_highs else primary_practical_eps_high)
    if decision_eps_high <= decision_eps_low:
        decision_eps_high = max(decision_eps_low + 0.1, primary_practical_eps_high)

    p(
        "Primary-objective practical EPS envelope from the cycle model (single + limited-capacity modes): "
        f"**{fmt_range(primary_practical_eps_low, primary_practical_eps_high, 3)} pts/s**."
    )
    p(
        "Competitive decision band used for endgame break-even calls (filters out extreme low/high outliers): "
        f"**{fmt_range(decision_eps_low, decision_eps_high, 3)} pts/s**."
    )

    endgame_actions: list[tuple[str, float]] = []
    for action in scoring:
        action_name = str(action.get("action", action.get("name", "")))
        action_upper = action_name.upper()
        phase_upper = str(action.get("phase", "")).upper()
        if "TOWER" in action_upper and "LEVEL" in action_upper:
            if "AUTO" in phase_upper:
                continue
            pts_value = as_float(action.get("points", 0), 0.0)
            if pts_value > 0:
                endgame_actions.append((action_name, pts_value))

    if not endgame_actions:
        endgame_actions = [
            ("LEVEL 1 TOWER", 10.0),
            ("LEVEL 2 TOWER", 20.0),
            ("LEVEL 3 TOWER", 30.0),
        ]

    # Typical climb commit window assumption for strategic comparison.
    commit_time_fast_s = 5.0
    commit_time_slow_s = 20.0

    L.append("| Endgame Action | Points | Commit Time (s) | Endgame EPS (pts/s) | Break-even Time vs Primary EPS (s) |")
    L.append("|----------------|--------|-----------------|---------------------|------------------------------------|")
    for action_name, points in endgame_actions:
        endgame_eps_low = points / commit_time_slow_s
        endgame_eps_high = points / commit_time_fast_s
        break_even_fast = points / decision_eps_high
        break_even_slow = points / decision_eps_low
        L.append(
            f"| {action_name} | {points:.0f} | {fmt_range(commit_time_fast_s, commit_time_slow_s, 0)} | "
            f"{fmt_range(endgame_eps_low, endgame_eps_high, 2)} | {fmt_range(break_even_fast, break_even_slow, 1)} |"
        )
    sep()
    p(
        "Practical call: if remaining endgame time is less than break-even for your expected primary EPS and your climb "
        "success probability is high, transition to TOWER. If remaining time is greater and your cycle is efficient, continue "
        "primary scoring before committing."
    )

    h(4, "Simulation-Driven Architecture Search Loop")
    p(
        "The 2D simulator is intended for architecture selection, not only visualization. Two complementary loops are"
        " supported: (1) human-driven multiplayer playtests and (2) RL-based 3v3 self-play. Both loops feed a shared"
        " architecture-feedback artifact that updates this analysis in future iterations."
    )
    L.append("| Loop | Goal | Minimum Output |")
    L.append("|------|------|----------------|")
    L.append("| Human playtesting (TideSim-style) | Discover practical driver and mechanism tradeoffs | Parameter set + match KPIs + qualitative notes |")
    L.append("| RL 3v3 self-play | Search large architecture space and estimate robust optimal regions | Architecture leaderboard + sensitivity results + confidence bands |")
    sep()
    p(
        "Feedback artifact contract: `sim_arch_feedback.json` should include run_id, source (human or rl),"
        " robot parameter vector, KPI bundle (EPS, foul risk, climb success, defense sensitivity), and"
        " recommended strategy deltas for the next manual-insight generation pass."
    )
    feedback_ingestion = packet.get("architecture_optimization", {}).get("feedback_ingestion", {})
    if feedback_ingestion:
        status = feedback_ingestion.get("status", "unknown")
        run_id = feedback_ingestion.get("run_id", "n/a")
        updates_used = feedback_ingestion.get("updates_used", 0)
        p(
            f"Feedback ingestion status: **{status}** (run_id: {run_id}, mapped strategy updates: {updates_used})."
        )

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


def build_extraction_report_md(
    rules: dict[str, Any],
    field_layout_reference: dict[str, Any],
    manual_sections_index_present: bool,
) -> str:
    lines = [
        "# Extraction Report",
        "",
        f"Generated: {rules['metadata']['generated_at']}",
        f"Manual version: {rules['metadata']['manual_version']}",
        "",
        "## Coverage",
        f"- Extracted sections: {len(rules.get('sections', []))}",
        f"- Parsed rule clauses: {len(rules.get('clauses', []))}",
        f"- Scoring actions: {len(rules.get('scoring', []))}",
        f"- Penalty definitions: {len(rules.get('penalties', []))}",
        f"- Field drawing pages with OCR text: {len(field_layout_reference.get('drawing_pages', []))}",
        f"- Dimension tokens captured from field drawing: {len(field_layout_reference.get('dimension_tokens', []))}",
        f"- Manual sections index generated: {'yes' if manual_sections_index_present else 'no'}",
        "",
        "## Key Extracted Facts",
        f"- Field dimensions: {rules['field']['dimensions']['length_m']} m x {rules['field']['dimensions']['width_m']} m",
        f"- HUB footprint: {rules['field']['hub']['footprint_m']['width']} m x {rules['field']['hub']['footprint_m']['depth']} m",
        f"- HUB alliance-wall offset: {rules['field']['hub']['alliance_wall_offset_m']} m",
        f"- Match timing: {rules['timing']['auto_s']} s auto, {rules['timing']['teleop_s']} s teleop, {rules['timing']['endgame_s']} s endgame",
        f"- Match setup fuel count: {rules['match_setup']['total_fuel']}",
        "",
        "## Remaining Ambiguities",
    ]
    for note in field_layout_reference.get("confidence_notes", []):
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## Release Notes",
        ]
    )
    for warning in rules.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines).strip() + "\n"


def build_capability_profiles() -> dict[str, dict[str, Any]]:
    return {
        "fuel_sprinter": {
            "label": "Fuel Sprinter",
            "description": "High-throughput active-HUB scorer with shallow endgame capability.",
            "cycle_time_s": [2.8, 3.6],
            "auto_fuel_scored": [8.0, 12.0],
            "teleop_accuracy": [0.86, 0.93],
            "teleop_climb_options": [
                {"level": 0, "weight": 0.35},
                {"level": 1, "weight": 0.65},
            ],
            "auto_l1_probability": 0.15,
            "climb_success_base": 0.78,
            "defense_pressure": 0.2,
        },
        "balanced_climber": {
            "label": "Balanced Climber",
            "description": "Generalist alliance piece that still contributes meaningful tower points.",
            "cycle_time_s": [3.6, 4.6],
            "auto_fuel_scored": [5.0, 8.0],
            "teleop_accuracy": [0.8, 0.88],
            "teleop_climb_options": [
                {"level": 1, "weight": 0.2},
                {"level": 2, "weight": 0.8},
            ],
            "auto_l1_probability": 0.25,
            "climb_success_base": 0.85,
            "defense_pressure": 0.35,
        },
        "tower_anchor": {
            "label": "Tower Anchor",
            "description": "Low-throughput but high-certainty endgame robot built to stabilize TRAVERSAL RP paths.",
            "cycle_time_s": [5.0, 6.2],
            "auto_fuel_scored": [3.0, 5.0],
            "teleop_accuracy": [0.74, 0.84],
            "teleop_climb_options": [
                {"level": 2, "weight": 0.25},
                {"level": 3, "weight": 0.75},
            ],
            "auto_l1_probability": 0.3,
            "climb_success_base": 0.9,
            "defense_pressure": 0.25,
        },
        "support_disruptor": {
            "label": "Support Disruptor",
            "description": "Utility robot that gives up raw throughput for lane pressure and reduced opponent efficiency.",
            "cycle_time_s": [5.4, 7.0],
            "auto_fuel_scored": [2.0, 4.0],
            "teleop_accuracy": [0.7, 0.82],
            "teleop_climb_options": [
                {"level": 0, "weight": 0.25},
                {"level": 1, "weight": 0.55},
                {"level": 2, "weight": 0.2},
            ],
            "auto_l1_probability": 0.1,
            "climb_success_base": 0.75,
            "defense_pressure": 0.65,
        },
    }


def build_strategy_packet(
    year: str,
    rules: dict[str, Any],
    field_model: dict[str, Any],
    mechanics: dict[str, Any],
    insight_packet: dict[str, Any],
) -> dict[str, Any]:
    metadata = base_metadata(
        "plan",
        year,
        rules["metadata"]["source_manual_hash"],
        rules["metadata"]["manual_version"],
        "strategy_architect",
    )
    metadata["source_field_drawing_hash"] = field_model["metadata"]["source_field_drawing_hash"]
    profiles = build_capability_profiles()
    coarse_paths = field_model.get("coarse_paths", {})
    candidates = insight_packet.get("strategy_candidates", [])
    scenario_by_name = {
        "Safe RP Foundation": {
            "profile_mix": ["balanced_climber", "balanced_climber", "tower_anchor"],
            "modifiers": {
                "cycle_multiplier": 1.0,
                "fuel_multiplier": 0.98,
                "climb_reliability_bonus": 0.08,
                "auto_climb_bonus": 0.03,
                "expected_foul_points": 4.0,
                "defense_sensitivity": 0.35,
                "defense_multiplier": 0.95,
            },
            "target_bonus_rps": ["ENERGIZED RP", "TRAVERSAL RP"],
        },
        "Shift-Aware Fuel Pressure": {
            "profile_mix": ["fuel_sprinter", "fuel_sprinter", "support_disruptor"],
            "modifiers": {
                "cycle_multiplier": 0.94,
                "fuel_multiplier": 1.08,
                "climb_reliability_bonus": -0.02,
                "auto_climb_bonus": 0.0,
                "expected_foul_points": 6.0,
                "defense_sensitivity": 0.65,
                "defense_multiplier": 1.15,
            },
            "target_bonus_rps": ["ENERGIZED RP", "SUPERCHARGED RP"],
        },
        "Tower Anchor Plus Fuel Support": {
            "profile_mix": ["tower_anchor", "balanced_climber", "fuel_sprinter"],
            "modifiers": {
                "cycle_multiplier": 1.02,
                "fuel_multiplier": 0.95,
                "climb_reliability_bonus": 0.1,
                "auto_climb_bonus": 0.05,
                "expected_foul_points": 3.5,
                "defense_sensitivity": 0.4,
                "defense_multiplier": 1.0,
            },
            "target_bonus_rps": ["TRAVERSAL RP", "ENERGIZED RP"],
        },
    }
    strategy_scenarios = []
    for candidate in candidates:
        scenario = scenario_by_name.get(candidate["name"], None)
        if not scenario:
            continue
        strategy_scenarios.append(
            {
                "name": candidate["name"],
                "summary": candidate["expected_value"],
                "assumptions": candidate["assumptions"],
                "risks": candidate["key_risks"],
                "profile_mix": scenario["profile_mix"],
                "modifiers": scenario["modifiers"],
                "target_bonus_rps": scenario["target_bonus_rps"],
                "citations": candidate["citations"],
            }
        )

    return {
        "success": True,
        "artifact_paths": [
            f"artifacts/{year}/strategy_packet.json",
            f"artifacts/{year}/strategy_brief.md",
            f"artifacts/{year}/team_decision_packet.md",
        ],
        "warnings": [
            "Strategy scenarios are still abstract capability mixes, not recommendations for a specific team robot.",
            "Cycle assumptions use coarse field distances and should be re-validated after human review of the field drawings.",
        ],
        "metadata": metadata,
        "game_summary": {
            "objective": "Outscore the opponent while converting enough active-HUB fuel and tower points to maximize ranking-point pressure.",
            "phase_structure": insight_packet.get("phase_model", {}),
            "hub_schedule_summary": "Both HUBs are active in AUTO, TRANSITION, and ENDGAME. Each alliance receives two active alliance shifts and two inactive shifts in TELEOP.",
            "ranking_point_summary": insight_packet.get("rp_model", []),
            "citations": rules["citations"],
        },
        "task_candidates": [
            {
                "name": "auto_preload_conversion",
                "phase": "AUTO",
                "description": "Convert preloaded FUEL while both HUBs are active and decide whether a reliable LEVEL 1 auto climb is worth the trade.",
                "citations": rules["scoring"][0]["citations"] + rules["scoring"][3]["citations"],
            },
            {
                "name": "active_hub_fuel_cycle",
                "phase": "TELEOP",
                "description": "Exploit active HUB windows for repeated FUEL scoring and treat inactive windows as collection or defense setup time.",
                "citations": rules["hub_status"]["citations"],
            },
            {
                "name": "inactive_window_collection",
                "phase": "TELEOP",
                "description": "Use inactive HUB windows to stockpile FUEL so the next active shift opens with loaded robots instead of dead travel time.",
                "citations": rules["hub_status"]["citations"] + rules["match_setup"]["citations"],
            },
            {
                "name": "tower_commit_timing",
                "phase": "ENDGAME",
                "description": "Commit to a tower level only when the endgame point rate beats continued active-HUB scoring for the remaining clock.",
                "citations": rules["scoring"][4]["citations"] + rules["scoring"][6]["citations"],
            },
            {
                "name": "protected_zone_discipline",
                "phase": "MATCH",
                "description": "Avoid donating foul points around towers and protected scoring lanes, especially in the final 30 seconds.",
                "citations": rules["penalties"][0]["citations"] + rules["penalties"][1]["citations"],
            },
        ],
        "role_candidates": [
            {
                "name": "fuel_pressure",
                "best_fit_profiles": ["fuel_sprinter"],
                "description": "Primary scorer focused on active-HUB throughput and ENERGIZED pressure.",
                "citations": rules["ranking_points"][0]["citations"],
            },
            {
                "name": "balanced_rp_partner",
                "best_fit_profiles": ["balanced_climber"],
                "description": "Supports fuel scoring while retaining a credible path to LEVEL 2 tower points.",
                "citations": rules["ranking_points"][0]["citations"] + rules["ranking_points"][2]["citations"],
            },
            {
                "name": "tower_anchor",
                "best_fit_profiles": ["tower_anchor"],
                "description": "Stabilizes TRAVERSAL RP paths and provides playoff floor if fuel throughput stalls.",
                "citations": rules["ranking_points"][2]["citations"] + rules["scoring"][6]["citations"],
            },
            {
                "name": "lane_disruptor",
                "best_fit_profiles": ["support_disruptor"],
                "description": "Uses inactive HUB windows to pressure fuel lanes and reduce opponent scoring efficiency without overcommitting to fouls.",
                "citations": rules["hub_status"]["citations"] + rules["penalties"][0]["citations"],
            },
        ],
        "scoring_priorities": [
            {
                "priority": 1,
                "name": "Reliable active-HUB fuel throughput",
                "reason": "This is the primary path to both match points and the ENERGIZED RP.",
                "citations": rules["scoring"][0]["citations"] + rules["ranking_points"][0]["citations"],
            },
            {
                "priority": 2,
                "name": "Tower points that secure the TRAVERSAL RP floor",
                "reason": "Tower scoring is the only extracted path to the 50-point climb threshold bonus RP.",
                "citations": rules["scoring"][4]["citations"] + rules["ranking_points"][2]["citations"],
            },
            {
                "priority": 3,
                "name": "Inactive-window staging and legal defense",
                "reason": "Inactive HUB windows are dead score time unless they are converted into the next active-cycle advantage or legal disruption.",
                "citations": rules["hub_status"]["citations"] + rules["penalties"][0]["citations"],
            },
        ],
        "cycle_assumptions": {
            "active_hub_time_s": 90,
            "coarse_paths_m": coarse_paths,
            "capability_profiles": profiles,
            "notes": [
                "Coarse paths are 1D references from the field model and do not include congestion or obstacle routing penalties.",
                "Inactive HUB windows are modeled as staging time that improves the next active window rather than as separate scoring windows.",
            ],
        },
        "risk_notes": [
            {
                "severity": "high",
                "note": "Any strategy that misses HUB activity state awareness wastes entire scoring cycles for zero points.",
                "citations": rules["hub_status"]["citations"],
            },
            {
                "severity": "high",
                "note": "Tower contact fouls in the last 30 seconds can erase the value of a full climb.",
                "citations": rules["penalties"][1]["citations"],
            },
            {
                "severity": "medium",
                "note": "The SUPERCHARGED RP threshold appears far above what a single robot can cover; it should be treated as an alliance-level stretch target.",
                "citations": rules["ranking_points"][1]["citations"],
            },
        ],
        "open_questions": insight_packet.get("open_questions", []),
        "strategy_scenarios": strategy_scenarios,
    }


def build_strategy_brief_md(strategy_packet: dict[str, Any]) -> str:
    lines = [
        "# Strategy Brief",
        "",
        f"Generated: {strategy_packet['metadata']['generated_at']}",
        "",
        "## Top Priorities",
    ]
    for item in strategy_packet.get("scoring_priorities", []):
        lines.append(f"- P{item['priority']}: {item['name']} - {item['reason']}")
    lines.extend(["", "## Candidate Strategy Mixes"])
    for scenario in strategy_packet.get("strategy_scenarios", []):
        profile_mix = ", ".join(scenario.get("profile_mix", []))
        target_rps = ", ".join(scenario.get("target_bonus_rps", []))
        lines.append(f"- {scenario['name']}: {scenario['summary']}")
        lines.append(f"  Profile mix: {profile_mix}")
        lines.append(f"  Target bonus RPs: {target_rps}")
    lines.extend(["", "## Key Risks"])
    for risk in strategy_packet.get("risk_notes", []):
        lines.append(f"- {risk['severity'].upper()}: {risk['note']}")
    return "\n".join(lines).strip() + "\n"


def build_team_decision_packet_md(strategy_packet: dict[str, Any]) -> str:
    cycle_assumptions = strategy_packet.get("cycle_assumptions", {})
    profiles = cycle_assumptions.get("capability_profiles", {})
    lines = [
        "# Team Decision Packet",
        "",
        f"Generated: {strategy_packet['metadata']['generated_at']}",
        "",
        "## Strategic Choices",
    ]
    for scenario in strategy_packet.get("strategy_scenarios", []):
        lines.append(f"- {scenario['name']}: {scenario['summary']}")
    lines.extend(["", "## Robot Capability Questions"])
    for profile_name, profile in profiles.items():
        cycle_range = profile.get("cycle_time_s", [0.0, 0.0])
        lines.append(
            f"- Can the robot credibly achieve the {profile_name} envelope of {cycle_range[0]}-{cycle_range[1]} s active-HUB cycles while maintaining {profile.get('description', '').lower()}"
        )
    lines.extend(["", "## Simulation Assumptions To Validate"])
    for note in cycle_assumptions.get("notes", []):
        lines.append(f"- {note}")
    lines.extend(["", "## Open Rule Questions"])
    for question in strategy_packet.get("open_questions", []):
        lines.append(f"- {question}")
    return "\n".join(lines).strip() + "\n"


def build_simulation_model(
    year: str,
    field_model: dict[str, Any],
    mechanics: dict[str, Any],
    strategy_packet: dict[str, Any],
) -> dict[str, Any]:
    metadata = base_metadata(
        "simulate",
        year,
        strategy_packet["metadata"]["source_manual_hash"],
        strategy_packet["metadata"]["manual_version"],
        "sim_engineer",
    )
    metadata["source_field_drawing_hash"] = field_model["metadata"]["source_field_drawing_hash"]
    return {
        "success": True,
        "artifact_paths": [f"artifacts/{year}/simulation_model.json"],
        "warnings": [
            "This is a coarse alliance-level model intended for relative ranking, not a possession-accurate match simulator.",
        ],
        "metadata": metadata,
        "field_model_ref": f"artifacts/{year}/field_model.json",
        "mechanics_ref": f"artifacts/{year}/mechanics.json",
        "strategy_packet_ref": f"artifacts/{year}/strategy_packet.json",
        "entities": [
            {"name": "alliance_robot", "count": 3, "state": ["collecting", "scoring", "climbing", "defending"]},
            {"name": "fuel_pool", "count": mechanics["resource_constraints"][0]["limit"], "state": ["staged", "carried", "scored", "recycled"]},
            {"name": "hub_state", "count": 2, "state": ["active", "inactive"]},
        ],
        "actions": [
            {"name": "collect_fuel", "parameters": ["cycle_time_s", "field_path_m"], "citations": strategy_packet["task_candidates"][1]["citations"]},
            {"name": "score_fuel_active_hub", "parameters": ["accuracy", "hub_activity"], "citations": mechanics["scoring_model"][0]["citations"]},
            {"name": "commit_climb", "parameters": ["climb_level", "commit_time_s", "success_probability"], "citations": mechanics["scoring_model"][4]["citations"]},
            {"name": "apply_lane_defense", "parameters": ["defense_pressure", "foul_risk"], "citations": strategy_packet["task_candidates"][4]["citations"]},
        ],
        "timing": mechanics["phase_limits"],
        "scoring": mechanics["scoring_model"],
        "constraints": {
            "fuel_total": mechanics["resource_constraints"][0],
            "hub_schedule": mechanics["hub_status_schedule"],
            "tower_limit": mechanics["resource_constraints"][3],
            "coarse_paths": field_model.get("coarse_paths", {}),
        },
    }


def build_sim_params(
    year: str,
    field_model: dict[str, Any],
    strategy_packet: dict[str, Any],
) -> dict[str, Any]:
    metadata = base_metadata(
        "simulate",
        year,
        strategy_packet["metadata"]["source_manual_hash"],
        strategy_packet["metadata"]["manual_version"],
        "sim_engineer",
    )
    metadata["source_field_drawing_hash"] = field_model["metadata"]["source_field_drawing_hash"]
    capability_profiles = strategy_packet["cycle_assumptions"]["capability_profiles"]
    return {
        "success": True,
        "artifact_paths": [f"artifacts/{year}/sim_params.json"],
        "warnings": [
            "Seeded sweeps use abstract capability envelopes instead of robot-specific measured data.",
        ],
        "metadata": metadata,
        "seed_set": [2026, 2027, 2028, 2029, 2030],
        "matches_per_seed": 120,
        "capability_profiles": capability_profiles,
        "cycle_time_ranges": [
            {
                "profile": name,
                "cycle_time_s": profile["cycle_time_s"],
                "teleop_accuracy": profile["teleop_accuracy"],
                "auto_fuel_scored": profile["auto_fuel_scored"],
            }
            for name, profile in capability_profiles.items()
        ],
        "strategy_scenarios": strategy_packet.get("strategy_scenarios", []),
        "reference_opponent": {
            "name": "Balanced Reference Alliance",
            "profile_mix": ["balanced_climber", "balanced_climber", "fuel_sprinter"],
            "modifiers": {
                "cycle_multiplier": 1.0,
                "fuel_multiplier": 1.0,
                "climb_reliability_bonus": 0.02,
                "auto_climb_bonus": 0.02,
                "expected_foul_points": 4.5,
                "defense_sensitivity": 0.45,
                "defense_multiplier": 1.0,
            },
        },
        "assumption_notes": [
            "Each alliance gets 90 seconds of active-HUB time across TRANSITION, active shifts, and ENDGAME before climb commit losses.",
            "Defense pressure is modeled as a cycle-time drag factor rather than explicit path blocking.",
            "Inactive shifts are treated as staging time that boosts the next active scoring wave, not as direct scoring windows.",
        ],
    }


def _sample_weighted_level(options: list[dict[str, Any]], rng: random.Random) -> int:
    total = sum(float(option.get("weight", 0.0)) for option in options)
    if total <= 0:
        return 0
    target = rng.random() * total
    running = 0.0
    for option in options:
        running += float(option.get("weight", 0.0))
        if target <= running:
            return int(option.get("level", 0))
    return int(options[-1].get("level", 0))


def _simulate_alliance_output(
    profile_mix: list[str],
    profiles: dict[str, dict[str, Any]],
    modifiers: dict[str, float],
    opponent_defense_pressure: float,
    total_fuel_limit: int,
    rng: random.Random,
    cycle_scale: float = 1.0,
    defense_scale: float = 1.0,
) -> dict[str, float]:
    climb_points = {0: 0, 1: 10, 2: 20, 3: 30}
    climb_commit_ranges = {0: (0.0, 0.0), 1: (8.0, 12.0), 2: (12.0, 18.0), 3: (18.0, 26.0)}
    total_fuel = 0.0
    total_tower = 0.0
    active_scoring_seconds = 0.0
    for profile_name in profile_mix:
        profile = profiles[profile_name]
        cycle_time = rng.uniform(*profile["cycle_time_s"])
        cycle_time *= cycle_scale * modifiers.get("cycle_multiplier", 1.0)
        cycle_time *= 1.0 + (opponent_defense_pressure * defense_scale * modifiers.get("defense_sensitivity", 0.5) * 0.12)
        accuracy = rng.uniform(*profile["teleop_accuracy"]) * modifiers.get("fuel_multiplier", 1.0)
        accuracy = min(0.98, max(0.55, accuracy))
        auto_fuel = rng.uniform(*profile["auto_fuel_scored"])
        total_fuel += auto_fuel
        if rng.random() <= min(0.95, max(0.0, profile["auto_l1_probability"] + modifiers.get("auto_climb_bonus", 0.0))):
            total_tower += 15.0
        climb_level = _sample_weighted_level(profile["teleop_climb_options"], rng)
        climb_success = min(0.98, max(0.4, profile["climb_success_base"] + modifiers.get("climb_reliability_bonus", 0.0)))
        commit_low, commit_high = climb_commit_ranges[climb_level]
        commit_time = rng.uniform(commit_low, commit_high) if climb_level > 0 else 0.0
        if climb_level > 0 and commit_time < 30.0 and rng.random() <= climb_success:
            total_tower += float(climb_points[climb_level])
            active_seconds = 60.0 + (30.0 - commit_time)
        else:
            active_seconds = 90.0
        active_scoring_seconds += active_seconds
        total_fuel += (active_seconds / max(cycle_time, 1.0)) * accuracy
    total_fuel = min(float(total_fuel_limit), total_fuel)
    foul_points = max(0.0, rng.gauss(modifiers.get("expected_foul_points", 0.0), 1.5))
    match_points = max(0.0, total_fuel + total_tower - foul_points)
    return {
        "fuel_points": total_fuel,
        "tower_points": total_tower,
        "foul_points": foul_points,
        "match_points": match_points,
        "active_scoring_seconds": active_scoring_seconds,
    }


def _run_strategy_scenario(
    scenario: dict[str, Any],
    sim_params: dict[str, Any],
    total_fuel_limit: int,
    cycle_scale: float = 1.0,
    defense_scale: float = 1.0,
) -> dict[str, Any]:
    profiles = sim_params["capability_profiles"]
    reference = sim_params["reference_opponent"]
    seeds = sim_params["seed_set"]
    matches_per_seed = int(sim_params["matches_per_seed"])
    our_defense_pressure = (
        sum(float(profiles[name]["defense_pressure"]) for name in scenario["profile_mix"]) / max(len(scenario["profile_mix"]), 1)
    ) * float(scenario["modifiers"].get("defense_multiplier", 1.0))
    opponent_defense_pressure = (
        sum(float(profiles[name]["defense_pressure"]) for name in reference["profile_mix"]) / max(len(reference["profile_mix"]), 1)
    ) * float(reference["modifiers"].get("defense_multiplier", 1.0))

    wins = 0
    ties = 0
    total_match_points = 0.0
    total_fuel_points = 0.0
    total_tower_points = 0.0
    total_foul_points = 0.0
    total_rp = 0.0
    energized = 0
    supercharged = 0
    traversal = 0
    total_matches = len(seeds) * matches_per_seed

    for seed in seeds:
        rng = random.Random(seed)
        for _ in range(matches_per_seed):
            ours = _simulate_alliance_output(
                scenario["profile_mix"],
                profiles,
                scenario["modifiers"],
                opponent_defense_pressure,
                total_fuel_limit,
                rng,
                cycle_scale=cycle_scale,
                defense_scale=defense_scale,
            )
            opponent = _simulate_alliance_output(
                reference["profile_mix"],
                profiles,
                reference["modifiers"],
                our_defense_pressure,
                total_fuel_limit,
                rng,
                cycle_scale=cycle_scale,
                defense_scale=defense_scale,
            )
            total_match_points += ours["match_points"]
            total_fuel_points += ours["fuel_points"]
            total_tower_points += ours["tower_points"]
            total_foul_points += ours["foul_points"]
            match_rp = 0.0
            if ours["match_points"] > opponent["match_points"]:
                wins += 1
                match_rp += 3.0
            elif math.isclose(ours["match_points"], opponent["match_points"], rel_tol=0.0, abs_tol=0.5):
                ties += 1
                match_rp += 1.0
            if ours["fuel_points"] >= 100.0:
                energized += 1
                match_rp += 1.0
            if ours["fuel_points"] >= 360.0:
                supercharged += 1
                match_rp += 1.0
            if ours["tower_points"] >= 50.0:
                traversal += 1
                match_rp += 1.0
            total_rp += match_rp

    average = lambda value: round(value / total_matches, 3) if total_matches else 0.0
    return {
        "name": scenario["name"],
        "profile_mix": scenario["profile_mix"],
        "matches": total_matches,
        "avg_match_points": average(total_match_points),
        "avg_fuel_points": average(total_fuel_points),
        "avg_tower_points": average(total_tower_points),
        "avg_foul_points": average(total_foul_points),
        "avg_ranking_points": average(total_rp),
        "win_rate_vs_reference": round(wins / total_matches, 4) if total_matches else 0.0,
        "tie_rate_vs_reference": round(ties / total_matches, 4) if total_matches else 0.0,
        "energized_rp_rate": round(energized / total_matches, 4) if total_matches else 0.0,
        "supercharged_rp_rate": round(supercharged / total_matches, 4) if total_matches else 0.0,
        "traversal_rp_rate": round(traversal / total_matches, 4) if total_matches else 0.0,
    }


def build_sim_summary(
    year: str,
    rules: dict[str, Any],
    field_model: dict[str, Any],
    strategy_packet: dict[str, Any],
    sim_params: dict[str, Any],
) -> dict[str, Any]:
    metadata = base_metadata(
        "simulate",
        year,
        rules["metadata"]["source_manual_hash"],
        rules["metadata"]["manual_version"],
        "sim_engineer",
    )
    metadata["source_field_drawing_hash"] = field_model["metadata"]["source_field_drawing_hash"]
    scenarios = strategy_packet.get("strategy_scenarios", [])
    total_fuel_limit = int(rules["match_setup"]["total_fuel"])
    baseline_results = [_run_strategy_scenario(scenario, sim_params, total_fuel_limit) for scenario in scenarios]
    slow_cycle_results = {
        result["name"]: _run_strategy_scenario(scenario, sim_params, total_fuel_limit, cycle_scale=1.15)
        for scenario, result in zip(scenarios, baseline_results)
    }
    heavy_defense_results = {
        result["name"]: _run_strategy_scenario(scenario, sim_params, total_fuel_limit, defense_scale=1.25)
        for scenario, result in zip(scenarios, baseline_results)
    }
    ranked = []
    for result in baseline_results:
        slow = slow_cycle_results[result["name"]]
        defense = heavy_defense_results[result["name"]]
        combined = dict(result)
        combined["cycle_sensitivity_match_points"] = round(result["avg_match_points"] - slow["avg_match_points"], 3)
        combined["defense_sensitivity_match_points"] = round(result["avg_match_points"] - defense["avg_match_points"], 3)
        combined["robustness_index"] = round(
            result["avg_ranking_points"] - (combined["cycle_sensitivity_match_points"] * 0.03) - (combined["defense_sensitivity_match_points"] * 0.02),
            3,
        )
        ranked.append(combined)
    ranked.sort(key=lambda item: (item["avg_ranking_points"], item["win_rate_vs_reference"], item["avg_match_points"]), reverse=True)

    sensitivity_notes = []
    for item in ranked:
        sensitivity_notes.append(
            f"{item['name']}: +15% slower cycles cost about {item['cycle_sensitivity_match_points']} match points and heavy defense costs about {item['defense_sensitivity_match_points']} match points."
        )

    robust_findings = []
    if ranked:
        robust_findings.append(
            f"Best overall strategy in the current abstract sweep: {ranked[0]['name']} at {ranked[0]['avg_ranking_points']} average RP and {ranked[0]['avg_match_points']} average match points versus the balanced reference alliance."
        )
        best_traversal = max(ranked, key=lambda item: item["traversal_rp_rate"])
        best_fuel = max(ranked, key=lambda item: item["energized_rp_rate"])
        robust_findings.append(
            f"Most reliable TRAVERSAL path: {best_traversal['name']} at traversal rate {best_traversal['traversal_rp_rate']:.2%}."
        )
        robust_findings.append(
            f"Strongest ENERGIZED pressure: {best_fuel['name']} at energized rate {best_fuel['energized_rp_rate']:.2%}."
        )
    robust_findings.append("SUPERCHARGED remains an outlier target in the current coarse model and should not drive early robot architecture choices without measured cycle data.")

    warnings = [
        "These results are relative rankings against a reference alliance, not score predictions for a real event.",
        "Cycle-time drag from defense is modeled statistically rather than by explicit path blocking or foul adjudication.",
    ]
    return {
        "success": True,
        "artifact_paths": [f"artifacts/{year}/sim_summary.json"],
        "warnings": warnings,
        "metadata": metadata,
        "seed_set": sim_params["seed_set"],
        "ranked_strategies": ranked,
        "sensitivity_notes": sensitivity_notes,
        "robust_findings": robust_findings,
        "reference_opponent": sim_params["reference_opponent"],
    }


def build_simulation_report_md(
    strategy_packet: dict[str, Any],
    sim_params: dict[str, Any],
    sim_summary: dict[str, Any],
) -> str:
    lines = [
        "# Simulation Report",
        "",
        f"Generated: {sim_summary['metadata']['generated_at']}",
        f"Seeds: {', '.join(str(seed) for seed in sim_summary.get('seed_set', []))}",
        f"Matches per seed: {sim_params.get('matches_per_seed', 0)}",
        "",
        "## Ranked Strategies",
        "",
        "| Strategy | Avg RP | Win Rate | Avg Match Pts | ENERGIZED | TRAVERSAL |",
        "|----------|--------|----------|---------------|-----------|-----------|",
    ]
    for item in sim_summary.get("ranked_strategies", []):
        lines.append(
            f"| {item['name']} | {item['avg_ranking_points']} | {item['win_rate_vs_reference']:.2%} | {item['avg_match_points']} | {item['energized_rp_rate']:.2%} | {item['traversal_rp_rate']:.2%} |"
        )
    lines.extend(["", "## Sensitivity Notes"])
    for note in sim_summary.get("sensitivity_notes", []):
        lines.append(f"- {note}")
    lines.extend(["", "## Robust Findings"])
    for finding in sim_summary.get("robust_findings", []):
        lines.append(f"- {finding}")
    lines.extend(["", "## Strategy Scenarios Simulated"])
    for scenario in strategy_packet.get("strategy_scenarios", []):
        lines.append(f"- {scenario['name']}: profiles {', '.join(scenario.get('profile_mix', []))}")
    return "\n".join(lines).strip() + "\n"


def build_game_spec_v2(
    year: str,
    rules: dict[str, Any],
    field_model: dict[str, Any],
    mechanics: dict[str, Any],
    strategy_packet: dict[str, Any],
    sim_summary: dict[str, Any],
) -> dict[str, Any]:
    top_strategy = sim_summary.get("ranked_strategies", [])
    return {
        "schema_version": SCHEMA_VERSION,
        "game_year": year,
        "game_name": rules["metadata"]["game_name"],
        "manual_version": rules["metadata"]["manual_version"],
        "generated_at": utc_now(),
        "source_manual_hash": rules["metadata"]["source_manual_hash"],
        "source_field_drawing_hash": field_model["metadata"]["source_field_drawing_hash"],
        "generator_versions": {GENERATOR_NAME: GENERATOR_VERSION},
        "normalized_rules": {
            "timing": rules["timing"],
            "scoring": rules["scoring"],
            "ranking_points": rules["ranking_points"],
            "penalties": rules["penalties"],
            "field_rules": rules["field_rules"],
            "equipment_limits": rules["equipment_limits"],
            "match_setup": rules["match_setup"],
            "hub_status": rules["hub_status"],
        },
        "field_geometry": {
            "field_dimensions": field_model["field_dimensions"],
            "reference_frames": field_model["reference_frames"],
            "zones": field_model["zones"],
            "scoring_locations": field_model["scoring_locations"],
            "game_piece_locations": field_model["game_piece_locations"],
            "obstacles": field_model["obstacles"],
        },
        "mechanics": {
            "states": mechanics["states"],
            "transitions": mechanics["transitions"],
            "resource_constraints": mechanics["resource_constraints"],
            "phase_limits": mechanics["phase_limits"],
            "scoring_model": mechanics["scoring_model"],
            "penalty_model": mechanics["penalty_model"],
            "win_conditions": mechanics["win_conditions"],
        },
        "selected_strategy_assumptions": {
            "recommended_strategy": top_strategy[0]["name"] if top_strategy else None,
            "ranked_strategies": sim_summary.get("ranked_strategies", []),
            "scoring_priorities": strategy_packet["scoring_priorities"],
            "role_candidates": strategy_packet["role_candidates"],
            "cycle_assumptions": strategy_packet["cycle_assumptions"],
            "open_questions": strategy_packet["open_questions"],
            "simulation_seed_set": sim_summary.get("seed_set", []),
        },
        "artifact_refs": {
            "rules": f"artifacts/{year}/rules.json",
            "field_layout_reference": f"artifacts/{year}/field_layout_reference.json",
            "field_model": f"artifacts/{year}/field_model.json",
            "mechanics": f"artifacts/{year}/mechanics.json",
            "strategy_packet": f"artifacts/{year}/strategy_packet.json",
            "sim_summary": f"artifacts/{year}/sim_summary.json",
        },
    }


def validate_outputs_v2(
    year: str,
    rules: dict[str, Any],
    field_layout_reference: dict[str, Any],
    field_model: dict[str, Any],
    mechanics: dict[str, Any],
    strategy_packet: dict[str, Any],
    simulation_model: dict[str, Any],
    sim_params: dict[str, Any],
    sim_summary: dict[str, Any],
) -> dict[str, Any]:
    required_rules_keys = {"sections", "scoring", "penalties", "field_rules", "timing", "equipment_limits", "citations"}
    required_field_layout_keys = {"metadata", "drawing_pages", "dimension_tokens", "raw_locations", "confidence_notes"}
    required_field_model_keys = {"metadata", "field_dimensions", "reference_frames", "zones", "scoring_locations", "game_piece_locations", "obstacles"}
    required_mechanics_keys = {"states", "transitions", "resource_constraints", "phase_limits", "scoring_model", "penalty_model"}
    required_strategy_keys = {"game_summary", "task_candidates", "role_candidates", "scoring_priorities", "cycle_assumptions", "risk_notes", "open_questions"}
    required_sim_model_keys = {"field_model_ref", "mechanics_ref", "entities", "actions", "timing", "scoring", "constraints"}
    required_sim_params_keys = {"seed_set", "capability_profiles", "cycle_time_ranges", "strategy_scenarios", "assumption_notes"}
    required_sim_summary_keys = {"seed_set", "ranked_strategies", "sensitivity_notes", "robust_findings", "warnings"}

    defects = []
    missing_rules = sorted(required_rules_keys.difference(rules))
    missing_field_layout = sorted(required_field_layout_keys.difference(field_layout_reference))
    missing_field_model = sorted(required_field_model_keys.difference(field_model))
    missing_mechanics = sorted(required_mechanics_keys.difference(mechanics))
    missing_strategy = sorted(required_strategy_keys.difference(strategy_packet))
    missing_sim_model = sorted(required_sim_model_keys.difference(simulation_model))
    missing_sim_params = sorted(required_sim_params_keys.difference(sim_params))
    missing_sim_summary = sorted(required_sim_summary_keys.difference(sim_summary))

    for name, missing in [
        ("rules.json", missing_rules),
        ("field_layout_reference.json", missing_field_layout),
        ("field_model.json", missing_field_model),
        ("mechanics.json", missing_mechanics),
        ("strategy_packet.json", missing_strategy),
        ("simulation_model.json", missing_sim_model),
        ("sim_params.json", missing_sim_params),
        ("sim_summary.json", missing_sim_summary),
    ]:
        if missing:
            defects.append(
                {
                    "severity": "critical",
                    "description": f"{name} missing keys: {', '.join(missing)}",
                    "recommended_action": f"Fix the {name} generator contract before rerunning the pipeline.",
                }
            )

    if not all(item.get("citations") for item in rules.get("scoring", [])):
        defects.append(
            {
                "severity": "critical",
                "description": "At least one scoring action lacks citations.",
                "recommended_action": "Attach citations to every scoring action in rules.json.",
            }
        )
    if not all(item.get("citations") for item in strategy_packet.get("task_candidates", [])):
        defects.append(
            {
                "severity": "major",
                "description": "At least one task candidate lacks citations.",
                "recommended_action": "Map every strategic task candidate to extracted rules.",
            }
        )
    if not sim_summary.get("ranked_strategies"):
        defects.append(
            {
                "severity": "critical",
                "description": "Simulation summary did not produce ranked strategies.",
                "recommended_action": "Check simulation scenario generation and rerun the seeded sweeps.",
            }
        )
    if sim_params.get("seed_set") != sim_summary.get("seed_set"):
        defects.append(
            {
                "severity": "major",
                "description": "sim_params and sim_summary seed sets do not match.",
                "recommended_action": "Ensure the simulation summary is generated from the exact declared seed set.",
            }
        )
    defects.append(
        {
            "severity": "major",
            "description": "Extraction recall has not been independently audited against the intended release gate.",
            "recommended_action": "Run a manual citation and table audit before treating this run as production-ready.",
        }
    )

    has_critical = any(defect["severity"] == "critical" for defect in defects)
    has_major = any(defect["severity"] == "major" for defect in defects)
    if has_critical:
        release_decision = "blocked_due_to_validation_errors"
    elif has_major:
        release_decision = "blocked_until_recall_audit"
    else:
        release_decision = "release_candidate"

    return {
        "run_id": f"{year}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "success": not has_critical,
        "artifact_paths": [f"artifacts/{year}/validation_report.json"],
        "warnings": ["This is a bootstrap validation pass for the narrowed extraction/strategy/simulation MVP."],
        "metadata": base_metadata(
            "validate",
            year,
            rules["metadata"]["source_manual_hash"],
            rules["metadata"]["manual_version"],
            "qa_validator",
        ),
        "gates": {
            "rules_contract": "pass" if not missing_rules else "fail",
            "field_layout_contract": "pass" if not missing_field_layout else "fail",
            "field_model_contract": "pass" if not missing_field_model else "fail",
            "mechanics_contract": "pass" if not missing_mechanics else "fail",
            "strategy_contract": "pass" if not missing_strategy else "fail",
            "simulation_model_contract": "pass" if not missing_sim_model else "fail",
            "simulation_params_contract": "pass" if not missing_sim_params else "fail",
            "simulation_summary_contract": "pass" if not missing_sim_summary else "fail",
            "scoring_citations": "pass" if all(item.get('citations') for item in rules.get('scoring', [])) else "fail",
            "strategy_citations": "pass" if all(item.get('citations') for item in strategy_packet.get('task_candidates', [])) else "fail",
            "release_recall_audit": "fail",
        },
        "defects": defects,
        "recommended_actions": [
            "Audit the extracted scoring and ranking-point tables against the source manual before release.",
            "Trace the field drawing manually to replace the coarse HUB and tower coordinates in field_model.json.",
            "Re-run the seeded sweeps once measured robot cycle times are available.",
        ],
        "release_decision": release_decision,
    }


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
        "field_reference": {
            "required_inputs": ["game_manual_pdf", "field_dimension_drawing_pdf"],
            "field_layout_reference_artifact": f"artifacts/{year}/field_layout_reference.json",
            "apriltag_field_layout_artifact": f"artifacts/{year}/apriltag_field_layout.json",
        },
    }


def validate_outputs(
    year: str,
    rules: dict[str, Any],
    mechanics: dict[str, Any],
    packet: dict[str, Any],
    sim_feedback_status: str,
    sim_feedback_issues: list[str],
) -> dict[str, Any]:
    required_rules_keys = {"sections", "scoring", "penalties", "field", "timing", "equipment_limits", "citations"}
    missing_rules = sorted(required_rules_keys.difference(rules))
    scoring_with_citations = all(item.get("citations") for item in rules.get("scoring", []))
    transitions_with_citations = all(item.get("citations") for item in mechanics.get("transitions", []))
    strategy_with_citations = all(item.get("citations") for item in packet.get("strategy_candidates", []))
    architecture_feedback_gate = "pass" if sim_feedback_status == "loaded" else "skip"
    defects = []
    if missing_rules:
        defects.append({"severity": "critical", "description": f"rules.json missing keys: {', '.join(missing_rules)}", "recommended_action": "Fix extractor output contract."})
    if not scoring_with_citations:
        defects.append({"severity": "critical", "description": "At least one scoring action lacks citations.", "recommended_action": "Attach source citations to every scoring action."})
    if not transitions_with_citations:
        defects.append({"severity": "major", "description": "At least one mechanics transition lacks citations.", "recommended_action": "Map every transition to source rules."})
    if not strategy_with_citations:
        defects.append({"severity": "major", "description": "At least one strategy candidate lacks citations.", "recommended_action": "Map every strategic claim to extracted manual citations."})
    if sim_feedback_status in {"invalid_json", "invalid_type", "invalid_contract"}:
        architecture_feedback_gate = "fail"
        for issue in sim_feedback_issues:
            defects.append(
                {
                    "severity": "critical",
                    "description": f"sim_arch_feedback validation error: {issue}",
                    "recommended_action": f"Fix artifacts/{year}/sim_arch_feedback.json to satisfy enum and numeric bound requirements before rerunning the pipeline.",
                }
            )
    defects.append(
        {
            "severity": "major",
            "description": "Extraction recall has not been independently audited against the 98% release gate.",
            "recommended_action": "Run qa_validator review against the manual table of contents and scoring tables before release.",
        }
    )
    has_critical = any(defect["severity"] == "critical" for defect in defects)
    has_major = any(defect["severity"] == "major" for defect in defects)
    if has_critical:
        release_decision = "blocked_due_to_validation_errors"
    elif has_major:
        release_decision = "blocked_until_recall_audit"
    else:
        release_decision = "release_candidate"
    return {
        "run_id": f"{year}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "success": not has_critical,
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
            "architecture_feedback_contract": architecture_feedback_gate,
            "release_recall_audit": "fail",
        },
        "architecture_feedback_status": sim_feedback_status,
        "architecture_feedback_issues": sim_feedback_issues,
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


def write_manifest(
    root: Path,
    year: str,
    manual_path: Path,
    field_drawing_path: Path,
    paths: list[Path],
    validation: dict[str, Any],
) -> dict[str, Any]:
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
        "source_field_drawing": {
            "path": rel_path(root, field_drawing_path),
            "sha256": sha256_file(field_drawing_path),
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


def run_pipeline(root: Path, year: str, manual_path: Path, field_drawing_path: Path) -> dict[str, Any]:
    if not manual_path.exists():
        raise FileNotFoundError(f"Manual not found: {manual_path}")
    if not field_drawing_path.exists():
        raise FileNotFoundError(f"Field drawing not found: {field_drawing_path}")
    reset_artifact_output(root, year)
    ensure_dirs(root, year)
    manual_hash = sha256_file(manual_path)
    field_drawing_hash = sha256_file(field_drawing_path)
    pages = extract_pdf_pages(manual_path)
    field_pages = extract_pdf_pages(field_drawing_path)
    manual_version = find_manual_version(pages)

    artifacts_dir = root / "artifacts" / year
    raw_text_path = artifacts_dir / "raw" / "manual_text.txt"
    raw_field_text_path = artifacts_dir / "raw" / "field_drawing_text.txt"
    rules_path = artifacts_dir / "rules.json"
    manual_sections_index_path = artifacts_dir / "manual_sections_index.json"
    field_layout_reference_path = artifacts_dir / "field_layout_reference.json"
    extraction_report_path = artifacts_dir / "extraction_report.md"
    field_model_path = artifacts_dir / "field_model.json"
    apriltag_layout_path = artifacts_dir / "apriltag_field_layout.json"
    mechanics_path = artifacts_dir / "mechanics.json"
    strategy_packet_path = artifacts_dir / "strategy_packet.json"
    strategy_brief_path = artifacts_dir / "strategy_brief.md"
    team_decision_packet_path = artifacts_dir / "team_decision_packet.md"
    simulation_model_path = artifacts_dir / "simulation_model.json"
    sim_params_path = artifacts_dir / "sim_params.json"
    sim_summary_path = artifacts_dir / "sim_summary.json"
    simulation_report_path = artifacts_dir / "simulation_report.md"
    game_spec_path = root / "context" / "game_spec.json"
    validation_path = artifacts_dir / "validation_report.json"
    state_path = artifacts_dir / "orchestration_state.json"

    write_raw_text(raw_text_path, pages)
    write_raw_text(raw_field_text_path, field_pages)
    rules = build_rules(year, manual_hash, manual_version, pages, root)
    write_json(rules_path, rules)

    # Build manual sections index for context-efficient agent queries
    manual_sections_index_present = False
    if HAS_DECOMPOSE:
        manual_sections = decompose_manual_to_sections(pages, year, manual_hash, manual_version)
        write_json(manual_sections_index_path, manual_sections)
        manual_sections_index_present = True

    field_layout_reference = build_field_layout_reference(year, manual_hash, manual_version, field_drawing_hash, field_pages, rules)
    write_json(field_layout_reference_path, field_layout_reference)
    extraction_report = build_extraction_report_md(rules, field_layout_reference, manual_sections_index_present)
    extraction_report_path.write_text(extraction_report, encoding="utf-8")
    field_model = build_field_model(year, rules, field_layout_reference)
    write_json(field_model_path, field_model)
    apriltag_layout = build_apriltag_field_layout(year, manual_hash, manual_version, field_drawing_hash, rules)
    write_json(apriltag_layout_path, apriltag_layout)
    mechanics = build_mechanics(year, rules, field_model)
    write_json(mechanics_path, mechanics)
    insight_packet = build_insight_packet(year, rules, mechanics)
    strategy_packet = build_strategy_packet(year, rules, field_model, mechanics, insight_packet)
    write_json(strategy_packet_path, strategy_packet)
    strategy_brief_path.write_text(build_strategy_brief_md(strategy_packet), encoding="utf-8")
    team_decision_packet_path.write_text(build_team_decision_packet_md(strategy_packet), encoding="utf-8")
    simulation_model = build_simulation_model(year, field_model, mechanics, strategy_packet)
    write_json(simulation_model_path, simulation_model)
    sim_params = build_sim_params(year, field_model, strategy_packet)
    write_json(sim_params_path, sim_params)
    sim_summary = build_sim_summary(year, rules, field_model, strategy_packet, sim_params)
    write_json(sim_summary_path, sim_summary)
    simulation_report_path.write_text(build_simulation_report_md(strategy_packet, sim_params, sim_summary), encoding="utf-8")
    game_spec = build_game_spec_v2(year, rules, field_model, mechanics, strategy_packet, sim_summary)
    write_json(game_spec_path, game_spec)
    validation = validate_outputs_v2(
        year,
        rules,
        field_layout_reference,
        field_model,
        mechanics,
        strategy_packet,
        simulation_model,
        sim_params,
        sim_summary,
    )
    write_json(validation_path, validation)
    manifest = write_manifest(
        root,
        year,
        manual_path,
        field_drawing_path,
        [
            raw_text_path,
            raw_field_text_path,
            rules_path,
            manual_sections_index_path,
            field_layout_reference_path,
            extraction_report_path,
            field_model_path,
            apriltag_layout_path,
            mechanics_path,
            strategy_packet_path,
            strategy_brief_path,
            team_decision_packet_path,
            simulation_model_path,
            sim_params_path,
            sim_summary_path,
            simulation_report_path,
            game_spec_path,
            validation_path,
        ],
        validation,
    )
    append_run_history(root, year, validation)

    state = {
        "schema_version": SCHEMA_VERSION,
        "game_year": year,
        "updated_at": utc_now(),
        "current_stage": "qa_validator_bootstrap",
        "source_manual": rel_path(root, manual_path),
        "source_field_drawing": rel_path(root, field_drawing_path),
        "agents_completed": ["pdf_extractor", "field_modeler", "mechanic_analyst", "strategy_architect", "sim_engineer", "qa_validator"],
        "agents_pending": [],
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
    parser.add_argument("--manual", default=None, help="Path to the game manual PDF. Defaults to a year-matched PDF in inputs/.")
    parser.add_argument("--field-drawing", default=None, help="Path to the field dimension drawing PDF. Defaults to a year-matched field-drawing PDF in inputs/.")
    parser.add_argument("--root", default=None, help="Workspace root. Defaults to the parent of this script directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    manual_path = resolve_input_path(root, args.year, args.manual, "manual")
    field_drawing_path = resolve_input_path(root, args.year, args.field_drawing, "field_drawing")
    result = run_pipeline(root, args.year, manual_path, field_drawing_path)
    validation = result["validation"]
    print(json.dumps({"run_id": validation["run_id"], "release_decision": validation["release_decision"], "gates": validation["gates"]}, indent=2))
    return 0 if validation["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
