"""Which build is running: pyproject version + git commit, and the changelog.

The launchers ``git pull`` on every start, so the commit is what pins down a
user's build in a bug report; the version marks releases in CHANGELOG.md.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"]["version"]


def _commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


# Read once at import: that is the code this process is actually running.
COMMIT = _commit()


def describe() -> str:
    return f"{VERSION} ({COMMIT})" if COMMIT else VERSION


def changelog(path: Path = ROOT / "CHANGELOG.md") -> list[dict]:
    """``## title`` sections with their ``- item`` lines, newest first."""
    sections: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            sections.append({"title": line[3:].strip(), "items": []})
        elif line.startswith("- ") and sections:
            sections[-1]["items"].append(line[2:].strip())
    return sections
