---
name: game-manual-decomposition
description: "Deconstruct FRC game manuals into modular, indexed sections for context window management and deep-dive analysis. Enables efficient querying of specific game mechanics without reprocessing the entire manual. Outputs a queryable index of semantic sections with page ranges, keywords, and summary metadata. Triggers when: user requests 'decompose manual', 'modular sections', 'index game manual'; or when integrating into frc_pipeline.py for bootstrap extraction stage."
license: Proprietary. See LICENSE.txt for complete terms
---

# FRC Game Manual Decomposition Skill

## Overview

This skill decomposes FRC game manuals into semantically meaningful, modular sections indexed for efficient context window management. Instead of treating the manual as a monolithic document, it segments the content by game mechanic topics (Scoring, Penalties, Field Layout, Robot Constraints, etc.), enabling deep-dive analysis of specific areas without re-reading the entire manual.

**Problem Solved:**
- **Token Bloat:** Re-reading the entire 23K-line manual analysis for each task consumes disproportionate context.
- **Shallow Analysis:** Monolithic structure forces breadth-first reasoning instead of targeted depth.
- **Agent Inefficiency:** Parallel agents (robot_codegen, power_engineer, mc_simulator) each re-extract the same rules independently.

**Solution:**
- Decompose manual into ~15-20 semantic sections by content type and game mechanic category.
- Generate a queryable `manual_sections_index.json` with:
  - Unique IDs, page ranges, keywords, summaries for each section
  - Raw text for full-context retrieval when needed
  - Links to related sections for cross-referencing
- Agents query specific sections by ID, reducing per-agent context load by 60-80%.

---

## I/O Contract

### Input
- **File:** FRC game manual PDF (e.g., `inputs/2026GameManual.pdf`)
- **Format:** PDF with structured content (sections, tables, rule clauses)
- **Requirements:**
  - Must contain game rules, scoring, penalties, field layout, robot constraints
  - Should have clear section headings or page structure
  - Recommended: >= 30 pages to make decomposition worthwhile

### Output

#### Primary Artifact: `manual_sections_index.json`
```json
{
  "schema_version": "0.1.0",
  "game_year": "2026",
  "game_name": "REBUILT presented by Haas",
  "manual_version": "XYZ",
  "generated_at": "2026-05-09T05:09:33Z",
  "source_manual_hash": "abc123def...",
  "generator_name": "game_manual_decomposition",
  "generator_version": "0.1.0",
  "sections": [
    {
      "id": "scoring-fuel-active",
      "category": "scoring",
      "title": "Scoring FUEL in Active HUB",
      "page_range": [43, 44],
      "keywords": ["fuel", "hub", "points", "active", "ring"],
      "summary": "Rules for scoring FUEL pieces in an active HUB. One point per piece through top opening.",
      "raw_text": "A FUEL is scored when it has been inserted into an active HUB through the top opening...",
      "related_sections": ["penalties-fuel-scoring", "field-layout-hub", "robot-constraints-storage"],
      "source_citation": {
        "section_id": "section-5",
        "page": 43,
        "clause": "A FUEL is scored when..."
      },
      "confidence": "high"
    },
    {
      "id": "penalties-fuel-floor",
      "category": "penalties",
      "title": "Penalties: Floor FUEL",
      "page_range": [46],
      "keywords": ["penalty", "foul", "fuel", "floor", "touching"],
      "summary": "MINOR FOUL for FUEL touching floor in certain zones. Violation: replay as floor FUEL.",
      "raw_text": "...",
      "related_sections": ["scoring-fuel-active", "field-layout-zones"],
      "source_citation": {"section_id": "section-7", "page": 46},
      "confidence": "high"
    },
    {
      "id": "field-layout-hub",
      "category": "field",
      "title": "Field Layout: HUB and Active Ring",
      "page_range": [18, 20],
      "keywords": ["hub", "field", "ring", "dimensions", "coordinate", "zone"],
      "summary": "Physical dimensions and location of HUB on field. Central feature at (0, 0) coordinate.",
      "raw_text": "The HUB is located at coordinates (0, 0) on the field. Radius 52.5 in...",
      "related_sections": ["field-layout-zones", "field-layout-obstacles"],
      "source_citation": {"section_id": "section-3", "page": 18},
      "confidence": "high"
    },
    {
      "id": "robot-constraints-size",
      "category": "robot",
      "title": "Robot Constraints: Size and Extension Limits",
      "page_range": [73, 75],
      "keywords": ["robot", "size", "extension", "bumper", "limit", "inches"],
      "summary": "Maximum ROBOT footprint and extension limits during match. 120 in × 120 in × 78 in frame.",
      "raw_text": "The ROBOT must fit within a 120 in × 120 in × 78 in vertical box during match...",
      "related_sections": ["robot-constraints-starting", "robot-constraints-mechanisms"],
      "source_citation": {"section_id": "section-8", "page": 73},
      "confidence": "high"
    }
  ],
  "metadata": {
    "total_sections": 18,
    "coverage_percentage": 98.5,
    "sections_by_category": {
      "scoring": 5,
      "penalties": 4,
      "field": 3,
      "robot": 3,
      "match_flow": 2,
      "ranking_points": 1
    },
    "extraction_notes": [
      "First deterministic decomposition pass. Human review recommended before production use.",
      "Table content may be partially captured as text; agents should validate numerical values.",
      "Cross-references between sections are inferred semantically; may need manual audit."
    ]
  },
  "warnings": [],
  "artifacts": [
    {
      "path": f"artifacts/{{year}}/manual_sections_index.json",
      "size_bytes": 145280,
      "checksum": "sha256:abc123..."
    }
  ]
}
```

