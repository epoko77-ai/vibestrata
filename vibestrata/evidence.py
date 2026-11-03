"""The evidence pack the Claude Code triage skill reads for one project.

Small on purpose (≤ 4 KB of JSON): facts the rules used, the last meaningful
prompts with dates, the tail of the agent's last reply when a transcript
survives, the freshest hand-off note, the last commit subjects and the newest
deliverables. Built on demand and printed; never written to disk. Anything
that looks like a credential is redacted.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from . import claude as claude_mod
from . import codex, gitinfo
from .claude import clip, redact
from .config import claude_dir, home

TAIL_BYTES = 768 * 1024


def _owner(cwd: str, path: str, others: List[str]) -> bool:
    if cwd != path and not cwd.startswith(path + os.sep):
        return False
    best = max((o for o in others if cwd == o or cwd.startswith(o + os.sep)), key=len, default=path)
    return best == path


def _tail_lines(f: str) -> List[bytes]:
    try:
        with open(f, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            data = fh.read()
    except OSError:
        return []
    lines = data.splitlines()
    return lines[1:] if size > TAIL_BYTES else lines


def _assistant_text(line: bytes) -> str:
    try:
        d = json.loads(line)
    except ValueError:
        return ""
    if not isinstance(d, dict):
        return ""
    if d.get("type") == "assistant":  # Claude Code
        content = (d.get("message") or {}).get("content")
        if isinstance(content, list):
            return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text").strip()
        return ""
    pl = d.get("payload")
    if isinstance(pl, dict):  # Codex
        if pl.get("type") == "message" and pl.get("role") == "assistant" and isinstance(pl.get("content"), list):
            return "\n".join(c.get("text", "") for c in pl["content"] if isinstance(c, dict)).strip()
        if pl.get("type") == "task_complete" and pl.get("last_agent_message"):
            return str(pl["last_agent_message"]).strip()
    return ""


def last_reply(path: str, others: List[str]) -> Optional[Dict[str, Any]]:
    """Tail of the agent's final answer in the newest surviving transcript."""
    files: List[tuple] = []
    base = claude_dir() / "projects"
    try:
        for folder in os.scandir(base):
            if not folder.is_dir(follow_symlinks=False):
                continue
            for f in os.scandir(folder.path):
                if f.name.endswith(".jsonl"):
                    files.append((f.stat().st_mtime, f.path, "claude"))
    except OSError:
        pass
    cx = codex.codex_dir()
    for f, st in codex._session_files(cx):
        files.append((st.st_mtime, f, "codex"))
    files.sort(reverse=True)
    for mtime, f, src in files:
        try:
            with open(f, "rb") as fh:
                head = fh.read(262144)
        except OSError:
            continue
        m = claude_mod._CWD_RE.search(head)
        if not m:
            continue
        try:
            cwd = os.path.normpath(json.loads(b'"' + m.group(1) + b'"'))
        except ValueError:
            continue
        if not _owner(cwd, path, others):
            continue
        for line in reversed(_tail_lines(f)):
            text = _assistant_text(line)
            if text:
                text = " ".join(text.split())
                return {"source": src, "ts": mtime, "text": redact(text[-700:])}
        return None
    return None


def _commits(path: str, n: int = 5) -> List[str]:
    out = gitinfo._git(path, "log", f"-{n}", "--format=%cs %s", "--", ".") or ""
    return [clip(ln, 120) for ln in out.splitlines() if ln.strip()]


def _date(ts: Optional[float]) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""


def build(p: Dict[str, Any], projects: List[Dict[str, Any]], limit: int = 4096) -> Dict[str, Any]:
    path = p["path"]
    others = [q["path"] for q in projects]
    act: Optional[claude_mod.Activity] = None
    for cwd, a in claude_mod.read_all().items():
        if _owner(cwd, path, others):
            if act is None:
                act = claude_mod.Activity()
            act.merge(a)
    prompts = [[_date(ts), clip(t, 240)] for ts, t in (act.recent(10) if act else [])]
    reply = last_reply(path, others)
    g = p.get("git") or {}
    h = str(home())
    pack: Dict[str, Any] = {
        "project": p["name"],
        "title": p.get("title") if p.get("title_src") != "folder" else "",
        "summary": clip(p.get("summary") or "", 200),
        "kind": p.get("kind"),
        "links": p.get("links") or {},
        "path": "~" + path[len(h):] if path.startswith(h + os.sep) else path,
        "rules": {"state": p.get("state"), "why": p.get("why"), "idle_days": round(p["days"]) if p.get("days") is not None else None},
        "facts": {
            "desc": clip(p.get("desc") or "", 200),
            "stack": p.get("stack"),
            "size": p.get("bytes"),
            "prompts": (act.meaningful if act else 0),
            "first_prompt": _date(act.p_first) if act else "",
            "git": {"branch": g.get("branch"), "dirty": g.get("dirty"), "unpushed": g.get("unpushed"),
                    "remote": g.get("remote"), "commits": g.get("commits"), "recent": _commits(path)} if g else None,
            "unsaved": (p.get("flags") or {}).get("unsaved"),
            "registered": (p.get("flags") or {}).get("registered"),
            "deliverables": {"count": (p.get("deliverables") or {}).get("count", 0),
                             "newest": _date((p.get("deliverables") or {}).get("newest")),
                             "recent": (p.get("deliverables") or {}).get("recent", [])},
            "byproducts_bytes": (p.get("byproducts") or {}).get("bytes", 0),
        },
        "resume_note": ({"file": p["resume"]["file"], "date": _date(p["resume"]["ts"]), "text": p["resume"]["text"]}
                        if p.get("resume") else None),
        "last_prompts": prompts,
        "last_reply": ({"date": _date(reply["ts"]), "text": reply["text"]} if reply else None),
        "basis": "transcript+prompts" if reply else ("prompts" if prompts else "files"),
    }
    # stay under the limit: shorten the oldest prompts first, then the reply
    while len(json.dumps(pack, ensure_ascii=False).encode("utf-8")) > limit:
        if len(pack["last_prompts"]) > 4:
            pack["last_prompts"].pop(0)
        elif pack["last_reply"] and len(pack["last_reply"]["text"]) > 200:
            pack["last_reply"]["text"] = "…" + pack["last_reply"]["text"][-200:]
        elif pack["resume_note"] and len(pack["resume_note"]["text"]) > 160:
            pack["resume_note"]["text"] = pack["resume_note"]["text"][:159] + "…"
        else:
            pack["last_prompts"] = [[d, clip(t, 100)] for d, t in pack["last_prompts"]]
            pack["facts"]["git"] and pack["facts"]["git"].update(recent=pack["facts"]["git"]["recent"][:2])
            break
    return pack
