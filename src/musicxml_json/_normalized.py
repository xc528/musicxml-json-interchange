#!/usr/bin/env python3
"""Convert MusicXML into a rich, notation-aware JSON representation.

Version 2 preserves voices, chords, lyrics, spanners, rhythmic notation, and
measure-level directions.  It intentionally produces only standard JSON types.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    from music21 import (
        bar,
        chord,
        clef,
        converter,
        dynamics,
        expressions,
        key,
        meter,
        note,
        spanner,
        stream,
        tempo,
    )
except ImportError:  # The CLI reports a concise installation hint below.
    bar = chord = clef = converter = dynamics = expressions = None
    key = meter = note = spanner = stream = tempo = None


SUPPORTED_EXTENSIONS = {".musicxml", ".xml"}


class MusicXMLConversionError(Exception):
    """Raised when the input cannot be converted into a meaningful score."""


@dataclass
class ParseStats:
    """Counters printed by --verbose to help validate rich-score detection."""

    parts: int = 0
    measures: int = 0
    voices: int = 0
    chords: int = 0
    lyrics: int = 0
    slurs: int = 0
    tuplets: int = 0


@dataclass
class NotationContext:
    """Cross-measure relationships that music21 stores as spanners."""

    slurs: dict[int, list[dict[str, Any]]]
    tremolos: dict[int, list[dict[str, Any]]]
    wedge_events: dict[int, list[dict[str, Any]]]
    endings: dict[int, list[dict[str, Any]]]
    stats: ParseStats
    warn: Callable[[str], None]


def _number(value: Any) -> int | float | None:
    """Convert music21 numeric values (including Fractions) for JSON."""
    if value is None:
        return None
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def _text(value: Any) -> str | None:
    """Convert metadata and identifier values without emitting 'None'."""
    return None if value is None else str(value)


def _class_name(value: Any) -> str:
    """Convert a music21 class name such as StrongAccent to strong-accent."""
    name = type(value).__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def _offset(element: Any, container: Any) -> int | float:
    """Return an element's position relative to a measure."""
    try:
        return _number(element.getOffsetInHierarchy(container)) or 0
    except Exception:
        return _number(getattr(element, "offset", 0)) or 0


def parse_musicxml(input_path: str | Path) -> Any:
    """Validate the path/XML root and parse a MusicXML score with music21."""
    path = Path(input_path)
    if not path.is_file():
        raise MusicXMLConversionError(f"Input file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise MusicXMLConversionError(
            f"Unsupported input extension '{path.suffix}'. Expected .musicxml or .xml."
        )

    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise MusicXMLConversionError(f"Malformed or unreadable XML: {exc}") from exc

    root_name = root.tag.rsplit("}", 1)[-1]
    if root_name not in {"score-partwise", "score-timewise"}:
        raise MusicXMLConversionError(
            f"Unsupported XML root <{root_name}>; expected a MusicXML score."
        )
    if converter is None:
        raise MusicXMLConversionError(
            "music21 is not installed. Install it with: python -m pip install music21"
        )

    try:
        score = converter.parse(str(path), format="musicxml")
    except Exception as exc:
        raise MusicXMLConversionError(
            f"Could not parse '{path}' as MusicXML: {exc}"
        ) from exc

    if not isinstance(score, stream.Score) or not score.parts:
        raise MusicXMLConversionError("The MusicXML file contains no score parts.")
    if not any(part.getElementsByClass(stream.Measure) for part in score.parts):
        raise MusicXMLConversionError("The MusicXML score contains no measures.")
    return score


def _contributors(metadata: Any, role: str) -> list[str]:
    """Return all contributor names for a metadata role."""
    if metadata is None:
        return []
    try:
        people = metadata.getContributorsByRole(role) or []
        return [str(person.name) for person in people if person.name]
    except Exception:
        value = getattr(metadata, role, None)
        return [str(value)] if value else []


