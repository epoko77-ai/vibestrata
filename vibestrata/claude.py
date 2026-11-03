"""Read the local footprints AI coding agents leave behind, to learn when —
and why — you last worked on each project.

Sources, all read-only and local:

* ``~/.claude/history.jsonl`` — one line per prompt you typed into Claude
  Code, with the project path, timestamp and session id. Survives session
  cleanup, so it is the strongest signal.
* ``~/.claude/projects/<encoded>/<session>.jsonl`` — Claude Code session
  transcripts. The folder-name encoding changed across versions (``_`` kept
  vs replaced by ``-``), so we never decode names; we read the ``cwd`` field.
* Codex CLI (``~/.codex``), see :mod:`vibestrata.codex`.

Only prompts are read, never the agent's answers.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .config import claude_dir

_CWD_RE = re.compile(rb'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')
DAY = 86400
TAIL = 12
HEAD = 6

# Acknowledgements and menu picks carry no intent: "quit", "응", "2", "계속해".
NOISE_RE = re.compile(
    r"^(?:quit|exit|q|y|n|yes|no|ok|okay|k|go|continue|proceed|next|done|sure|thanks?|"
    r"응|ㅇ+|ㅇㅋ|네|넵|예|좋아|오케이|계속|계속해|계속해줘|진행|진행해|진행해줘|고|ㄱ+|다음)"
    r"[.!~ ]*$|^[\d\s.,)(/*-]+$",
    re.I,
)
# Checkpoint rituals end a session without saying anything about the project.
RITUAL_RE = re.compile(r"중간\s*저장|저장해\s*줘|여기까지|커밋\s*해|푸시\s*해|commit|push|save (?:it|progress|this)", re.I)


# Prompts sometimes carry pasted credentials. Nothing that looks like one is
# kept, shown or handed to the triage skill.
SECRET_RE = re.compile(
    r"AIza[0-9A-Za-z_\-]{30,}|\bsk-[A-Za-z0-9_\-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b|\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"
    r"|(?i:(?:api[_-]?key|token|secret|password|passwd)\s*[:=]\s*)\S{8,}"
    r"|\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{32,}\b"
)


def redact(text: str) -> str:
    return SECRET_RE.sub("[redacted]", text)


def clip(text: str, n: int = 280) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def is_meaningful(prompt: str) -> bool:
    """Anything that is not a slash command, shell escape or bare acknowledgement."""
    p = prompt.strip()
    if not p or p.startswith(("/", "!", "#")):
        return False
    return not NOISE_RE.match(p)


def _hangul(s: str) -> int:
    return sum(1 for ch in s if "가" <= ch <= "힣")


def is_substantive(prompt: str) -> bool:
    """Meaningful and long enough to quote as "where you left off"."""
    p = prompt.strip()
    if not is_meaningful(p):
        return False
    if RITUAL_RE.search(p) and len(p) < 24:
        return False
    return len(p) >= 8 or (len(p.split()) >= 2 and len(p) >= 5) or _hangul(p) >= 4


# Kept for older imports.
_is_meaningful = is_substantive
_clip = clip


@dataclass
class Activity:
    prompts: int = 0
    meaningful: int = 0
    sessions: Set[str] = field(default_factory=set)
    first_ts: float = 0.0
    last_ts: float = 0.0
    days: Set[int] = field(default_factory=set)
    first_prompt: Tuple[float, str] = (0.0, "")
    last_prompt: Tuple[float, str] = (0.0, "")
    p_first: float = 0.0          # first / last meaningful prompt
    p_last: float = 0.0
    tail: List[Tuple[float, str]] = field(default_factory=list)
    head: List[Tuple[float, str]] = field(default_factory=list)   # earliest substantive prompts
    sources: Dict[str, int] = field(default_factory=dict)
    last_session: Tuple[float, str, str, str] = (0.0, "", "", "")  # ts, id, cwd, source

    def touch(self, ts: float, session: Optional[str] = None) -> None:
        if session:
            self.sessions.add(session)
        if ts:
            self.days.add(int(ts // DAY))
            if not self.first_ts or ts < self.first_ts:
                self.first_ts = ts
            if ts > self.last_ts:
                self.last_ts = ts

    def saw_session(self, ts: float, session: Optional[str], cwd: str, source: str) -> None:
        if session and ts >= self.last_session[0]:
            self.last_session = (ts, session, cwd, source)

    def add_prompt(self, ts: float, text: str, session: Optional[str], source: str, cwd: str = "") -> None:
        self.prompts += 1
        self.sources[source] = self.sources.get(source, 0) + 1
        self.touch(ts, session)
        if cwd:
            self.saw_session(ts, session, cwd, source)
        if not is_meaningful(text):
            return
        text = redact(text)
        self.meaningful += 1
        if not self.p_first or ts < self.p_first:
            self.p_first = ts
        if ts > self.p_last:
            self.p_last = ts
        self.tail.append((ts, clip(text, 400)))
        if len(self.tail) > TAIL * 4:
            self._trim()
        if is_substantive(text):
            if len(self.head) < HEAD or ts < self.head[-1][0]:
                self.head.append((ts, clip(text, 400)))
                self.head.sort(key=lambda x: x[0])
                del self.head[HEAD:]
            if not self.first_prompt[1] or ts < self.first_prompt[0]:
                self.first_prompt = (ts, clip(text))
            if ts >= self.last_prompt[0]:
                self.last_prompt = (ts, clip(text))

    def _trim(self) -> None:
        self.tail.sort(key=lambda x: x[0])
        del self.tail[:-TAIL]

    def merge(self, other: "Activity") -> None:
        self.prompts += other.prompts
        self.meaningful += other.meaningful
        self.sessions |= other.sessions
        self.days |= other.days
        for k, v in other.sources.items():
            self.sources[k] = self.sources.get(k, 0) + v
        if other.first_ts and (not self.first_ts or other.first_ts < self.first_ts):
            self.first_ts = other.first_ts
        self.last_ts = max(self.last_ts, other.last_ts)
        if other.p_first and (not self.p_first or other.p_first < self.p_first):
            self.p_first = other.p_first
        self.p_last = max(self.p_last, other.p_last)
        if other.first_prompt[1] and (not self.first_prompt[1] or other.first_prompt[0] < self.first_prompt[0]):
            self.first_prompt = other.first_prompt
        if other.last_prompt[1] and other.last_prompt[0] >= self.last_prompt[0]:
            self.last_prompt = other.last_prompt
        self.tail.extend(other.tail)
        self._trim()
        self.head = sorted(self.head + other.head, key=lambda x: x[0])[:HEAD]
        if other.last_session[0] >= self.last_session[0] and other.last_session[1]:
            self.last_session = other.last_session

    def recent(self, n: int = TAIL) -> List[Tuple[float, str]]:
        self._trim()
        return self.tail[-n:]

    def to_json(self, with_prompts: bool) -> dict:
        return {
            "prompts": self.prompts,
            "meaningful": self.meaningful,
            "sessions": len(self.sessions),
            "first": self.first_ts or None,
            "last": self.last_ts or None,
            "p_first": self.p_first or None,
            "p_last": self.p_last or None,
            "sources": dict(self.sources),
            "first_prompt": self.first_prompt[1] if with_prompts else "",
            "last_prompt": self.last_prompt[1] if with_prompts else "",
            "recent": [[ts, clip(t, 200)] for ts, t in self.recent(6)] if with_prompts else [],
            "asks": [[ts, clip(t, 240)] for ts, t in self.head[:4]] if with_prompts else [],
            # what "continue where I left off" reopens (an id, not content)
            "session": ({"id": self.last_session[1], "cwd": self.last_session[2], "source": self.last_session[3],
                         "ts": self.last_session[0]} if self.last_session[1] else None),
        }


ClaudeActivity = Activity  # older name


def read_history(path: Optional[Path] = None) -> Dict[str, Activity]:
    path = path or claude_dir() / "history.jsonl"
    out: Dict[str, Activity] = {}
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            proj = row.get("project") if isinstance(row, dict) else None
            if not proj:
                continue
            ts = row.get("timestamp") or 0
            ts = ts / 1000.0 if ts > 1e11 else float(ts)
            proj = os.path.normpath(proj)
            act = out.setdefault(proj, Activity())
            act.add_prompt(ts, str(row.get("display") or ""), row.get("sessionId"), "claude", proj)
    return out


def read_sessions(base: Optional[Path] = None) -> Dict[str, Activity]:
    """Index session transcripts by the ``cwd`` recorded inside them."""
    base = base or claude_dir() / "projects"
    out: Dict[str, Activity] = {}
    try:
        folders = list(os.scandir(base))
    except OSError:
        return out
    for folder in folders:
        if not folder.is_dir(follow_symlinks=False):
            continue
        try:
            files = [e for e in os.scandir(folder.path) if e.name.endswith(".jsonl") and e.is_file()]
        except OSError:
            continue
        for f in files:
            try:
                with open(f.path, "rb") as fh:
                    head = fh.read(16384)
                st = f.stat()
            except OSError:
                continue
            m = _CWD_RE.search(head)
            if not m:
                continue
            try:
                cwd = json.loads(b'"' + m.group(1) + b'"')
            except ValueError:
                continue
            cwd = os.path.normpath(cwd)
            act = out.setdefault(cwd, Activity())
            birth = getattr(st, "st_birthtime", 0) or st.st_mtime
            act.touch(birth, f.name[:-6])
            act.touch(st.st_mtime)
            act.saw_session(st.st_mtime, f.name[:-6], cwd, "claude")
    return out


def _fold(into: Dict[str, Activity], extra: Dict[str, Activity]) -> None:
    for cwd, act in extra.items():
        if cwd in into:
            into[cwd].merge(act)
        else:
            into[cwd] = act


def read_all() -> Dict[str, Activity]:
    """Merge every source, keyed by absolute working directory."""
    from . import codex

    merged = read_history()
    sessions = read_sessions()
    for cwd, act in sessions.items():
        if cwd in merged:
            # transcripts add sessions and times, never prompts (history has them)
            m = merged[cwd]
            m.sessions |= act.sessions
            m.days |= act.days
            m.last_ts = max(m.last_ts, act.last_ts)
            if act.last_session[1] and act.last_session[0] >= m.last_session[0]:
                m.last_session = act.last_session
            if act.first_ts and (not m.first_ts or act.first_ts < m.first_ts):
                m.first_ts = act.first_ts
        else:
            merged[cwd] = act
    _fold(merged, codex.read_all())
    return merged
