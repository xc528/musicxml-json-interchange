# Music Score JSON Schema v1.0.0

## Purpose

Music Score JSON is a structured intermediate representation for symbolic music
notation.

```text
MusicXML
   ↓
Music Score JSON
   ↓
MusicXML 
```

The primary goal is a stable, machine-validatable representation that retains
the musical meaning needed for MusicXML reconstruction and future notation
conversion.

## Goals

- Musically lossless round trips for supported MusicXML semantics
- A stable public representation with explicit versioning
- Exact source timing alongside useful normalized interpretation
- Human-readable Unicode JSON
- Deterministic source identity where practical
- A semantic fallback for meaningful notation not modeled directly
- Future Jianpu and other symbolic-notation conversion workflows

## Non-goals

- Identical engraving or pagination
- Page-layout reconstruction
- Identical XML serialization or namespace prefixes
- XML whitespace or attribute-order preservation
- Byte-for-byte round trips

## Version policy

The root `schema_version` follows semantic versioning:

- **Patch**: validation or documentation corrections that do not change
  compatible documents.
- **Minor**: backward-compatible optional fields or event types.
- **Major**: renamed/removed fields, changed meanings, or incompatible types.

The generator's software version is separate. It may change without changing
the public schema version.

## Presence conventions

- Fields declared `required` are always emitted.
- A required scalar whose value is genuinely unknown uses `null` only when its
  schema explicitly permits null.
- Repeated data uses an array and is represented by `[]` when absent.
- Optional fields are reserved for subtype-specific or source-dependent data;
  omission means the field does not apply to that object.
- Identifiers—including part, measure, and voice identities—are strings even
  when the source happens to use digits.
- Quarter lengths and convenience offsets are JSON numbers. Exact timing is
  additionally stored as a canonical fraction string such as `"1/3"`.
- XML duration is a nonnegative integer in source divisions, or `null` for a
  grace note without `<duration>`.

## Ordering

- Parts and measures preserve source score order.
- Voices preserve their first source appearance.
- Voice events are ordered by measure offset, then source XML event index.
- Simultaneous events use `xml_event_index` to retain deterministic source order.
- Chord members and lyrics preserve source order.
- Measure-level events are ordered by offset, then source XML event index.

## Event discrimination

Musical events use the `type` discriminator:

- `note`
- `rest`
- `chord`

Measure-level semantic events similarly use `direction`, `key_signature`,
`time_signature`, or `clef`. The schema uses `oneOf` definitions to distinguish
these objects.

## Authoritative and normalized information

Source-derived MusicXML information is authoritative for round-trip export.
This includes `pitch`, `duration_source`, exact ties/slurs, explicit accidentals,
lyrics, staff assignments, XML sequence indices, and semantic fallback XML.

`normalized_music21` and `normalized_music21_events` are supplementary. They
are useful for analysis and downstream notation conversion, but must never
replace contradictory source-derived values during reconstruction.

## Source references

`source_ref` identifies an originating MusicXML event with:

- `part_id`: source `<part id>`
- `measure_number`: source measure identifier
- `measure_index`: one-based source measure position within the part
- `voice` and `staff`: explicit source identities when present
- `xml_event_index`: one-based semantic child position in the source measure
- `xml_id`: original MusicXML `id`, or `null`
- `source_id`: the original ID or a deterministic generated identifier

Generated IDs use part, measure, and XML event position. They are stable for an
unchanged source score and are not random UUIDs.

## Semantic fallback

Unsupported but musically meaningful XML is retained using a fallback object:

```json
{
  "path": "note/notations/glissando",
  "tag": "glissando",
  "attributes": {"type": "start", "number": "1"},
  "text": null,
  "raw_xml": "<glissando type=\"start\" number=\"1\"/>"
}
```

This prevents technical notation, glissandi, measure styles, and vendor
extensions from being silently discarded. Raw fragments exclude known layout
attributes. Exporters must reinsert the fragment or report a lossy condition;
strict export must fail if reinsertion is ambiguous.

## Layout exclusion

Schema v1 intentionally excludes page sizes, margins, system and staff spacing,
fonts, graphical coordinates, print spacing, namespace-prefix spelling, XML
whitespace, and attribute ordering. Their absence does not make a semantic
round trip lossy under this schema's scope.

## Examples

- `examples/simple.json`: monophonic score
- `examples/polyphonic.json`: multiple voices and chord notation
- `examples/rich_notation.json`: multi-staff score with rich notation/fallbacks

All examples are generated from the synthetic fixtures and validated against
the exact schema in this directory.
