"""Sort projects by *state*, not by age.

A project that hasn't been touched for 90 days can be a tool you still use, a
finished job with drafts lying next to the deliverable, a thing you stopped
mid-way, or something you gave up on because it kept breaking. The states are
a first guess from local evidence; you correct them in one click.

=========== ================================================================
active       building: code or files changed within 14 days
in-use       a finished tool you still use: a skill, launch agent, MCP server
             or shell alias points at it, or its outputs keep changing while
             its code doesn't
delivered    done: pushed and clean, hand-off words ("나머진 내가 할게"), or a
             deliverable written right at the end
superseded   a newer sibling (``landing-v2`` → ``landing-v3``) took over
blocked      stuck: the last thing you said was a problem that wasn't fixed
             ("여전히 안 돼", "still broken") or a wait on someone
paused       on hold: stopped mid-way (stale after 45 days)
one-shot     tried once: a handful of prompts over a day or two
closed       dropped: only you (or the triage skill) say so
=========== ================================================================

Intent ("I'm done with this") is never inferred from words alone: closing
words are politeness ("고생했어" goes with "내일 계속하자" half the time) and
checkpoint rituals ("중간저장") end most sessions.

Flags sit on top of the state: unsaved work (uncommitted, unpushed, or source
code without version control); *registered* means another config
(``~/CLAUDE.md``, a skill, a launch agent, a shell alias) points at the
folder, which vetoes superseded/one-shot.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .claude import RITUAL_RE, clip, redact
from .config import claude_dir, home

DAY = 86400
STATES = ("active", "in-use", "delivered", "superseded", "blocked", "paused", "one-shot")
USER_STATES = STATES + ("closed",)
LEGACY_STATES = {"keep": "in-use"}
# how the list groups them: doing now · done · unfinished
GROUPS = (("now", ("active",)), ("done", ("in-use", "delivered", "superseded")),
          ("unfinished", ("blocked", "paused", "one-shot", "closed")))
# states whose leftovers sweep --byproducts may take (see plan())
FINISHED = ("delivered", "superseded", "closed")
TOOL_SOURCES = ("skill:", "agent:", "command:", "launch agent:", "mcp:", "shell config")

DELIVERABLE_EXT = {".pdf", ".pptx", ".key", ".docx", ".xlsx", ".hwp", ".hwpx", ".epub", ".mp4", ".mov", ".m4v"}
CHAIN_EXT = DELIVERABLE_EXT | {".png", ".jpg", ".jpeg", ".webp", ".gif", ".psd", ".wav", ".mp3", ".m4a", ".zip", ".webm"}
CODE_EXT = {".py", ".ipynb", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".go", ".rs", ".java",
            ".kt", ".swift", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".sh", ".typ", ".tex", ".sql",
            ".r", ".lua", ".dart", ".scala"}
CHAIN_MIN_BYTES = 256 * 1024
WORKSPACE_RE = re.compile(r"^_workspace(?:[-_].+)?$", re.I)
# Folders that usually hold what you were given, not what you made.
INPUT_RE = re.compile(r"^(?:docs?|refs?|references?|inputs?|sources?|raw|downloads?|자료|참고(?:\s*자료)?|원본|원문|첨부)$", re.I)

# A version suffix: report_v3, deck-final, slide_regen2, 보고서_최종, file (1)
VERSION_RE = re.compile(
    r"(?:[-_ .](?:v\d{1,3}|rev\d{1,3}|regen\d*|final\d*|최종\d*|copy\d*|복사본|old|bak|backup|draft\d*|초안|수정본?\d*|fix(?:ed)?\d*))+$"
    r"|\s*\(\d{1,2}\)$",
    re.I,
)
_VNUM = re.compile(r"(?:v|rev|regen|final|최종|draft|fix)(\d+)", re.I)

# "Still broken" says the fix didn't work: a project stuck on a problem.
PERSIST_RE = re.compile(
    r"여전히|아직도|아직\s*안\s?(?:돼|되|나|보|떠|뜨)|똑같|그대로(?:야|네|다|인데|잖)|또\s*(?:그러|안\s|에러|오류|이러|나오|떠|뜨|틀|깨)|또그러"
    r"|안\s?고쳐|안\s?고처|해결(?:이)?\s*안|못\s*(?:고치|하겠)"
    r"|\bstill\s+(?:not|broken|fail|failing|doesn|isn|won|can|getting|seeing|the same|wrong|crash|no\b|nothing|error|slow|off\b"
    r"|(?:drift|happen|show|return|break|hang|freez|time|look|load|flicker|overlap|jump|leak)\w*)"
    r"|\bsame\s+(?:error|issue|problem|bug|result)|keeps? (?:failing|crashing|breaking)|nothing (?:changed|happens)",
    re.I,
)
# A first bug report. Only counts when it is the very last thing you said.
COMPLAINT_RE = re.compile(
    r"안\s?(?:돼|되네|되는데|되잖|됨|나와|나오|열려|열리|보여|보이|떠|뜨|먹|움직|바뀌|맞아|맞네)|깨져|깨짐|깨지|꺠져|먹통"
    r"|(?:에러|오류)(?:가|는)?\s*(?:나|떠|뜨|발생)|실패(?:했|하네|해|함)|작동(?:을|이)?\s*안|왜\s*안|망가|고장|못\s*(?:찾|하네)"
    r"|doesn'?t work|does not work|not working|isn'?t working|won'?t (?:load|start|work|build|compile|run|open)|\bbroken\b",
    re.I,
)
WAIT_RE = re.compile(
    r"(?:승인|심사|답변|연락|회신|발급|결제|입금|권한)\S{0,3}\s*(?:나면|오면|받으면|되면|기다|대기)|기다리는\s*중|대기\s*중"
    r"|waiting (?:for|on)|blocked (?:by|on)|once (?:\w+ ){0,3}(?:approves|replies|responds|is approved)",
    re.I,
)
# Handing the work over: enough on its own.
HANDOFF_RE = re.compile(
    r"(?:나머지|나머진)\S*\s+(?:\S+\s+){0,2}(?:내가|제가)\s*(?:할게|하겠|할께|마무리|미세|수정|편집|손볼|손 볼|처리|채울)"
    r"|내가\s*(?:할게|할께|하겠|마무리할|미세\s*수정|편집할|손볼|수정할게)"
    r"|작업\s*(?:끝|완료|종료)(?!\s*(?:되면|하면|한\s*후|한후|후|하고|\?))|이대로\s*(?:확정|제출)|최종\s*(?:확정|본으로)"
    r"|제출(?:했|완료)|배포\s*(?:완료|끝)(?!\s*(?:되면|하면|\?))"
    r"|\b(?:ship it|that'?s it|all done|we'?re done|i'?ll take it from here)\b",
    re.I,
)
SKILL_SAVE_RE = re.compile(r"(?:이\s*)?스킬\s*(?:로\s*)?(?:저장|등록)해", re.I)
# Satisfaction: only counts next to a deliverable written at the same time.
SATISFIED_RE = re.compile(
    r"만족(?!\s*(?:하는데|한데|하지만|하나|스럽지|못|안))|맘에\s*들(?!\s*(?:지만|는데))|마음에\s*들(?!\s*(?:지만|는데))"
    r"|잘\s*(?:나왔|됐|만들었)|완벽(?:해|하다|하네|해요)|(?:다\s*)?끝났(?:다|어|네)|확정(?:할게|하자|이야|!|\.|$)"
    r"|\b(?:lgtm|looks good|perfect|great job|nailed it|deployed|shipped)\b",
    re.I,
)
# A clause that asks for something is a request, not a hand-over.
REQUEST_RE = re.compile(r"해\s*줘|줘(?:요)?\b|줘$|하자|해봐|만들어|고쳐|해야|하게|하도록|할\s*수\s*있|\b(?:please|can you|could you|make it)\b", re.I)
_CLAUSE = re.compile(r"[^.!?\n,;]+[.!?]?")


def _said(rx: "re.Pattern[str]", text: str, requests_ok: bool = False) -> Optional[str]:
    """The clause where ``rx`` matches as a statement (not a question, not a request)."""
    for c in _CLAUSE.findall(text):
        c = c.strip()
        if not c or not rx.search(c) or c.endswith("?"):
            continue
        if not requests_ok and REQUEST_RE.search(c):
            continue
        return c
    return None


CONTINUE_RE = re.compile(
    r"내일|다음에|다음\s*번|나중에|추후|이어서|이어가|계속\s*(?:하자|할게|진행)|이따|모레|다음\s*주|다음\s*세션"
    r"|tomorrow|later|next time|to be continued",
    re.I,
)

RESUME_RE = re.compile(
    r"^(?:handoff|checkpoint|resume|next[-_ ]?steps?|todo|status|project[-_ ]status|progress|changelog|worklog|devlog)"
    r"(?:[-_ .][^/]*)?(?:\.md|\.txt)?$",
    re.I,
)
LOG_RE = re.compile(r"^(?:changelog|worklog|devlog|progress)", re.I)
DATE_RE = re.compile(r"(20\d\d)[-./](\d{1,2})[-./](\d{1,2})")


# ── per-project evidence (computed during the survey) ─────────────────────

def version_chains(files: List[Dict[str, Any]], root: str) -> List[Dict[str, Any]]:
    """Older members of version chains in the same folder.

    ``files``: [{path, bytes, mtime}] with CHAIN_EXT extensions. The newest
    member of each chain (by mtime) and the highest-numbered one are kept.
    """
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for f in files:
        d, name = os.path.split(f["path"])
        stem, ext = os.path.splitext(name)
        base = VERSION_RE.sub("", stem).strip().lower()
        if not base:
            continue
        groups.setdefault((d, base, ext.lower()), []).append(f)
    out = []
    for (_d, _b, _e), members in groups.items():
        if len(members) < 2:
            continue
        # a chain needs at least one versioned name; "a.png" + "a.png" can't happen
        if not any(VERSION_RE.search(os.path.splitext(os.path.basename(m["path"]))[0]) for m in members):
            continue
        newest = max(members, key=lambda m: m["mtime"])

        def vnum(m: Dict[str, Any]) -> int:
            nums = [int(x) for x in _VNUM.findall(os.path.basename(m["path"]))]
            return max(nums) if nums else -1

        highest = max(members, key=vnum)
        keep = {newest["path"], highest["path"]}
        for m in members:
            if m["path"] in keep or m["bytes"] < CHAIN_MIN_BYTES:
                continue
            out.append({"path": m["path"], "rel": os.path.relpath(m["path"], root), "bytes": m["bytes"],
                        "mtime": m["mtime"], "why": "older-version", "kind": "file",
                        "keep": os.path.relpath(newest["path"], root)})
    return out


def _md_lines(text: str) -> List[str]:
    out = []
    fence = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("```"):
            fence = not fence
            continue
        if fence or not s or s.startswith(("<!--", "---", "![", "[!")) or re.fullmatch(r"\|?[\s:|-]+\|?", s):
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
            s = "|" + " · ".join(cells)
        s = re.sub(r"^[-*+]\s+\[[ xX]\]\s*", "☐ ", s)
        s = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", s)
        s = re.sub(r"(\*\*|__|`|\[([^\]]*)\]\([^)]*\))", lambda m: m.group(2) or "", s)
        out.append(s)
    return out


Section = Tuple[int, str, List[str]]


def _sections(text: str) -> List[Section]:
    secs: List[Section] = [(0, "", [])]
    for ln in _md_lines(text):
        m = re.match(r"^(#{1,6})\s*(.*)$", ln)
        if m:
            secs.append((len(m.group(1)), m.group(2).strip(), []))
        else:
            secs[-1][2].append(ln.lstrip("> ").strip())
    return [x for x in secs if x[1] or x[2]]


def _block(secs: List[Section], i: int) -> List[str]:
    """A section's own lines plus those of its subsections."""
    lvl, _h, body = secs[i]
    lines = [b.lstrip("|") for b in body]
    for lv, h, b in secs[i + 1:]:
        if lv <= lvl:
            break
        if b:
            lines.append(f"{h}: " + " · ".join(x.lstrip("|") for x in b[:4]) if h else " · ".join(b[:4]))
    return [x for x in lines if x]