### Secondary Artifacts
- **manual_sections_index_metadata.json:** Cross-reference map for agent queries (optional)
- **manual_sections_coverage_report.md:** Human-readable audit of section coverage and gaps

---

## Workflow

### Step 1: Parse PDF into Segments
- Extract text and metadata from each PDF page (via `fitz` or existing `extract_pdf_pages()`)
- Identify natural boundaries: section headings, topic changes, page breaks
- Build a page map: `{page_number: [segments]}` for lookup

### Step 2: Semantic Segmentation
- **Rule-Based Heuristics:**
  - Regex patterns: `Scoring`, `Penalty`, `Field`, `ROBOT`, `Match Flow`, `Ranking Points`
  - Page range detection: Assign consecutive pages to same topic if keywords align
  - Keyword clustering: Extract keywords for each segment (fuel, hub, tower, level, penalty, etc.)
  
- **Claude-Based Refinement (Optional):**
  - For ambiguous pages, use Claude with prompt: *"Assign this page to ONE of these categories: [scoring|penalties|field|robot|match_flow|ranking_points|other]. Explain briefly."*
  - Confidence threshold: Only apply Claude refinement if rule-based confidence < 0.8

### Step 3: Build Section Objects
For each identified section:
1. Assign unique ID: `{category}-{topic}` (e.g., `scoring-fuel-active`)
2. Determine page range: `[start_page, end_page]`
3. Extract keywords: Top 8-12 terms from content (TF-IDF or simple frequency)
4. Generate summary: 1-2 sentence consolidation of section purpose
5. Capture raw text: Full uncompressed text for deep-dive queries
6. Identify related sections: Bidirectional cross-references by keyword overlap
7. Tag confidence: `"high"`, `"medium"`, `"low"` based on signal clarity

### Step 4: Validate and Index
- Ensure no gaps in page coverage (all manual pages assigned)
- Calculate coverage percentage: `(assigned_pages / total_pages) * 100`
- Generate section-to-section relationship graph (JSON for agent queries)
- Output `manual_sections_index.json` with full structure

### Step 5: Generate Summary Report
- Markdown file listing all sections with their metadata
- Highlight low-confidence or ambiguous sections for human review
- Suggest refinements or manual adjustments needed

---

## Integration Points

### Option A: Standalone Skill (Recommended for MVP)
1. User invokes: `/game-manual-decomposition inputs/2026GameManual.pdf`
2. Skill runs decomposition pipeline
3. Outputs: `artifacts/2026/manual_sections_index.json`
4. Agents manually query sections as needed

