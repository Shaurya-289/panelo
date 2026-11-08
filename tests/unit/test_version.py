from __future__ import annotations

import tomllib
from pathlib import Path

import panelo


def test_package_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        version = tomllib.load(fh)["project"]["version"]
    assert panelo.__version__ == version