def extract_score_metadata(score: Any) -> dict[str, Any]:
    """Extract score-level bibliographic metadata, using null when absent."""
    metadata = getattr(score, "metadata", None)
    composers = _contributors(metadata, "composer")
    lyricists = _contributors(metadata, "lyricist")
    return {
        "title": _text(getattr(metadata, "title", None)),
        "composer": composers[0] if composers else None,
        "composers": composers,
        "lyricist": lyricists[0] if lyricists else None,
        "lyricists": lyricists,
        "movement_title": _text(getattr(metadata, "movementName", None)),
        "movement_number": _text(getattr(metadata, "movementNumber", None)),
        "copyright": _text(getattr(metadata, "copyright", None)),
    }


def _relationship_marks(elements: list[Any], number: Any) -> dict[int, list[dict[str, Any]]]:
    """Mark the first, middle, and final elements of a spanner relationship."""
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, element in enumerate(elements):
        if len(elements) == 1:
            mark_type = "start"
        elif index == 0:
            mark_type = "start"
        elif index == len(elements) - 1:
            mark_type = "stop"
        else:
            mark_type = "continue"
        result[id(element)].append({"type": mark_type, "number": number})
    return result


def _merge_marks(
    target: dict[int, list[dict[str, Any]]],
    incoming: dict[int, list[dict[str, Any]]],
) -> None:
    for element_id, marks in incoming.items():
        target[element_id].extend(marks)


def _build_notation_context(
    score: Any, stats: ParseStats, warn: Callable[[str], None]
) -> NotationContext:
    """Index slurs, wedges, tremolo spanners, and volta brackets by element."""
    slur_marks: dict[int, list[dict[str, Any]]] = defaultdict(list)
    tremolo_marks: dict[int, list[dict[str, Any]]] = defaultdict(list)
    wedge_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    endings: dict[int, list[dict[str, Any]]] = defaultdict(list)

    element_measure: dict[int, Any] = {}
    for part in score.parts:
        for measure in part.getElementsByClass(stream.Measure):
            element_measure[id(measure)] = measure
            for element in measure.recurse():
                element_measure[id(element)] = measure

    all_spanners = list(score.recurse().getElementsByClass(spanner.Spanner))
    for spanner_index, relation in enumerate(all_spanners, start=1):
        elements = list(relation.getSpannedElements())
        number = _text(getattr(relation, "idLocal", None)) or str(spanner_index)

        if isinstance(relation, spanner.Slur):
            stats.slurs += 1
            _merge_marks(slur_marks, _relationship_marks(elements, number))
            continue

        if isinstance(relation, expressions.TremoloSpanner):
            _merge_marks(tremolo_marks, _relationship_marks(elements, number))
            continue

        if isinstance(relation, dynamics.DynamicWedge):
            relationship = _relationship_marks(elements, number)
            for element in elements:
                measure = element_measure.get(id(element))
                if measure is None:
                    continue
                for mark in relationship[id(element)]:
                    wedge_events[id(measure)].append(
                        {
                            "type": _class_name(relation),
                            "action": mark["type"],
                            "number": number,
                            "offset": _offset(element, measure),
                            "placement": _text(getattr(relation, "placement", None)),
                        }
                    )
            continue

        if isinstance(relation, spanner.RepeatBracket):
            for index, measure in enumerate(elements):
                if not isinstance(measure, stream.Measure):
                    continue
                if len(elements) == 1:
                    bracket_type = "start-stop"
                elif index == 0:
                    bracket_type = "start"
                elif index == len(elements) - 1:
                    bracket_type = "stop"
                else:
                    bracket_type = "continue"
                endings[id(measure)].append(
                    {
                        "type": bracket_type,
                        "number": _text(getattr(relation, "number", None)),
                    }
                )
            continue

        warn(
            f"Spanner {type(relation).__name__} is not represented in the v2 schema."
        )

    return NotationContext(
        slurs=slur_marks,
        tremolos=tremolo_marks,
        wedge_events=wedge_events,
        endings=endings,
        stats=stats,
        warn=warn,
    )


