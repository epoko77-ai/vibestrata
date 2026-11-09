"""Read the local footprints AI coding agents leave behind, to learn when —
and why — you last worked on each project.

Sources, all read-only and local:

* ``~/.claude/history.jsonl`` — one line per prompt you typed into Claude
  Code, with the project path, timestamp and session id. Survives session
  cleanup, so it is the strongest signal.
* ``~/.claude/projects/<encoded>/<session>.jsonl`` — Claude Code session
  transcripts. The folder-name encoding changed across versions (``_`` kept
  vs replaced by ``-``), so we never decode names; we read the ``cwd`` field.
  The same pass notes where the session's work landed (files it wrote,
  folders it ``cd``'d into), so a session opened in one folder that worked
  in another is credited to the folder where the work happened
  (:func:`relocate`).
* Codex CLI (``~/.codex``), see :mod:`vibestrata.codex`.

Only prompts are read, never the agent's answers; of the agent's actions,
only the paths it wrote to or moved into.
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
# Where a session's work landed: the files it wrote, the folders it cd'd into.
_EDIT_RE = re.compile(rb'"name"\s*:\s*"(?:Write|Edit|MultiEdit|NotebookEdit)"\s*,\s*"input"\s*:\s*\{'
                      rb'(?:"replace_all"\s*:\s*(?:true|false)\s*,\s*)?"(?:file_path|notebook_path)"\s*:\s*"((?:[^"\\]|\\.)*)"')
_CD_RE = re.compile(rb'"name"\s*:\s*"Bash"\s*,\s*"input"\s*:\s*\{\s*"command"\s*:\s*"cd ((?:/|~/)[^"\\\s&;|)]*)')
CHUNK = 8 << 20
OVERLAP = 8192
# A session moves to another project when at least this many of its writes
# and cd's land there, and that is at least two thirds of those that land in
# any project. A session that glances at another folder stays put.
MOVE_MIN = 5
MOVE_SHARE = 2 / 3
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
    worked_from: Dict[str, int] = field(default_factory=dict)   # folder sessions were opened in → sessions
    work_in: Dict[str, int] = field(default_factory=dict)       # project sessions opened here worked in → sessions

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
        for mine, theirs in ((self.worked_from, other.worked_from), (self.work_in, other.work_in)):
            for k, v in theirs.items():
                mine[k] = mine.get(k, 0) + v

    def empty(self) -> bool:
        """Only links, no sessions or prompts of its own."""
        return not (self.sessions or self.prompts or self.last_ts)

    def links(self) -> Dict[str, List[List]]:
        """``from``: folders whose sessions worked here; ``to``: projects that
        sessions opened here worked in. ``[[path, sessions], …]``, most first."""
        out = {}
        for k, d in (("from", self.worked_from), ("to", self.work_in)):
            if d:
                out[k] = [[path, n] for path, n in sorted(d.items(), key=lambda x: -x[1])]
        return out

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


@dataclass
class Mover:
    """A session whose work landed outside the folder it was opened in. Held
    apart until :func:`relocate` knows which folders are projects."""
    cwd: str
    hits: Dict[str, int]        # folder → writes and cd's there
    act: Activity = field(default_factory=Activity)


class Index(dict):
    """cwd → :class:`Activity`, plus the sessions that may belong elsewhere."""

    def __init__(self, *args, **kw) -> None:
        super().__init__(*args, **kw)
        self.movers: Dict[str, Mover] = {}

    def hints(self) -> List[str]:
        """Every folder a session was opened in, moved or not."""
        return list(self.keys()) + [m.cwd for m in self.movers.values()]


def _landed(raw: bytes, home: str) -> Optional[str]:
    try:
        path = json.loads(b'"' + raw + b'"')
    except ValueError:
        return None
    if path.startswith("~/"):
        path = home + path[1:]
    return os.path.normpath(path) if path.startswith("/") else None


def _work(fh, first: bytes) -> Dict[str, int]:
    """Folders a session wrote files in or cd'd into, with counts, from the
    rest of an open transcript (``first`` is what was already read)."""
    home = os.path.expanduser("~")
    hits: Dict[str, int] = {}
    buf = first
    while True:
        more = fh.read(CHUNK)
        # a match that starts in the last OVERLAP bytes waits for the next chunk
        end = len(buf) if not more else max(0, len(buf) - OVERLAP)
        for rx, folder in ((_EDIT_RE, True), (_CD_RE, False)):
            for m in rx.finditer(buf):
                if m.start() >= end:
                    break
                path = _landed(m.group(1), home)
                if path:
                    d = os.path.dirname(path) if folder else path
                    hits[d] = hits.get(d, 0) + 1
        if not more:
            return hits
        buf = buf[end:] + more


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def read_history(path: Optional[Path] = None, movers: Optional[Dict[str, Mover]] = None) -> Dict[str, Activity]:
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
            sid = row.get("sessionId")
            mv = movers.get(sid) if movers and sid else None
            act = mv.act if mv else out.setdefault(proj, Activity())
            act.add_prompt(ts, str(row.get("display") or ""), sid, "claude", proj)
    return out


def read_sessions(base: Optional[Path] = None, movers: Optional[Dict[str, Mover]] = None) -> Dict[str, Activity]:
    """Index session transcripts by the ``cwd`` recorded inside them. With
    ``movers``, a session that wrote or cd'd outside its cwd at least
    ``MOVE_MIN`` times goes there instead, for :func:`relocate` to place."""
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
            hits: Dict[str, int] = {}
            try:
                with open(f.path, "rb") as fh:
                    head = fh.read(16384)
                    m = _CWD_RE.search(head)
                    if not m:
                        continue
                    if movers is not None:
                        hits = _work(fh, head)
                st = f.stat()
            except OSError:
                continue
            try:
                cwd = json.loads(b'"' + m.group(1) + b'"')
            except ValueError:
                continue
            cwd = os.path.normpath(cwd)
            sid = f.name[:-6]
            away = sum(n for d, n in hits.items() if not _inside(d, cwd))
            if movers is not None and away >= MOVE_MIN:
                act = movers.setdefault(sid, Mover(cwd, hits)).act
            else:
                act = out.setdefault(cwd, Activity())
            birth = getattr(st, "st_birthtime", 0) or st.st_mtime
            act.touch(birth, sid)
            act.touch(st.st_mtime)
            act.saw_session(st.st_mtime, f.name[:-6], cwd, "claude")
    return out


def _fold(into: Dict[str, Activity], extra: Dict[str, Activity]) -> None:
    for cwd, act in extra.items():
        if cwd in into:
            into[cwd].merge(act)
        else:
            into[cwd] = act


def read_all() -> Index:
    """Merge every source, keyed by absolute working directory. Sessions whose
    work landed elsewhere wait in ``.movers`` for :func:`relocate`."""
    from . import codex

    movers: Dict[str, Mover] = {}
    sessions = read_sessions(movers=movers)
    merged = read_history(movers=movers)
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
    out = Index(merged)
    out.movers = movers
    return out


def relocate(index: Dict[str, Activity], projects: List[str]) -> Dict[str, Activity]:
    """Place each held-apart session: in the project where most of its work
    landed (``MOVE_MIN`` and ``MOVE_SHARE``), else back in the folder it was
    opened in. A moved session links the two both ways: the project it moved
    to says where it was worked on from, the one it left says where its
    sessions work. Resuming still uses the folder the session was opened in."""
    movers = getattr(index, "movers", None) or {}
    out: Dict[str, Activity] = dict(index)
    roots = sorted({os.path.normpath(p) for p in projects}, key=len, reverse=True)

    def owner(path: str) -> Optional[str]:
        return next((r for r in roots if _inside(path, r)), None)

    def put(key: str, act: Activity) -> None:
        if key in out:
            out[key].merge(act)
        else:
            out[key] = act

    for mv in movers.values():
        tally: Dict[str, int] = {}
        for d, n in mv.hits.items():
            o = owner(d)
            if o:
                tally[o] = tally.get(o, 0) + n
        src = owner(mv.cwd)
        dest = max(tally, key=lambda k: tally[k]) if tally else None
        n = tally.get(dest, 0) if dest else 0
        if dest and dest != src and n >= MOVE_MIN and n >= MOVE_SHARE * sum(tally.values()):
            frm = src or mv.cwd
            mv.act.worked_from[frm] = mv.act.worked_from.get(frm, 0) + 1
            put(dest, mv.act)
            if src:
                link = Activity()
                link.work_in[dest] = 1
                put(src, link)
        else:
            put(mv.cwd, mv.act)
    return out
