#!/usr/bin/env python3
"""Compare two MusicXML scores using V3's source-derived musical semantics."""

from __future__ import annotations

import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

try:
    from lxml import etree
except ImportError:
    etree = None  # type: ignore[assignment]

try:
    from . import musicxml_to_json as v3
except ImportError:
    v3 = None  # type: ignore[assignment]


class ComparisonError(Exception):
    """Raised when either input cannot be normalized for comparison."""


CATEGORY_LABELS = {
    "structure": "Parts/measures",
    "notes": "Notes",
    "rests": "Rests",
    "chords": "Chords",
    "voices": "Voices",
    "staff_assignments": "Staff assignments",
    "lyrics": "Lyrics",
    "ties": "Ties",
    "slurs": "Slurs",
    "tuplets": "Tuplets",
    "beams": "Beams",
    "articulations": "Articulations",
    "ornaments": "Ornaments",
    "directions": "Directions",
    "key_signatures": "Key signatures",
    "time_signatures": "Time signatures",
    "clefs": "Clefs",
    "repeats_endings": "Repeats/endings",
    "fallbacks": "Semantic fallbacks",
}


def _fraction_text(value: Any) -> str:
    fraction = Fraction(str(value or 0))
    return str(fraction.numerator) if fraction.denominator == 1 else f"{fraction.numerator}/{fraction.denominator}"


def _canonical_xml(raw: str) -> Any:
    if etree is None:
        return raw
    try:
        element = etree.fromstring(raw.encode("utf-8"))
    except (ValueError, etree.XMLSyntaxError):
        return raw

    def convert(node: Any) -> dict[str, Any]:
        return {
            "tag": etree.QName(node).localname,
            "attributes": sorted(
                (etree.QName(key).localname, value) for key, value in node.attrib.items()
            ),
            "text": (node.text or "").strip() or None,
            "children": [convert(child) for child in node if isinstance(child.tag, str)],
        }

    return convert(element)


def _clean(value: Any) -> Any:
    """Remove identity/normalization fields and canonicalize fallback XML."""
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {
            "source_ref", "source_refs", "xml_event_index", "normalized_music21",
            "offset", "offset_fraction", "xml_id", "source_id", "measure_index",
        }:
            continue
        if key == "raw_xml" and isinstance(item, str):
            result[key] = _canonical_xml(item)
        elif key == "text" and isinstance(item, str) and not item.strip():
            result[key] = None
        else:
            result[key] = _clean(item)
    return result


def _location(
    part: dict[str, Any],
    measure: dict[str, Any],
    event: dict[str, Any] | None = None,
    *,
    member: int | None = None,
) -> dict[str, Any]:
    location: dict[str, Any] = {
        "part": part.get("id"),
        "measure": str(measure.get("number")),
    }
    if event is not None:
        location.update({
            "voice": str(event.get("voice") or "1"),
            "staff": event.get("staff"),
            "offset": _fraction_text(event.get("offset_fraction", event.get("offset", 0))),
        })
    if member is not None:
        location["chord_member"] = member
    return location


def _pitch(pitch: dict[str, Any] | None) -> Any:
    if pitch is None:
        return None
    if pitch.get("unpitched"):
        return {
            "unpitched": True,
            "display_step": pitch.get("display_step"),
            "display_octave": pitch.get("display_octave"),
        }
    return {
        "step": pitch.get("step"),
        "alter": pitch.get("alter", 0),
        "octave": pitch.get("octave"),
    }


def _event_core(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "voice": str(event.get("voice") or "1"),
        "staff": event.get("staff"),
        "offset": _fraction_text(event.get("offset_fraction", event.get("offset", 0))),
        "duration": _fraction_text(
            (event.get("duration_source") or {}).get(
                "quarter_length_fraction", event.get("duration", 0)
            )
        ),
        "written_type": event.get("written_type"),
        "dots": event.get("dots", 0),
        "grace": bool(event.get("grace")),
        "grace_info": _clean(event.get("grace_info")),
    }


def _note_core(event: dict[str, Any]) -> dict[str, Any]:
    return {
        **_event_core(event),
        "pitch": _pitch(event.get("pitch")),
        "accidental": _clean(event.get("accidental_details")),
    }


def _record(location: dict[str, Any], value: Any) -> dict[str, Any]:
    return {"location": location, "value": value}