def extract_lyrics(note_object: Any, stats: ParseStats) -> list[dict[str, Any]]:
    """Extract every lyric/verse attached to a note, including composites."""
    lyrics: list[dict[str, Any]] = []
    for lyric in getattr(note_object, "lyrics", ()):
        stats.lyrics += 1
        item: dict[str, Any] = {
            "number": _text(getattr(lyric, "identifier", None))
            or _text(getattr(lyric, "number", None)),
            "text": _text(getattr(lyric, "text", None)) or "",
            "syllabic": _text(getattr(lyric, "syllabic", None)),
            # music21 currently does not retain MusicXML <extend> consistently.
            "extend": getattr(lyric, "extend", None),
        }
        components = getattr(lyric, "components", None)
        if components:
            item["components"] = [
                {
                    "text": _text(component.text) or "",
                    "syllabic": _text(component.syllabic),
                    "elision_before": _text(component.elisionBefore),
                }
                for component in components
            ]
        lyrics.append(item)
    return lyrics


def extract_articulations(note_object: Any) -> list[str]:
    """Return normalized names for all music21 articulations."""
    names = [_class_name(item) for item in getattr(note_object, "articulations", ())]
    # Fermata is represented by music21 as an expression, though musically it
    # belongs with the requested per-note articulation/marking information.
    for item in getattr(note_object, "expressions", ()):
        if isinstance(item, expressions.Fermata):
            names.append("fermata")
    return names


def extract_slurs(note_object: Any, context: NotationContext) -> list[dict[str, Any]]:
    """Return indexed slur start/continue/stop marks for an event."""
    return list(context.slurs.get(id(note_object), ()))


def extract_tuplets(note_object: Any, stats: ParseStats) -> list[dict[str, Any]]:
    """Extract rhythmic ratios and written start/stop boundaries."""
    result: list[dict[str, Any]] = []
    for tuplet in getattr(note_object.duration, "tuplets", ()):
        stats.tuplets += 1
        actual_duration = getattr(tuplet, "durationActual", None)
        normal_duration = getattr(tuplet, "durationNormal", None)
        result.append(
            {
                "actual_notes": int(tuplet.numberNotesActual),
                "normal_notes": int(tuplet.numberNotesNormal),
                "type": _text(tuplet.type),
                "id": _number(getattr(tuplet, "tupletId", None)),
                "nested_level": _number(getattr(tuplet, "nestedLevel", None)),
                "actual_note_type": _text(getattr(actual_duration, "type", None)),
                "normal_note_type": _text(getattr(normal_duration, "type", None)),
                "bracket": getattr(tuplet, "bracket", None),
                "placement": _text(getattr(tuplet, "placement", None)),
            }
        )
    return result


def _extract_beams(note_object: Any) -> list[dict[str, Any]]:
    beams = getattr(note_object, "beams", None)
    if beams is None:
        return []
    return [
        {
            "number": _number(item.number),
            "type": _text(item.type),
            "direction": _text(item.direction),
        }
        for item in beams.beamsList
    ]


def _extract_ties(note_object: Any) -> list[dict[str, Any]]:
    tie = getattr(note_object, "tie", None)
    return [] if tie is None else [{"type": _text(tie.type)}]


def _staff_number(note_object: Any) -> int | str | None:
    """Return staff identity when music21 exposes it on the parsed object."""
    for attribute in ("staffNumber", "staff"):
        value = getattr(note_object, attribute, None)
        if value is not None:
            return _number(value) if isinstance(value, (int, float)) else str(value)
    return None


def _pitch_to_dict(pitch: Any) -> dict[str, Any]:
    accidental = pitch.accidental
    return {
        "step": pitch.step,
        "alter": 0 if accidental is None else _number(accidental.alter),
        "accidental": None if accidental is None else _text(accidental.name),
        "accidental_explicit": (
            None if accidental is None else accidental.displayStatus
        ),
        "octave": pitch.octave,
    }


