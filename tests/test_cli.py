from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_validation_cli_from_non_project_directory(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "validate_json_schema.py"
    example = ROOT / "schema" / "examples" / "simple.json"
    result = subprocess.run(
        [sys.executable, str(script), str(example)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Valid Music Score JSON v1.0.0" in result.stdout


def test_all_cli_help_commands_work_from_non_project_directory(tmp_path: Path) -> None:
    scripts = [
        "musicxml_to_json.py",
        "json_to_musicxml.py",
        "compare_musicxml_semantics.py",
    ]
    for name in scripts:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / name), "--help"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{name}: {result.stderr}"

