#!/usr/bin/env python3
"""Source-tree CLI wrapper for semantic MusicXML comparison."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from musicxml_json.compare_semantics import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