def _extract_ornaments(note_object: Any, context: NotationContext) -> list[Any]:
    ornaments: list[Any] = []
    for item in getattr(note_object, "expressions", ()):
        if isinstance(item, expressions.Ornament):
            ornament: dict[str, Any] = {"type": _class_name(item)}
            if isinstance(item, expressions.Tremolo):
                ornament["marks"] = _number(getattr(item, "numberOfMarks", None))
            ornaments.append(ornament)
    for mark in context.tremolos.get(id(note_object), ()):
        ornaments.append({"type": "tremolo-spanner", **mark})
    return ornaments


def _common_event_fields(
    event: Any,
    measure: Any,
    voice_id: str,
    context: NotationContext,
) -> dict[str, Any]:
    """Extract rhythmic and notational fields shared by notes/chords/rests."""
    duration = event.duration
    is_grace = bool(getattr(duration, "isGrace", False))
    result: dict[str, Any] = {
        "duration": _number(duration.quarterLength),
        "written_type": _text(getattr(duration, "type", None)),
        "dots": int(getattr(duration, "dots", 0) or 0),
        "offset": _offset(event, measure),
        "voice": voice_id,
        "staff": _staff_number(event),
        "grace": is_grace,
    }
    if is_grace:
        result["grace_slash"] = getattr(duration, "slash", None)
    result["tuplets"] = extract_tuplets(event, context.stats)
    result["beams"] = _extract_beams(event)
    return result


def extract_note(
    note_object: Any,
    measure: Any,
    voice_id: str,
    context: NotationContext,
) -> dict[str, Any]:
    """Convert a pitched note and all accessible attached notation."""
    result = {
        "type": "note",
        **_pitch_to_dict(note_object.pitch),
        **_common_event_fields(note_object, measure, voice_id, context),
        "lyrics": extract_lyrics(note_object, context.stats),
        "ties": _extract_ties(note_object),
        "slurs": extract_slurs(note_object, context),
        "articulations": extract_articulations(note_object),
        "ornaments": _extract_ornaments(note_object, context),
    }
    return result


def extract_rest(
    rest_object: Any,
    measure: Any,
    voice_id: str,
    context: NotationContext,
) -> dict[str, Any]:
    """Convert a rest while retaining written duration and position."""
    full_measure = getattr(rest_object, "fullMeasure", False)
    return {
        "type": "rest",
        **_common_event_fields(rest_object, measure, voice_id, context),
        # music21 uses the string "auto" when this was not explicitly set.
        "full_measure": full_measure if isinstance(full_measure, bool) else None,
    }


def _chord_member(note_object: Any, context: NotationContext) -> dict[str, Any]:
    """Extract a chord pitch plus pitch-specific ties, lyrics, and slurs."""
    return {
        **_pitch_to_dict(note_object.pitch),
        "ties": _extract_ties(note_object),
        "lyrics": extract_lyrics(note_object, context.stats),
        "slurs": extract_slurs(note_object, context),
    }


def extract_chord(
    chord_object: Any,
    measure: Any,
    voice_id: str,
    context: NotationContext,
) -> dict[str, Any]:
    """Preserve simultaneous pitches as one chord event."""
    context.stats.chords += 1
    return {
        "type": "chord",
        **_common_event_fields(chord_object, measure, voice_id, context),
        "notes": [_chord_member(item, context) for item in chord_object.notes],
        "lyrics": extract_lyrics(chord_object, context.stats),
        "ties": _extract_ties(chord_object),
        "slurs": extract_slurs(chord_object, context),
        "articulations": extract_articulations(chord_object),
        "ornaments": _extract_ornaments(chord_object, context),
    }


def _extract_event(
    event: Any,
    measure: Any,
    voice_id: str,
    context: NotationContext,
) -> dict[str, Any] | None:
    try:
        if isinstance(event, note.Rest):
            return extract_rest(event, measure, voice_id, context)
        if isinstance(event, chord.Chord):
            return extract_chord(event, measure, voice_id, context)
        if isinstance(event, note.Note):
            return extract_note(event, measure, voice_id, context)
        context.warn(f"Skipping unsupported musical event {type(event).__name__}.")
    except Exception as exc:
        context.warn(f"Skipping {type(event).__name__} that could not be parsed: {exc}")
    return None