def _append_attached(
    categories: dict[str, list[Any]],
    part: dict[str, Any],
    measure: dict[str, Any],
    parent_event: dict[str, Any],
    attachment: dict[str, Any],
    member_index: int | None,
) -> None:
    location = _location(part, measure, parent_event, member=member_index)
    mapping = {
        "lyrics": "lyrics",
        "ties": "ties",
        "slurs": "slurs",
        "tuplets": "tuplets",
        "beams": "beams",
        "articulations": "articulations",
        "ornaments": "ornaments",
    }
    for field, category in mapping.items():
        value = attachment.get(field)
        if value:
            categories[category].append(_record(location, _clean(value)))
    staff = attachment.get("staff", parent_event.get("staff"))
    if staff is not None:
        categories["staff_assignments"].append(_record(location, staff))
    for fallback in attachment.get("unhandled_notations") or []:
        categories["fallbacks"].append(
            _record(location, {
                "path": fallback.get("path"),
                "xml": _canonical_xml(fallback.get("raw_xml", "")),
            })
        )


def canonical_categories(data: dict[str, Any]) -> dict[str, list[Any]]:
    categories: dict[str, list[Any]] = {name: [] for name in CATEGORY_LABELS}
    source_metadata = (data.get("metadata") or {}).get("source") or {}
    categories["structure"].append({"metadata": _clean(source_metadata)})

    for part in data.get("parts") or []:
        measures = part.get("measures") or []
        source_part = part.get("source_part") or {}
        categories["structure"].append({
            "part": part.get("id"),
            "name": source_part.get("part_name") or part.get("name"),
            "measure_count": len(measures),
            "source_part": _clean(source_part),
        })
        for measure_index, measure in enumerate(measures, start=1):
            measure_location = _location(part, measure)
            categories["structure"].append({
                "location": measure_location,
                "index": measure_index,
                "implicit": bool(measure.get("implicit")),
                "divisions": measure.get("divisions"),
                "duration": _fraction_text(measure.get("duration_fraction", measure.get("duration", 0))),
            })
            categories["voices"].append(_record(
                measure_location,
                sorted(str(voice.get("id")) for voice in measure.get("voices") or []),
            ))

            for voice in measure.get("voices") or []:
                for event in voice.get("events") or []:
                    location = _location(part, measure, event)
                    event_type = event.get("type")
                    if event_type == "note":
                        categories["notes"].append(_record(location, _note_core(event)))
                        _append_attached(categories, part, measure, event, event, None)
                    elif event_type == "rest":
                        rest_core = {**_event_core(event), "rest": _clean(event.get("rest"))}
                        categories["rests"].append(_record(location, rest_core))
                        _append_attached(categories, part, measure, event, event, None)
                    elif event_type == "chord":
                        chord_value = {
                            **_event_core(event),
                            "members": [_note_core(member) for member in event.get("notes") or []],
                        }
                        categories["chords"].append(_record(location, chord_value))
                        for member_index, member in enumerate(event.get("notes") or []):
                            _append_attached(
                                categories, part, measure, event, member, member_index
                            )

            for event in measure.get("events") or []:
                event_type = event.get("type")
                location = _location(part, measure)
                location["offset"] = _fraction_text(event.get("offset", 0))
                if event_type == "direction":
                    categories["directions"].append(_record(location, {
                        "voice": event.get("voice"),
                        "staff": event.get("staff"),
                        "placement": event.get("placement"),
                        "direction_types": _clean(event.get("direction_types")),
                        "tempo": _clean(event.get("tempo")),
                        "sound": _clean(event.get("sound")),
                    }))
                elif event_type == "key_signature":
                    categories["key_signatures"].append(_record(location, _clean(event)))
                elif event_type == "time_signature":
                    categories["time_signatures"].append(_record(location, _clean(event)))
                elif event_type == "clef":
                    categories["clefs"].append(_record(location, _clean(event)))

            for barline in measure.get("barlines") or []:
                categories["repeats_endings"].append(
                    _record(measure_location, _clean(barline))
                )
            for fallback in measure.get("unhandled_musicxml") or []:
                categories["fallbacks"].append(_record(measure_location, {
                    "path": fallback.get("path"),
                    "offset": _fraction_text(fallback.get("offset", 0)),
                    "xml": _canonical_xml(fallback.get("raw_xml", "")),
                }))

    for values in categories.values():
        values.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return categories


def normalize_musicxml(path: str | Path) -> dict[str, Any]:
    if v3 is None:
        raise ComparisonError("The musicxml_json.musicxml_to_json module could not be imported.")
    file_path = Path(path)
    if not file_path.is_file():
        raise ComparisonError(f"MusicXML file does not exist: {file_path}")
    try:
        m21_score = v3.parse_with_music21(file_path)
        xml_tree = v3.parse_xml_tree(file_path)
        data, _context = v3.convert_score(m21_score, xml_tree, verbose=False)
        return data
    except Exception as exc:
        raise ComparisonError(f"Could not parse '{file_path}': {exc}") from exc


