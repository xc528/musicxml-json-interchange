#!/usr/bin/env python3
"""Hybrid MusicXML -> semantically rich JSON converter (format 3.0).

music21 supplies normalized musical meaning.  lxml supplies exact source
notation and deterministic source references.  Layout-only data is omitted.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
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

try:
    from music21 import converter, stream
except ImportError:
    converter = stream = None  # type: ignore[assignment]

try:
    from . import _normalized as v2
except ImportError:
    v2 = None  # type: ignore[assignment]

from . import SCHEMA_VERSION, __version__


SUPPORTED_EXTENSIONS = {".musicxml", ".xml"}
LAYOUT_ATTRIBUTES = {
    "default-x", "default-y", "relative-x", "relative-y", "font-family",
    "font-style", "font-size", "font-weight", "color", "halign", "valign",
    "print-object", "print-spacing",
}
LAYOUT_ELEMENTS = {
    "print", "appearance", "defaults", "credit", "system-layout",
    "page-layout", "staff-layout", "measure-layout",
}
NOTE_HANDLED_CHILDREN = {
    "pitch", "unpitched", "rest", "duration", "tie", "grace", "chord",
    "voice", "type", "dot", "accidental", "time-modification", "staff",
    "beam", "notations", "lyric", "stem",
}
NOTATION_HANDLED_CHILDREN = {
    "tied", "slur", "tuplet", "articulations", "ornaments", "fermata",
}


class MusicXMLConversionError(Exception):
    """A fatal input, dependency, parsing, or output error."""


@dataclass
class V3Stats:
    parts: int = 0
    measures: int = 0
    notes: int = 0
    rests: int = 0
    chords: int = 0
    staff_assignments: int = 0
    lyrics: int = 0
    lyric_extensions: int = 0
    ties: int = 0
    tied_notations: int = 0
    slurs: int = 0
    tuplets: int = 0
    grace_notes: int = 0
    beams: int = 0
    articulations: int = 0
    ornaments: int = 0
    directions: int = 0
    unhandled: int = 0
    reconciliation_warnings: int = 0
    voice_keys: set[tuple[str, str]] = field(default_factory=set)
    unhandled_tags: set[str] = field(default_factory=set)


@dataclass
class ParseContext:
    stats: V3Stats
    verbose: bool = False
    warnings: list[str] = field(default_factory=list)
    lossy_conditions: list[str] = field(default_factory=list)

    def warn(self, message: str, *, lossy: bool = False) -> None:
        self.warnings.append(message)
        if lossy:
            self.lossy_conditions.append(message)
        if self.verbose:
            print(f"WARNING: {message}", file=sys.stderr)


def _tag(element: Any) -> str:
    return etree.QName(element).localname


def _children(element: Any, name: str | None = None) -> list[Any]:
    values = [child for child in element if isinstance(child.tag, str)]
    return values if name is None else [child for child in values if _tag(child) == name]


def _child(element: Any, name: str) -> Any | None:
    return next(iter(_children(element, name)), None)


def _child_text(element: Any, name: str) -> str | None:
    found = _child(element, name)
    return None if found is None or found.text is None else found.text


def _clean_text(value: str | None) -> str | None:
    return value if value not in (None, "") else None


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def _fraction(value: str | int | None, divisions: int) -> Fraction:
    if value in (None, ""):
        return Fraction(0)
    return Fraction(int(value), divisions)


def _fraction_number(value: Fraction) -> int | float:
    return int(value) if value.denominator == 1 else float(value)


def _fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _attrs(element: Any) -> dict[str, str]:
    return {
        etree.QName(key).localname: value
        for key, value in element.attrib.items()
        if etree.QName(key).localname not in LAYOUT_ATTRIBUTES
    }


def _semantic_clone(element: Any) -> Any:
    clone = copy.deepcopy(element)
    for node in clone.iter():
        for key in list(node.attrib):
            if etree.QName(key).localname in LAYOUT_ATTRIBUTES:
                del node.attrib[key]
    return clone


def _raw_xml(element: Any) -> str:
    return etree.tostring(_semantic_clone(element), encoding="unicode", with_tail=False)


def _generic_element(element: Any, path: str) -> dict[str, Any]:
    return {
        "path": path,
        "tag": _tag(element),
        "attributes": _attrs(element),
        "text": _clean_text(element.text),
        "raw_xml": _raw_xml(element),
    }


def _safe_id_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return cleaned or "unknown"


def _source_ref(
    part_id: str,
    measure_number: str,
    measure_index: int,
    event_index: int,
    element: Any,
    *,
    voice: str | None = None,
    staff: int | None = None,
    suffix: str = "",
) -> dict[str, Any]:
    generated = (
        f"{_safe_id_component(part_id)}-M{_safe_id_component(measure_number)}"
        f"-E{event_index:03d}{suffix}"
    )
    xml_id = element.get("id")
    return {
        "part_id": part_id,
        "measure_number": measure_number,
        "measure_index": measure_index,
        "voice": voice,
        "staff": staff,
        "xml_event_index": event_index,
        "xml_id": xml_id,
        "source_id": xml_id or generated,
    }


def parse_with_music21(input_path: str | Path) -> Any:
    """Parse normalized semantics using music21."""
    if converter is None or stream is None or v2 is None:
        raise MusicXMLConversionError(
            "music21 is required. Install it with: python -m pip install music21"
        )
    try:
        score = converter.parse(str(input_path), format="musicxml")
    except Exception as exc:
        raise MusicXMLConversionError(f"music21 could not parse the score: {exc}") from exc
    if not isinstance(score, stream.Score) or not score.parts:
        raise MusicXMLConversionError("The MusicXML file contains no score parts.")
    return score


def parse_xml_tree(input_path: str | Path) -> Any:
    """Parse the fidelity layer securely using lxml."""
    if etree is None:
        raise MusicXMLConversionError(
            "lxml is required. Install it with: python -m pip install lxml"
        )
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        tree = etree.parse(str(input_path), parser)
    except (OSError, etree.XMLSyntaxError) as exc:
        raise MusicXMLConversionError(f"Malformed or unreadable MusicXML: {exc}") from exc
    root_name = _tag(tree.getroot())
    if root_name != "score-partwise":
        raise MusicXMLConversionError(
            f"V3 currently requires score-partwise MusicXML; found <{root_name}>."
        )
    return tree


def _validate_input_path(input_path: str | Path) -> Path:
    path = Path(input_path)
    if not path.is_file():
        raise MusicXMLConversionError(f"Input file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise MusicXMLConversionError(
            f"Unsupported extension '{path.suffix}'; expected .musicxml or .xml."
        )
    return path


def extract_score_metadata(m21_score: Any, root: Any) -> dict[str, Any]:
    """Merge normalized metadata with exact MusicXML identification values."""
    normalized = v2.extract_score_metadata(m21_score)
    creators = []
    rights = []
    identification = _child(root, "identification")
    if identification is not None:
        creators = [
            {"type": item.get("type"), "text": item.text or ""}
            for item in _children(identification, "creator")
        ]
        rights = [
            {"type": item.get("type"), "text": item.text or ""}
            for item in _children(identification, "rights")
        ]
    work = _child(root, "work")
    source = {
        "work_number": None if work is None else _child_text(work, "work-number"),
        "work_title": None if work is None else _child_text(work, "work-title"),
        "movement_number": _child_text(root, "movement-number"),
        "movement_title": _child_text(root, "movement-title"),
        "creators": creators,
        "rights": rights,
    }
    return {**normalized, "source": source}


def extract_pitch(note_element: Any) -> dict[str, Any] | None:
    pitch = _child(note_element, "pitch")
    if pitch is None:
        unpitched = _child(note_element, "unpitched")
        if unpitched is None:
            return None
        return {
            "unpitched": True,
            "display_step": _child_text(unpitched, "display-step"),
            "display_octave": _number(_child_text(unpitched, "display-octave")),
        }
    return {
        "step": _child_text(pitch, "step"),
        "alter": _number(_child_text(pitch, "alter")) or 0,
        "octave": _number(_child_text(pitch, "octave")),
    }


def extract_accidental(note_element: Any) -> dict[str, Any]:
    accidental = _child(note_element, "accidental")
    if accidental is None:
        return {
            "value": None,
            "explicit": False,
            "cautionary": False,
            "editorial": False,
            "attributes": {},
        }
    attributes = _attrs(accidental)
    return {
        "value": _clean_text(accidental.text),
        "explicit": True,
        "cautionary": attributes.get("cautionary") == "yes",
        "editorial": attributes.get("editorial") == "yes",
        "attributes": attributes,
    }


def extract_duration(
    note_element: Any, divisions: int, *, is_grace: bool
) -> tuple[int | float, dict[str, Any]]:
    raw = _child_text(note_element, "duration")
    exact = _fraction(raw, divisions)
    if is_grace and raw is None:
        exact = Fraction(0)
    return _fraction_number(exact), {
        "quarter_length": _fraction_number(exact),
        "quarter_length_fraction": _fraction_text(exact),
        "xml_duration": None if raw is None else int(raw),
        "divisions": divisions,
        "type": _child_text(note_element, "type"),
        "dots": len(_children(note_element, "dot")),
    }


def extract_lyrics(note_element: Any, context: ParseContext) -> list[dict[str, Any]]:
    result = []
    for lyric in _children(note_element, "lyric"):
        texts = [item.text or "" for item in _children(lyric, "text")]
        elisions = [
            {"text": item.text or "", "attributes": _attrs(item)}
            for item in _children(lyric, "elision")
        ]
        extend = _child(lyric, "extend")
        context.stats.lyrics += 1
        if extend is not None:
            context.stats.lyric_extensions += 1
        result.append(
            {
                "number": lyric.get("number"),
                "name": lyric.get("name"),
                "syllabic": _child_text(lyric, "syllabic"),
                "text": "".join(texts),
                "texts": texts,
                "elisions": elisions,
                "extend": {
                    "present": extend is not None,
                    "type": None if extend is None else extend.get("type"),
                },
                "humming": _child(lyric, "humming") is not None,
                "laughing": _child(lyric, "laughing") is not None,
                "end_line": _child(lyric, "end-line") is not None,
                "end_paragraph": _child(lyric, "end-paragraph") is not None,
                "attributes": _attrs(lyric),
            }
        )
    return result


def extract_ties(note_element: Any, context: ParseContext) -> dict[str, Any]:
    sound = [{"type": item.get("type"), **_attrs(item)} for item in _children(note_element, "tie")]
    notations = _child(note_element, "notations")
    notation = [] if notations is None else [
        {"type": item.get("type"), **_attrs(item)}
        for item in _children(notations, "tied")
    ]
    context.stats.ties += len(sound)
    context.stats.tied_notations += len(notation)
    return {"sound": sound, "notation": notation}


def extract_slurs(note_element: Any, context: ParseContext) -> list[dict[str, Any]]:
    notations = _child(note_element, "notations")
    if notations is None:
        return []
    result = []
    for item in _children(notations, "slur"):
        result.append({"type": item.get("type"), "number": item.get("number"), **_attrs(item)})
    context.stats.slurs += len(result)
    return result


def extract_tuplets(note_element: Any, context: ParseContext) -> list[dict[str, Any]]:
    time_mod = _child(note_element, "time-modification")
    actual = None if time_mod is None else _number(_child_text(time_mod, "actual-notes"))
    normal = None if time_mod is None else _number(_child_text(time_mod, "normal-notes"))
    normal_type = None if time_mod is None else _child_text(time_mod, "normal-type")
    normal_dots = 0 if time_mod is None else len(_children(time_mod, "normal-dot"))
    notations = _child(note_element, "notations")
    marks = [] if notations is None else _children(notations, "tuplet")
    result = []
    if marks:
        for item in marks:
            result.append(
                {
                    "actual_notes": actual,
                    "normal_notes": normal,
                    "normal_type": normal_type,
                    "normal_dots": normal_dots,
                    "type": item.get("type"),
                    "number": item.get("number"),
                    "bracket": item.get("bracket"),
                    "show_number": item.get("show-number"),
                    "show_type": item.get("show-type"),
                    "attributes": _attrs(item),
                }
            )
    elif time_mod is not None:
        result.append(
            {
                "actual_notes": actual,
                "normal_notes": normal,
                "normal_type": normal_type,
                "normal_dots": normal_dots,
                "type": None,
                "number": None,
                "bracket": None,
                "show_number": None,
                "show_type": None,
                "attributes": {},
            }
        )
    context.stats.tuplets += len(result)
    return result


def extract_beams(note_element: Any, context: ParseContext) -> list[dict[str, Any]]:
    result = [
        {
            "number": item.get("number", "1"),
            "value": (item.text or "").strip(),
            "attributes": _attrs(item),
        }
        for item in _children(note_element, "beam")
    ]
    context.stats.beams += len(result)
    return result


def extract_articulations(note_element: Any, context: ParseContext) -> list[dict[str, Any]]:
    notations = _child(note_element, "notations")
    container = None if notations is None else _child(notations, "articulations")
    result = [] if container is None else [
        {"type": _tag(item), "attributes": _attrs(item), "text": _clean_text(item.text)}
        for item in _children(container)
    ]
    if notations is not None:
        for fermata in _children(notations, "fermata"):
            result.append(
                {"type": "fermata", "attributes": _attrs(fermata), "text": _clean_text(fermata.text)}
            )
    context.stats.articulations += len(result)
    return result


def extract_ornaments(note_element: Any, context: ParseContext) -> list[dict[str, Any]]:
    notations = _child(note_element, "notations")
    container = None if notations is None else _child(notations, "ornaments")
    result = [] if container is None else [
        {
            "type": _tag(item),
            "attributes": _attrs(item),
            "text": _clean_text(item.text),
            "raw_xml": _raw_xml(item),
        }
        for item in _children(container)
    ]
    context.stats.ornaments += len(result)
    return result


def extract_grace_info(note_element: Any) -> dict[str, Any]:
    grace = _child(note_element, "grace")
    if grace is None:
        return {"present": False, "slash": None, "steal_time_previous": None,
                "steal_time_following": None, "make_time": None}
    return {
        "present": True,
        "slash": grace.get("slash") == "yes" if grace.get("slash") is not None else None,
        "steal_time_previous": _number(grace.get("steal-time-previous")),
        "steal_time_following": _number(grace.get("steal-time-following")),
        "make_time": _number(grace.get("make-time")),
        "attributes": _attrs(grace),
    }


def extract_unhandled_musicxml(note_element: Any, context: ParseContext) -> list[dict[str, Any]]:
    """Preserve unsupported semantic note/notation children as compact XML."""
    result = []
    notations = _child(note_element, "notations")
    if notations is not None:
        for item in _children(notations):
            if _tag(item) not in NOTATION_HANDLED_CHILDREN:
                generic = _generic_element(item, f"note/notations/{_tag(item)}")
                result.append(generic)
                context.stats.unhandled_tags.add(generic["path"])
    for item in _children(note_element):
        name = _tag(item)
        if name not in NOTE_HANDLED_CHILDREN and name not in LAYOUT_ELEMENTS:
            generic = _generic_element(item, f"note/{name}")
            result.append(generic)
            context.stats.unhandled_tags.add(generic["path"])
    context.stats.unhandled += len(result)
    return result


def _extract_xml_note(
    element: Any,
    part_id: str,
    measure_number: str,
    measure_index: int,
    event_index: int,
    offset: Fraction,
    divisions: int,
    context: ParseContext,
) -> dict[str, Any]:
    voice = _child_text(element, "voice") or "1"
    staff_text = _child_text(element, "staff")
    staff = None if staff_text is None else int(staff_text)
    grace_info = extract_grace_info(element)
    duration, duration_source = extract_duration(
        element, divisions, is_grace=grace_info["present"]
    )
    pitch = extract_pitch(element)
    accidental = extract_accidental(element)
    is_rest = _child(element, "rest") is not None
    source_ref = _source_ref(
        part_id, measure_number, measure_index, event_index, element,
        voice=voice, staff=staff,
    )
    event: dict[str, Any] = {
        "type": "rest" if is_rest else "note",
        "source_ref": source_ref,
        "xml_event_index": event_index,
        "offset": _fraction_number(offset),
        "offset_fraction": _fraction_text(offset),
        "duration": duration,
        "duration_source": duration_source,
        "written_type": duration_source["type"],
        "dots": duration_source["dots"],
        "voice": voice,
        "staff": staff,
        "grace": grace_info["present"],
        "grace_info": grace_info,
        "beams": extract_beams(element, context),
        "tuplets": extract_tuplets(element, context),
        "lyrics": extract_lyrics(element, context),
        "ties": extract_ties(element, context),
        "slurs": extract_slurs(element, context),
        "articulations": extract_articulations(element, context),
        "ornaments": extract_ornaments(element, context),
        "unhandled_notations": extract_unhandled_musicxml(element, context),
        "xml_attributes": _attrs(element),
        "_is_chord_member": _child(element, "chord") is not None,
    }
    if staff is not None:
        context.stats.staff_assignments += 1
    if grace_info["present"]:
        context.stats.grace_notes += 1
    if is_rest:
        rest = _child(element, "rest")
        event["rest"] = {"measure": rest.get("measure") == "yes", "attributes": _attrs(rest)}
    else:
        event["pitch"] = pitch
        if pitch and not pitch.get("unpitched"):
            event.update({
                "step": pitch["step"], "alter": pitch["alter"], "octave": pitch["octave"]
            })
        event["accidental_details"] = accidental
        event["accidental"] = accidental["value"]
    return event


def _chord_member(event: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "source_ref", "pitch", "step", "alter", "octave", "accidental",
        "accidental_details", "ties", "slurs", "lyrics", "articulations",
        "ornaments", "unhandled_notations", "grace", "grace_info",
        "duration", "duration_source", "written_type", "dots", "beams",
        "tuplets", "voice", "staff",
    }
    return {key: value for key, value in event.items() if key in keys}


def extract_note(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Public modular note extractor; dispatches to the XML fidelity layer."""
    return _extract_xml_note(*args, **kwargs)


