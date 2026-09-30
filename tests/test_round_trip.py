from __future__ import annotations

from pathlib import Path

import pytest

from musicxml_json.compare_semantics import compare_files
from musicxml_json.json_to_musicxml import convert_file as export_musicxml
from musicxml_json.musicxml_to_json import convert_file as import_musicxml
from musicxml_json.schema_validation import validate_json_file


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.mark.parametrize(
    "fixture",
    ["simple.musicxml", "polyphonic.musicxml", "rich_notation.musicxml"],
)
def test_semantic_round_trip(fixture: str, tmp_path: Path) -> None:
    source = FIXTURES / fixture
    json_path = tmp_path / f"{source.stem}.json"
    reconstructed = tmp_path / f"{source.stem}.reconstructed.musicxml"

    _data, parse_context = import_musicxml(
        source, json_path, strict=True, validate=True
    )
    assert not parse_context.lossy_conditions
    validate_json_file(json_path)

    export_context = export_musicxml(json_path, reconstructed, strict=True)
    assert not export_context.lossy_conditions

    report = compare_files(source, reconstructed)
    assert report["passed"], report["differences"]
    assert report["difference_count"] == 0
    assert all(category["passed"] for category in report["categories"].values())

