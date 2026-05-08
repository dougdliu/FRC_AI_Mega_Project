# Data Flow and Architecture

## Architecture Style
Hybrid architecture:
- Batch ETL for PDF parsing and normalization.
- Query-time retrieval for analysis and question answering.
- Tool generation layer that composes reusable game-analysis utilities.

## Core Components
1. Source Manager
- Stores manual PDF(s), release version, and checksum.

2. Document Parser
- Extracts text blocks, headers, page numbers, tables, and figures metadata.
- Emits page-level and section-level structured output.

3. Canonicalizer
- Converts raw extraction into typed entities:
	- Definitions
	- Rules and constraints
	- Scoring actions
	- Match phases and timing
	- Penalties and exceptions

4. Knowledge Store
- Relational or document store for typed entities.
- Vector index for semantic retrieval.
- Keyword index for deterministic exact-match retrieval.

5. Tool Generator
- Builds task-specific utilities from canonical entities.
- Examples: scoring calculator, penalty risk checker, role optimization helper.

6. Validation Engine
- Executes rule consistency checks.
- Runs extraction quality tests and schema compliance tests.

7. Interface Layer
- CLI and optional web dashboard for coaches and strategists.

## Data Flow
1. Input PDF enters Source Manager.
2. Document Parser produces extraction JSON.
3. Canonicalizer maps extraction to schema entities.
4. Knowledge Store updates indexes and graph relations.
5. Tool Generator emits analysis modules and reports.
6. Validation Engine runs before publish.
7. Interface Layer exposes approved outputs.

## Canonical Entity Schema (Draft)
- Rule
	- id
	- title
	- body
	- applies_to_phase
	- source_page
	- source_section
- ScoringAction
	- id
	- action
	- points
	- conditions
	- max_count
	- phase
- Penalty
	- id
	- trigger
	- severity
	- points_or_card
	- exceptions
- FieldElement
	- id
	- name
	- dimensions
	- interactions
- MatchPhase
	- id
	- name
	- duration_seconds
	- legal_actions

## Quality and Traceability Requirements
- Every entity must include source page and section anchors.
- Conflicting extractions must be flagged, never silently merged.
- Confidence scoring must be stored for each extracted field.

## Recommended Storage Layout
- data/raw
- data/extracted
- data/canonical
- data/index/vector
- data/index/keyword
- artifacts/tools
- artifacts/reports

