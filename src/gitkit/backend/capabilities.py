"""Detect the git version and derive a capability table.

The version drives how commands are built: on 1.8.3.1 we must avoid -C,
switch/restore, porcelain=v2, etc. (see docs/backend/git-1.8-command-map.md §6).
"""
from __future__ import annotations

import re
import subprocess

from gitkit.core.models import Capabilities

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(version_line: str) -> tuple[int, int, int]:
    """'git version 2.54.0.windows.1' -> (2, 54, 0)."""
    m = _VERSION_RE.search(version_line)
    if not m:
        raise ValueError(f"cannot parse git version from: {version_line!r}")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def derive(version: tuple[int, int, int]) -> Capabilities:
    return Capabilities(
        version=version,
        has_dash_C=version >= (1, 8, 5),
        has_switch_restore=version >= (2, 23, 0),
        has_porcelain_v2=version >= (2, 11, 0),
    )


def detect(git: str = "git") -> Capabilities:
    proc = subprocess.run(
        [git, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    line = proc.stdout.decode("utf-8", "replace").strip()
    return derive(parse_version(line))
