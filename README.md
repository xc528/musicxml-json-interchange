# MusicXML JSON Interchange
[![Tests](https://github.com/xc528/musicxml-json-interchange/actions/workflows/tests.yml/badge.svg)](https://github.com/xc528/musicxml-json-interchange/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Schema](https://img.shields.io/badge/Schema-JSON%202020--12-blue)

`musicxml-json-interchange` converts MusicXML into a structured, human-readable
JSON representation and reconstructs MusicXML from that representation. It also
provides a semantic comparator for validating round trips.

It is designed for musically lossless round-trip conversion for supported
MusicXML semantics, excluding engraving and page-layout information. It does
not claim universal or byte-for-byte MusicXML losslessness.

```text
MusicXML → Music Score JSON → MusicXML
                         └──→ analysis / future Jianpu conversion
```

The public Music Score JSON schema is **v1.0.0**. Jianpu rendering is explicitly
outside this repository's current scope.

## Capabilities

- Notes, rests, chords, voices, staves, exact source durations, and offsets
- Pitches, semantic alterations, and explicitly written accidentals
- Lyrics, elisions, extensions, sound ties, notation ties, and numbered slurs
- Beams, tuplets, grace notes, articulations, ornaments, and raw semantic fallback
- Directions, dynamics, wedges, words, rehearsal marks, metronome and sound tempo
- Key/time signatures, clefs, repeats, volta endings, and timing operations
- Deterministic source references and supplementary `music21` normalization
- Draft 2020-12 JSON Schema validation
- Semantic MusicXML comparison with machine-readable reports

## Dependencies and acknowledgements

The MusicXML parser combines
[music21](https://github.com/cuthbertLab/music21) for normalized musical
interpretation and semantic score objects with direct XML processing through
[lxml](https://lxml.de/) for source-level semantic fidelity and round-trip
reconstruction. JSON Schema validation is provided by
[jsonschema](https://github.com/python-jsonschema/jsonschema).

The reconciliation logic, source-preservation model, public JSON schema,
MusicXML exporter, semantic comparator, and round-trip architecture are
project-specific work. The project does not imply that the authors or
contributors of its dependencies endorse it.

The direct runtime dependencies are distributed under BSD 3-Clause or MIT
licenses. Exact bundled license texts and attribution details are collected in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) and the
[`licenses/`](licenses/) directory. These third-party licenses are separate
from this project's own license, which has not yet been selected.

## Requirements and installation

Python 3.10 or newer is required. Development and release validation currently
use Python 3.12, `music21` 10.5, `lxml` 6.1, `jsonschema` 4.26,
and `pytest` 8.4.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
```


## Command-line usage

From a source checkout:

```bash
python scripts/musicxml_to_json.py input.musicxml output.json --strict --validate

python scripts/json_to_musicxml.py output.json reconstructed.musicxml --strict

python scripts/compare_musicxml_semantics.py \
  input.musicxml reconstructed.musicxml --report comparison.json

python scripts/validate_json_schema.py output.json
```

After installation, equivalent console commands are available:

```bash
musicxml-to-json input.musicxml output.json --strict --validate
json-to-musicxml output.json reconstructed.musicxml --strict
compare-musicxml-semantics input.musicxml reconstructed.musicxml
```

The comparator exits with `0` for equivalence, `1` for semantic differences,
and `2` for input/parsing failures.

## Public schema

The schema is [schema/music-score.schema.json](schema/music-score.schema.json),
with field semantics and version policy documented in
[schema/README.md](schema/README.md). Every public document begins with:

```json
{
  "schema_version": "1.0.0",
  "generator": {
    "name": "musicxml-json-interchange",
    "version": "1.0.0"
  }
}
```

Source-derived MusicXML fields are authoritative for reconstruction.
`normalized_music21` is supplementary analytical information and must not
override the source representation during export.

## Round-trip validation

The automated suite performs:

```text
synthetic MusicXML
    → JSON
    → JSON Schema validation
    → reconstructed MusicXML
    → semantic comparison
```

Run it with:

```bash
pytest
```

The included fixtures are synthetic and cover monophonic, polyphonic, and
multi-staff/rich-notation cases. A larger local score has been used for private
regression testing but is not distributed because its copyright status is
unclear.

## Project structure

```text
src/musicxml_json/       reusable parser, exporter, comparator, validation
scripts/                 thin source-tree CLI wrappers
schema/                  public schema, documentation, generated examples
tests/fixtures/          synthetic redistributable MusicXML scores
tests/                   schema and round-trip tests
```

## Known limitations

### Unsupported input formats

- `score-timewise` MusicXML is not supported.
- Compressed `.mxl` input is not supported.

### Semantic edge cases

- Mid-measure `<divisions>` changes cannot be reconstructed if their exact
  source position is absent from schema v1; strict export detects this.
- Unusual vendor extensions may be retained as semantic raw XML fallbacks.
  Export fails in strict mode if their insertion point is ambiguous.
- Mixed lyric content is reconstructed from ordered text and elision arrays;
  highly unusual child ordering may require a future ordered-token field.
- Vendor-specific `music21` part splitting may produce reconciliation warnings.

### Intentionally excluded

- Page dimensions, margins, system/staff spacing, and line breaks
- Fonts and engraving coordinates such as `default-x` or `relative-y`
- XML whitespace, namespace-prefix choice, attribute ordering, and byte identity

## Citation

Until a public repository URL, authorship record, or archival DOI is available,
a minimal software citation is:

> *MusicXML JSON Interchange*, version 1.0.0, computer software.  
> Repository URL: **TODO before public release**.

Replace the placeholder with the final repository URL and add the verified
author or organization before publication. Do not infer a DOI; one may be added
later if a release is archived through a service such as Zenodo.

This project uses music21 for normalized musical interpretation. Academic work
that materially relies on music21 functionality should also cite music21
according to the current guidance from the
[official music21 documentation](https://www.music21.org/music21docs/). No
specific music21 bibliographic entry is reproduced here because an authoritative
citation string was not present in the locally installed documentation.

