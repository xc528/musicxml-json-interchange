#!/usr/bin/env python3
"""Reconstruct score-partwise MusicXML from Music Score JSON v1."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

try:
    from lxml import etree
except ImportError:
    etree = None  # type: ignore[assignment]


class ExportError(Exception):
    """Fatal JSON, reconstruction, or output error."""


@dataclass
class ExportStats:
    parts: int = 0
    measures: int = 0
    notes: int = 0
    rests: int = 0
    chords: int = 0
    lyrics: int = 0
    slurs: int = 0
    ties: int = 0
    tuplets: int = 0
    fallbacks: int = 0


@dataclass
class ExportContext:
    verbose: bool = False
    stats: ExportStats = field(default_factory=ExportStats)
    warnings: list[str] = field(default_factory=list)
    lossy_conditions: list[str] = field(default_factory=list)

    def warn(self, message: str, *, lossy: bool = False) -> None:
        self.warnings.append(message)
        if lossy:
            self.lossy_conditions.append(message)
        if self.verbose:
            print(f"WARNING: {message}", file=sys.stderr)


def _fraction(value: Any) -> Fraction:
    if value in (None, ""):
        return Fraction(0)
    return Fraction(str(value))


def _event_offset(event: dict[str, Any]) -> Fraction:
    return _fraction(event.get("offset_fraction", event.get("offset", 0)))


def _set_attributes(element: Any, attributes: dict[str, Any] | None) -> None:
    for name, value in (attributes or {}).items():
        if value is not None:
            element.set(str(name), str(value))


def _sub(parent: Any, name: str, text: Any = None, attributes: dict[str, Any] | None = None) -> Any:
    element = etree.SubElement(parent, name)
    _set_attributes(element, attributes)
    if text is not None:
        element.text = str(text)
    return element


def _strip_namespaces(element: Any) -> Any:
    for node in element.iter():
        if isinstance(node.tag, str):
            node.tag = etree.QName(node).localname
        for key in list(node.attrib):
            local = etree.QName(key).localname
            if key != local:
                node.attrib[local] = node.attrib.pop(key)
    etree.cleanup_namespaces(element)
    return element


def _parse_fragment(
    fragment: dict[str, Any], context: ExportContext, *, count_fallback: bool = True
) -> Any | None:
    raw = fragment.get("raw_xml")
    if not raw:
        context.warn(
            f"Fallback {fragment.get('path') or fragment.get('tag')} has no raw_xml.",
            lossy=True,
        )
        return None
    try:
        element = etree.fromstring(raw.encode("utf-8"))
        if count_fallback:
            context.stats.fallbacks += 1
        return _strip_namespaces(element)
    except (ValueError, etree.XMLSyntaxError) as exc:
        context.warn(f"Could not reinsert fallback XML: {exc}", lossy=True)
        return None


def load_json(input_path: str | Path) -> dict[str, Any]:
    path = Path(input_path)
    if not path.is_file():
        raise ExportError(f"Input JSON does not exist: {path}")
    try:
        with path.open(encoding="utf-8") as input_file:
            data = json.load(input_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"Could not read JSON: {exc}") from exc
    if data.get("schema_version") != "1.0.0":
        raise ExportError("Expected Music Score JSON schema_version '1.0.0'.")
    if not isinstance(data.get("parts"), list) or not data["parts"]:
        raise ExportError("V3 JSON contains no parts.")
    try:
        from .schema_validation import SchemaValidationError, validate_json_data
        validate_json_data(data)
    except SchemaValidationError as exc:
        raise ExportError(f"Input JSON does not satisfy schema v1.0.0: {exc}") from exc
    return data


def _write_metadata(root: Any, data: dict[str, Any]) -> None:
    metadata = data.get("metadata") or {}
    source = metadata.get("source") or {}
    if source.get("work_number") is not None or source.get("work_title") is not None:
        work = _sub(root, "work")
        if source.get("work_number") is not None:
            _sub(work, "work-number", source["work_number"])
        if source.get("work_title") is not None:
            _sub(work, "work-title", source["work_title"])
    if source.get("movement_number") is not None:
        _sub(root, "movement-number", source["movement_number"])
    if source.get("movement_title") is not None:
        _sub(root, "movement-title", source["movement_title"])
    creators = source.get("creators") or []
    rights = source.get("rights") or []
    if creators or rights:
        identification = _sub(root, "identification")
        for creator in creators:
            _sub(
                identification,
                "creator",
                creator.get("text", ""),
                {"type": creator.get("type")},
            )
        for item in rights:
            _sub(
                identification,
                "rights",
                item.get("text", ""),
                {"type": item.get("type")},
            )


def _write_part_list(root: Any, parts: list[dict[str, Any]]) -> None:
    part_list = _sub(root, "part-list")
    for index, part in enumerate(parts, start=1):
        part_id = str(part.get("id") or f"P{index}")
        source = part.get("source_part") or {}
        score_part = _sub(part_list, "score-part", attributes={"id": part_id})
        _sub(score_part, "part-name", source.get("part_name") or part.get("name") or f"Part {index}")
        abbreviation = source.get("part_abbreviation") or part.get("abbreviation")
        if abbreviation:
            _sub(score_part, "part-abbreviation", abbreviation)
        for instrument in source.get("score_instruments") or []:
            item = _sub(score_part, "score-instrument", attributes={"id": instrument.get("id")})
            if instrument.get("instrument_name") is not None:
                _sub(item, "instrument-name", instrument["instrument_name"])
            if instrument.get("instrument_abbreviation") is not None:
                _sub(item, "instrument-abbreviation", instrument["instrument_abbreviation"])
            if instrument.get("instrument_sound") is not None:
                _sub(item, "instrument-sound", instrument["instrument_sound"])
        for instrument in source.get("midi_instruments") or []:
            item = _sub(score_part, "midi-instrument", attributes={"id": instrument.get("id")})
            mapping = (
                ("midi-channel", "midi_channel"), ("midi-program", "midi_program"),
                ("midi-unpitched", "midi_unpitched"), ("volume", "volume"), ("pan", "pan"),
            )
            for xml_name, json_name in mapping:
                if instrument.get(json_name) is not None:
                    _sub(item, xml_name, instrument[json_name])


def _write_pitch(parent: Any, pitch: dict[str, Any] | None, context: ExportContext) -> None:
    if not pitch:
        context.warn("Pitched note has no pitch object.", lossy=True)
        return
    if pitch.get("unpitched"):
        unpitched = _sub(parent, "unpitched")
        if pitch.get("display_step") is not None:
            _sub(unpitched, "display-step", pitch["display_step"])
        if pitch.get("display_octave") is not None:
            _sub(unpitched, "display-octave", pitch["display_octave"])
        return
    pitch_element = _sub(parent, "pitch")
    _sub(pitch_element, "step", pitch.get("step"))
    if pitch.get("alter", 0) != 0:
        _sub(pitch_element, "alter", pitch["alter"])
    _sub(pitch_element, "octave", pitch.get("octave"))


def _write_accidental(parent: Any, accidental: dict[str, Any] | None) -> None:
    if not accidental or not accidental.get("explicit"):
        return
    attributes = dict(accidental.get("attributes") or {})
    if accidental.get("cautionary") and "cautionary" not in attributes:
        attributes["cautionary"] = "yes"
    if accidental.get("editorial") and "editorial" not in attributes:
        attributes["editorial"] = "yes"
    _sub(parent, "accidental", accidental.get("value"), attributes)


def _write_time_modification(parent: Any, tuplets: list[dict[str, Any]]) -> None:
    source = next(
        (item for item in tuplets if item.get("actual_notes") and item.get("normal_notes")),
        None,
    )
    if source is None:
        return
    time_modification = _sub(parent, "time-modification")
    _sub(time_modification, "actual-notes", source["actual_notes"])
    _sub(time_modification, "normal-notes", source["normal_notes"])
    if source.get("normal_type") is not None:
        _sub(time_modification, "normal-type", source["normal_type"])
    for _ in range(int(source.get("normal_dots") or 0)):
        _sub(time_modification, "normal-dot")


def _write_lyrics(parent: Any, lyrics: list[dict[str, Any]], context: ExportContext) -> None:
    for lyric in lyrics:
        attributes = dict(lyric.get("attributes") or {})
        if lyric.get("number") is not None:
            attributes["number"] = str(lyric["number"])
        if lyric.get("name") is not None:
            attributes["name"] = str(lyric["name"])
        item = _sub(parent, "lyric", attributes=attributes)
        if lyric.get("syllabic") is not None:
            _sub(item, "syllabic", lyric["syllabic"])
        texts = lyric.get("texts")
        if texts is None:
            texts = [lyric.get("text", "")]
        elisions = lyric.get("elisions") or []
        for index, text in enumerate(texts):
            _sub(item, "text", text)
            if index < len(elisions):
                elision = elisions[index]
                _sub(item, "elision", elision.get("text", ""), elision.get("attributes"))
        if lyric.get("humming"):
            _sub(item, "humming")
        if lyric.get("laughing"):
            _sub(item, "laughing")
        extend = lyric.get("extend") or {}
        if extend.get("present"):
            _sub(item, "extend", attributes={"type": extend.get("type")})
        if lyric.get("end_line"):
            _sub(item, "end-line")
        if lyric.get("end_paragraph"):
            _sub(item, "end-paragraph")
        context.stats.lyrics += 1


def _write_notations(parent: Any, event: dict[str, Any], context: ExportContext) -> None:
    ties = (event.get("ties") or {}).get("notation") or []
    slurs = event.get("slurs") or []
    tuplets = [item for item in event.get("tuplets") or [] if item.get("type")]
    articulations = event.get("articulations") or []
    ornaments = event.get("ornaments") or []
    fallbacks = [
        item for item in event.get("unhandled_notations") or []
        if str(item.get("path", "")).startswith("note/notations/")
    ]
    if not any((ties, slurs, tuplets, articulations, ornaments, fallbacks)):
        return
    notations = _sub(parent, "notations")
    for item in ties:
        attributes = dict(item)
        tie_type = attributes.pop("type", None)
        attributes["type"] = tie_type
        _sub(notations, "tied", attributes=attributes)
        context.stats.ties += 1
    for item in slurs:
        attributes = dict(item)
        _sub(notations, "slur", attributes=attributes)
        context.stats.slurs += 1
    for item in tuplets:
        attributes = dict(item.get("attributes") or {})
        mapping = {
            "type": "type", "number": "number", "bracket": "bracket",
            "show_number": "show-number", "show_type": "show-type",
        }
        for json_name, xml_name in mapping.items():
            if item.get(json_name) is not None:
                attributes[xml_name] = str(item[json_name])
        _sub(notations, "tuplet", attributes=attributes)
        context.stats.tuplets += 1
    ordinary_articulations = [item for item in articulations if item.get("type") != "fermata"]
    for item in articulations:
        if item.get("type") == "fermata":
            _sub(notations, "fermata", item.get("text"), item.get("attributes"))
    if ordinary_articulations:
        container = _sub(notations, "articulations")
        for item in ordinary_articulations:
            _sub(container, item["type"], item.get("text"), item.get("attributes"))
    if ornaments:
        container = _sub(notations, "ornaments")
        for item in ornaments:
            raw = item.get("raw_xml")
            if raw:
                parsed = _parse_fragment(item, context, count_fallback=False)
                if parsed is not None:
                    container.append(parsed)
                    continue
            _sub(container, item["type"], item.get("text"), item.get("attributes"))
    for fragment in fallbacks:
        parsed = _parse_fragment(fragment, context)
        if parsed is not None:
            notations.append(parsed)


def _note_fallbacks(event: dict[str, Any], tags: set[str]) -> list[dict[str, Any]]:
    return [
        item for item in event.get("unhandled_notations") or []
        if str(item.get("path", "")).startswith("note/")
        and not str(item.get("path", "")).startswith("note/notations/")
        and item.get("tag") in tags
    ]


def _append_fallbacks(parent: Any, fragments: list[dict[str, Any]], context: ExportContext) -> None:
    for fragment in fragments:
        parsed = _parse_fragment(fragment, context)
        if parsed is not None:
            parent.append(parsed)


def _merged_member(chord: dict[str, Any], member: dict[str, Any]) -> dict[str, Any]:
    merged = {
        key: copy.deepcopy(value)
        for key, value in chord.items()
        if key not in {"notes", "source_refs", "normalized_music21"}
    }
    merged.update(copy.deepcopy(member))
    merged["type"] = "note"
    return merged


def _write_note(parent: Any, event: dict[str, Any], context: ExportContext, *, chord_member: bool = False) -> None:
    note_element = _sub(parent, "note", attributes=event.get("xml_attributes"))
    xml_id = (event.get("source_ref") or {}).get("xml_id")
    if xml_id:
        note_element.set("id", str(xml_id))
    grace = event.get("grace_info") or {}
    if grace.get("present"):
        attributes = dict(grace.get("attributes") or {})
        if grace.get("slash") is not None:
            attributes["slash"] = "yes" if grace["slash"] else "no"
        mapping = {
            "steal_time_previous": "steal-time-previous",
            "steal_time_following": "steal-time-following",
            "make_time": "make-time",
        }
        for json_name, xml_name in mapping.items():
            if grace.get(json_name) is not None:
                attributes[xml_name] = str(grace[json_name])
        _sub(note_element, "grace", attributes=attributes)
    _append_fallbacks(note_element, _note_fallbacks(event, {"cue"}), context)
    if chord_member:
        _sub(note_element, "chord")
    if event.get("type") == "rest":
        rest = event.get("rest") or {}
        attributes = dict(rest.get("attributes") or {})
        if rest.get("measure"):
            attributes["measure"] = "yes"
        _sub(note_element, "rest", attributes=attributes)
        context.stats.rests += 1
    else:
        _write_pitch(note_element, event.get("pitch"), context)
        context.stats.notes += 1
    duration_source = event.get("duration_source") or {}
    if duration_source.get("xml_duration") is not None:
        if int(duration_source["xml_duration"]) < 0:
            context.warn("Negative note duration cannot be exported.", lossy=True)
        _sub(note_element, "duration", duration_source["xml_duration"])
    for tie in (event.get("ties") or {}).get("sound") or []:
        _sub(note_element, "tie", attributes=tie)
        context.stats.ties += 1
    _append_fallbacks(
        note_element,
        _note_fallbacks(event, {"instrument", "footnote", "level"}),
        context,
    )
    if event.get("voice") is not None:
        _sub(note_element, "voice", event["voice"])
    if event.get("written_type") is not None:
        _sub(note_element, "type", event["written_type"])
    for _ in range(int(event.get("dots") or 0)):
        _sub(note_element, "dot")
    _write_accidental(note_element, event.get("accidental_details"))
    _write_time_modification(note_element, event.get("tuplets") or [])
    _append_fallbacks(
        note_element,
        _note_fallbacks(event, {"notehead", "notehead-text"}),
        context,
    )
    if event.get("staff") is not None:
        _sub(note_element, "staff", event["staff"])
    for beam in event.get("beams") or []:
        attributes = dict(beam.get("attributes") or {})
        attributes["number"] = str(beam.get("number", "1"))
        _sub(note_element, "beam", beam.get("value"), attributes)
    _write_notations(note_element, event, context)
    _write_lyrics(note_element, event.get("lyrics") or [], context)
    _append_fallbacks(
        note_element,
        _note_fallbacks(event, {"play", "listen"}),
        context,
    )
    recognized_paths = {
        "note/cue", "note/instrument", "note/footnote", "note/level",
        "note/notehead", "note/notehead-text", "note/play", "note/listen",
    }
    ambiguous = [
        item for item in event.get("unhandled_notations") or []
        if str(item.get("path", "")).startswith("note/")
        and not str(item.get("path", "")).startswith("note/notations/")
        and item.get("path") not in recognized_paths
    ]
    for item in ambiguous:
        context.warn(
            f"Cannot determine schema position for fallback {item.get('path')}.",
            lossy=True,
        )


def _write_key(parent: Any, event: dict[str, Any]) -> None:
    key = _sub(parent, "key", attributes=event.get("attributes"))
    if event.get("cancel") is not None:
        _sub(key, "cancel", event["cancel"], event.get("cancel_attributes"))
    if event.get("custom_components"):
        for item in event["custom_components"]:
            _sub(key, item["type"], item.get("value"), item.get("attributes"))
    else:
        if event.get("fifths") is not None:
            _sub(key, "fifths", event["fifths"])
        if event.get("mode") is not None:
            _sub(key, "mode", event["mode"])


def _write_time(parent: Any, event: dict[str, Any], context: ExportContext) -> None:
    time = _sub(parent, "time", attributes=event.get("attributes"))
    beats = event.get("beats") or []
    beat_types = event.get("beat_types") or []
    for index, beats_value in enumerate(beats):
        _sub(time, "beats", beats_value)
        if index < len(beat_types):
            _sub(time, "beat-type", beat_types[index])
    if event.get("senza_misura") is not None:
        _sub(time, "senza-misura", event["senza_misura"])
    interchangeable = event.get("interchangeable")
    if interchangeable:
        parsed = _parse_fragment(interchangeable, context, count_fallback=False)
        if parsed is not None:
            time.append(parsed)


def _write_clef(parent: Any, event: dict[str, Any]) -> None:
    clef = _sub(parent, "clef", attributes=event.get("attributes"))
    if event.get("sign") is not None:
        _sub(clef, "sign", event["sign"])
    if event.get("line") is not None:
        _sub(clef, "line", event["line"])
    if event.get("octave_change") is not None:
        _sub(clef, "clef-octave-change", event["octave_change"])


def _write_attributes(
    parent: Any,
    entries: list[dict[str, Any]],
    divisions: int,
    context: ExportContext,
    *,
    include_divisions: bool,
) -> None:
    attributes = _sub(parent, "attributes")
    if include_divisions:
        _sub(attributes, "divisions", divisions)
    def attribute_order(item: dict[str, Any]) -> int:
        if item.get("type") == "key_signature":
            return 1
        if item.get("type") == "time_signature":
            return 2
        if item.get("type") == "clef":
            return 6
        tag = item.get("tag") or str(item.get("path", "")).rsplit("/", 1)[-1]
        return {
            "staves": 3, "part-symbol": 4, "instruments": 5,
            "staff-details": 7, "transpose": 8, "for-part": 8,
            "directive": 9, "measure-style": 10,
        }.get(tag, 9)

    for entry in sorted(entries, key=attribute_order):
        if entry.get("type") == "key_signature":
            _write_key(attributes, entry)
        elif entry.get("type") == "time_signature":
            _write_time(attributes, entry, context)
        elif entry.get("type") == "clef":
            _write_clef(attributes, entry)
        elif entry.get("raw_xml"):
            parsed = _parse_fragment(entry, context)
            if parsed is not None:
                attributes.append(parsed)
        else:
            context.warn(f"Unsupported attribute event {entry.get('type')}", lossy=True)


def _write_direction(parent: Any, event: dict[str, Any], cursor: Fraction, divisions: int, context: ExportContext) -> None:
    direction = _sub(parent, "direction", attributes=event.get("attributes"))
    if event.get("placement") is not None:
        direction.set("placement", str(event["placement"]))
    for direction_type in event.get("direction_types") or []:
        container = _sub(direction, "direction-type")
        name = direction_type.get("type")
        if direction_type.get("raw_xml"):
            parsed = _parse_fragment(direction_type, context, count_fallback=False)
            if parsed is not None:
                container.append(parsed)
                continue
        if name == "dynamics":
            dynamics = _sub(container, "dynamics", attributes=direction_type.get("attributes"))
            for value in direction_type.get("values") or []:
                if value and value.replace("-", "").isalnum():
                    _sub(dynamics, value)
                else:
                    _sub(dynamics, "other-dynamics", value)
        else:
            _sub(container, name, direction_type.get("text"), direction_type.get("attributes"))
    target = _event_offset(event)
    difference = (target - cursor) * divisions
    if difference.denominator != 1:
        context.warn(
            f"Direction {event.get('source_ref', {}).get('source_id')} offset cannot be represented "
            f"with divisions={divisions}.",
            lossy=True,
        )
    elif difference:
        _sub(direction, "offset", int(difference))
    if event.get("voice") is not None:
        _sub(direction, "voice", event["voice"])
    if event.get("staff") is not None:
        _sub(direction, "staff", event["staff"])
    if event.get("sound") is not None:
        _sub(direction, "sound", attributes=event["sound"])


def _write_barline(parent: Any, event: dict[str, Any], context: ExportContext) -> None:
    barline = _sub(parent, "barline", attributes={"location": event.get("location")})
    if event.get("bar_style") is not None:
        _sub(barline, "bar-style", event["bar_style"])
    for fragment in event.get("other_musical_elements") or []:
        parsed = _parse_fragment(fragment, context)
        if parsed is not None:
            barline.append(parsed)
    ending = event.get("ending")
    if ending:
        _sub(barline, "ending", ending.get("text"), ending.get("attributes"))
    repeat = event.get("repeat")
    if repeat:
        _sub(barline, "repeat", attributes=repeat.get("attributes"))


def _sequence_items(measure: dict[str, Any]) -> dict[int, list[tuple[str, dict[str, Any]]]]:
    sequence: dict[int, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for event in measure.get("events") or []:
        index = int(event.get("xml_event_index") or (event.get("source_ref") or {}).get("xml_event_index") or 0)
        if event.get("type") in {"key_signature", "time_signature", "clef"}:
            kind = "attribute"
        elif event.get("type") == "direction":
            kind = "direction"
        else:
            kind = "unknown"
        sequence[index].append((kind, event))
    for fallback in measure.get("unhandled_musicxml") or []:
        index = int(fallback.get("xml_event_index") or (fallback.get("source_ref") or {}).get("xml_event_index") or 0)
        kind = "attribute" if str(fallback.get("path", "")).startswith("attributes/") else "fallback"
        sequence[index].append((kind, fallback))
    for timing in measure.get("timing_operations") or []:
        sequence[int(timing.get("xml_event_index") or 0)].append(("timing", timing))
    for barline in measure.get("barlines") or []:
        sequence[int(barline.get("xml_event_index") or 0)].append(("barline", barline))
    for voice in measure.get("voices") or []:
        for event in voice.get("events") or []:
            if event.get("type") == "chord":
                for member_index, member in enumerate(event.get("notes") or []):
                    source_ref = member.get("source_ref") or {}
                    index = int(source_ref.get("xml_event_index") or event.get("xml_event_index") or 0)
                    payload = _merged_member(event, member)
                    payload["_chord_member"] = member_index > 0
                    payload["_logical_chord_root"] = member_index == 0
                    sequence[index].append(("note", payload))
            else:
                index = int(event.get("xml_event_index") or (event.get("source_ref") or {}).get("xml_event_index") or 0)
                payload = copy.deepcopy(event)
                payload["_chord_member"] = False
                payload["_logical_chord_root"] = False
                sequence[index].append(("note", payload))
    return sequence


def _write_measure(parent: Any, measure: dict[str, Any], context: ExportContext) -> None:
    attributes = {"number": str(measure.get("number", ""))}
    if measure.get("implicit"):
        attributes["implicit"] = "yes"
    measure_element = _sub(parent, "measure", attributes=attributes)
    context.stats.measures += 1
    divisions = int(measure.get("divisions") or 1)
    sequence = _sequence_items(measure)
    cursor = Fraction(0)
    divisions_written = False

    observed_divisions = {
        int((event.get("duration_source") or {}).get("divisions"))
        for voice in measure.get("voices") or []
        for event in voice.get("events") or []
        if (event.get("duration_source") or {}).get("divisions") is not None
    }
    observed_divisions.update(
        int(item["divisions"])
        for item in measure.get("timing_operations") or []
        if item.get("divisions") is not None
    )
    if len(observed_divisions) > 1 or (
        observed_divisions and divisions not in observed_divisions
    ):
        context.warn(
            f"Measure {measure.get('number')} contains divisions changes whose source "
            "positions are not represented by the V3 schema.",
            lossy=True,
        )

    for index in sorted(sequence):
        grouped_attributes = [item for kind, item in sequence[index] if kind == "attribute"]
        if grouped_attributes:
            _write_attributes(
                measure_element,
                grouped_attributes,
                divisions,
                context,
                include_divisions=not divisions_written,
            )
            divisions_written = True
        for kind, item in sequence[index]:
            if kind == "attribute":
                continue
            if kind == "timing":
                duration = item.get("xml_duration")
                if duration is None:
                    context.warn(f"Timing operation at index {index} lacks xml_duration.", lossy=True)
                    continue
                _sub(measure_element, item["type"])
                operation = measure_element[-1]
                _sub(operation, "duration", duration)
                amount = Fraction(int(duration), int(item.get("divisions") or divisions))
                cursor += -amount if item["type"] == "backup" else amount
            elif kind == "note":
                is_chord_member = bool(item.pop("_chord_member", False))
                if item.pop("_logical_chord_root", False):
                    context.stats.chords += 1
                if item.get("type") not in {"note", "rest"}:
                    context.warn(
                        f"Unsupported voice event type {item.get('type')} at index {index}.",
                        lossy=True,
                    )
                    continue
                _write_note(measure_element, item, context, chord_member=is_chord_member)
                if not is_chord_member and not item.get("grace"):
                    cursor += _fraction(
                        (item.get("duration_source") or {}).get(
                            "quarter_length_fraction", item.get("duration", 0)
                        )
                    )
                if is_chord_member:
                    pass
            elif kind == "direction":
                _write_direction(measure_element, item, cursor, divisions, context)
            elif kind == "barline":
                _write_barline(measure_element, item, context)
            elif kind == "fallback":
                parsed = _parse_fragment(item, context)
                if parsed is not None:
                    measure_element.append(parsed)
            else:
                context.warn(f"Unsupported sequence item {kind} at index {index}.", lossy=True)

    if not divisions_written:
        # A divisions declaration is needed before any duration-bearing notes.
        attributes_element = etree.Element("attributes")
        _sub(attributes_element, "divisions", divisions)
        measure_element.insert(0, attributes_element)


def build_musicxml(data: dict[str, Any], *, verbose: bool = False) -> tuple[Any, ExportContext]:
    if etree is None:
        raise ExportError("lxml is required. Install it with: python -m pip install lxml")
    context = ExportContext(verbose=verbose)
    root = etree.Element("score-partwise", version="4.0")
    _write_metadata(root, data)
    parts = data["parts"]
    _write_part_list(root, parts)
    context.stats.parts = len(parts)
    for index, part in enumerate(parts, start=1):
        part_id = str(part.get("id") or f"P{index}")
        part_element = _sub(root, "part", attributes={"id": part_id})
        for measure in part.get("measures") or []:
            _write_measure(part_element, measure, context)
    return etree.ElementTree(root), context


def validate_output(tree: Any, context: ExportContext) -> None:
    root = tree.getroot()
    if root.tag != "score-partwise":
        raise ExportError("Generated XML root is not score-partwise.")
    listed = {
        item.get("id")
        for item in root.xpath("./part-list/score-part")
    }
    actual = {item.get("id") for item in root.xpath("./part")}
    if listed != actual:
        context.warn(f"Part-list IDs {listed} do not match score parts {actual}.", lossy=True)
    for note in root.xpath(".//note"):
        duration = note.find("duration")
        if duration is not None and int(duration.text or 0) < 0:
            context.warn("Generated note has a negative duration.", lossy=True)
        if note.find("chord") is not None:
            previous = note.getprevious()
            if previous is None or previous.tag != "note":
                context.warn("Chord continuation does not follow a note.", lossy=True)
        voice = note.findtext("voice")
        staff = note.findtext("staff")
        if voice is not None and not voice.strip():
            context.warn("Generated note has an empty voice.", lossy=True)
        if staff is not None and int(staff) < 1:
            context.warn("Generated note has an invalid staff number.", lossy=True)


def save_musicxml(tree: Any, output_path: str | Path) -> None:
    try:
        tree.write(
            str(output_path),
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
            standalone=False,
        )
        etree.parse(str(output_path))
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ExportError(f"Could not write valid MusicXML: {exc}") from exc


def _print_summary(context: ExportContext) -> None:
    stats = context.stats
    print("JSON → MusicXML export summary", file=sys.stderr)
    print("------------------------------", file=sys.stderr)
    values = {
        "Parts": stats.parts, "Measures": stats.measures, "Notes": stats.notes,
        "Rests": stats.rests, "Chords": stats.chords, "Lyrics": stats.lyrics,
        "Slurs": stats.slurs, "Ties": stats.ties, "Tuplets": stats.tuplets,
        "Unhandled fallback fragments reinserted": stats.fallbacks,
        "Lossy conditions": len(context.lossy_conditions),
    }
    for name, value in values.items():
        print(f"{name}: {value}", file=sys.stderr)


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    verbose: bool = False,
    strict: bool = False,
) -> ExportContext:
    data = load_json(input_path)
    tree, context = build_musicxml(data, verbose=verbose)
    validate_output(tree, context)
    save_musicxml(tree, output_path)
    if verbose:
        _print_summary(context)
    if strict and context.lossy_conditions:
        raise ExportError(
            f"Strict mode found {len(context.lossy_conditions)} lossy condition(s); "
            f"diagnostic MusicXML was written to '{output_path}'."
        )
    return context


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconstruct MusicXML from V3 JSON.")
    parser.add_argument("input", help="Input V3 .json file")
    parser.add_argument("output", help="Output .musicxml file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print warnings and statistics")
    parser.add_argument("--strict", action="store_true", help="Fail if semantic data cannot be reconstructed")
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    try:
        convert_file(args.input, args.output, verbose=args.verbose, strict=args.strict)
    except ExportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: unexpected export failure: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote MusicXML to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
