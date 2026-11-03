"""What counts as regenerable, and the proof we demand before touching it.

Every candidate gets a *tier*:

* ``1`` — safe by construction (a lockfile/manifest proves it can be rebuilt).
  Pre-selected in the UI.
* ``2`` — probably regenerable but sometimes a deliverable (``dist``,
  ``build``, ``out``…). Shown, never pre-selected; the user opts in per item.
* ``0`` — blocked. Shown with the reason, cannot be selected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

SF_DATALESS = 0x40000000  # macOS: iCloud/File Provider placeholder


def _has(dir_: str, *names: str) -> bool:
    return any(os.path.exists(os.path.join(dir_, n)) for n in names)


def _has_prefix(dir_: str, *prefixes: str) -> bool:
    try:
        return any(n.startswith(prefixes) for n in os.listdir(dir_))
    except OSError:
        return False


def _pkg_mentions(dir_: str, word: str) -> bool:
    try:
        with open(os.path.join(dir_, "package.json"), encoding="utf-8", errors="replace") as fh:
            return f'"{word}"' in fh.read(200_000)
    except OSError:
        return False


JS = ("package.json",)
PY = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "poetry.lock", "uv.lock")
BUILD_CONFIGS = JS + PY + ("Cargo.toml", "go.mod", "Makefile", "CMakeLists.txt", "build.gradle", "pom.xml")


@dataclass(frozen=True)
class Rule:
    category: str
    tier: int
    # proof(parent_dir, candidate_path) -> True if regenerable
    proof: Callable[[str, str], bool]
    proof_text: str


def _always(_p: str, _c: str) -> bool:
    return True


RULES: Dict[str, Rule] = {
    "node_modules": Rule("deps", 1, lambda p, c: _has(p, *JS), "package.json next to it"),
    ".venv": Rule("deps", 1, lambda p, c: _has(c, "pyvenv.cfg"), "pyvenv.cfg inside"),
    "venv": Rule("deps", 1, lambda p, c: _has(c, "pyvenv.cfg"), "pyvenv.cfg inside"),
    ".tox": Rule("deps", 1, lambda p, c: _has(p, "tox.ini", *PY), "tox/python config next to it"),
    "__pycache__": Rule("cache", 1, _always, "bytecode cache"),
    ".pytest_cache": Rule("cache", 1, _always, "test cache"),
    ".mypy_cache": Rule("cache", 1, _always, "type-check cache"),
    ".ruff_cache": Rule("cache", 1, _always, "lint cache"),
    ".next": Rule("build", 1, lambda p, c: _has_prefix(p, "next.config") or _pkg_mentions(p, "next"), "Next.js project"),
    ".nuxt": Rule("build", 1, lambda p, c: _has(p, *JS), "package.json next to it"),
    ".svelte-kit": Rule("build", 1, lambda p, c: _has(p, *JS), "package.json next to it"),
    ".turbo": Rule("cache", 1, lambda p, c: _has(p, *JS), "package.json next to it"),
    ".parcel-cache": Rule("cache", 1, lambda p, c: _has(p, *JS), "package.json next to it"),
    ".angular": Rule("cache", 1, lambda p, c: _has(p, "angular.json"), "angular.json next to it"),
    "target": Rule("build", 1, lambda p, c: _has(p, "Cargo.toml"), "Cargo.toml next to it"),
    ".gradle": Rule("cache", 1, lambda p, c: _has(p, "build.gradle", "build.gradle.kts", "settings.gradle"), "Gradle project"),
    # Tier 2: often regenerable, sometimes the deliverable.
    "dist": Rule("output", 2, lambda p, c: _has(p, *BUILD_CONFIGS), "build config next to it"),
    "build": Rule("output", 2, lambda p, c: _has(p, *BUILD_CONFIGS), "build config next to it"),
    "out": Rule("output", 2, lambda p, c: _has(p, *JS), "package.json next to it"),
    "coverage": Rule("output", 2, lambda p, c: _has(p, *BUILD_CONFIGS), "build config next to it"),
    ".cache": Rule("cache", 2, lambda p, c: _has(p, *JS), "package.json next to it"),
}

# Names we never descend into even when the proof fails (they are huge and
# never hand-written), but which are only actionable with proof.
OPAQUE = {"node_modules", ".venv", "venv", "__pycache__", ".next", "target", ".tox", ".gradle"}

NEVER = {".git", ".claude", ".hg", ".svn", ".vibestrata-quarantine"}

# Project markers. A top-level folder with any of these is a project.
MARKERS = (
    ".git", "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Cargo.toml",
    "go.mod", "CLAUDE.md", "AGENTS.md", ".claude", ".cursor", "README.md", "README",
    "index.html", "Makefile", "vercel.json", ".vercel", "deno.json", "Gemfile", "pom.xml",
    "build.gradle", "Dockerfile", "docker-compose.yml", "main.py", "app.py",
)


def match(name: str) -> Optional[Rule]:
    return RULES.get(name)


def has_marker(path: str) -> bool:
    try:
        names = set(os.listdir(path))
    except OSError:
        return False
    if names & set(MARKERS):
        return True
    return any(n.endswith((".ipynb", ".sln", ".xcodeproj")) for n in names)


def detect_stack(path: str) -> List[str]:
    try:
        names = set(os.listdir(path))
    except OSError:
        return []
    s: List[str] = []
    if any(n.startswith("next.config") for n in names) or ("package.json" in names and _pkg_mentions(path, "next")):
        s.append("Next.js")
    elif "package.json" in names:
        s.append("Vite" if any(n.startswith("vite.config") for n in names) else "Node")
    if names & {"pyproject.toml", "requirements.txt", "setup.py", "uv.lock", "Pipfile"} or any(n.endswith(".py") for n in names):
        s.append("Python")
    if any(n.endswith(".ipynb") for n in names):
        s.append("Jupyter")
    if "Cargo.toml" in names:
        s.append("Rust")
    if "go.mod" in names:
        s.append("Go")
    if any(n.endswith(".typ") for n in names):
        s.append("Typst")
    if any(n.endswith((".pptx", ".key")) for n in names):
        s.append("Deck")
    if "index.html" in names and not s:
        s.append("Static")
    if names & {"CLAUDE.md", ".claude"}:
        s.append("Claude")
    if names & {".cursor", ".cursorrules"}:
        s.append("Cursor")
    if names & {"vercel.json", ".vercel"}:
        s.append("Vercel")
    return s


def _cloudish(p: str) -> bool:
    return "/Library/Mobile Documents/" in p or "com~apple~CloudDocs" in p or "/Library/CloudStorage/" in p


def is_cloud_path(path: str) -> bool:
    """Checks both the given and the physical path (symlinked roots)."""
    return _cloudish(path) or _cloudish(os.path.realpath(path))