def _date_key(s: str) -> Optional[Tuple[int, int, int]]:
    m = DATE_RE.search(s)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


# Heading priority for hand-off notes: what's next beats where we are.
_NEXT = [re.compile(r"다음\s*(?:할|작업|단계|세션)|남은|할\s*일|미완|이어갈|todo|to do|next|remaining|pending|blocker|open (?:items|issues|questions)", re.I),
         re.compile(r"현재\s*상태|진행\s*상황|상태|status|progress|where we are", re.I),
         re.compile(r"재개|resume|다음", re.I)]


def _snippet(name: str, text: str) -> str:
    secs = _sections(text)
    if not secs:
        return ""
    lines: List[str] = []
    head = ""
    if LOG_RE.match(name):
        dated = [(k, i) for i, x in enumerate(secs) if (k := _date_key(x[1]))]
        rows = [b for x in secs for b in x[2] if b.startswith("|") and _date_key(b)]
        if dated:
            i = max(dated)[1]
            head, lines = secs[i][1], _block(secs, i)
        elif rows:
            lines = [max(rows, key=lambda r: _date_key(r) or (0, 0, 0)).lstrip("|")]
        else:
            body = [i for i, x in enumerate(secs) if x[2]]
            if body:
                head, lines = secs[body[-1]][1], _block(secs, body[-1])
        text_ = (f"{head} — " if head else "") + " · ".join(lines[:8])
    else:
        for rx in _NEXT:
            hit = next((i for i, x in enumerate(secs) if x[1] and rx.search(x[1]) and _block(secs, i)), None)
            if hit is not None:
                head, lines = secs[hit][1], _block(secs, hit)
                break
        else:
            i = next((i for i, x in enumerate(secs) if x[2]), None)
            if i is None:
                return ""
            lines = _block(secs, i)
        text_ = (f"{head}: " if head else "") + " · ".join(lines[:8])
    return clip(text_, 320) if lines else ""