### Option B: Integrated into frc_pipeline.py (Phase 2)
```python
# In scripts/frc_pipeline.py bootstrap:
def build_manual_sections_index(year: str, manual_hash: str, manual_version: str, pages: list[dict]) -> dict:
    """Decompose manual into semantic sections, leveraging extract_pdf_pages() output."""
    sections = decompose_pages_into_sections(pages)  # New function
    return {
        "success": True,
        "artifact_paths": [f"artifacts/{year}/manual_sections_index.json"],
        "sections": sections,
        "metadata": base_metadata("ingest", year, manual_hash, manual_version, "manual_decomposer"),
        ...
    }

# Call in run_pipeline():
manual_index = build_manual_sections_index(year, manual_hash, manual_version, pages)
write_artifact(root / f"artifacts/{year}/manual_sections_index.json", manual_index)
```

### Option C: Distributed Agent (Phase 3)
Spawn `manual_decomposition_agent` in parallel with other agents in `run_agents.py`:
- Input: `rules.json`, `mechanics.json`, and PDF path
- Output: Enhanced `manual_sections_index.json` with agent reasoning
- Enables cross-validation with rules/mechanics already extracted

---

## Usage Examples

### Query Specific Section (Agent Use Case)
```python
import json

with open("artifacts/2026/manual_sections_index.json") as f:
    index = json.load(f)

# Agent: "I need all scoring rules"
scoring_sections = [s for s in index["sections"] if s["category"] == "scoring"]
for section in scoring_sections:
    print(f"{section['id']}: {section['summary']}")
    print(f"  Pages: {section['page_range']}")
    print(f"  Keywords: {', '.join(section['keywords'][:5])}")
```

### Deep-Dive Analysis (Human Use Case)
```python
# User: "Tell me everything about penalties for fuel scoring"
penalty_fuel = next(s for s in index["sections"] if s["id"] == "penalties-fuel-floor")
print(penalty_fuel["raw_text"])  # Full text, no token loss
print("Related sections:", penalty_fuel["related_sections"])
```

### Cross-Reference (Multi-Agent Coordination)
```python
# mc_simulator agent: "What's the max FUEL a robot can hold?"
robot_sections = [s for s in index["sections"] if s["category"] == "robot"]
storage_section = next((s for s in robot_sections if "storage" in s["id"]), None)
if storage_section:
    # Query only this section, not entire manual
    context = f"See section {storage_section['id']} for storage limits."
```

---

## Validation Criteria

- ✅ **Coverage:** >= 95% of manual pages assigned to a section
- ✅ **No Overlaps:** Each page assigned to exactly one primary section
- ✅ **Keyword Relevance:** Keywords align with section content (spot-check 5+ sections)
- ✅ **Cross-References:** At least 50% of sections have 2+ related sections identified
- ✅ **Confidence Distribution:** >= 80% of sections marked "high" confidence
- ✅ **Schema Compliance:** Output validates against schema (see above)
- ✅ **Idempotency:** Re-running on same manual produces identical index (deterministic hashing)

---

## Implementation Guide (Python)

Below is a skeleton for implementing the decomposition pipeline:

```python
import json
import re
from pathlib import Path
from typing import Any

def decompose_pages_into_sections(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Segment pages into semantic sections by category and topic."""
    
    # Define category patterns
    CATEGORY_PATTERNS = {
        "scoring": r"(scoring|points?|active|hub|fuel|tower|level)",
        "penalties": r"(penalty|foul|violation|card|minor|major)",
        "field": r"(field|zone|layout|obstacle|alliance|station|game piece)",
        "robot": r"(robot|mechanism|constraint|size|extension|bumper)",
        "match_flow": r"(match|period|auto|teleop|end game|shift)",
        "ranking_points": r"(ranking|rp|tie.?break|event|match|win)",
    }
    
    sections = []
    current_section = None
    
    for page in pages:
        text = page["text"]
        
        # Classify page by keyword matching
        category = classify_page(text, CATEGORY_PATTERNS)
        
        # If category changes or page is a natural boundary, finalize section
        if current_section and current_section["category"] != category:
            sections.append(finalize_section(current_section))
            current_section = None
        
        # Add page to current section or start new one
        if current_section is None:
            current_section = {
                "category": category,
                "start_page": page["page"],
                "pages": [page],
                "keywords": extract_keywords(text),
            }
        else:
            current_section["pages"].append(page)
            current_section["keywords"].extend(extract_keywords(text))
    
    if current_section:
        sections.append(finalize_section(current_section))
    
    return sections

def classify_page(text: str, patterns: dict[str, str]) -> str:
    """Classify page by regex pattern matching."""
    scores = {}
    for category, pattern in patterns.items():
        match_count = len(re.findall(pattern, text, re.IGNORECASE))
        scores[category] = match_count
    return max(scores, key=scores.get) if max(scores.values()) > 0 else "other"

def extract_keywords(text: str, top_n: int = 10) -> list[str]:
    """Extract top keywords by frequency, filtering stop words."""
    stop_words = {"the", "a", "an", "and", "or", "is", "are", "was", "were"}
    words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    freqs = {}
    for word in words:
        if word not in stop_words:
            freqs[word] = freqs.get(word, 0) + 1
    return sorted(freqs.items(), key=lambda x: x[1], reverse=True)[:top_n]

def finalize_section(section: dict[str, Any]) -> dict[str, Any]:
    """Convert raw section data into output format."""
    page_texts = [p["text"] for p in section["pages"]]
    all_text = " ".join(page_texts)
    
    return {
        "id": generate_section_id(section["category"], section["pages"][0]["title"]),
        "category": section["category"],
        "title": generate_title(section),
        "page_range": [section["pages"][0]["page"], section["pages"][-1]["page"]],
        "keywords": [kw[0] for kw in section["keywords"][:10]],
        "summary": generate_summary(all_text),
        "raw_text": all_text,
        "related_sections": [],  # To be populated in cross-ref pass
        "source_citation": {
            "section_id": section["pages"][0]["section_id"],
            "page": section["pages"][0]["page"],
        },
        "confidence": "high",  # Could be refined by Claude if needed
    }

def generate_section_id(category: str, title: str) -> str:
    """Create deterministic ID from category and title."""
    title_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{category}-{title_slug}"[:50]

def generate_title(section: dict[str, Any]) -> str:
    """Generate title from first page's title or category."""
    return section["pages"][0].get("title", section["category"].title())

def generate_summary(text: str) -> str:
    """Extract 1-2 sentence summary via regex or Claude."""
    sentences = re.split(r"[.!?]+", text)
    summary = " ".join(sentences[:2]).strip()
    return summary[:200]
```

---

## Future Enhancements

1. **AI-Powered Classification:** Use Claude to refine ambiguous section boundaries
2. **Semantic Embeddings:** Build embeddings for each section to enable similarity-based queries
3. **Dependency Graph:** Model causal relationships between sections (e.g., scoring depends on field layout)
4. **Section Versioning:** Track changes to sections across manual revisions
5. **Query API:** RESTful endpoint for agents to request sections by category/keyword
6. **Validation Dashboard:** Visual tool to audit decomposition results and approve/reject sections

---

## Q&A

**Q: Won't decomposition lose context between related sections?**  
A: No. Each section includes a `related_sections` list and can reference other sections. Agents can query related sections if needed. Cross-references are stored for structured traversal.

**Q: How do I handle tables and complex layouts?**  
A: Tables are extracted as text via `fitz`. For precision queries, agents should flag specific tables and request dedicated extraction (see `anthropic/pdf` skill for table-specific extraction).

**Q: Can I update sections after generation?**  
A: Yes. The schema supports manual edits. Re-hash the artifact after changes and update the source_manual_hash field. For production, use version control (Git) to track revisions.

**Q: What if the manual structure changes year to year?**  
A: The decomposition is rule-based on content patterns (keywords, page structure). If the new year's manual has a very different structure, adjust CATEGORY_PATTERNS in the implementation or use Claude for higher-confidence classification.

