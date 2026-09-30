from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from musicxml_json.schema_validation import SchemaValidationError, validate_json_data, validate_json_file


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "schema" / "examples"


def _simple() -> dict:
    return json.loads((EXAMPLES / "simple.json").read_text(encoding="utf-8"))


def _first_note(data: dict) -> dict:
    return next(
        event
        for part in data["parts"]
        for measure in part["measures"]
        for voice in measure["voices"]
        for event in voice["events"]
        if event["type"] == "note"
    )


@pytest.mark.parametrize(
    "example",
    ["simple.json", "polyphonic.json", "rich_notation.json"],
)
def test_public_examples_validate(example: str) -> None:
    validate_json_file(EXAMPLES / example)


def test_schema_version_is_required() -> None:
    data = _simple()
    del data["schema_version"]
    with pytest.raises(SchemaValidationError, match="schema_version"):
        validate_json_data(data)


def test_parts_must_be_an_array() -> None:
    data = _simple()
    data["parts"] = {}
    with pytest.raises(SchemaValidationError, match="parts"):
        validate_json_data(data)


def test_note_pitch_is_required() -> None:
    data = _simple()
    del _first_note(data)["pitch"]
    with pytest.raises(SchemaValidationError, match="pitch"):
        validate_json_data(data)


def test_octave_must_be_an_integer() -> None:
    data = _simple()
    _first_note(data)["pitch"]["octave"] = "4"
    with pytest.raises(SchemaValidationError, match="octave"):
        validate_json_data(data)


def test_unknown_event_type_is_rejected() -> None:
    data = _simple()
    _first_note(data)["type"] = "unknown"
    with pytest.raises(SchemaValidationError):
        validate_json_data(data)


def test_negative_duration_is_rejected() -> None:
    data = _simple()
    _first_note(data)["duration"] = -1
    with pytest.raises(SchemaValidationError, match="duration"):
        validate_json_data(data)


def test_malformed_tuplet_ratio_is_rejected() -> None:
    data = json.loads((EXAMPLES / "rich_notation.json").read_text(encoding="utf-8"))
    tuplet = next(
        tuplet
        for part in data["parts"]
        for measure in part["measures"]
        for voice in measure["voices"]
        for event in voice["events"]
        for member in (event.get("notes") or [event])
        for tuplet in member.get("tuplets", [])
    )
    tuplet["actual_notes"] = 0
    with pytest.raises(SchemaValidationError, match="actual_notes"):
        validate_json_data(data)


def test_packaged_and_public_schemas_match() -> None:
    public = (ROOT / "schema" / "music-score.schema.json").read_bytes()
    packaged = (ROOT / "src" / "musicxml_json" / "resources" / "music-score.schema.json").read_bytes()
    assert public == packaged