def resume_note(path: str) -> Optional[Dict[str, Any]]:
    """The freshest hand-off/checkpoint/changelog note, or a status section of
    the project's CLAUDE.md."""
    cands: List[Tuple[float, str]] = []
    dirs = [path, os.path.join(path, "docs")]
    try:
        dirs += [e.path for e in os.scandir(path) if e.is_dir(follow_symlinks=False) and WORKSPACE_RE.match(e.name)]
    except OSError:
        pass
    for d in dirs:
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if RESUME_RE.match(e.name) and e.is_file(follow_symlinks=False):
                    cands.append((e.stat(follow_symlinks=False).st_mtime, e.path))
            except OSError:
                continue
    for ts, f in sorted(cands, reverse=True):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                text = fh.read(60000)
        except OSError:
            continue
        snip = redact(_snippet(os.path.basename(f), text))
        if snip:
            return {"file": os.path.relpath(f, path), "ts": ts, "text": snip,
                    "kind": "log" if LOG_RE.match(os.path.basename(f)) else "handoff"}
    try:
        f = os.path.join(path, "CLAUDE.md")
        with open(f, encoding="utf-8", errors="replace") as fh:
            text = fh.read(60000)
        secs = _sections(text)
        for rx in _NEXT[:2]:
            hit = next((i for i, x in enumerate(secs) if x[1] and rx.search(x[1]) and _block(secs, i)), None)
            if hit is not None:
                return {"file": "CLAUDE.md", "ts": os.path.getmtime(f), "kind": "claude-md",
                        "text": redact(clip(f"{secs[hit][1]}: " + " · ".join(_block(secs, hit)[:8]), 320))}
    except OSError:
        pass
    return None