def extract_rest(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Public modular rest extractor; dispatches to the XML fidelity layer."""
    return _extract_xml_note(*args, **kwargs)


def extract_chord(root_event: dict[str, Any], member: dict[str, Any]) -> dict[str, Any]:
    """Combine consecutive MusicXML <note>/<chord/> elements logically."""
    if root_event["type"] != "chord":
        root_event = {
            **{key: value for key, value in root_event.items() if key not in {
                "pitch", "step", "alter", "octave", "accidental",
                "accidental_details", "ties", "slurs", "lyrics",
                "articulations", "ornaments", "unhandled_notations",
            }},
            "type": "chord",
            "notes": [_chord_member(root_event)],
            "source_refs": [root_event["source_ref"]],
        }
    root_event["notes"].append(_chord_member(member))
    root_event["source_refs"].append(member["source_ref"])
    return root_event


def extract_key(element: Any, source_ref: dict[str, Any], offset: Fraction) -> dict[str, Any]:
    cancel = _child(element, "cancel")
    return {
        "type": "key_signature",
        "source_ref": source_ref,
        "xml_event_index": source_ref["xml_event_index"],
        "offset": _fraction_number(offset),
        "staff": _number(element.get("number")),
        "fifths": _number(_child_text(element, "fifths")),
        "mode": _child_text(element, "mode"),
        "cancel": _number(_child_text(element, "cancel")),
        "cancel_attributes": {} if cancel is None else _attrs(cancel),
        "custom": [
            {"step": step.text, "alter": _number(alter.text) if alter is not None else None}
            for step, alter in zip(_children(element, "key-step"), _children(element, "key-alter"))
        ],
        "custom_components": [
            {"type": _tag(item), "value": _clean_text(item.text), "attributes": _attrs(item)}
            for item in _children(element)
            if _tag(item) in {"key-step", "key-alter", "key-accidental"}
        ],
        "attributes": _attrs(element),
    }


def extract_time_signature(
    element: Any, source_ref: dict[str, Any], offset: Fraction
) -> dict[str, Any]:
    interchangeable = _child(element, "interchangeable")
    return {
        "type": "time_signature",
        "source_ref": source_ref,
        "xml_event_index": source_ref["xml_event_index"],
        "offset": _fraction_number(offset),
        "staff": _number(element.get("number")),
        "beats": [item.text or "" for item in _children(element, "beats")],
        "beat_types": [item.text or "" for item in _children(element, "beat-type")],
        "symbol": element.get("symbol"),
        "senza_misura": _child_text(element, "senza-misura"),
        "interchangeable": (
            None if interchangeable is None else _generic_element(
                interchangeable, "attributes/time/interchangeable"
            )
        ),
        "attributes": _attrs(element),
    }


def extract_clef(element: Any, source_ref: dict[str, Any], offset: Fraction) -> dict[str, Any]:
    return {
        "type": "clef",
        "source_ref": source_ref,
        "xml_event_index": source_ref["xml_event_index"],
        "offset": _fraction_number(offset),
        "staff": _number(element.get("number")),
        "sign": _child_text(element, "sign"),
        "line": _number(_child_text(element, "line")),
        "octave_change": _number(_child_text(element, "clef-octave-change")),
        "attributes": _attrs(element),
    }


def extract_tempo(direction: Any) -> list[dict[str, Any]]:
    result = []
    for direction_type in _children(direction, "direction-type"):
        for metronome in _children(direction_type, "metronome"):
            result.append({
                "kind": "metronome",
                "beat_unit": _child_text(metronome, "beat-unit"),
                "beat_unit_dots": len(_children(metronome, "beat-unit-dot")),
                "per_minute": _child_text(metronome, "per-minute"),
                "attributes": _attrs(metronome),
                "raw_xml": _raw_xml(metronome),
            })
    sound = _child(direction, "sound")
    if sound is not None and sound.get("tempo") is not None:
        result.append({"kind": "sound", "tempo": _number(sound.get("tempo"))})
    return result


def extract_directions(
    direction: Any,
    part_id: str,
    measure_number: str,
    measure_index: int,
    event_index: int,
    offset: Fraction,
    divisions: int,
    context: ParseContext,
) -> dict[str, Any]:
    voice = _child_text(direction, "voice")
    staff_text = _child_text(direction, "staff")
    staff = None if staff_text is None else int(staff_text)
    direction_offset = _fraction(_child_text(direction, "offset"), divisions)
    final_offset = offset + direction_offset
    direction_types = []
    for container in _children(direction, "direction-type"):
        for item in _children(container):
            name = _tag(item)
            entry: dict[str, Any] = {
                "type": name,
                "attributes": _attrs(item),
                "text": _clean_text(item.text),
            }
            if name == "dynamics":
                entry["values"] = [
                    _tag(dynamic) if _tag(dynamic) != "other-dynamics" else (dynamic.text or "")
                    for dynamic in _children(item)
                ]
            elif name == "metronome":
                entry.update({
                    "kind": "metronome",
                    "beat_unit": _child_text(item, "beat-unit"),
                    "beat_unit_dots": len(_children(item, "beat-unit-dot")),
                    "per_minute": _child_text(item, "per-minute"),
                    "raw_xml": _raw_xml(item),
                })
            elif _children(item):
                entry["raw_xml"] = _raw_xml(item)
            direction_types.append(entry)
    sound = _child(direction, "sound")
    source_ref = _source_ref(
        part_id, measure_number, measure_index, event_index, direction,
        voice=voice, staff=staff,
    )
    context.stats.directions += 1
    if staff is not None:
        context.stats.staff_assignments += 1
    return {
        "type": "direction",
        "source_ref": source_ref,
        "xml_event_index": event_index,
        "offset": _fraction_number(final_offset),
        "offset_fraction": _fraction_text(final_offset),
        "voice": voice,
        "staff": staff,
        "placement": direction.get("placement"),
        "direction_types": direction_types,
        "tempo": extract_tempo(direction),
        "sound": None if sound is None else _attrs(sound),
        "attributes": _attrs(direction),
    }


def extract_barlines(
    element: Any,
    part_id: str,
    measure_number: str,
    measure_index: int,
    event_index: int,
    context: ParseContext,
) -> dict[str, Any]:
    repeat = _child(element, "repeat")
    ending = _child(element, "ending")
    other_children = [
        _generic_element(item, f"barline/{_tag(item)}")
        for item in _children(element)
        if _tag(item) not in {"bar-style", "repeat", "ending"}
    ]
    context.stats.unhandled += len(other_children)
    context.stats.unhandled_tags.update(item["path"] for item in other_children)
    return {
        "source_ref": _source_ref(
            part_id, measure_number, measure_index, event_index, element
        ),
        "xml_event_index": event_index,
        "location": element.get("location", "right"),
        "bar_style": _child_text(element, "bar-style"),
        "repeat": None if repeat is None else {
            "direction": repeat.get("direction"),
            "times": _number(repeat.get("times")),
            "winged": repeat.get("winged"),
            "attributes": _attrs(repeat),
        },
        "ending": None if ending is None else {
            "number": ending.get("number"),
            "type": ending.get("type"),
            "text": _clean_text(ending.text),
            "attributes": _attrs(ending),
        },
        "other_musical_elements": other_children,
    }


def _extract_attributes(
    element: Any,
    part_id: str,
    measure_number: str,
    measure_index: int,
    event_index: int,
    offset: Fraction,
    context: ParseContext,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = []
    unhandled = []
    for child_index, item in enumerate(_children(element), start=1):
        name = _tag(item)
        ref = _source_ref(
            part_id, measure_number, measure_index, event_index, item,
            staff=_number(item.get("number")), suffix=f"-{child_index}",
        )
        if name == "key":
            events.append(extract_key(item, ref, offset))
        elif name == "time":
            events.append(extract_time_signature(item, ref, offset))
        elif name == "clef":
            events.append(extract_clef(item, ref, offset))
        elif name not in {"divisions"} and name not in LAYOUT_ELEMENTS:
            generic = _generic_element(item, f"attributes/{name}")
            generic.update({"source_ref": ref, "offset": _fraction_number(offset)})
            unhandled.append(generic)
            context.stats.unhandled_tags.add(generic["path"])
    context.stats.unhandled += len(unhandled)
    return events, unhandled


def build_xml_event_map(root: Any, context: ParseContext) -> list[dict[str, Any]]:
    """Build the authoritative XML timeline, honoring backup/forward/chord."""
    parts = []
    for part_index, part_element in enumerate(_children(root, "part"), start=1):
        part_id = part_element.get("id") or f"P{part_index}"
        divisions = 1
        part_data = {"id": part_id, "measures": []}
        for measure_index, measure in enumerate(_children(part_element, "measure"), start=1):
            context.stats.measures += 1
            number = measure.get("number") or str(measure_index)
            cursor = Fraction(0)
            max_cursor = Fraction(0)
            last_event: dict[str, Any] | None = None
            voices: dict[str, list[dict[str, Any]]] = defaultdict(list)
            measure_events: list[dict[str, Any]] = []
            timing_operations: list[dict[str, Any]] = []
            barlines = []
            unhandled = []

            for event_index, element in enumerate(_children(measure), start=1):
                name = _tag(element)
                if name == "attributes":
                    divisions_text = _child_text(element, "divisions")
                    if divisions_text is not None:
                        divisions = int(divisions_text)
                    attribute_events, generic = _extract_attributes(
                        element, part_id, number, measure_index, event_index,
                        cursor, context,
                    )
                    measure_events.extend(attribute_events)
                    unhandled.extend(generic)
                    last_event = None
                elif name in {"backup", "forward"}:
                    amount = _fraction(_child_text(element, "duration"), divisions)
                    if name == "backup":
                        cursor -= amount
                    else:
                        cursor += amount
                        max_cursor = max(max_cursor, cursor)
                    timing_operations.append({
                        "type": name,
                        "xml_event_index": event_index,
                        "xml_duration": int(_child_text(element, "duration") or 0),
                        "divisions": divisions,
                        "quarter_length": _fraction_number(amount),
                        "resulting_offset": _fraction_number(cursor),
                        "source_ref": _source_ref(
                            part_id, number, measure_index, event_index, element
                        ),
                    })
                    last_event = None
                elif name == "note":
                    event = _extract_xml_note(
                        element, part_id, number, measure_index, event_index,
                        cursor if _child(element, "chord") is None or last_event is None
                        else Fraction(str(last_event["offset_fraction"])),
                        divisions, context,
                    )
                    is_member = event.pop("_is_chord_member")
                    if is_member and last_event is not None and last_event["type"] in {"note", "chord"}:
                        old_voice = last_event["voice"]
                        old_list = voices[old_voice]
                        combined = extract_chord(last_event, event)
                        old_list[-1] = combined
                        last_event = combined
                        if len(combined["notes"]) == 2:
                            context.stats.chords += 1
                            # The former root is now counted as a chord rather
                            # than as a standalone note event.
                            context.stats.notes -= 1
                    else:
                        voices[event["voice"]].append(event)
                        last_event = event
                        if event["type"] == "rest":
                            context.stats.rests += 1
                        else:
                            context.stats.notes += 1
                    if not is_member and not event["grace"]:
                        cursor += Fraction(str(event["duration_source"]["quarter_length_fraction"]))
                        max_cursor = max(max_cursor, cursor)
                elif name == "direction":
                    measure_events.append(extract_directions(
                        element, part_id, number, measure_index, event_index,
                        cursor, divisions, context,
                    ))
                    last_event = None
                elif name == "barline":
                    barlines.append(extract_barlines(
                        element, part_id, number, measure_index, event_index, context
                    ))
                    last_event = None
                elif name not in LAYOUT_ELEMENTS:
                    generic = _generic_element(element, f"measure/{name}")
                    generic.update({
                        "source_ref": _source_ref(
                            part_id, number, measure_index, event_index, element
                        ),
                        "offset": _fraction_number(cursor),
                        "xml_event_index": event_index,
                    })
                    unhandled.append(generic)
                    context.stats.unhandled += 1
                    context.stats.unhandled_tags.add(generic["path"])
                    last_event = None

            voice_list = []
            for voice_id, events in voices.items():
                context.stats.voice_keys.add((part_id, voice_id))
                events.sort(key=lambda item: (item["offset"], item["xml_event_index"]))
                voice_list.append({"id": voice_id, "events": events})
            measure_events.sort(
                key=lambda item: (item.get("offset", 0), item.get("xml_event_index", 0))
            )
            part_data["measures"].append({
                "number": number,
                "implicit": measure.get("implicit") == "yes",
                "width_ignored": measure.get("width") is not None,
                "divisions": divisions,
                "duration": _fraction_number(max_cursor),
                "duration_fraction": _fraction_text(max_cursor),
                "voices": voice_list,
                "events": measure_events,
                "timing_operations": timing_operations,
                "barlines": barlines,
                "unhandled_musicxml": unhandled,
            })
        parts.append(part_data)
    context.stats.parts = len(parts)
    return parts


def build_music21_event_map(m21_score: Any, *, verbose: bool = False) -> dict[str, Any]:
    """Reuse V2's normalized semantic extraction without modifying V2."""
    data, _stats = v2.score_to_dict(m21_score, verbose=verbose)
    return data


def _event_signature(event: dict[str, Any]) -> tuple[str, str, float]:
    return (
        event.get("type", ""),
        str(event.get("voice") or "1"),
        float(event.get("offset") or 0),
    )


def _pitch_tuple(event: dict[str, Any]) -> tuple[Any, Any, Any] | None:
    pitch = event.get("pitch")
    if pitch and not pitch.get("unpitched"):
        return pitch.get("step"), pitch.get("alter", 0), pitch.get("octave")
    if event.get("type") == "note":
        return event.get("step"), event.get("alter", 0), event.get("octave")
    return None


def _reconciliation_warning(
    context: ParseContext, source_ref: dict[str, Any], message: str
) -> None:
    context.stats.reconciliation_warnings += 1
    context.warn(f"{source_ref['source_id']}: {message}", lossy=True)


def _compare_events(
    xml_event: dict[str, Any], normalized: dict[str, Any], context: ParseContext
) -> None:
    source_ref = xml_event["source_ref"]
    if abs(float(xml_event["duration"]) - float(normalized.get("duration", 0))) > 1e-7:
        _reconciliation_warning(
            context, source_ref,
            f"music21 duration {normalized.get('duration')} != XML-derived {xml_event['duration']}",
        )
    if xml_event["type"] == "note":
        if _pitch_tuple(xml_event) != _pitch_tuple(normalized):
            _reconciliation_warning(
                context, source_ref,
                f"music21 pitch {_pitch_tuple(normalized)} != XML pitch {_pitch_tuple(xml_event)}",
            )
    elif xml_event["type"] == "chord":
        xml_pitches = [_pitch_tuple({"type": "note", **item}) for item in xml_event["notes"]]
        norm_pitches = [
            (item.get("step"), item.get("alter", 0), item.get("octave"))
            for item in normalized.get("notes", [])
        ]
        if xml_pitches != norm_pitches:
            _reconciliation_warning(
                context, source_ref,
                f"music21 chord pitches {norm_pitches} != XML pitches {xml_pitches}",
            )


def reconcile_events(
    xml_parts: list[dict[str, Any]],
    normalized_data: dict[str, Any],
    context: ParseContext,
) -> None:
    """Match XML-authoritative events with normalized V2 events and compare."""
    normalized_parts = normalized_data.get("parts", [])
    for part_index, xml_part in enumerate(xml_parts):
        # music21 expands a multi-staff MusicXML <part> into PartStaff objects
        # whose IDs conventionally end in -Staff1, -Staff2, etc. Recombine
        # those candidates here while keeping the XML part/staff authoritative.
        matching_parts = [
            part for part in normalized_parts
            if str(part.get("id")) == xml_part["id"]
            or str(part.get("id", "")).startswith(f"{xml_part['id']}-Staff")
        ]
        if not matching_parts and part_index < len(normalized_parts):
            matching_parts = [normalized_parts[part_index]]
        if not matching_parts:
            context.warn(f"No music21 part matches XML part {xml_part['id']}", lossy=True)
            continue
        for measure_index, xml_measure in enumerate(xml_part["measures"]):
            available_measures = [
                (part, part["measures"][measure_index])
                for part in matching_parts
                if measure_index < len(part.get("measures", []))
            ]
            if not available_measures:
                context.warn(
                    f"No music21 measure matches {xml_part['id']} measure {xml_measure['number']}",
                    lossy=True,
                )
                continue
            candidates = []
            for normalized_part, normalized_measure in available_measures:
                staff_match = re.search(r"-Staff(\d+)$", str(normalized_part.get("id", "")))
                normalized_staff = int(staff_match.group(1)) if staff_match else None
                for voice in normalized_measure.get("voices", []):
                    for source_event in voice.get("events", []):
                        candidate = dict(source_event)
                        candidate["source_staff"] = normalized_staff
                        candidates.append(candidate)
            used: set[int] = set()
            for voice in xml_measure["voices"]:
                for xml_event in voice["events"]:
                    signature = _event_signature(xml_event)
                    best = None
                    for candidate_index, candidate in enumerate(candidates):
                        if candidate_index in used:
                            continue
                        candidate_signature = _event_signature(candidate)
                        voice_or_staff_matches = (
                            signature[1] == candidate_signature[1]
                            or (
                                xml_event.get("staff") is not None
                                and candidate.get("source_staff") == xml_event.get("staff")
                            )
                        )
                        if (
                            signature[0] == candidate_signature[0]
                            and voice_or_staff_matches
                            and abs(signature[2] - candidate_signature[2]) < 1e-7
                        ):
                            best = candidate_index
                            break
                    if best is None:
                        _reconciliation_warning(
                            context, xml_event["source_ref"],
                            "no matching music21 event (XML data retained)",
                        )
                        continue
                    used.add(best)
                    normalized = candidates[best]
                    xml_event["normalized_music21"] = normalized
                    _compare_events(xml_event, normalized, context)
            for candidate_index, candidate in enumerate(candidates):
                if candidate_index not in used:
                    context.stats.reconciliation_warnings += 1
                    context.warn(
                        f"{xml_part['id']} measure {xml_measure['number']}: unmatched music21 "
                        f"{candidate.get('type')} at offset {candidate.get('offset')}",
                        lossy=True,
                    )
            xml_measure["normalized_music21_events"] = [
                event
                for _part, normalized_measure in available_measures
                for event in normalized_measure.get("events", [])
            ]
            # Retain V2's convenient active-state fields alongside exact XML changes.
            for field_name in (
                "time_signature", "time_signature_details", "key_signature", "key", "clef"
            ):
                xml_measure[field_name] = available_measures[0][1].get(field_name)

        norm_part = matching_parts[0]
        xml_part.update({
            key_name: norm_part.get(key_name)
            for key_name in ("name", "abbreviation", "instrument")
        })


def _part_list_data(root: Any) -> dict[str, dict[str, Any]]:
    result = {}
    part_list = _child(root, "part-list")
    if part_list is None:
        return result
    for score_part in _children(part_list, "score-part"):
        part_id = score_part.get("id") or ""
        result[part_id] = {
            "id": part_id,
            "part_name": _child_text(score_part, "part-name"),
            "part_abbreviation": _child_text(score_part, "part-abbreviation"),
            "score_instruments": [
                {
                    "id": item.get("id"),
                    "instrument_name": _child_text(item, "instrument-name"),
                    "instrument_abbreviation": _child_text(item, "instrument-abbreviation"),
                    "instrument_sound": _child_text(item, "instrument-sound"),
                }
                for item in _children(score_part, "score-instrument")
            ],
            "midi_instruments": [
                {
                    "id": item.get("id"),
                    "midi_channel": _number(_child_text(item, "midi-channel")),
                    "midi_program": _number(_child_text(item, "midi-program")),
                    "midi_unpitched": _number(_child_text(item, "midi-unpitched")),
                    "volume": _number(_child_text(item, "volume")),
                    "pan": _number(_child_text(item, "pan")),
                }
                for item in _children(score_part, "midi-instrument")
            ],
        }
    return result


def extract_measure(measure: dict[str, Any]) -> dict[str, Any]:
    """Named schema boundary retained for future independent measure transforms."""
    return measure


def extract_part(part: dict[str, Any], source_parts: dict[str, Any]) -> dict[str, Any]:
    part["source_part"] = source_parts.get(part["id"])
    part["measures"] = [extract_measure(measure) for measure in part["measures"]]
    return part


def validate_semantic_integrity(data: dict[str, Any], context: ParseContext) -> None:
    """Run non-fatal schema and source-count checks after reconciliation."""
    event_count = 0
    lyric_count = 0
    for part in data["parts"]:
        for measure in part["measures"]:
            for voice in measure["voices"]:
                if not voice.get("id"):
                    context.warn(f"{part['id']} measure {measure['number']}: empty voice ID", lossy=True)
                for event in voice["events"]:
                    event_count += 1
                    if float(event.get("duration", 0)) < 0:
                        context.warn(
                            f"{event['source_ref']['source_id']}: negative duration", lossy=True
                        )
                    members = event.get("notes", [event])
                    if event["type"] in {"note", "chord"}:
                        for member in members:
                            pitch = member.get("pitch")
                            if pitch is None:
                                context.warn(
                                    f"{member['source_ref']['source_id']}: missing pitch", lossy=True
                                )
                    lyric_count += sum(len(member.get("lyrics", [])) for member in members)
                    for tuplet in event.get("tuplets", []):
                        if not tuplet.get("actual_notes") or not tuplet.get("normal_notes"):
                            context.warn(
                                f"{event['source_ref']['source_id']}: invalid tuplet ratio",
                                lossy=True,
                            )
    if lyric_count != context.stats.lyrics:
        context.warn(
            f"Lyric integrity mismatch: JSON {lyric_count}, XML {context.stats.lyrics}",
            lossy=True,
        )
    if event_count == 0:
        context.warn("No musical events were extracted.", lossy=True)


def _summary_dict(context: ParseContext) -> dict[str, Any]:
    stats = context.stats
    return {
        "parts": stats.parts,
        "measures": stats.measures,
        "notes": stats.notes,
        "rests": stats.rests,
        "chords": stats.chords,
        "voices": len(stats.voice_keys),
        "staff_assignments": stats.staff_assignments,
        "lyrics": stats.lyrics,
        "lyric_extensions": stats.lyric_extensions,
        "ties": stats.ties,
        "tied_notations": stats.tied_notations,
        "slurs": stats.slurs,
        "tuplets": stats.tuplets,
        "grace_notes": stats.grace_notes,
        "beams": stats.beams,
        "articulations": stats.articulations,
        "ornaments": stats.ornaments,
        "directions": stats.directions,
        "unhandled_musical_elements": stats.unhandled,
        "reconciliation_warnings": stats.reconciliation_warnings,
    }


def _print_summary(context: ParseContext) -> None:
    print("V3 parse summary", file=sys.stderr)
    print("----------------", file=sys.stderr)
    for name, value in _summary_dict(context).items():
        print(f"{name.replace('_', ' ').title()}: {value}", file=sys.stderr)


def convert_score(
    m21_score: Any, xml_tree: Any, *, verbose: bool = False
) -> tuple[dict[str, Any], ParseContext]:
    context = ParseContext(stats=V3Stats(), verbose=verbose)
    root = xml_tree.getroot()
    xml_parts = build_xml_event_map(root, context)
    normalized = build_music21_event_map(m21_score, verbose=False)
    reconcile_events(xml_parts, normalized, context)
    source_parts = _part_list_data(root)
    metadata = extract_score_metadata(m21_score, root)
    data = {
        "schema_version": SCHEMA_VERSION,
        "generator": {
            "name": "musicxml-json-interchange",
            "version": __version__,
        },
        "title": metadata.get("title"),
        "metadata": metadata,
        "parts": [extract_part(part, source_parts) for part in xml_parts],
        "conversion": {
            "parser": {"semantic": "music21", "fidelity": "lxml"},
            "layout_data_omitted": True,
            "unhandled_tags": sorted(context.stats.unhandled_tags),
        },
    }
    validate_semantic_integrity(data, context)
    data["conversion"].update({
        "semantically_lossless": not context.lossy_conditions,
        "lossy_conditions": context.lossy_conditions,
        "warnings": context.warnings,
        "statistics": _summary_dict(context),
    })
    return data, context


def save_json(data: dict[str, Any], output_path: str | Path) -> None:
    try:
        with Path(output_path).open("w", encoding="utf-8") as output_file:
            json.dump(data, output_file, ensure_ascii=False, indent=2)
            output_file.write("\n")
    except (OSError, TypeError, ValueError) as exc:
        raise MusicXMLConversionError(f"Could not write '{output_path}': {exc}") from exc


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    verbose: bool = False,
    strict: bool = False,
    validate: bool = False,
) -> tuple[dict[str, Any], ParseContext]:
    path = _validate_input_path(input_path)
    m21_score = parse_with_music21(path)
    xml_tree = parse_xml_tree(path)
    data, context = convert_score(m21_score, xml_tree, verbose=verbose)
    if validate:
        from .schema_validation import SchemaValidationError, validate_json_data
        try:
            validate_json_data(data)
        except SchemaValidationError as exc:
            raise MusicXMLConversionError(f"Schema validation failed: {exc}") from exc
    save_json(data, output_path)
    if verbose:
        _print_summary(context)
    if strict and context.lossy_conditions:
        raise MusicXMLConversionError(
            f"Strict mode found {len(context.lossy_conditions)} potentially lossy condition(s); "
            f"diagnostic JSON was written to '{output_path}'."
        )
    return data, context


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert MusicXML to hybrid, reconstruction-oriented JSON 3.0."
    )
    parser.add_argument("input", help="Input .musicxml or .xml score")
    parser.add_argument("output", help="Output .json path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print warnings and statistics")
    parser.add_argument(
        "--strict", action="store_true",
        help="Fail after writing diagnostics if potentially lossy conditions are found",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate generated JSON against the public schema before writing",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    try:
        convert_file(
            args.input,
            args.output,
            verbose=args.verbose,
            strict=args.strict,
            validate=args.validate,
        )
    except MusicXMLConversionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: unexpected conversion failure: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote JSON to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
