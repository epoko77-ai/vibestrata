"""Plain-language views of the survey, for the terminal and for Claude Code.

* :func:`overview` — every project at a glance: what's in progress, what's
  stuck and on what words, what you forgot. Printed by ``npx vibestrata``
  before the window opens.
* :func:`situation` — one project, in a few sentences: when it started,
  where it stopped, what's at stake. What the Claude Code SessionStart hook
  shows when you come back to a folder you've been away from.

The dashboard builds the same sentences in JS (static/app.js, ``brief()``).
"""

from __future__ import annotations

import os
import time
import unicodedata
from typing import Any, Dict, List, Optional

from . import segment
from .claude import clip

DAY = 86400

T = {
    "en": {
        "state": {"active": "Building", "in-use": "In use", "delivered": "Done", "superseded": "Moved on", "blocked": "Stuck",
                  "paused": "On hold", "one-shot": "Tried once", "closed": "Dropped"},
        "group": {"now": "In progress", "done": "Done", "unfinished": "Unfinished"},
        "head": "{p} projects made with AI over {m} months",
        "head_nospan": "{p} projects",
        "counts": "{now} in progress · {done} done · {un} unfinished",
        "stuck": "Stuck on a problem",
        "forgot": "Forgotten (45+ days, most recent first)",
        "building": "In progress",
        "tools": "Tools in use",
        "disk": "{t} on disk · {r} rebuildable · {b} leftovers in finished projects",
        "now": "now: ",
        "ago": "{d}d ago",
        "today": "today",
        "open": "Dashboard: {url}",
        "more": "… and {n} more",
        # situation
        "ai": "Started {start}; {n} prompts to your AI agent through {end} ({s} sessions).",
        "files": "Files last changed {end}; no AI conversations recorded here.",
        "away": "You've been away {d} days.",
        "s_active": "You're building this.",
        "s_tool": "{src} uses this folder: a finished tool you still use.",
        "s_blocked": "It stopped on your words {q}; nothing was fixed after that.",
        "s_handoff": "You wrapped it up with {q}",
        "s_sibling": "A newer folder, {n}, took over.",
        "s_note": "Hand-off note ({f}): {text}",
        "s_last": "Last thing you asked: {q}",
        "s_memo": "Your note: {text}",
        "s_outputs": "{n} deliverables, newest {f}.",
        "s_no_vcs": "No git: this folder is the only copy.",
        "s_unsaved": "Not backed up: {what}.",
        "state_line": "State: {s}",
        "first": "Mapping your projects for the first time, in the background (10–20 s). Next time you open one you've "
                 "been away from, you'll see where you left off. The whole map: npx vibestrata",
    },
    "ko": {
        "state": {"active": "만드는 중", "in-use": "쓰는 중", "delivered": "완료", "superseded": "새 버전으로 옮김", "blocked": "막혀서 멈춤",
                  "paused": "하다 멈춤", "one-shot": "잠깐 해 본 것", "closed": "접음"},
        "group": {"now": "지금 하는 것", "done": "다 된 것", "unfinished": "하다 만 것"},
        "head": "{m}개월 동안 AI와 만든 프로젝트 {p}개",
        "head_nospan": "프로젝트 {p}개",
        "counts": "만드는 중 {now} · 다 된 것 {done} · 하다 만 것 {un}",
        "stuck": "막혀서 멈춘 것",
        "forgot": "잊고 있던 것 (45일 넘음, 최근 순)",
        "building": "만드는 중",
        "tools": "쓰고 있는 도구",
        "disk": "디스크 {t} · 다시 설치되는 것 {r} · 끝난 프로젝트의 작업 찌꺼기 {b}",
        "now": "지금: ",
        "ago": "{d}일 전",
        "today": "오늘",
        "open": "대시보드: {url}",
        "more": "… 외 {n}개",
        "ai": "{start}에 시작해 {end}까지 AI와 {n}번 대화했어요 (세션 {s}개).",
        "files": "{end}에 마지막으로 파일이 바뀌었어요. AI 대화 기록은 없어요.",
        "away": "{d}일 만에 돌아왔어요.",
        "s_active": "지금 만들고 있는 프로젝트예요.",
        "s_tool": "{src}가 이 폴더를 써요. 다 만들어서 쓰고 있는 도구예요.",
        "s_blocked": "마지막에 한 말 {q}에서 멈췄고, 그 뒤로 고친 흔적이 없어요.",
        "s_handoff": "{q}라고 하며 마무리했어요.",
        "s_sibling": "더 새 폴더 {n}가 이어받았어요.",
        "s_note": "인계 메모({f}): {text}",
        "s_last": "마지막으로 한 말: {q}",
        "s_memo": "남겨 둔 메모: {text}",
        "s_outputs": "결과물 {n}개, 최신은 {f}.",
        "s_no_vcs": "git이 없어서 이 폴더가 유일한 사본이에요.",
        "s_unsaved": "백업이 안 된 작업이 있어요: {what}.",
        "state_line": "상태: {s}",
        "first": "프로젝트 지도를 처음 그리고 있어요(뒤에서 10~20초). 다음부터 오래 쉰 프로젝트를 열면 어디서 멈췄는지 "
                 "알려 드릴게요. 전체 지도: npx vibestrata",
    },
}
REG = {"en": {"skill": "the skill “{n}”", "agent": "the agent “{n}”", "command": "/{n}", "launch agent": "the launch agent {n}",
              "mcp": "the MCP server “{n}”"},
       "ko": {"skill": "스킬 “{n}”", "agent": "에이전트 “{n}”", "command": "명령어 /{n}", "launch agent": "자동 실행 {n}",
              "mcp": "MCP 서버 “{n}”"}}
