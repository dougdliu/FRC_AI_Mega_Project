# Skill Specification: FRC Manual Intelligence

## Skill Intent
Transform an FRC game manual into structured knowledge and actionable strategic tools with fast turnaround.

## Inputs
- game_manual_pdf_path
- optional_previous_snapshot_id
- requested_tool_set
- strictness_level (high, medium, low)

## Outputs
- canonical_snapshot_id
- generated_tool_manifest
- validation_report
- summary_brief

## Required Competencies
- PDF structure extraction and cleanup.
- Rule and scoring interpretation from natural language.
- Structured schema mapping and conflict detection.
- Retrieval-aware summarization.
- Tool generation with source-backed explanations.

## Behavioral Rules
- Prefer exact rule text when ambiguity exists.
- Always include citation to page and section in conclusions.
- Distinguish assumptions from extracted facts.
- If confidence is below threshold, request review rather than guessing.

## Confidence Policy
- high: direct rule statement with unambiguous wording.
- medium: inferred from nearby context or examples.
- low: conflicting passages or unclear OCR output.

## Example Generated Tools
- Scoring Optimizer
- Endgame Value Analyzer
- Penalty Avoidance Assistant
- Match Phase Checklist Generator
- Human Player Decision Guide

## Acceptance Criteria
- 95%+ schema-valid canonical entities.
- 0 critical citation omissions.
- Regression checks pass against previous snapshot.
- Tool outputs reference source material for every recommendation.