def extract_voice(
    voice_id: str,
    events: list[Any],
    measure: Any,
    context: NotationContext,
) -> dict[str, Any]:
    """Convert one explicit or synthesized voice without flattening others."""
    converted = [
        item
        for event in events
        if (item := _extract_event(event, measure, voice_id, context)) is not None
    ]
    converted.sort(key=lambda item: (item["offset"], item["type"]))
    return {"id": voice_id, "events": converted}


def _key_to_dict(signature: Any) -> dict[str, Any]:
    result = {
        "sharps": int(signature.sharps),
        "tonic": None,
        "mode": None,
    }
    if isinstance(signature, key.Key):
        result["tonic"] = signature.tonic.name
        result["mode"] = _text(signature.mode)
    return result


def _time_to_dict(signature: Any) -> dict[str, Any]:
    return {
        "ratio": signature.ratioString,
        "numerator": int(signature.numerator),
        "denominator": int(signature.denominator),
        "symbol": _text(getattr(signature, "symbol", None)),
    }


def _clef_to_dict(clef_object: Any) -> dict[str, Any]:
    return {
        "sign": _text(getattr(clef_object, "sign", None)),
        "line": _number(getattr(clef_object, "line", None)),
        "octave_change": _number(getattr(clef_object, "octaveChange", None)),
    }


def _tempo_to_dict(mark: Any, measure: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "tempo",
        "offset": _offset(mark, measure),
        "text": _text(getattr(mark, "text", None)),
        "bpm": None,
        "quarter_bpm": None,
        "beat_unit": None,
        "beat_unit_dots": None,
    }
    if isinstance(mark, tempo.MetronomeMark):
        item["bpm"] = _number(mark.number)
        try:
            item["quarter_bpm"] = _number(mark.getQuarterBPM())
        except Exception:
            pass
        referent = getattr(mark, "referent", None)
        if referent is not None:
            item["beat_unit"] = _text(referent.type)
            item["beat_unit_dots"] = int(referent.dots or 0)
    return item


def _measure_level_events(measure: Any, context: NotationContext) -> list[dict[str, Any]]:
    """Extract directions and text that live at measure rather than note level."""
    events: list[dict[str, Any]] = []
    seen: set[int] = set()

    for mark in measure.recurse().getElementsByClass(tempo.TempoIndication):
        seen.add(id(mark))
        events.append(_tempo_to_dict(mark, measure))

    for mark in measure.recurse().getElementsByClass(dynamics.Dynamic):
        seen.add(id(mark))
        events.append(
            {
                "type": "dynamic",
                "value": _text(mark.value),
                "offset": _offset(mark, measure),
                "placement": _text(getattr(mark, "placement", None)),
            }
        )

    for mark in measure.recurse().getElementsByClass(expressions.RehearsalMark):
        seen.add(id(mark))
        events.append(
            {
                "type": "rehearsal_mark",
                "text": _text(getattr(mark, "content", None)),
                "offset": _offset(mark, measure),
            }
        )

    for mark in measure.recurse().getElementsByClass(expressions.TextExpression):
        if id(mark) in seen:
            continue
        events.append(
            {
                "type": "text_expression",
                "text": _text(getattr(mark, "content", None)),
                "offset": _offset(mark, measure),
                "placement": _text(getattr(mark, "placement", None)),
            }
        )

    events.extend(context.wedge_events.get(id(measure), ()))
    events.sort(key=lambda item: (item.get("offset", 0), item["type"]))
    return events


def _barline_to_dict(barline: Any) -> dict[str, Any] | None:
    if barline is None:
        return None
    result: dict[str, Any] = {"type": _text(getattr(barline, "type", None))}
    if isinstance(barline, bar.Repeat):
        result.update(
            {
                "repeat": True,
                "direction": _text(barline.direction),
                "times": _number(getattr(barline, "times", None)),
            }
        )
    else:
        result["repeat"] = False
    return result