UNSAVED = {"en": {"dirty": "{n} uncommitted", "unpushed": "{n} unpushed", "no_remote": "no remote"},
           "ko": {"dirty": "커밋 안 한 변경 {n}", "unpushed": "푸시 안 한 커밋 {n}", "no_remote": "원격 없음"}}


def _t(lang: str, key: str, **kw: Any) -> str:
    s = T.get(lang, T["en"]).get(key) or T["en"][key]
    for k, v in kw.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def guess_lang(projects: List[Dict[str, Any]]) -> str:
    """Speak the language you speak to your agents."""
    env = os.environ.get("VIBESTRATA_LANG", "")
    if env in T:
        return env
    texts = [(p.get("claude") or {}).get("last_prompt") or "" for p in projects]
    ko = sum(1 for x in texts if any("가" <= ch <= "힣" for ch in x))
    return "ko" if texts and ko >= max(1, len([x for x in texts if x]) // 3) else "en"


def history_lang() -> str:
    """Before the first survey: the language of your latest prompts."""
    import json

    from .config import claude_dir

    env = os.environ.get("VIBESTRATA_LANG", "")
    if env in T:
        return env
    texts = []
    try:
        with open(claude_dir() / "history.jsonl", "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 65536))
            for line in fh.read().splitlines()[1:]:
                try:
                    texts.append(str(json.loads(line).get("display") or ""))
                except (ValueError, AttributeError):
                    continue
    except OSError:
        pass
    texts = [x for x in texts if x]
    ko = sum(1 for x in texts if any("가" <= ch <= "힣" for ch in x))
    return "ko" if texts and ko >= max(1, len(texts) // 3) else "en"


def first_run(lang: str) -> Dict[str, Any]:
    """What the hook says while the very first survey runs in the background."""
    return {"systemMessage": "vibestrata · " + _t(lang, "first")}


def title(p: Dict[str, Any], st: Dict[str, Any]) -> str:
    return ((st.get("names") or {}).get(p["id"]) or {}).get("title") \
        or ((st.get("labels") or {}).get(p["id"]) or {}).get("title") or p.get("title") or p["name"]


def summary(p: Dict[str, Any], st: Dict[str, Any]) -> str:
    return ((st.get("names") or {}).get(p["id"]) or {}).get("summary") \
        or ((st.get("labels") or {}).get(p["id"]) or {}).get("summary") or p.get("summary") or ""


def _date(ts: Optional[float], lang: str) -> str:
    if not ts:
        return "—"
    t = time.localtime(ts)
    same = t.tm_year == time.localtime().tm_year
    if lang == "ko":
        return (f"{t.tm_year}년 " if not same else "") + f"{t.tm_mon}월 {t.tm_mday}일"
    return time.strftime("%b %-d" + ("" if same else ", %Y"), t)


def reg_label(label: str, lang: str) -> str:
    kind, _, name = label.partition(": ")
    tpl = REG.get(lang, REG["en"]).get(kind)
    return tpl.replace("{n}", name) if tpl and name else label


def _unsaved(u: Dict[str, Any], lang: str) -> str:
    tpl = UNSAVED.get(lang, UNSAVED["en"])
    return " · ".join(tpl[k].replace("{n}", str(u[k])) for k in ("dirty", "unpushed", "no_remote") if u.get(k))


def finished_leftovers(projects: List[Dict[str, Any]], st: Dict[str, Any]) -> int:
    """Leftovers (``_workspace``, older versions) in projects whose state lets
    ``sweep --byproducts`` consider them: the figure the summary lists."""
    return sum((p.get("byproducts") or {}).get("bytes", 0) for p in projects
               if segment.effective(p, st) in segment.FINISHED)


def situation(p: Dict[str, Any], st: Dict[str, Any], lang: str = "en") -> List[str]:
    """A few sentences about one project, in the order you'd want them."""
    c = p.get("claude") or {}
    s = segment.effective(p, st)
    mine = (st.get("overrides") or {}).get(p["id"]) or ((st.get("labels") or {}).get(p["id"]) or {}).get("state")
    why = {k: (v or {}) for k, v in (p.get("why") or [])}
    out: List[str] = []
    q = lambda x: "“" + clip(x, 160) + "”"  # noqa: E731
    if c.get("meaningful"):
        out.append(_t(lang, "ai", start=_date(c.get("p_first") or c.get("first"), lang), end=_date(c.get("p_last") or c.get("last"), lang),
                      n=c["meaningful"], s=c.get("sessions", 0)))
    else:
        out.append(_t(lang, "files", end=_date((p.get("activity") or {}).get("last"), lang)))
    out.append(_t(lang, "state_line", s=_t(lang, "state")[s]))
    if s == "active":
        out.append(_t(lang, "s_active"))
    elif s == "in-use" and (why.get("tool") or why.get("used") or {}).get("src"):
        out.append(_t(lang, "s_tool", src=reg_label((why.get("tool") or why.get("used"))["src"], lang)))
    elif s == "blocked" and not mine and (why.get("complaint") or why.get("report") or why.get("wait")):
        out.append(_t(lang, "s_blocked", q=q((why.get("complaint") or why.get("report") or why.get("wait"))["q"])))
    elif s == "delivered" and not mine and why.get("handoff"):
        out.append(_t(lang, "s_handoff", q=q(why["handoff"]["q"])))
    elif s == "superseded" and why.get("sibling"):
        out.append(_t(lang, "s_sibling", n=why["sibling"]["n"]))
    memo = ((st.get("notes") or {}).get(p["id"]) or {}).get("text")
    if memo:
        out.append(_t(lang, "s_memo", text=clip(memo, 300)))
    if p.get("resume"):
        out.append(_t(lang, "s_note", f=p["resume"]["file"], text=p["resume"]["text"]))
    elif c.get("last_prompt") and not (s == "blocked" and not mine):
        out.append(_t(lang, "s_last", q=q(c["last_prompt"])))
    dl = p.get("deliverables") or {}
    if dl.get("count"):
        out.append(_t(lang, "s_outputs", n=dl["count"], f=(dl.get("recent") or [""])[0].split("/")[-1]))
    u = (p.get("flags") or {}).get("unsaved")
    if u:
        out.append(_t(lang, "s_no_vcs") if u.get("no_vcs") else _t(lang, "s_unsaved", what=_unsaved(u, lang)))
    return out


# ── terminal ──────────────────────────────────────────────────────────────

def _w(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _fit(s: str, width: int) -> str:
    """Cut to ``width`` display columns (Korean counts double), then pad."""
    out, w = "", 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width - (1 if _w(s) > width else 0):
            out += "…"
            w += 1
            break
        out += ch
        w += cw
    return out + " " * max(0, width - w)


def _cut(s: str, width: int) -> str:
    """Cut to ``width`` display columns, no padding."""
    if _w(s) <= width:
        return s
    out, w = "", 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width - 1:
            break
        out += ch
        w += cw
    return out + "…"


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def overview(result: Dict[str, Any], st: Dict[str, Any], lang: Optional[str] = None, width: int = 100,
             color: bool = False, url: str = "") -> str:
    ps = result.get("projects") or []
    lang = lang or guess_lang(ps)
    now = time.time()
    B = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
    D = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
    eff = {p["id"]: segment.effective(p, st) for p in ps}
    grp = {s: g for g, ss in segment.GROUPS for s in ss}
    cnt = {"now": 0, "done": 0, "unfinished": 0}
    for p in ps:
        cnt[grp.get(eff[p["id"]], "unfinished")] += 1
    firsts = [(p.get("claude") or {}).get("first") for p in ps if (p.get("claude") or {}).get("first")]
    months = max(1, round((now - min(firsts)) / DAY / 30.4)) if firsts else 0
    lines = ["", "  " + B(_t(lang, "head", p=len(ps), m=months) if months else _t(lang, "head_nospan", p=len(ps))),
             "  " + _t(lang, "counts", now=cnt["now"], done=cnt["done"], un=cnt["unfinished"])]
    name_w = min(34, max(18, width // 3))
    tail_w = max(20, width - name_w - 16)

    def ago(p: Dict[str, Any]) -> str:
        d = (now - ((p.get("activity") or {}).get("last") or now)) / DAY
        return _t(lang, "today") if d < 1 else _t(lang, "ago", d=round(d))

    def row(p: Dict[str, Any], tail: str) -> str:
        return "    " + _fit(title(p, st), name_w) + "  " + D(_fit(ago(p), 8)) + "  " + _cut(" ".join(tail.split()), tail_w) if tail else \
            "    " + _fit(title(p, st), name_w) + "  " + D(ago(p))

    def section(mark: str, head: str, rows: List[Dict[str, Any]], tail, limit: int = 6) -> None:
        if not rows:
            return
        lines.append("")
        lines.append(f"  {mark} {B(head)} {D(str(len(rows)))}")
        for p in rows[:limit]:
            lines.append(row(p, tail(p)))
        if len(rows) > limit:
            lines.append("    " + D(_t(lang, "more", n=len(rows) - limit)))

    last = lambda p: (p.get("activity") or {}).get("last") or 0  # noqa: E731
    building = sorted((p for p in ps if eff[p["id"]] == "active"), key=lambda p: -last(p))
    section("●", _t(lang, "building"), building,
            lambda p: (_t(lang, "now") + "“" + (p.get("claude") or {}).get("last_prompt", "") + "”") if (p.get("claude") or {}).get("last_prompt") else summary(p, st))
    stuck = sorted((p for p in ps if eff[p["id"]] == "blocked"), key=lambda p: -last(p))

    def stuck_tail(p: Dict[str, Any]) -> str:
        for k, v in p.get("why") or []:
            if (v or {}).get("q"):
                return "✕ “" + v["q"] + "”"
        return summary(p, st)
    section("✕", _t(lang, "stuck"), stuck, stuck_tail)
    forgot = sorted((p for p in ps if eff[p["id"]] == "paused" and p.get("stale")), key=lambda p: -last(p))

    def forgot_tail(p: Dict[str, Any]) -> str:
        memo = ((st.get("notes") or {}).get(p["id"]) or {}).get("text")
        if memo:
            return "✎ " + memo
        if p.get("resume"):
            return "↳ " + p["resume"]["text"]
        return summary(p, st) or (p.get("claude") or {}).get("last_prompt", "")
    section("○", _t(lang, "forgot"), forgot, forgot_tail, limit=5)
    tools = sorted((p for p in ps if eff[p["id"]] == "in-use"), key=lambda p: -last(p))
    if tools:
        lines.append("")
        names = ", ".join(title(p, st) for p in tools[:8]) + (f" +{len(tools) - 8}" if len(tools) > 8 else "")
        lines.append(f"  ◆ {B(_t(lang, 'tools'))} {D(str(len(tools)))}  {_cut(names, width - 24)}")
    t = result.get("totals") or {}
    lines += ["", "  " + D(_t(lang, "disk", t=_human(t.get("bytes", 0)), r=_human(t.get("reclaim", 0)), b=_human(finished_leftovers(ps, st))))]
    if url:
        lines.append("  " + _t(lang, "open", url=url))
    lines.append("")
    return "\n".join(lines)


# ── Claude Code: "you're back" ────────────────────────────────────────────

def owner(projects: List[Dict[str, Any]], cwd: str) -> Optional[Dict[str, Any]]:
    """The project this folder belongs to (the deepest one that contains it)."""
    cwd = os.path.realpath(cwd)
    best = None
    for p in projects:
        root = os.path.realpath(p["path"])
        if cwd == root or cwd.startswith(root + os.sep):
            if best is None or len(root) > len(os.path.realpath(best["path"])):
                best = p
    return best


def last_prompt_ts(path: str) -> float:
    """Newest prompt you typed in this project, read live (the survey may be days old)."""
    from .config import claude_dir

    newest = 0.0
    needle = path.encode()
    try:
        with open(claude_dir() / "history.jsonl", "rb") as fh:
            for line in fh:
                if needle not in line:
                    continue
                try:
                    import json as _json
                    row = _json.loads(line)
                except ValueError:
                    continue
                proj = os.path.normpath(str(row.get("project") or ""))
                if proj == path or proj.startswith(path + os.sep):
                    ts = row.get("timestamp") or 0
                    newest = max(newest, ts / 1000.0 if ts > 1e11 else float(ts))
    except OSError:
        pass
    return newest


def hook_payload(result: Dict[str, Any], st: Dict[str, Any], cwd: str, min_days: float = 3) -> Optional[Dict[str, Any]]:
    """What the SessionStart hook says when you open Claude Code in a project
    you've been away from: a line for you, a short note for Claude. Nothing
    when you were here recently or the folder isn't a surveyed project."""
    ps = result.get("projects") or []
    p = owner(ps, cwd)
    if not p:
        return None
    last = max(last_prompt_ts(p["path"]), (p.get("activity") or {}).get("last") or 0)
    away = (time.time() - last) / DAY if last else None
    if away is not None and away < min_days:
        return None
    lang = guess_lang(ps)
    s = segment.effective(p, st)
    lines = situation(p, st, lang)
    head = f"{title(p, st)} · {_t(lang, 'state')[s]}"
    if away is not None:
        head += " · " + _t(lang, "away", d=round(away))
    stop = next((x for x in lines if x.startswith(("마지막에 한 말", "It stopped", "남겨 둔", "Your note", "인계 메모", "Hand-off",
                                                   "마지막으로 한 말", "Last thing you asked"))), "")
    context = (
        "vibestrata (a local project tracker the user installed) noticed the user is returning to this project. "
        "Where they left off, from their own files and earlier prompts:\n- " + "\n- ".join(lines)
        + "\nIf it helps, open with one short line on where things stood. Don't act on it before the user asks."
    )
    return {"systemMessage": "vibestrata · " + head + (f"\n{stop}" if stop else ""),
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
