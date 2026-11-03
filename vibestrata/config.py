"""Paths, user config and persisted UI state.

Everything vibestrata writes lives under one directory (``~/.vibestrata`` by default,
override with ``VIBESTRATA_HOME``). Uninstalling is ``rm -rf ~/.vibestrata``.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator

try:  # POSIX advisory locks; Windows falls back to no locking.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

HOME = Path(os.path.expanduser("~"))

# Top-level home folders that are never projects.
DEFAULT_SKIP = [
    "Library", "Applications", "Desktop", "Documents", "Downloads", "Movies",
    "Music", "Pictures", "Public", "Sites", "Dropbox", "OneDrive",
    "Google Drive", "iCloud Drive", "Creative Cloud Files",
    # toolchains / package managers living in ~
    "go", "miniconda3", "anaconda3", "miniforge3", "opt", "bin", "snap",
]

# Read-only "toolchain caches" KPI. Not managed — just shown.
TOOLCHAIN_DIRS = [
    ".npm", ".pnpm-store", ".yarn", ".bun", ".cache", ".cargo", ".rustup",
    ".nvm", ".pyenv", ".gradle", ".m2", "go", "miniconda3", "anaconda3",
    "miniforge3", "Library/Caches/pip", "Library/Caches/ms-playwright",
    "Library/pnpm", "Library/Caches/Yarn",
]

DEFAULT_CONFIG: Dict[str, Any] = {
    # Folders whose children are candidate projects.
    "roots": ["~", "~/code", "~/projects", "~/dev", "~/src", "~/work", "~/repos", "~/Developer"],
    "skip": DEFAULT_SKIP,
    # Status thresholds in days since last activity.
    "thresholds": {"active": 14, "cooling": 45, "dormant": 120},
    # Files at or above this size are listed as "heavy outputs".
    "big_file_mb": 20,
    # Read prompts from ~/.claude/history.jsonl to show where you left off.
    "claude_prompts": True,
    # Claude Code plugin: say where you left off after this many days away,
    # and refresh the survey in the background once it is this many hours old.
    "session_brief": True,
    "session_brief_days": 3,
    "session_survey_hours": 12,
    # Extra "Open in…" command. {path} is replaced. e.g. "cursor {path}"
    "editor_command": "",
}


def state_dir() -> Path:
    d = Path(os.environ.get("VIBESTRATA_HOME") or HOME / ".vibestrata")
    d.mkdir(parents=True, exist_ok=True)
    return d


def claude_dir() -> Path:
    return Path(os.environ.get("VIBESTRATA_CLAUDE_DIR") or HOME / ".claude")


def home() -> Path:
    return Path(os.environ.get("VIBESTRATA_USER_HOME") or HOME)


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    lock = path.with_suffix(path.suffix + ".lock")
    with open(lock, "a+") as fh:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_UN)


def read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic write: temp file in the same dir, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_config() -> Dict[str, Any]:
    path = state_dir() / "config.json"
    user = read_json(path, None)
    if user is None:
        write_json(path, DEFAULT_CONFIG)
        user = {}
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def load_state() -> Dict[str, Any]:
    st = read_json(state_dir() / "state.json", {})
    st.setdefault("pins", [])
    st.setdefault("overrides", {})   # id -> state you set (wins over everything)
    st.setdefault("labels", {})      # id -> the triage skill's reading (wins over the rules)
    st.setdefault("names", {})       # id -> {title, summary} you gave it
    st.setdefault("notes", {})       # id -> your note ("next: fix the mobile PDF fonts")
    st.pop("verdicts", None)         # the review screen is gone; corrections are overrides
    # 0.1 stored age-based statuses; states replaced them. "keep" became "in-use".
    for k, v in list(st["overrides"].items()):
        if v in ("cooling", "dormant", "fossil"):
            del st["overrides"][k]
        elif v == "keep":
            st["overrides"][k] = "in-use"
    return st


def update_state(fn) -> Dict[str, Any]:
    path = state_dir() / "state.json"
    with locked(path):
        st = load_state()
        fn(st)
        write_json(path, st)
    return st


def expand(p: str) -> Path:
    if p == "~" or p.startswith("~/"):
        return home() / p[2:] if p != "~" else home()
    return Path(os.path.expanduser(p))
