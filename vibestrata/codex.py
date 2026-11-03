"""Codex CLI footprints (read-only).

* ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`` — one file per session. Its
  first line is a ``session_meta`` record with the session ``id`` and ``cwd``.
* ``~/.codex/history.jsonl`` — the prompts you typed, keyed by session id.

There can be tens of thousands of session files (``codex exec`` pipelines), so
the id → cwd map is cached in ``~/.vibestrata/codex-index.json``. A session's
first line never changes, so each file is read once.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .claude import Activity
from .config import HOME, read_json, state_dir, write_json


def codex_dir() -> Path:
    return Path(os.environ.get("VIBESTRATA_CODEX_DIR") or os.environ.get("CODEX_HOME") or HOME / ".codex")


def _session_files(base: Path) -> List[Tuple[str, os.stat_result]]:
    out: List[Tuple[str, os.stat_result]] = []
    stack = [str(base / "sessions"), str(base / "archived_sessions")]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.name.endswith(".jsonl") and e.is_file(follow_symlinks=False):
                            out.append((e.path, e.stat(follow_symlinks=False)))
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def _meta(path: str) -> Optional[Tuple[str, str]]:
    try:
        with open(path, "rb") as fh:
            line = fh.readline(262144)
        d = json.loads(line.decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    pl = d.get("payload") if isinstance(d, dict) else None
    if not isinstance(pl, dict) or not pl.get("cwd"):
        return None
    return str(pl.get("id") or pl.get("session_id") or ""), str(pl["cwd"])


def read_all(base: Optional[Path] = None, cache: bool = True) -> Dict[str, Activity]:
    base = base or codex_dir()
    if not base.is_dir():
        return {}
    cache_path = state_dir() / "codex-index.json"
    old = (read_json(cache_path, {}) or {}).get("files", {}) if cache else {}
    index: Dict[str, List[str]] = {}
    out: Dict[str, Activity] = {}
    sid_cwd: Dict[str, str] = {}
    changed = False
    for path, st in _session_files(base):
        rel = os.path.relpath(path, base)
        hit = old.get(rel)
        if not hit or not hit[1]:
            m = _meta(path)
            hit = list(m) if m else ["", ""]
            changed = True
        if hit[1]:
            index[rel] = hit  # a failed read (half-written first line) is retried next time
        sid, cwd = hit
        if not cwd:
            continue
        cwd = os.path.normpath(cwd)
        if sid:
            sid_cwd[sid] = cwd
        act = out.setdefault(cwd, Activity())
        act.touch(getattr(st, "st_birthtime", 0) or st.st_mtime, sid or None)
        act.touch(st.st_mtime)
        act.saw_session(st.st_mtime, sid or None, cwd, "codex")
    if cache and (changed or len(index) != len(old)) and index != old:
        try:
            write_json(cache_path, {"v": 1, "files": index})
        except OSError:
            pass
    try:
        fh = open(base / "history.jsonl", encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            cwd = sid_cwd.get(str(row.get("session_id") or ""))
            if not cwd:
                continue
            ts = float(row.get("ts") or 0)
            ts = ts / 1000.0 if ts > 1e11 else ts
            out.setdefault(cwd, Activity()).add_prompt(ts, str(row.get("text") or ""), row.get("session_id"), "codex", cwd)
    return out
