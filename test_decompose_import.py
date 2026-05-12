#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path().resolve() / "skills"))

try:
    from anthropic.game_manual_decomposition.decompose import decompose_manual_to_sections
    print("SUCCESS: decompose module imported")
except ImportError as e:
    print(f"FAILED: {e}")
