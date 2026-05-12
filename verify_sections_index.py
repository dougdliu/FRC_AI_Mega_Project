#!/usr/bin/env python3
import json

with open('artifacts/2026/manual_sections_index.json', encoding='utf-8') as f:
    data = json.load(f)
    print('Schema version:', data['schema_version'])
    print('Game year:', data['game_year'])
    print('Total sections:', data['metadata']['total_sections'])
    print('Coverage:', data['metadata']['coverage_percentage'], '%')
    print('By category:', data['metadata']['sections_by_category'])
    print()
    print('First 3 sections:')
    for section in data['sections'][:3]:
        print(f"  - {section['id']}: {section['title']} (pages {section['page_range']}, confidence: {section['confidence']})")
    print()
    print('Sample section details:')
    sample = data['sections'][0]
    print(f"  ID: {sample['id']}")
    print(f"  Category: {sample['category']}")
    print(f"  Keywords: {', '.join(sample['keywords'][:5])}")
    print(f"  Summary: {sample['summary'][:100]}...")
    print(f"  Related sections: {sample['related_sections']}")
