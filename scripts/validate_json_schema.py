#!/usr/bin/env python3
"""Validate a Music Score JSON document against the public schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from musicxml_json.schema_validation import (  # noqa: E402
    SchemaValidationError,
    validate_json_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Music Score JSON v1.0.0.")
    parser.add_argument("input", help="JSON document to validate")
    parser.add_argument("--schema", help="Optional alternate JSON Schema path")
    args = parser.parse_args()
    try:
        validate_json_file(args.input, args.schema)
    except SchemaValidationError as exc:
        print("Schema validation failed:", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Valid Music Score JSON v1.0.0: {args.input}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
