"""Reference implementation for manual decomposition.

This module provides utilities to decompose FRC game manual PDFs into
modular, indexed sections for context-efficient agent queries.

Usage:
    from skills.anthropic.game_manual_decomposition.decompose import decompose_manual_to_sections
    sections_index = decompose_manual_to_sections(
        pages=pages_from_extract_pdf_pages(),
        year="2026",
        manual_hash=hash_value,
        manual_version="2.0"
    )
"""

from __future__ import annotations

import re
from typing import Any


# Category classification patterns
CATEGORY_PATTERNS: dict[str, tuple[str, int]] = {
    "scoring": (r"(scoring|points?|active|hub|fuel|tower|level|bonus|rp|match points)", 5),
    "penalties": (r"(penalty|foul|violation|card|minor|major|flag|tech)", 4),
    "field": (r"(field|zone|layout|obstacle|alliance|station|game piece|carpet|cable|rung)", 3),
    "robot": (r"(robot|mechanism|constraint|size|extension|bumper|starting|configuration|frame)", 3),
    "match_flow": (r"(match|period|auto|teleop|end game|shift|seconds)", 2),
    "ranking_points": (r"(ranking|rp|tie.?break|event|match|win)", 1),
}

STOP_WORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "were", "be", "been",
    "have", "has", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "must", "can", "shall", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "up", "about", "as", "into", "through",
}


def decompose_manual_to_sections(
    pages: list[dict[str, Any]],
    year: str,
    manual_hash: str,
    manual_version: str,
) -> dict[str, Any]:
    """Decompose extracted manual pages into semantic sections.
    
    Args:
        pages: Output from extract_pdf_pages() containing page text and metadata
        year: Game year (e.g., "2026")
        manual_hash: SHA256 hash of manual PDF
        manual_version: Manual version string (e.g., "2.0")
    
    Returns:
        Dictionary with schema_version, sections, metadata, and warnings.
    """
    
    # Step 1: Classify pages into categories
    classified_pages = [
        {
            **page,
            "category": classify_page_content(page["text"]),
        }
        for page in pages
    ]
    
    # Step 2: Segment consecutive pages into sections
    raw_sections = segment_pages_into_sections(classified_pages)
    
    # Step 3: Build section objects with full metadata
    sections = [
        build_section_object(seg, idx)
        for idx, seg in enumerate(raw_sections)
    ]
    
    # Step 4: Identify cross-references
    sections = link_related_sections(sections)
    
    # Step 5: Calculate coverage
    coverage = calculate_coverage(classified_pages, sections)
    
    # Step 6: Build output
    return {
        "schema_version": "0.1.0",
        "game_year": year,
        "game_name": "REBUILT presented by Haas",
        "manual_version": manual_version,
        "generated_at": _utc_now(),
        "source_manual_hash": manual_hash,
        "generator_name": "game_manual_decomposition",
        "generator_version": "0.1.0",
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "coverage_percentage": coverage,
            "sections_by_category": _count_by_category(sections),
            "extraction_notes": [
                "First deterministic decomposition pass. Human review recommended.",
                "Section boundaries are inferred from content; may need manual adjustment.",
                "Cross-references are keyword-based; not exhaustive.",
            ],
        },
        "warnings": _generate_warnings(sections, coverage),
        "success": coverage >= 90,
    }


def classify_page_content(text: str) -> str:
    """Classify a page by keyword pattern matching.
    
    Returns the category with the highest weighted match count.
    """
    scores: dict[str, int] = {}
    
    for category, (pattern, weight) in CATEGORY_PATTERNS.items():
        matches = len(re.findall(pattern, text, re.IGNORECASE))
        scores[category] = matches * weight
    
    # Return highest scoring category, or "other" if no matches
    if not scores or max(scores.values()) == 0:
        return "other"
    return max(scores, key=scores.get)