def _attribute_changes(measure: Any) -> list[tuple[int | float, str, Any]]:
    """Collect every key, meter, and clef change, including mid-measure ones."""
    changes: list[tuple[int | float, str, Any]] = []
    for item in measure.recurse().getElementsByClass(meter.TimeSignature):
        changes.append((_offset(item, measure), "time_signature", item))
    for item in measure.recurse().getElementsByClass(key.KeySignature):
        changes.append((_offset(item, measure), "key_signature", item))
    for item in measure.recurse().getElementsByClass(clef.Clef):
        changes.append((_offset(item, measure), "clef", item))
    changes.sort(key=lambda change: change[0])
    return changes


def extract_measure(
    measure: Any,
    state: dict[str, Any],
    context: NotationContext,
) -> dict[str, Any]:
    """Extract signatures, voices, musical events, markings, and barlines."""
    changes = _attribute_changes(measure)
    change_events: list[dict[str, Any]] = []

    # Offset-zero attributes define the measure's active header state. Later
    # changes are retained as events and become the state inherited next time.
    for offset, change_type, value in changes:
        if change_type == "time_signature":
            details = _time_to_dict(value)
            event = {"type": change_type, "offset": offset, **details}
            if offset == 0:
                state["time_signature"] = details
        elif change_type == "key_signature":
            details = _key_to_dict(value)
            event = {"type": change_type, "offset": offset, **details}
            if offset == 0:
                state["key"] = details
        else:
            details = _clef_to_dict(value)
            event = {"type": change_type, "offset": offset, **details}
            if offset == 0:
                state["clef"] = details
        change_events.append(event)

    active_time = dict(state["time_signature"]) if state.get("time_signature") else None
    active_key = dict(state["key"]) if state.get("key") else None
    active_clef = dict(state["clef"]) if state.get("clef") else None

    explicit_voices = list(measure.getElementsByClass(stream.Voice))
    claimed_event_ids: set[int] = set()
    voices: list[dict[str, Any]] = []
    used_voice_ids: set[str] = set()

    for voice_index, voice in enumerate(explicit_voices, start=1):
        voice_events = list(voice.recurse().notesAndRests)
        claimed_event_ids.update(id(item) for item in voice_events)
        voice_id = _text(voice.id) or str(voice_index)
        used_voice_ids.add(voice_id)
        voices.append(extract_voice(voice_id, voice_events, measure, context))

    unclaimed = [
        item
        for item in measure.recurse().notesAndRests
        if id(item) not in claimed_event_ids
    ]
    if unclaimed or not voices:
        default_id = "1" if not voices and "1" not in used_voice_ids else "default"
        voices.append(extract_voice(default_id, unclaimed, measure, context))

    context.stats.measures += 1
    context.stats.voices += len(voices)

    # Apply all changes after capturing the state at the start of this measure.
    for _offset_value, change_type, value in changes:
        if change_type == "time_signature":
            state["time_signature"] = _time_to_dict(value)
        elif change_type == "key_signature":
            state["key"] = _key_to_dict(value)
        else:
            state["clef"] = _clef_to_dict(value)

    events = change_events + _measure_level_events(measure, context)
    events.sort(key=lambda item: (item.get("offset", 0), item["type"]))
    return {
        "number": measure.number,
        "number_suffix": _text(getattr(measure, "numberSuffix", None)),
        # These three fields preserve the convenient v1-style active state.
        "time_signature": None if active_time is None else active_time["ratio"],
        "key_signature": None if active_key is None else active_key["sharps"],
        "clef": active_clef,
        "time_signature_details": active_time,
        "key": active_key,
        "barline": {
            "left": _barline_to_dict(measure.leftBarline),
            "right": _barline_to_dict(measure.rightBarline),
        },
        "repeats": [
            {"location": location, **barline_data}
            for location, barline_data in (
                ("left", _barline_to_dict(measure.leftBarline)),
                ("right", _barline_to_dict(measure.rightBarline)),
            )
            if barline_data and barline_data.get("repeat")
        ],
        "endings": list(context.endings.get(id(measure), ())),
        "events": events,
        "voices": voices,
    }


