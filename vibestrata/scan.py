"""Discover projects and survey each one.

The scan is read-only. It never follows symlinks, never descends into
dependency folders, and sizes them with ``du -skP`` (fast, hard-link aware).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import claude as claude_mod
from . import gitinfo, identity, rules, segment
from .config import TOOLCHAIN_DIRS, expand, home, load_config, load_state, read_json, state_dir, write_json

DAY = 86400
DU = shutil.which("du")
SPRAWL_RE = re.compile(r"(?i)(?:[-_ .(](?:v\d{1,3}|final|최종|copy|복사본|old|bak|backup|draft|초안|수정)[)\d]*)|\(\d+\)")
LINEAGE_RE = re.compile(r"(?i)^(?:new[-_ ]?)?(.*?)[-_ ]?(?:v?\d{1,2}|final|copy|old|bak|new)?$")
HEAVY_EXT = {
    ".mp4": "video", ".mov": "video", ".webm": "video", ".mkv": "video", ".m4v": "video",
    ".wav": "audio", ".mp3": "audio", ".m4a": "audio", ".flac": "audio",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image", ".psd": "image", ".tif": "image", ".tiff": "image",
    ".pdf": "doc", ".pptx": "doc", ".key": "doc", ".docx": "doc", ".hwp": "doc", ".hwpx": "doc",
    ".zip": "archive", ".tar": "archive", ".gz": "archive", ".tgz": "archive", ".7z": "archive", ".dmg": "archive",
    ".csv": "data", ".json": "data", ".jsonl": "data", ".parquet": "data", ".sqlite": "data", ".db": "data",
    ".pt": "model", ".bin": "model", ".safetensors": "model", ".gguf": "model", ".onnx": "model", ".ckpt": "model",
}


def pid(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]


def du_bytes(path: str) -> int:
    """Allocated bytes under ``path`` without following symlinks."""
    if DU:
        try:
            r = subprocess.run([DU, "-skP", path], capture_output=True, text=True, timeout=600)
            first = (r.stdout or "").split("\t", 1)[0].strip()
            if first.isdigit():
                return int(first) * 1024
        except (OSError, subprocess.SubprocessError):
            pass
    total, seen = 0, set()
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for n in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, n))
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if st.st_nlink > 1:
                if key in seen:
                    continue
                seen.add(key)
            total += getattr(st, "st_blocks", 0) * 512 or st.st_size
    return total


# ── discovery ──────────────────────────────────────────────────────────────

def _children(path: str, skip: set) -> List[str]:
    try:
        entries = list(os.scandir(path))
    except OSError:
        return []
    out = []
    for e in entries:
        if e.name.startswith(".") or e.name in skip:
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                out.append(os.path.normpath(e.path))
        except OSError:
            continue
    return sorted(out)


def discover(cfg: Dict[str, Any], claude_cwds: List[str]) -> List[Tuple[str, str, Optional[str]]]:
    """Return ``(project_path, root, group)`` triples."""
    roots = []
    for r in cfg.get("roots", []):
        p = os.path.normpath(str(expand(r)))
        if os.path.isdir(p) and p not in roots:
            roots.append(p)
    skip = set(cfg.get("skip", []))
    own = os.path.normpath(str(state_dir()))
    found: Dict[str, Tuple[str, str, Optional[str]]] = {}
    cwd_set = [os.path.normpath(c) for c in claude_cwds]

    def claude_children(group: str) -> List[str]:
        hits = set()
        for c in cwd_set:
            if c.startswith(group + os.sep):
                top = group + os.sep + c[len(group) + 1:].split(os.sep)[0]
                if os.path.isdir(top):
                    hits.add(top)
        return sorted(hits)

    for root in roots:
        for child in _children(root, skip):
            if child in roots or child == own or child in found:
                continue
            if rules.has_marker(child):
                found[child] = (child, root, None)
                continue
            subs = _children(child, set())
            marked = [s for s in subs if rules.has_marker(s)]
            if len(marked) >= 2:
                for s in sorted(set(marked) | set(claude_children(child))):
                    found.setdefault(s, (s, root, os.path.basename(child)))
                continue
            # A markerless folder. Keep it if it holds anything at all —
            # vibe projects are often just a folder of files.
            try:
                with os.scandir(child) as it:
                    if any(True for _ in it):
                        found[child] = (child, root, None)
            except OSError:
                pass
    return list(found.values())


# ── per-project survey ─────────────────────────────────────────────────────

def _walk(path: str, big_bytes: int) -> Dict[str, Any]:
    seen_inodes = set()
    source_bytes = 0
    files = 0
    code_files = 0
    code_last = 0.0                       # newest code or skill definition you wrote
    last_ts = 0.0
    big: List[Dict[str, Any]] = []
    cands: List[Dict[str, Any]] = []
    chain: List[Dict[str, Any]] = []      # versionable outputs (report_v3.pdf, slide_regen2.png)
    deliv: List[Dict[str, Any]] = []      # deliverables (PDF, PPTX, DOCX, MP4…)
    workspaces: Dict[str, Dict[str, Any]] = {}
    dataless = 0
    dev = os.lstat(path).st_dev
    # (dir, enclosing _workspace or None, depth, inside an input folder like docs/ or 참고/)
    stack: List[Tuple[str, Optional[str], int, bool]] = [(path, None, 0, False)]
    git_dirs: List[str] = []
    while stack:
        d, ws, depth, inp = stack.pop()
        try:
            with os.scandir(d) as sc:
                it = list(sc)
        except OSError:
            continue
        for e in it:
            try:
                is_link = e.is_symlink()
                if e.is_dir(follow_symlinks=False):
                    name = e.name
                    if name in rules.NEVER:
                        if name == ".git":
                            git_dirs.append(e.path)
                            if ws is not None:
                                workspaces[ws]["nested"] += 1
                        continue  # never offered, never counted as yours
                    rule = rules.match(name)
                    if rule:
                        ok = rule.proof(d, e.path)
                        if ok or name in rules.OPAQUE:
                            st = e.stat(follow_symlinks=False)
                            cands.append({
                                "path": e.path, "name": name, "category": rule.category,
                                "tier": rule.tier if ok else 0,
                                "reason": "" if ok else "no-proof", "proof": rule.proof_text,
                                "mtime": st.st_mtime, "dev": st.st_dev,
                            })
                            continue
                    if e.stat(follow_symlinks=False).st_dev != dev:
                        continue  # mount point inside a project: leave it
                    sub_ws = ws
                    if ws is None and depth <= 1 and segment.WORKSPACE_RE.match(name):
                        sub_ws = e.path
                        workspaces[e.path] = {"bytes": 0, "files": 0, "mtime": 0.0, "deliv": [], "code": 0, "nested": 0}
                    stack.append((e.path, sub_ws, depth + 1, inp or bool(segment.INPUT_RE.match(name))))
                elif is_link:
                    if e.name in rules.RULES:
                        cands.append({"path": e.path, "name": e.name, "category": rules.RULES[e.name].category,
                                      "tier": 0, "reason": "symlink", "proof": "", "mtime": 0, "dev": dev})
                    continue
                elif e.is_file(follow_symlinks=False):
                    if e.name == ".git" and ws is not None:  # a worktree or submodule inside a workspace
                        workspaces[ws]["nested"] += 1
                    st = e.stat(follow_symlinks=False)
                    if getattr(st, "st_flags", 0) & rules.SF_DATALESS:
                        dataless += 1
                        continue
                    if st.st_nlink > 1:
                        key = (st.st_dev, st.st_ino)
                        if key in seen_inodes:
                            continue
                        seen_inodes.add(key)
                    alloc = getattr(st, "st_blocks", 0) * 512 or st.st_size
                    source_bytes += alloc
                    files += 1
                    if e.name != ".DS_Store" and st.st_mtime > last_ts:
                        last_ts = st.st_mtime
                    ext = os.path.splitext(e.name)[1].lower()
                    if ext in segment.CODE_EXT:
                        code_files += 1
                        if ws is None and not inp and st.st_mtime > code_last:
                            code_last = st.st_mtime
                    elif e.name == "SKILL.md" and st.st_mtime > code_last:
                        code_last = st.st_mtime
                    if ws is not None:
                        w_ = workspaces[ws]
                        w_["bytes"] += alloc
                        w_["files"] += 1
                        w_["mtime"] = max(w_["mtime"], st.st_mtime)
                        if ext in segment.CODE_EXT:
                            w_["code"] += 1
                    if ext in segment.DELIVERABLE_EXT and st.st_size >= 4096 and not e.name.startswith("~$"):
                        if ws is not None:
                            workspaces[ws]["deliv"].append(e.name)
                        if not inp:  # a PDF under docs/ or 참고/ is usually something you downloaded
                            deliv.append({"path": e.path, "rel": os.path.relpath(e.path, path), "bytes": alloc,
                                          "mtime": st.st_mtime, "ws": ws})
                    if ext in segment.CHAIN_EXT and st.st_size >= segment.CHAIN_MIN_BYTES and not inp:
                        chain.append({"path": e.path, "bytes": alloc, "mtime": st.st_mtime, "ws": ws})
                    if st.st_size >= big_bytes:
                        big.append({
                            "path": e.path, "rel": os.path.relpath(e.path, path), "bytes": alloc,
                            "mtime": st.st_mtime, "kind": HEAVY_EXT.get(ext, "other"),
                            "sprawl": bool(SPRAWL_RE.search(os.path.splitext(e.name)[0])),
                        })
            except OSError:
                continue
    big.sort(key=lambda b: -b["bytes"])
    return {
        "source_bytes": source_bytes, "files": files, "last_file": last_ts or None,
        "big": big[:12], "big_total": sum(b["bytes"] for b in big), "big_count": len(big),
        "cands": cands, "dataless": dataless, "git_dirs": git_dirs,
        "code_files": code_files, "code_last": code_last or None, "chain": chain, "deliv": deliv, "workspaces": workspaces,
    }


def lineage_stem(name: str) -> str:
    m = LINEAGE_RE.match(name.strip())
    stem = (m.group(1) if m else name) or name
    return re.sub(r"[-_ .]", "", stem.lower())


def status_for(days: Optional[float], th: Dict[str, int]) -> str:
    if days is None:
        return "fossil"
    if days < th["active"]:
        return "active"
    if days < th["cooling"]:
        return "cooling"
    if days < th["dormant"]:
        return "dormant"
    return "fossil"


def _mentions(text: str, rel: str) -> bool:
    return bool(text) and re.search(re.escape(rel) + r"(?![\w.\-가-힣])", text) is not None


def _byproducts(path: str, w: Dict[str, Any], git: Optional[Dict[str, Any]], cloud: bool) -> Dict[str, Any]:
    """Your own files that a finished project no longer needs: harness
    ``_workspace`` folders and older members of version chains. Never
    regenerable, so never pre-selected unless the project looks done.

    ``auto`` marks what may be pre-selected at all. A workspace that holds a
    deliverable found nowhere else, holds code, or is named in the project's
    own CLAUDE.md/AGENTS.md (a harness writes its outputs there) is listed but
    never pre-selected; one that holds a nested git repository is blocked.
    """
    outside = {os.path.basename(d["path"]) for d in w["deliv"] if d["ws"] is None}
    own = ""
    for name in ("CLAUDE.md", "AGENTS.md"):
        try:
            with open(os.path.join(path, name), encoding="utf-8", errors="replace") as fh:
                own += fh.read(200_000)
        except OSError:
            pass
    items: List[Dict[str, Any]] = []
    for wpath, info_ in w["workspaces"].items():
        if info_["bytes"] < 1024 * 1024:
            continue
        held = sorted({n for n in info_["deliv"] if n not in outside})
        rel = os.path.relpath(wpath, path)
        items.append({"path": wpath, "rel": rel, "bytes": info_["bytes"],
                      "mtime": info_["mtime"], "why": "workspace", "kind": "workspace", "files": info_["files"],
                      "holds": len(held), "held": held[:3], "code": info_["code"],
                      "referenced": _mentions(own, rel), "blocked": "nested-repo" if info_["nested"] else ""})
    items += segment.version_chains([c for c in w["chain"] if c["ws"] is None], path)
    if items and (git or gitinfo.has_git_ancestor(path)):
        try:
            if not git:
                raise gitinfo.GitUnavailable(path)
            tracked = gitinfo.blocked_paths(git["root"], [b["path"] for b in items])
            for b in items:
                if b["path"] in tracked:
                    b["blocked"] = "git-tracked"
        except gitinfo.GitUnavailable:
            for b in items:
                b["blocked"] = "git-unavailable"
    for b in items:
        if cloud:
            b["blocked"] = "cloud-synced"
        b.setdefault("blocked", "")
        for k, v in (("holds", 0), ("code", 0), ("referenced", False)):
            b.setdefault(k, v)
        b["auto"] = not (b["blocked"] or b["holds"] or b["code"] or b["referenced"])
    items.sort(key=lambda b: -b["bytes"])
    free = [b for b in items if not b["blocked"]]
    return {"bytes": sum(b["bytes"] for b in free), "count": len(free),
            "safe_bytes": sum(b["bytes"] for b in free if b["auto"]), "items": items[:40]}


def survey(path: str, root: str, group: Optional[str], cfg: Dict[str, Any],
           claude_act: Optional[claude_mod.ClaudeActivity], pool: ThreadPoolExecutor) -> Dict[str, Any]:
    t0 = time.time()
    big_bytes = int(cfg.get("big_file_mb", 20)) * 1024 * 1024
    # sessions opened elsewhere that worked here, or opened here that worked elsewhere
    ai_links = claude_act.links() if claude_act else {}
    if claude_act is not None and claude_act.empty():
        claude_act = None
    w = _walk(path, big_bytes)
    cloud = rules.is_cloud_path(path) or w["dataless"] > 0

    # size dependency/build folders and .git in parallel
    futs = {c["path"]: pool.submit(du_bytes, c["path"]) for c in w["cands"] if c["reason"] != "symlink"}
    git_futs = [pool.submit(du_bytes, g) for g in w["git_dirs"]]

    git = gitinfo.info(path)
    live = [c for c in w["cands"] if c["tier"]]
    if live:
        # Fail closed: if git can't answer for a folder inside a repo, block.
        unavailable = False
        tracked: set = set()
        if git:
            try:
                tracked = gitinfo.blocked_paths(git["root"], [c["path"] for c in live])
            except gitinfo.GitUnavailable:
                unavailable = True
        elif gitinfo.has_git_ancestor(path):
            unavailable = True
        for c in live:
            if unavailable:
                c["tier"], c["reason"] = 0, "git-unavailable"
            elif c["path"] in tracked:
                c["tier"], c["reason"] = 0, "git-tracked"

    by_cat: Dict[str, int] = {}
    reclaim = {1: 0, 2: 0}
    cands_out = []
    for c in w["cands"]:
        b = futs[c["path"]].result() if c["path"] in futs else 0
        if cloud and c["tier"]:
            c["tier"], c["reason"] = 0, "cloud-synced"
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + b
        if c["tier"] in reclaim:
            reclaim[c["tier"]] += b
        cands_out.append({
            "path": c["path"], "rel": os.path.relpath(c["path"], path), "name": c["name"],
            "category": c["category"], "tier": c["tier"], "reason": c["reason"],
            "proof": c["proof"], "bytes": b, "mtime": c["mtime"],
        })
    cands_out.sort(key=lambda c: (-c["bytes"]))
    git_bytes = sum(f.result() for f in git_futs)
    byproducts = _byproducts(path, w, git, cloud)
    junk_bytes = sum(by_cat.values())
    total = w["source_bytes"] + junk_bytes + git_bytes

    # activity triad
    now = time.time()
    sources = {
        "commit": git["last_commit"] if git else None,
        "claude": claude_act.last_ts if claude_act and claude_act.last_ts else None,
        "files": w["last_file"],
    }
    present = {k: v for k, v in sources.items() if v}
    if present:
        winner = max(present, key=lambda k: present[k])
        last = present[winner]
    else:
        winner, last = None, None
    days = (now - last) / DAY if last else None
    th = cfg["thresholds"]
    status = status_for(days, th)

    today = int(now // DAY)
    pulse = set()
    if git:
        pulse |= {today - int(ts // DAY) for ts in git["recent"]}
    if claude_act:
        pulse |= {today - d for d in claude_act.days if today - d < 90}
    pulse = sorted(x for x in pulse if 0 <= x < 90)

    chips = []
    if git:
        stale = status != "active"
        if stale and (git["dirty"] or git["unpushed"] or not git["remote"] or not git["commits"]):
            chips.append("half-finished")
        if git["remote"] and not git["dirty"] and not git["unpushed"] and git["commits"]:
            chips.append("shipped")
    else:
        chips.append("no-git")
    if cloud:
        chips.append("cloud")
    if winner == "files" and (sources["commit"] or sources["claude"]):
        other = max(v for k, v in present.items() if k != "files")
        if last - other > 30 * DAY:
            chips.append("files-only")

    if git:
        git = {k: v for k, v in git.items() if k != "recent"}
    with_prompts = cfg.get("claude_prompts", True)
    signals = segment.prompt_signals(claude_act.recent()) if claude_act else {}
    if not with_prompts:
        for k in ("complaint",):
            if k in signals:
                signals[k] = {**signals[k], "text": ""}
        for k in ("handoff_words", "satisfied_words"):
            if k in signals:
                signals[k] = "…"
    docs = identity.read_docs(path)
    doc_title, doc_para = identity.describe(docs)
    out = {
        "id": pid(path), "name": os.path.basename(path), "path": path, "root": root,
        "group": group, "stack": rules.detect_stack(path),
        "desc": (f"{doc_title} — {doc_para}" if doc_title and doc_para else doc_title or doc_para)[:240],
        "doc_title": doc_title, "doc_para": doc_para, "links": identity.links(path, docs, git),
        "stem": lineage_stem(os.path.basename(path)), "lineage": None,
        "bytes": total, "source_bytes": w["source_bytes"], "git_bytes": git_bytes,
        "junk_bytes": junk_bytes, "by_category": by_cat,
        "reclaim": reclaim[1], "reclaim_optional": reclaim[2],
        "candidates": cands_out, "big": w["big"], "big_total": w["big_total"], "big_count": w["big_count"],
        "files": w["files"], "git": git, "code_files": w["code_files"], "code_last": w["code_last"],
        "deliverables": segment.evidence(w["deliv"]), "byproducts": byproducts,
        "resume": segment.resume_note(path), "signals": signals,
        "state": None, "why": [], "plan": {"regen": False, "byproducts": False}, "flags": {},
        "claude": claude_act.to_json(with_prompts) if claude_act else None,
        "ai_links": ai_links,
        "activity": {"last": last, "source": winner, **sources},
        "days": days, "status": status, "chips": chips, "pulse": pulse, "cloud": cloud,
        "scan_ms": int((time.time() - t0) * 1000),
    }
    out.update(identity.name(out))
    return out


# ── whole-machine scan with progress ───────────────────────────────────────

def _claude_for(path: str, index: Dict[str, claude_mod.ClaudeActivity], projects: List[str]) -> Optional[claude_mod.ClaudeActivity]:
    """Merge Claude activity whose cwd is this project or a subfolder that
    does not belong to a more specific project."""
    acc: Optional[claude_mod.ClaudeActivity] = None
    pre = path + os.sep
    for cwd, act in index.items():
        if cwd == path or cwd.startswith(pre):
            owner = max((p for p in projects if cwd == p or cwd.startswith(p + os.sep)), key=len, default=None)
            if owner != path:
                continue
            if acc is None:
                acc = claude_mod.ClaudeActivity()
            acc.merge(act)
    return acc


class Scanner:
    def __init__(self, load: bool = True) -> None:
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.progress: Dict[str, Any] = {"running": False, "done": 0, "total": 0, "current": [], "started": None}
        self.result: Dict[str, Any] = (read_json(state_dir() / "scan.json", {}) or {}) if load else {}

    def start(self) -> bool:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            self.progress = {"running": True, "done": 0, "total": 0, "current": [], "started": time.time()}
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return True

    def _run(self) -> None:
        try:
            self.result = full_scan(self.progress)
            write_json(state_dir() / "scan.json", self.result)
        except Exception as exc:  # surface, don't crash the server
            self.progress["error"] = repr(exc)
        finally:
            self.progress["running"] = False

    def drop(self, paths: Iterable[str]) -> None:
        """Forget projects that went to the Trash."""
        gone = set(paths)
        with self.lock:
            self.result["projects"] = [p for p in self.result.get("projects", []) if p["path"] not in gone]
            self.result["totals"] = totals(self.result["projects"], self.result.get("toolchain", {}))
            write_json(state_dir() / "scan.json", self.result)

    def readd(self, path: str) -> Optional[Dict[str, Any]]:
        """Survey a project that came back from the Trash."""
        if not os.path.isdir(path):
            return None
        cfg = load_config()
        projects = [p for p in self.result.get("projects", []) if p["path"] != path]
        index = claude_mod.relocate(claude_mod.read_all(), [p["path"] for p in projects] + [path])
        with ThreadPoolExecutor(6) as pool:
            fresh = survey(path, os.path.dirname(path), None, cfg,
                           _claude_for(path, index, [p["path"] for p in projects] + [path]), pool)
        with self.lock:
            self.result["projects"] = projects + [fresh]
            segment.apply_all(self.result["projects"], cfg["thresholds"])
            self.result["totals"] = totals(self.result["projects"], self.result.get("toolchain", {}))
            write_json(state_dir() / "scan.json", self.result)
        return fresh

    def rescan_one(self, path: str) -> Optional[Dict[str, Any]]:
        projects = self.result.get("projects", [])
        hit = next((p for p in projects if p["path"] == path), None)
        if not hit:
            return None
        cfg = load_config()
        all_paths = [p["path"] for p in projects]
        index = claude_mod.relocate(claude_mod.read_all(), all_paths)
        with ThreadPoolExecutor(6) as pool:
            fresh = survey(path, hit["root"], hit.get("group"), cfg, _claude_for(path, index, all_paths), pool)
        with self.lock:
            self.result["projects"] = [fresh if p["path"] == path else p for p in projects]
            segment.apply_all(self.result["projects"], cfg["thresholds"])
            self.result["totals"] = totals(self.result["projects"], self.result.get("toolchain", {}))
            write_json(state_dir() / "scan.json", self.result)
        return fresh


def toolchain_sizes(pool: ThreadPoolExecutor) -> Dict[str, int]:
    h = home()
    paths = [(d, str(h / d)) for d in TOOLCHAIN_DIRS if os.path.isdir(h / d) and not os.path.islink(h / d)]
    futs = [(d, pool.submit(du_bytes, p)) for d, p in paths]
    return {d: f.result() for d, f in futs}


def totals(projects: List[Dict[str, Any]], toolchain: Dict[str, int]) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    by_state: Dict[str, int] = {}
    for p in projects:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
        by_state[p.get("state") or "paused"] = by_state.get(p.get("state") or "paused", 0) + 1
    return {
        "by_state": by_state,
        "byproducts": sum((p.get("byproducts") or {}).get("bytes", 0) for p in projects),
        "projects": len(projects),
        "bytes": sum(p["bytes"] for p in projects),
        "reclaim": sum(p["reclaim"] for p in projects),
        "reclaim_optional": sum(p["reclaim_optional"] for p in projects),
        "reclaim_stale": sum(p["reclaim"] for p in projects if p["status"] in ("dormant", "fossil")),
        "big_total": sum(p["big_total"] for p in projects),
        "by_status": by_status,
        "toolchain": sum(toolchain.values()),
    }


SURVEY_VERSION = 3  # older surveys lack titles and states: resurvey


def full_scan(progress: Optional[Dict[str, Any]] = None, workers: int = 6) -> Dict[str, Any]:
    progress = progress if progress is not None else {}
    t0 = time.time()
    cfg = load_config()
    index = claude_mod.read_all()
    found = discover(cfg, index.hints())
    all_paths = [f[0] for f in found]
    index = claude_mod.relocate(index, all_paths)
    progress["total"] = len(found)
    progress["done"] = 0
    projects: List[Dict[str, Any]] = []
    current: List[str] = progress.setdefault("current", [])
    plock = threading.Lock()

    with ThreadPoolExecutor(workers) as du_pool, ThreadPoolExecutor(workers) as proj_pool:
        tool_fut = proj_pool.submit(toolchain_sizes, du_pool)

        def job(t: Tuple[str, str, Optional[str]]) -> Optional[Dict[str, Any]]:
            path, root, group = t
            name = os.path.basename(path)
            with plock:
                current.append(name)
            try:
                return survey(path, root, group, cfg, _claude_for(path, index, all_paths), du_pool)
            except OSError:
                return None
            finally:
                with plock:
                    if name in current:
                        current.remove(name)
                    progress["done"] = progress.get("done", 0) + 1

        for res in proj_pool.map(job, found):
            if res:
                projects.append(res)
        toolchain = tool_fut.result()

    # lineage siblings, flags, state, plan
    segment.apply_all(projects, cfg["thresholds"])

    projects.sort(key=lambda p: -(p["bytes"]))
    return {
        "version": SURVEY_VERSION, "scanned_at": time.time(), "scan_seconds": round(time.time() - t0, 1),
        "home": str(home()), "thresholds": cfg["thresholds"],
        "projects": projects, "toolchain": toolchain, "totals": totals(projects, toolchain),
    }