def segment_pages_into_sections(pages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Segment consecutive pages with the same category into sections.
    
    A new section starts when:
    1. The category changes
    2. A page title or heading suggests a new topic (heuristic)
    """
    if not pages:
        return []
    
    sections: list[list[dict[str, Any]]] = []
    current_section = [pages[0]]
    
    for page in pages[1:]:
        # Start new section if category changes
        if page["category"] != current_section[0]["category"]:
            sections.append(current_section)
            current_section = [page]
        # Start new section if page title suggests topic boundary
        elif _is_topic_boundary(page["title"]):
            sections.append(current_section)
            current_section = [page]
        else:
            current_section.append(page)
    
    if current_section:
        sections.append(current_section)
    
    return sections


def build_section_object(page_group: list[dict[str, Any]], section_idx: int) -> dict[str, Any]:
    """Build a section object from a group of consecutive pages."""
    
    # Combine text and metadata from all pages
    all_text = " ".join([p["text"] for p in page_group])
    category = page_group[0]["category"]
    first_page = page_group[0]
    last_page = page_group[-1]
    
    # Generate section ID
    section_id = _generate_section_id(category, first_page["title"], section_idx)
    
    # Extract keywords
    keywords = extract_keywords(all_text, top_n=10)
    
    # Generate summary (first 1-2 sentences)
    summary = _generate_summary(all_text, max_length=200)
    
    return {
        "id": section_id,
        "category": category,
        "title": _generate_title(category, first_page["title"]),
        "page_range": [first_page["page"], last_page["page"]],
        "keywords": keywords,
        "summary": summary,
        "raw_text": all_text,
        "related_sections": [],  # Populated in link_related_sections()
        "source_citation": {
            "section_id": first_page["section_id"],
            "page": first_page["page"],
        },
        "confidence": _estimate_confidence(category, len(page_group), all_text),
    }


def extract_keywords(text: str, top_n: int = 10) -> list[str]:
    """Extract top keywords by frequency, filtering stop words.
    
    Returns list of up to top_n lowercase keywords.
    """
    # Extract words (3+ chars)
    words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    
    # Count frequencies, excluding stop words
    freqs: dict[str, int] = {}
    for word in words:
        if word not in STOP_WORDS:
            freqs[word] = freqs.get(word, 0) + 1
    
    # Sort by frequency and return top N
    sorted_words = sorted(freqs.items(), key=lambda x: x[1], reverse=True)
    return [word for word, _ in sorted_words[:top_n]]


def link_related_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify and link related sections via keyword overlap and category proximity.
    
    Modifies sections in-place to populate related_sections lists.
    """
    for i, section in enumerate(sections):
        section_keywords = set(section["keywords"])
        related: list[str] = []
        
        # Look for sections with keyword overlap (excluding same section)
        for j, other in enumerate(sections):
            if i == j:
                continue
            
            other_keywords = set(other["keywords"])
            overlap = section_keywords & other_keywords
            
            # If there's keyword overlap, consider it related
            if len(overlap) >= 2:  # At least 2 keywords in common
                related.append(other["id"])
        
        # Also link by category proximity (e.g., scoring -> field)
        if not related or len(related) < 2:
            for j, other in enumerate(sections):
                if i == j:
                    continue
                # Link complementary categories
                if _are_related_categories(section["category"], other["category"]):
                    if other["id"] not in related:
                        related.append(other["id"])
        
        section["related_sections"] = related[:5]  # Max 5 related sections
    
    return sections


def calculate_coverage(classified_pages: list[dict[str, Any]], sections: list[dict[str, Any]]) -> float:
    """Calculate percentage of manual pages assigned to a section.
    
    Returns value between 0 and 100.
    """
    if not classified_pages:
        return 0.0
    
    # Collect all page numbers from sections
    assigned_pages = set()
    for section in sections:
        start, end = section["page_range"]
        assigned_pages.update(range(start, end + 1))
    
    total_pages = len(classified_pages)
    coverage = (len(assigned_pages) / total_pages) * 100 if total_pages > 0 else 0.0
    return round(coverage, 1)


# ============================================================================
# Helper Functions
# ============================================================================


def _utc_now() -> str:
    """ISO 8601 UTC timestamp."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_topic_boundary(title: str) -> bool:
    """Heuristic to detect if a title indicates a new topic section."""
    section_keywords = ["section", "chapter", "part", "rule", "regulation"]
    return any(kw in title.lower() for kw in section_keywords)


def _generate_section_id(category: str, title: str, idx: int) -> str:
    """Generate deterministic section ID from category, title, and index."""
    # Slugify title
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    # Truncate to reasonable length
    slug = slug[:30]
    # Build ID
    section_id = f"{category}-{slug}" if slug else f"{category}-{idx}"
    return section_id[:50]


def _generate_title(category: str, fallback: str) -> str:
    """Generate human-readable title, preferring the fallback if it's good."""
    if fallback and len(fallback) > 5:
        return fallback
    return category.replace("_", " ").title()


def _generate_summary(text: str, max_length: int = 200) -> str:
    """Extract summary from first 1-2 sentences."""
    # Split by sentence delimiters
    sentences = re.split(r"[.!?]+", text.strip())
    # Take first 1-2 non-empty sentences
    valid_sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
    summary = " ".join(valid_sentences[:2])
    # Truncate if too long
    return summary[:max_length].rstrip()


def _estimate_confidence(category: str, num_pages: int, text: str) -> str:
    """Estimate confidence in section classification.
    
    Returns "high", "medium", or "low".
    """
    # High confidence: multiple pages, clear category
    if num_pages >= 2 and len(text) > 1000:
        return "high"
    # Medium: single page, decent content
    if num_pages == 1 and len(text) > 500:
        return "medium"
    # Low: ambiguous or sparse content
    return "low"


def _count_by_category(sections: list[dict[str, Any]]) -> dict[str, int]:
    """Count sections by category."""
    counts: dict[str, int] = {}
    for section in sections:
        cat = section["category"]
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def _generate_warnings(sections: list[dict[str, Any]], coverage: float) -> list[str]:
    """Generate warnings based on decomposition results."""
    warnings = []
    
    # Check coverage
    if coverage < 95:
        warnings.append(f"Coverage is {coverage}%. Some manual content may be unassigned.")
    
    # Check for low-confidence sections
    low_conf_count = sum(1 for s in sections if s["confidence"] == "low")
    if low_conf_count > 0:
        warnings.append(f"{low_conf_count} sections have low confidence. Manual review recommended.")
    
    # Check for sections with no related sections
    orphan_count = sum(1 for s in sections if not s["related_sections"])
    if orphan_count > len(sections) * 0.3:  # More than 30% orphaned
        warnings.append(f"{orphan_count} sections have no cross-references. Consider manual linking.")
    
    return warnings


def _are_related_categories(cat1: str, cat2: str) -> bool:
    """Check if two categories are semantically related."""
    # Define category relationships
    relationships = {
        "scoring": {"field", "penalties", "robot"},
        "penalties": {"scoring", "field", "robot"},
        "field": {"scoring", "penalties", "robot"},
        "robot": {"scoring", "penalties", "field"},
        "match_flow": {"scoring", "penalties", "ranking_points"},
        "ranking_points": {"match_flow", "scoring"},
    }
    
    if cat1 in relationships:
        return cat2 in relationships[cat1]
    return False