def extract_part(part: Any, index: int, context: NotationContext) -> dict[str, Any]:
    """Extract instrument identity and every measure in one score part."""
    instrument = part.getInstrument(returnDefault=False)
    name = part.partName
    if not name and instrument is not None:
        name = instrument.instrumentName or instrument.bestName()
    name = name or f"Part {index}"

    state: dict[str, Any] = {
        "time_signature": None,
        "key": None,
        "clef": None,
    }
    measures = [
        extract_measure(measure, state, context)
        for measure in part.getElementsByClass(stream.Measure)
    ]
    return {
        "id": _text(part.id),
        "name": str(name),
        "abbreviation": _text(getattr(part, "partAbbreviation", None)),
        "instrument": None
        if instrument is None
        else {
            "name": _text(instrument.instrumentName or instrument.bestName()),
            "abbreviation": _text(getattr(instrument, "instrumentAbbreviation", None)),
            "midi_program": _number(getattr(instrument, "midiProgram", None)),
            "midi_channel": _number(getattr(instrument, "midiChannel", None)),
        },
        "measures": measures,
    }


def score_to_dict(
    score: Any, *, verbose: bool = False
) -> tuple[dict[str, Any], ParseStats]:
    """Convert a parsed score and return JSON-ready data plus debug counters."""
    stats = ParseStats(parts=len(score.parts))

    def warn(message: str) -> None:
        if verbose:
            print(f"Warning: {message}", file=sys.stderr)

    context = _build_notation_context(score, stats, warn)
    metadata = extract_score_metadata(score)
    result = {
        "format_version": 2,
        # Root title is retained for compatibility with the v1 output.
        "title": metadata["title"],
        "metadata": metadata,
        "parts": [
            extract_part(part, index, context)
            for index, part in enumerate(score.parts, start=1)
        ],
    }
    return result, stats


def save_json(data: dict[str, Any], output_path: str | Path) -> None:
    """Write readable, Unicode-preserving JSON."""
    path = Path(output_path)
    try:
        with path.open("w", encoding="utf-8") as output_file:
            json.dump(data, output_file, ensure_ascii=False, indent=2)
            output_file.write("\n")
    except (OSError, TypeError, ValueError) as exc:
        raise MusicXMLConversionError(f"Could not write '{path}': {exc}") from exc


def _print_stats(stats: ParseStats) -> None:
    print("Detection summary:", file=sys.stderr)
    print(f"  parts:   {stats.parts}", file=sys.stderr)
    print(f"  measures:{stats.measures:>4}", file=sys.stderr)
    print(f"  voices:  {stats.voices}", file=sys.stderr)
    print(f"  chords:  {stats.chords}", file=sys.stderr)
    print(f"  lyrics:  {stats.lyrics}", file=sys.stderr)
    print(f"  slurs:   {stats.slurs}", file=sys.stderr)
    print(f"  tuplets: {stats.tuplets}", file=sys.stderr)


def convert_file(
    input_path: str | Path, output_path: str | Path, *, verbose: bool = False
) -> ParseStats:
    """Parse, convert, and save one MusicXML file."""
    score = parse_musicxml(input_path)
    data, stats = score_to_dict(score, verbose=verbose)
    save_json(data, output_path)
    if verbose:
        _print_stats(stats)
    return stats


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert MusicXML to rich, notation-aware JSON."
    )
    parser.add_argument("input", help="Path to an input .musicxml or .xml file")
    parser.add_argument("output", help="Path for the output .json file")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detected parts, measures, voices, chords, lyrics, slurs, and tuplets",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    try:
        convert_file(args.input, args.output, verbose=args.verbose)
    except MusicXMLConversionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # Last-resort guard for unexpected library behavior.
        print(f"Error: unexpected conversion failure: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote JSON to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