def _first_difference(original: Any, reconstructed: Any, path: str = "") -> tuple[str, Any, Any]:
    if type(original) is not type(reconstructed):
        return path or "value", original, reconstructed
    if isinstance(original, dict):
        for key in sorted(set(original) | set(reconstructed)):
            child_path = f"{path}.{key}" if path else key
            if key not in original:
                return child_path, None, reconstructed[key]
            if key not in reconstructed:
                return child_path, original[key], None
            if original[key] != reconstructed[key]:
                return _first_difference(original[key], reconstructed[key], child_path)
    elif isinstance(original, list):
        if len(original) != len(reconstructed):
            return f"{path}.length", len(original), len(reconstructed)
        for index, (left, right) in enumerate(zip(original, reconstructed)):
            if left != right:
                return _first_difference(left, right, f"{path}[{index}]")
    return path or "value", original, reconstructed


def compare_files(original_path: str | Path, reconstructed_path: str | Path) -> dict[str, Any]:
    original = canonical_categories(normalize_musicxml(original_path))
    reconstructed = canonical_categories(normalize_musicxml(reconstructed_path))
    category_reports = {}
    differences = []

    for category in CATEGORY_LABELS:
        left = original[category]
        right = reconstructed[category]
        category_differences = 0
        maximum = max(len(left), len(right))
        for index in range(maximum):
            if index >= len(left):
                differences.append({
                    "category": category, "location": right[index].get("location"),
                    "field": "event", "original": None, "reconstructed": right[index],
                })
                category_differences += 1
            elif index >= len(right):
                differences.append({
                    "category": category, "location": left[index].get("location"),
                    "field": "event", "original": left[index], "reconstructed": None,
                })
                category_differences += 1
            elif left[index] != right[index]:
                field, old, new = _first_difference(left[index], right[index])
                differences.append({
                    "category": category,
                    "location": left[index].get("location") or right[index].get("location"),
                    "field": field,
                    "original": old,
                    "reconstructed": new,
                })
                category_differences += 1
        category_reports[category] = {
            "passed": category_differences == 0,
            "difference_count": category_differences,
        }

    return {
        "passed": not differences,
        "difference_count": len(differences),
        "categories": category_reports,
        "differences": differences,
    }


def save_report(report: dict[str, Any], output_path: str | Path) -> None:
    try:
        with Path(output_path).open("w", encoding="utf-8") as output_file:
            json.dump(report, output_file, ensure_ascii=False, indent=2)
            output_file.write("\n")
    except OSError as exc:
        raise ComparisonError(f"Could not write report '{output_path}': {exc}") from exc


def print_report(report: dict[str, Any], *, verbose: bool = False) -> None:
    print("Semantic comparison")
    print("-------------------")
    for category, label in CATEGORY_LABELS.items():
        result = report["categories"][category]
        status = "PASS" if result["passed"] else f"FAIL ({result['difference_count']})"
        print(f"{label}: {status}")
    print(f"\nSemantic differences: {report['difference_count']}")
    if report["differences"]:
        limit = None if verbose else 10
        for difference in report["differences"][:limit]:
            print("\nDIFFERENCE")
            location = difference.get("location") or {}
            if location:
                print("Location: " + ", ".join(f"{k}={v}" for k, v in location.items()))
            print(f"Category: {difference['category']}")
            print(f"Field: {difference['field']}")
            print(f"Original: {difference['original']!r}")
            print(f"Reconstructed: {difference['reconstructed']!r}")
        if limit is not None and len(report["differences"]) > limit:
            print(f"\n... {len(report['differences']) - limit} more; use --verbose or --report.")
    print("\nRESULT: " + (
        "MUSICAL ROUND TRIP PASSED" if report["passed"]
        else "MUSICAL ROUND TRIP FAILED"
    ))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two MusicXML scores semantically.")
    parser.add_argument("original", help="Original MusicXML file")
    parser.add_argument("reconstructed", help="Reconstructed MusicXML file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print every semantic difference")
    parser.add_argument("--report", help="Optional machine-readable JSON report path")
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    try:
        report = compare_files(args.original, args.reconstructed)
        if args.report:
            save_report(report, args.report)
        print_report(report, verbose=args.verbose)
        return 0 if report["passed"] else 1
    except ComparisonError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: unexpected comparison failure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
