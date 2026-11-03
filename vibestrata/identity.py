"""What a project *is*, for people: a readable title, one line about it, its
kind, and where its results live.

Folder names are often useless ("0614", "udo", "test"). The clues, best
first: the title of the project's own README/CLAUDE.md, the name of the
deliverable it produced ("AI_전략_워크숍_보고서.pdf" → "AI 전략 워크숍
보고서"), and the first real thing you asked for. Rules get most of them;
the triage skill can propose the rest, and you can always rename.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from .claude import clip, redact

DOC_FILES = ("CLAUDE.md", "README.md", "README", "readme.md", "AGENTS.md")
DOC_EXT = (".pdf", ".pptx", ".key", ".docx", ".hwp", ".hwpx", ".xlsx", ".epub")
VIDEO_EXT = (".mp4", ".mov", ".m4v")

PASTE_RE = re.compile(r"\[(?:Pasted text|Image|붙여넣은 텍스트)[^\]]*\]", re.I)
URL_RE = re.compile(r"https?://[^\s)\]>\"'`*]+")
# Opening moves that say nothing about the project: checking skills, logging
# in, cloning, greetings.
SETUP_RE = re.compile(
    r"스킬\s*(?:확인|점검|체크|목록|리스트)|(?:활성화된|사용\s*가능한|쓸\s*수\s*있는)\s*스킬|^claude\b|\blog\s?(?:in|out)\b"
    r"|로그(?:인|아웃)|^git\s+(?:clone|init|pull|status)\b|^(?:cd|ls|npm|pip|brew|uv)\s|^(?:install|설치)\b"
    r"|mcp\s*(?:확인|설정|연결|목록)|^(?:hi|hello|hey|안녕)\b",
    re.I,
)
GENERIC = {"test", "sample", "example", "output", "untitled", "document", "final", "slide", "slides", "report", "deck",
           "draft", "new", "copy", "file", "image", "video", "main", "index", "readme", "presentation", "result",
           "results", "export", "claude", "agents", "project", "프로젝트", "개요", "보고서", "발표자료", "install",
           "installation", "usage", "overview", "introduction", "quickstart", "gettingstarted", "features", "contents",
           "tableofcontents", "소개", "설치", "시작하기", "목차", "사용법"}
# "codex-ppt — 프로젝트 CLAUDE.md": the part after the dash is about the file, not the project
META_RE = re.compile(
    r"^(?:프로젝트\s*)?(?:CLAUDE\.md|AGENTS\.md|README|SSOT|Project Instructions|Project SSOT|프로젝트\s*SSOT|프로젝트\s*헌장"
    r"|(?:Claude Code\s*)?작업\s*지시서|instructions)\b",
    re.I,
)
PREFIX_RE = re.compile(r"^(?:하네스|harness|프로젝트|project)\s*[:：]\s*", re.I)
VER_TAIL = re.compile(
    r"(?:(?:[-_ .]|(?<=[가-힣]))(?:v\d+(?:\.\d+)*|rev\d+|final\d*|최종\d*|copy\d*|복사본|old|bak|backup|draft\d*|초안|수정본?\d*"
    r"|tagged-backup|\d{8}|\d{6}|20\d\d[-_.]\d\d[-_.]\d\d))+$|\s*\(\d{1,2}\)$",
    re.I,
)
JUNK_STEM = re.compile(r"^(?:sample|example|test|tmp|temp|untitled)\b|^(?:s|v|p|page|img|image|slide|part)\d+$", re.I)
# words that mark an input or a working file, not the thing you made
JUNK_WORD = re.compile(r"\b(?:sample|source|reference|data|input|raw|template|tmp|temp|test|backup|copy)\b", re.I)
# a paragraph about the file itself, not the project
META_PARA = re.compile(r"SSOT|단일 진실 원천|이 (?:파일|문서)|본 (?:파일|문서|디렉토리)|핸드오프|HANDOFF|라우팅|routing|this (?:file|document)"
                       r"|Claude Code가 이 저장소|bootstrapped with|읽어야 할|먼저 읽는다", re.I)
BOILERPLATE = re.compile(r"bootstrapped with \[?`?create-|^getting started$|^create react app|^welcome to\b"
                         r"|this is not the next\.js you know|nextjs-agent-rules", re.I | re.M)
DOC_HOSTS = re.compile(r"(?:^|\.)(?:vercel\.com|nextjs\.org|react\.dev|reactjs\.org|anthropic\.com|claude\.ai|openai\.com"
                       r"|google\.com|youtube\.com|npmjs\.com|pypi\.org|wikipedia\.org|python\.org|nodejs\.org|vitejs\.dev"
                       r"|tailwindcss\.com|shields\.io|img\.shields\.io)$", re.I)
DEPLOY_HOST = re.compile(r"\.(?:vercel\.app|netlify\.app|pages\.dev|github\.io|web\.app|firebaseapp\.com|fly\.dev"
                         r"|onrender\.com|railway\.app|streamlit\.app|hf\.space|surge\.sh)\b", re.I)
DEPLOY_WORD = re.compile(r"배포|deploy|production|프로덕션|live\b|demo\b|데모|사이트|site\b|homepage|서비스\s*주소|url\s*[:：]", re.I)


def nfc(s: str) -> str:
    """macOS hands out Korean file names decomposed (NFD); match and show them composed."""
    return unicodedata.normalize("NFC", s)


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-.·]+", "", s.lower())


def _hangul(s: str) -> int:
    return sum(1 for ch in s if "가" <= ch <= "힣")


# ── reading the project's own docs ────────────────────────────────────────

def read_docs(path: str, limit: int = 20000) -> List[Tuple[str, str]]:
    out = []
    for name in DOC_FILES:
        try:
            with open(os.path.join(path, name), encoding="utf-8", errors="replace") as fh:
                out.append((name, fh.read(limit)))
        except OSError:
            continue
    return out


def describe(docs: List[Tuple[str, str]]) -> Tuple[str, str]:
    """(title, first paragraph about the project) from the first doc that has
    either. Paragraphs about the file itself ("이 파일이 SSOT…") and framework
    boilerplate ("bootstrapped with create-next-app") are skipped; a "목표:"
    line is taken as the summary it usually is."""
    for _name, text in docs:
        if BOILERPLATE.search(text[:600]):
            continue
        title, paras, cur = "", [], []
        fence = False
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("```"):
                fence = not fence
                continue
            if fence or s.startswith(("<", "|", "---", "![", "[!", "@")):
                continue
            if not s or s.startswith("#"):
                if cur:
                    paras.append(" ".join(cur))
                    cur = []
                if s.startswith("#") and not title:
                    title = s.lstrip("# ").strip()
                if len(paras) >= 6:
                    break
                continue
            cur.append(re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", s.lstrip("> ").strip()))
        if cur:
            paras.append(" ".join(cur))
        para = next((x for x in paras if not META_PARA.search(x[:80]) and len(x) > 12), "")
        title, para = (re.sub(r"(\*\*|__|`|\[([^\]]*)\]\([^)]*\))", lambda m: m.group(2) or "", x) for x in (title, para))
        para = re.sub(r"^(?:목표|목적|goal|purpose)\s*[:：]\s*", "", para, flags=re.I)
        if BOILERPLATE.search(title):
            title = ""
        if title or para:
            return nfc(redact(title))[:160], nfc(redact(clip(para, 240)))
    return "", ""


def links(path: str, docs: List[Tuple[str, str]], git: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Where the project lives outside this folder: its repository and a
    deployed address, when the project says so itself."""
    out: Dict[str, str] = {}
    if git and git.get("remote_url"):
        out["repo"] = git["remote_url"]
    site = ""
    try:
        with open(os.path.join(path, "CNAME"), encoding="utf-8", errors="replace") as fh:
            dom = fh.read(200).strip().split()[0] if fh else ""
            if re.match(r"^[\w.-]+\.[a-z]{2,}$", dom, re.I):
                site = "https://" + dom
    except (OSError, IndexError):
        pass
    if not site:
        try:
            with open(os.path.join(path, "package.json"), encoding="utf-8", errors="replace") as fh:
                hp = (json.loads(fh.read(200_000)) or {}).get("homepage") or ""
            if isinstance(hp, str) and hp.startswith("http") and "github.com" not in hp:
                site = hp
        except (OSError, ValueError, AttributeError):
            pass
    if not site:
        best = None
        for _name, text in docs:
            for ln in text.splitlines():
                for u in URL_RE.findall(ln):
                    u = u.rstrip(".,;:")
                    host = re.sub(r"^https?://([^/:?#]+).*$", r"\1", u).lower()
                    if re.search(r"localhost|127\.0\.0\.1|0\.0\.0\.0|github\.com|githubusercontent", host) or DOC_HOSTS.search(host):
                        continue
                    score = 2 if DEPLOY_HOST.search(u) else 1 if DEPLOY_WORD.search(ln) else 0
                    if score and (best is None or score > best[0]):
                        best = (score, u)
                if best and best[0] == 2:
                    break
            if best and best[0] == 2:
                break
        if best:
            site = best[1]
    if site and not redact(site).startswith("[redacted]"):
        out["site"] = site
    return out


