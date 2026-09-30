"""JSON Schema loading and validation for Music Score JSON v1.0.0."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[assignment]


class SchemaValidationError(Exception):
    """One or more JSON Schema validation failures."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors[:20]))


def default_schema_path() -> Path:
    """Locate the source-tree schema, falling back to the packaged resource."""
    repository_schema = Path(__file__).resolve().parents[2] / "schema" / "music-score.schema.json"
    if repository_schema.is_file():
        return repository_schema
    resource = resources.files("musicxml_json").joinpath("resources/music-score.schema.json")
    return Path(str(resource))


def load_schema(schema_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(schema_path) if schema_path is not None else default_schema_path()
    try:
        with path.open(encoding="utf-8") as schema_file:
            return json.load(schema_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaValidationError([f"Could not load schema '{path}': {exc}"]) from exc


def _json_path(error: Any) -> str:
    path = "$"
    for component in error.absolute_path:
        path += f"[{component}]" if isinstance(component, int) else f".{component}"
    return path


def _leaf_errors(error: Any) -> list[Any]:
    """Expose actionable oneOf/allOf causes instead of only their parent error."""
    if not error.context:
        return [error]
    result = []
    for child in error.context:
        result.extend(_leaf_errors(child))
    return result


def validate_json_data(
    data: Any, schema_path: str | Path | None = None
) -> None:
    """Validate parsed JSON data and raise with exact JSON paths on failure."""
    if Draft202012Validator is None:
        raise SchemaValidationError(
            ["jsonschema is required. Install it with: python -m pip install jsonschema"]
        )
    schema = load_schema(schema_path)
    validator = Draft202012Validator(schema)
    top_level_errors = list(validator.iter_errors(data))
    errors = [leaf for error in top_level_errors for leaf in _leaf_errors(error)]
    errors.sort(key=lambda item: (list(item.absolute_path), item.message))
    if errors:
        raise SchemaValidationError(
            [f"{_json_path(error)}: {error.message}" for error in errors]
        )


def validate_json_file(
    input_path: str | Path, schema_path: str | Path | None = None
) -> dict[str, Any]:
    path = Path(input_path)
    try:
        with path.open(encoding="utf-8") as input_file:
            data = json.load(input_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaValidationError([f"Could not read JSON '{path}': {exc}"]) from exc
    validate_json_data(data, schema_path)
    return data