def prompt_signals(recent: List[Tuple[float, str]]) -> Dict[str, Any]:
    """Read the end of the final session (prompts within 6 h of the last one).

    * blocked: a "still broken" in the last two real prompts (checkpoint
      rituals like "중간저장" skipped), or a first bug report / wait on
      someone that is the very last thing said.
    * handoff: hand-off words in the last two prompts, with no "tomorrow".
    * satisfied: weaker; the caller needs a deliverable to go with it.
    """
    if not recent:
        return {}
    last_ts = recent[-1][0]
    final = [(ts, t) for ts, t in recent if last_ts - ts < 6 * 3600]
    real = [(ts, t) for ts, t in final if not (RITUAL_RE.search(t) and len(t) < 24)]
    out: Dict[str, Any] = {"last_ts": last_ts}
    for ts, t in reversed(real[-2:]):
        if PERSIST_RE.search(t):
            out["complaint"] = {"ts": ts, "text": clip(t, 160), "kind": "persist"}
            break
    if "complaint" not in out:
        ts, t = final[-1]
        if COMPLAINT_RE.search(t):
            out["complaint"] = {"ts": ts, "text": clip(t, 160), "kind": "report"}
        elif WAIT_RE.search(t):
            out["complaint"] = {"ts": ts, "text": clip(t, 160), "kind": "wait"}
    tail = final[-2:]
    joined = " ".join(t for _, t in tail)
    if CONTINUE_RE.search(joined):
        out["continue"] = True
    else:
        for _, t in reversed(tail):
            if "handoff_words" not in out and (_said(HANDOFF_RE, t) or _said(SKILL_SAVE_RE, t, requests_ok=True)):
                out["handoff_words"] = clip(t, 160)
            if "satisfied_words" not in out and _said(SATISFIED_RE, t):
                out["satisfied_words"] = clip(t, 160)
    return out


