# Game Manual Decomposition Skill - Reference

This directory contains the FRC game manual decomposition skill, which breaks down monolithic game manual PDFs into modular, indexed sections for efficient context window management.

## Files

- **SKILL.md** - Complete skill documentation, I/O contracts, integration patterns, and usage examples
- **decompose.py** - Reference implementation with core decomposition logic

## Quick Start

### As a Standalone Skill

Import and use in Python:

```python
from decompose import decompose_manual_to_sections

# After extracting pages via extract_pdf_pages()
sections_index = decompose_manual_to_sections(
    pages=pages,
    year="2026",
    manual_hash="abc123def...",
    manual_version="2.0"
)
```

### Integrated into frc_pipeline.py

The pipeline now includes manual decomposition by default:

```bash
cd /path/to/frc-megaproject
python scripts/frc_pipeline.py run --year 2026
# Output includes artifacts/2026/manual_sections_index.json
```

### Agent Queries

Agents can query specific sections to reduce context bloat:

```python
import json

with open("artifacts/2026/manual_sections_index.json") as f:
    index = json.load(f)

# Query: "Get all scoring sections"
scoring = [s for s in index["sections"] if s["category"] == "scoring"]

# Query: "Get section by ID"
fuel_section = next(s for s in index["sections"] if s["id"] == "scoring-fuel-active")

# Deep-dive: full text without re-reading entire manual
print(fuel_section["raw_text"])

# Cross-reference: explore related sections
related_ids = fuel_section["related_sections"]
```

## Output Schema

See [SKILL.md](SKILL.md) for the complete output schema including:
- Section structure with IDs, categories, page ranges, keywords, summaries
- Metadata with coverage statistics and extraction notes
- Warnings and validation gates

## Key Features

✅ **Deterministic**: Same PDF always produces identical index  
✅ **Modular**: Sections queryable by ID, category, or keyword  
✅ **Linked**: Cross-references between related sections  
✅ **Efficient**: ~60-80% context reduction per agent query  
✅ **Fallback**: Graceful degradation if decompose module unavailable  

## Coverage & Validation

The decomposition targets:
- **95%+ page coverage** of the manual
- **No overlaps**: Each page in exactly one primary section
- **80%+ high-confidence sections** (rule-based classification)
- **Keyword alignment**: Top 8-12 keywords per section match content
- **Cross-reference density**: Most sections linked to 2-5 related sections

## Integration Testing

Run the pipeline and validate the output:

```bash
# Check if sections_index was generated
ls -lh artifacts/2026/manual_sections_index.json

# Validate schema
python -c "import json; json.load(open('artifacts/2026/manual_sections_index.json'))"

# Inspect sections (Python REPL)
import json
with open("artifacts/2026/manual_sections_index.json") as f:
    idx = json.load(f)
    print(f"Total sections: {idx['metadata']['total_sections']}")
    print(f"Coverage: {idx['metadata']['coverage_percentage']}%")
    print(f"By category: {idx['metadata']['sections_by_category']}")
```

## Future Enhancements

See [SKILL.md](SKILL.md) for planned improvements including:
- AI-powered boundary detection via Claude
- Semantic embeddings for similarity-based queries
- Dependency graph modeling
- RESTful query API
- Validation dashboard

---

For full documentation, see [SKILL.md](SKILL.md)
