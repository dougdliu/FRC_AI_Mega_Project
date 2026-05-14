#!/usr/bin/env python3
"""Compatibility wrapper for the narrowed FRC analysis pipeline.

This project now treats extraction, field modeling, mechanics, strategy,
simulation, and validation as one in-process workflow. The legacy expanded
runner was removed because robot code generation and downstream apps are no
longer part of the core MVP.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from frc_pipeline import resolve_input_path, run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the narrowed FRC analysis pipeline.")
    parser.add_argument("--year", default="2026", help="Game year to process.")
    parser.add_argument("--manual", default=None, help="Optional path to the game manual PDF.")
    parser.add_argument("--field-drawing", default=None, help="Optional path to the field dimension drawing PDF.")
    parser.add_argument("--root", default=None, help="Workspace root. Defaults to the repository root.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    manual_path = resolve_input_path(root, args.year, args.manual, "manual")
    field_drawing_path = resolve_input_path(root, args.year, args.field_drawing, "field_drawing")
    result = run_pipeline(root, args.year, manual_path, field_drawing_path)
    validation = result["validation"]
    print(
        json.dumps(
            {
                "run_id": validation["run_id"],
                "release_decision": validation["release_decision"],
                "gates": validation["gates"],
            },
            indent=2,
        )
    )
    return 0 if validation["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