# ── registry: who else points at this folder ──────────────────────────────

def _read(path: Path, limit: int = 400_000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def registry_sources() -> List[Tuple[str, str]]:
    """(label, text) for every config that can reference a project folder."""
    h, c = home(), claude_dir()
    out: List[Tuple[str, str]] = []
    for p, label in ((h / "CLAUDE.md", "~/CLAUDE.md"), (c / "CLAUDE.md", "~/.claude/CLAUDE.md"),
                     (h / "AGENTS.md", "~/AGENTS.md"), (h / ".codex" / "AGENTS.md", "~/.codex/AGENTS.md")):
        t = _read(p)
        if t:
            out.append((label, t))
    for base in skill_dirs():
        try:
            for e in os.scandir(base):
                if ".bak" in e.name:
                    continue
                t = _read(Path(e.path) / "SKILL.md")
                if e.is_symlink():  # a skill that lives inside a project folder
                    t = os.path.realpath(e.path) + "\n" + t
                if t:
                    out.append((f"skill: {e.name}", t))
        except OSError:
            pass
    for base, kind in ((c / "commands", "command"), (c / "agents", "agent")):
        try:
            for e in os.scandir(base):
                if e.name.endswith(".md"):
                    t = _read(Path(e.path))
                    if t:
                        out.append((f"{kind}: {e.name[:-3]}", t))
        except OSError:
            pass
    for rc in (".zshrc", ".bashrc", ".bash_profile", ".profile", ".config/fish/config.fish"):
        t = _read(h / rc)
        if t:
            out.append(("shell config", t))
    for label, text in _mcp_sources(h):
        out.append((label, text))
    try:
        for e in os.scandir(h / "Library" / "LaunchAgents"):
            if e.name.endswith(".plist"):
                t = _read(Path(e.path), 100_000)
                if t:
                    out.append((f"launch agent: {e.name[:-6]}", t))
    except OSError:
        pass
    return out


def skill_dirs() -> List[Path]:
    h = home()
    return [claude_dir() / "skills", h / ".codex" / "skills", h / ".agents" / "skills"]


def skill_names() -> List[str]:
    out: List[str] = []
    for base in skill_dirs():
        try:
            out += [e.name for e in os.scandir(base) if ".bak" not in e.name and not e.name.startswith(".")]
        except OSError:
            pass
    return out


def _mcp_sources(h: Path) -> List[Tuple[str, str]]:
    """MCP servers configured for Claude Code, Claude Desktop and Codex: a
    server whose command or arguments point into a project makes it a tool."""
    out: List[Tuple[str, str]] = []

    def servers(d: Any) -> Iterable[Tuple[str, Any]]:
        return d.items() if isinstance(d, dict) else []

    import json as _json
    for f in (h / ".claude.json", h / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                d = _json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        groups = [d.get("mcpServers")] + [v.get("mcpServers") for v in (d.get("projects") or {}).values() if isinstance(v, dict)]
        for g in groups:
            for name, srv in servers(g):
                if isinstance(srv, dict):
                    out.append((f"mcp: {name}", _json.dumps(srv, ensure_ascii=False)))
    t = _read(h / ".codex" / "config.toml")
    for block in re.split(r"(?m)^\[mcp_servers\.", t)[1:]:
        name = block.split("]", 1)[0].strip().strip('"')
        out.append((f"mcp: {name}", block[:4000]))
    return out


_PATHCH = re.compile(r"[\w.\-가-힣]")


def references(path: str, sources: List[Tuple[str, str]]) -> List[str]:
    h = str(home()).rstrip(os.sep)
    spellings = [path]
    if path.startswith(h + os.sep):
        spellings += ["~" + path[len(h):], "$HOME" + path[len(h):]]
    hits: List[str] = []
    for label, text in sources:
        for sp in spellings:
            i = text.find(sp)
            found = False
            while i >= 0:
                j = i + len(sp)
                if j >= len(text) or not _PATHCH.match(text[j]):
                    found = True
                    break
                i = text.find(sp, j)
            if found:
                if label not in hits:
                    hits.append(label)
                break
    return hits


# ── classification ────────────────────────────────────────────────────────

def is_tool(p: Dict[str, Any], skills: Iterable[str] = ()) -> Optional[str]:
    """Why this folder is a tool you use (a skill, launch agent, MCP server,
    command or alias points at it), or None."""
    for label in (p.get("flags") or {}).get("registered") or []:
        if label.startswith(TOOL_SOURCES):
            return label
    # a global skill named after the project ("tokensave" ↔ ~/tokensave), when
    # the project is a harness (it has agents/skills) or your CLAUDE.md lists it
    n = re.sub(r"[\W_]+", "", p["name"].lower())
    if len(n) < 4:
        return None
    for sk in skills:
        k = re.sub(r"[\W_]+", "", sk.lower())
        if k == n or (sk.lower().startswith(p["name"].lower() + "-")):
            harness = any(os.path.isdir(os.path.join(p["path"], d)) for d in (".claude/agents", ".claude/skills", "skills", "agents"))
            if harness or "~/CLAUDE.md" in ((p.get("flags") or {}).get("registered") or []):
                return f"skill: {sk}"
    return None

def classify(p: Dict[str, Any], th: Dict[str, int], newer_sibling: Optional[Dict[str, Any]]) -> Tuple[str, List[list]]:
    days = p.get("days")
    days = 9999.0 if days is None else days
    sig = p.get("signals") or {}
    ai = p.get("claude") or {}
    g = p.get("git") or {}
    reg = (p.get("flags") or {}).get("registered") or []
    dl = p.get("deliverables") or {}
    why: List[list] = []

    tool = p.get("tool")
    if days < th["active"]:
        # touched recently: building, unless only its outputs changed
        code_last = p.get("code_last") or 0
        commits = (g.get("last_commit") or 0) if g else 0
        fresh_code = max(code_last, commits) and (time.time() - max(code_last, commits)) / DAY < th["active"]
        newest = (dl.get("newest") or 0)
        outputs = newest and (time.time() - newest) / DAY < th["active"]
        if not fresh_code and (tool or (outputs and (p.get("code_files") or 0) >= 10)):
            return "in-use", [["used", {"src": tool or ""}]]
        return "active", [["idle", {"d": round(days)}]]

    if newer_sibling and not reg:
        why.append(["sibling", {"n": newer_sibling["name"]}])
        return "superseded", why

    comp = sig.get("complaint")
    if comp:
        fixed_later = g.get("last_commit") and g["last_commit"] > comp["ts"] + 600
        if not fixed_later:
            why.append([{"persist": "complaint", "report": "report", "wait": "wait"}.get(comp.get("kind"), "complaint"),
                        {"q": comp["text"]}])
            return "blocked", why

    if tool:
        why.append(["tool", {"src": tool}])
        return "in-use", why

    if "shipped" in p.get("chips", []) and not sig.get("continue"):
        why.append(["shipped", {"b": g.get("branch", "")}])
        return "delivered", why
    if sig.get("handoff_words"):
        why.append(["handoff", {"q": sig["handoff_words"], "f": (dl.get("recent") or [""])[0]}])
        return "delivered", why
    newest = dl.get("newest") or 0
    p_last = ai.get("p_last") or 0
    if newest and p_last and abs(newest - p_last) < 2 * DAY:
        if sig.get("satisfied_words"):
            why.append(["handoff", {"q": sig["satisfied_words"], "f": (dl.get("recent") or [""])[0]}])
            return "delivered", why
        if (ai.get("meaningful") or 0) <= 8:
            why.append(["quick-deliverable", {"n": ai.get("meaningful") or 0, "f": (dl.get("recent") or [""])[0]}])
            return "delivered", why

    n = ai.get("meaningful") or 0
    span = ((ai.get("p_last") or 0) - (ai.get("p_first") or 0)) / DAY
    if 1 <= n <= 8 and span < 2 and not reg and (not g or (g.get("commits") or 0) <= 3):
        why.append(["one-shot", {"n": n, "dl": dl.get("count", 0)}])
        return "one-shot", why

    why.append(["idle", {"d": round(days)}])
    if not ai and not g:
        why.append(["files-only-evidence", {}])
    return "paused", why


def unsaved(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    g = p.get("git")
    if g:
        u = {"dirty": g.get("dirty", 0), "unpushed": g.get("unpushed", 0),
             "no_remote": bool(g.get("commits")) and not g.get("remote")}
        return u if any(u.values()) else None
    if (p.get("code_files") or 0) > 0:
        return {"no_vcs": True}
    return None


def plan(state: str, p: Dict[str, Any], th: Dict[str, int]) -> Dict[str, bool]:
    """What is pre-selected by default. Regenerables never risk your work
    (proof + git guards); byproducts are your files, so they also need a
    finished-looking state *and* no unsaved work."""
    days = p.get("days")
    days = 9999.0 if days is None else days
    if state in ("active", "in-use"):
        return {"regen": False, "byproducts": False}
    stale = days >= th["cooling"]
    regen = state in ("delivered", "superseded", "one-shot", "closed") or stale
    gate = not (p.get("flags") or {}).get("unsaved")
    byp = state in FINISHED and gate
    return {"regen": regen, "byproducts": byp}


def apply_all(projects: List[Dict[str, Any]], th: Dict[str, int], sources: Optional[List[Tuple[str, str]]] = None,
              skills: Optional[List[str]] = None) -> None:
    """Link lineage siblings, flags, state, why, kind and plan, in place."""
    from . import identity

    sources = registry_sources() if sources is None else sources
    skills = skill_names() if skills is None else skills
    for p in projects:
        flags = p.setdefault("flags", {})
        flags["registered"] = references(p["path"], sources)
        flags["unsaved"] = unsaved(p)
        p["tool"] = is_tool(p, skills)
        p["kind"] = identity.kind(p, bool(p["tool"]))
    stems: Dict[str, List[Dict[str, Any]]] = {}
    # two folders pushing to the same repository are versions of one project
    # (korean-bill-debate-mcp-core → assembly), whatever their names say
    by_repo: Dict[str, List[Dict[str, Any]]] = {}
    for p in projects:
        repo = (p.get("links") or {}).get("repo")
        if repo and not (p.get("git") or {}).get("nested"):
            by_repo.setdefault(repo, []).append(p)
    for repo, members in by_repo.items():
        if len(members) > 1:
            for m in members:
                m["stem"] = "repo:" + repo.rsplit("/", 1)[-1]
    for p in projects:
        stem = p.get("stem") or p.get("lineage")
        p["stem"] = stem
        stems.setdefault(stem or "", []).append(p)
    for p in projects:
        p["lineage"], p["lineage_latest"], p["lineage_size"] = None, False, 0
    for stem, members in stems.items():
        if not stem or len(members) < 2 or len(stem) < 3 or stem.isdigit():
            continue
        latest = max(members, key=lambda m: m["activity"]["last"] or 0)
        for m in members:
            m["lineage"], m["lineage_latest"], m["lineage_size"] = stem.replace("repo:", ""), m is latest, len(members)
    for p in projects:
        sib = None
        if p.get("lineage") and not p.get("lineage_latest"):
            latest = max((m for m in stems[p["stem"]] if m is not p), key=lambda m: m["activity"]["last"] or 0)
            if (latest["activity"]["last"] or 0) - (p["activity"]["last"] or 0) > 3 * DAY:
                sib = latest
        state, why = classify(p, th, sib)
        p["state"], p["why"] = state, why
        p["stale"] = state not in ("active", "in-use") and (p.get("days") is None or p["days"] >= th["cooling"])
        p["plan"] = plan(state, p, th)


def evidence(files: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Deliverable summary: count, bytes, newest mtime, five newest names."""
    fs = sorted(files, key=lambda f: -f["mtime"])
    return {"count": len(fs), "bytes": sum(f["bytes"] for f in fs), "newest": fs[0]["mtime"] if fs else None,
            "recent": [f["rel"] for f in fs[:5]]}


# ── effective state: you > the triage skill > the rules ───────────────────

def effective(p: Dict[str, Any], st: Dict[str, Any]) -> str:
    o = (st.get("overrides") or {}).get(p["id"])
    o = LEGACY_STATES.get(o, o)
    if o in USER_STATES:
        return o
    lab = ((st.get("labels") or {}).get(p["id"]) or {}).get("state")
    lab = LEGACY_STATES.get(lab, lab)
    if lab in USER_STATES:
        return lab
    return p.get("state") or "paused"


def plan_for(p: Dict[str, Any], st: Dict[str, Any], th: Dict[str, int]) -> Dict[str, bool]:
    if p["id"] in (st.get("pins") or []):
        return {"regen": False, "byproducts": False}
    return plan(effective(p, st), p, th)