# ── naming ────────────────────────────────────────────────────────────────

def title_from_doc(doc_title: str, folder: str) -> str:
    t = PREFIX_RE.sub("", doc_title.strip())
    parts = [x.strip() for x in re.split(r"\s+[—–-]{1,2}\s+", t) if x.strip()]
    parts = [x for x in parts if not META_RE.match(x) and not re.search(r"SSOT|CLAUDE\.md|작업\s*지시서|Instructions", x)] or parts[:1]
    t = " — ".join(parts) if len(" — ".join(parts)) <= 56 else parts[0] if parts else ""
    t = clip(t, 72)
    if not t or META_RE.match(t) or _norm(t) in GENERIC or BOILERPLATE.search(t):
        return ""
    return t


def title_from_file(rel: str, folder: str) -> str:
    stem = nfc(os.path.splitext(os.path.basename(rel))[0])
    prev = None
    while prev != stem:
        prev, stem = stem, VER_TAIL.sub("", stem).strip(" _-.")
    t = re.sub(r"[_]+", " ", stem)
    t = re.sub(r"(?<=[A-Za-z0-9])-(?=[A-Za-z0-9가-힣])|(?<=[가-힣])-(?=[A-Za-z0-9가-힣])", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t or JUNK_STEM.match(t) or JUNK_WORD.search(t) or _norm(t) in GENERIC or _norm(t) == _norm(folder):
        return ""
    words = [w for w in t.split() if re.search(r"[A-Za-z가-힣]", w)]
    letters = sum(1 for ch in t if ch.isalpha())
    if _hangul(t) >= 4 or (len(words) >= 2 and letters >= 8):
        return clip(t, 72)
    return ""


def clean_ask(text: str) -> str:
    t = PASTE_RE.sub(" ", nfc(text))
    t = " ".join(t.split())
    bare = URL_RE.sub("", t).strip()
    if len(bare) < 16 or SETUP_RE.search(bare[:40]) or (len(bare) < 30 and (bare.endswith("?") or re.search(r"설치\s*해|install", bare, re.I))):
        return ""
    return t


def first_ask(asks: List[List[Any]]) -> str:
    for _ts, text in asks or []:
        t = clean_ask(text)
        if t:
            return t
    return ""


def name(p: Dict[str, Any]) -> Dict[str, str]:
    """title, summary and where each came from."""
    folder = p["name"]
    title, t_src = title_from_doc(p.get("doc_title") or "", folder), "doc"
    if not title:
        dl = (p.get("deliverables") or {}).get("recent") or []
        # finished documents first; outputs inside a harness workspace after those at the top level
        ranked = sorted(dl, key=lambda r: (not r.lower().endswith(DOC_EXT), "_workspace" in r.lower(), "backup" in r.lower()))
        for rel in ranked:
            if rel.lower().endswith(VIDEO_EXT) or re.search(r"(?:^|/)(?:build|dist|tmp|temp|test|cache|draft|drafts|scratch)/", rel, re.I):
                continue
            title = title_from_file(rel, folder)
            if title:
                t_src = "file"
                break
    if not title:
        title, t_src = folder, "folder"
    ask = first_ask(((p.get("claude") or {}).get("asks")) or [])
    para = p.get("doc_para") or ""
    if para and _norm(para) != _norm(title):
        summary, s_src = clip(para, 140), "doc"
    elif ask:
        summary, s_src = clip(ask, 140), "ask"
    else:
        summary, s_src = "", ""
    return {"title": title, "title_src": t_src, "summary": summary, "summary_src": s_src, "ask": clip(ask, 240)}


# ── kind ──────────────────────────────────────────────────────────────────

WEB_STACK = {"Next.js", "Vite", "Node", "Static", "Vercel"}


def kind(p: Dict[str, Any], tool: bool) -> str:
    """tool · app · doc · other. A guess for sorting and wording, nothing more."""
    if tool:
        return "tool"
    stack = set(p.get("stack") or [])
    code = p.get("code_files") or 0
    dl = (p.get("deliverables") or {}).get("count", 0)
    if (p.get("links") or {}).get("site") or stack & {"Next.js", "Vite", "Static"}:
        return "app"
    if dl and code < 40:  # a few build scripts next to the PDF: still a document job
        return "doc"
    if stack & {"Node", "Python", "Rust", "Go", "Vercel"} or code >= 5:
        return "app"
    if dl or stack & {"Deck", "Typst"}:
        return "doc"
    return "other"
