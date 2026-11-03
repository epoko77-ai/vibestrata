"""Thin, read-only wrappers around the ``git`` CLI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Dict, Iterable, List, Optional, Set

GIT = shutil.which("git")
_ENV = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C", GIT_TERMINAL_PROMPT="0")


class GitUnavailable(Exception):
    """git could not answer for a path that lives inside a repository.

    Callers must fail closed: treat everything as tracked.
    """


def has_git_ancestor(path: str) -> bool:
    """True if ``path`` or any physical ancestor contains ``.git``."""
    p = os.path.realpath(path)
    while True:
        if os.path.lexists(os.path.join(p, ".git")):
            return True
        parent = os.path.dirname(p)
        if parent == p:
            return False
        p = parent


def _git(cwd: str, *args: str, stdin: Optional[str] = None, timeout: float = 20) -> Optional[str]:
    if not GIT:
        return None
    try:
        r = subprocess.run(
            [GIT, "-C", cwd, *args], input=stdin, capture_output=True, text=True,
            timeout=timeout, env=_ENV, errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode in (0, 1) else None


def toplevel(path: str) -> Optional[str]:
    """Physical repo root, or None when ``path`` is not in a repository.

    Raises GitUnavailable when a ``.git`` exists above ``path`` but git cannot
    answer (not installed, dubious ownership, corrupt config, timeout).
    """
    out = _git(path, "rev-parse", "--show-toplevel")
    if out and out.strip():
        return os.path.realpath(out.strip())
    if has_git_ancestor(path):
        raise GitUnavailable(path)
    return None


def info(path: str, since_days: int = 90) -> Optional[Dict]:
    """Repo facts for a project directory, or None when not in a repo."""
    try:
        top = toplevel(path)
    except GitUnavailable:
        return None
    if not top:
        return None
    status = _git(path, "status", "--porcelain=v1", "-b", "--untracked-files=normal", "--", ".") or ""
    lines = status.splitlines()
    head = lines[0] if lines and lines[0].startswith("##") else ""
    dirty = sum(1 for ln in lines if not ln.startswith("##"))
    branch = head[3:].split("...")[0].strip() if head else ""
    if branch.startswith("No commits yet on "):
        branch = branch[len("No commits yet on "):]
    ahead = 0
    if "[ahead " in head:
        try:
            ahead = int(head.split("[ahead ")[1].split("]")[0].split(",")[0])
        except ValueError:
            ahead = 0
    remotes = (_git(path, "remote") or "").split()
    url = ""
    if remotes:
        name = "origin" if "origin" in remotes else remotes[0]
        url = (_git(path, "config", "--get", f"remote.{name}.url") or "").strip()
    log = _git(path, "log", f"--since={since_days}.days", "--format=%ct", "--", ".") or ""
    commit_days = [int(x) for x in log.split() if x.isdigit()]
    last = _git(path, "log", "-1", "--format=%ct|%s", "--", ".") or ""
    last_ts, _, subject = last.strip().partition("|")
    count = (_git(path, "rev-list", "--count", "HEAD", "--", ".") or "0").strip()
    unpushed = ahead
    if remotes and "..." not in head and branch:
        # branch with no upstream: everything is unpushed
        n = _git(path, "rev-list", "--count", "HEAD", "--not", "--remotes")
        unpushed = int(n.strip()) if n and n.strip().isdigit() else 0
    return {
        "root": top,
        "nested": os.path.realpath(path) != top,
        "branch": branch,
        "dirty": dirty,
        "remote": bool(remotes),
        "remote_url": web_url(url),
        "unpushed": unpushed,
        "commits": int(count) if count.isdigit() else 0,
        "last_commit": float(last_ts) if last_ts.strip().isdigit() else None,
        "last_subject": subject.strip()[:160],
        "recent": commit_days,
    }


def web_url(url: str) -> str:
    """https:// form of a remote (git@github.com:a/b.git → https://github.com/a/b),
    credentials stripped. Empty for anything that isn't a known web host."""
    url = url.strip()
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", url)
    if m:
        host, repo = m.group(1), m.group(2)
    else:
        m = re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", url)
        if not m:
            return ""
        host, repo = m.group(1), m.group(2)
    if not re.match(r"^[\w.-]+$", host) or not re.match(r"^[\w.\-/]+$", repo):
        return ""
    return f"https://{host}/{repo}"


def blocked_paths(top: str, candidates: Iterable[str]) -> Set[str]:
    """Candidates that contain git-tracked files (and are not ignored).

    A directory is safe when ``git check-ignore`` says it is ignored, or when
    ``git ls-files`` lists nothing under it.
    """
    # git speaks physical paths; callers may hold a path through a symlinked
    # root (~/code -> /Volumes/Ext/code). Compare physical, report original.
    rtop = os.path.realpath(top)
    back = {}
    for c in candidates:
        back[os.path.relpath(os.path.realpath(c), rtop)] = c
    cands = list(back)
    if not cands:
        return set()
    if any(c == ".." or c.startswith(".." + os.sep) for c in cands):
        raise GitUnavailable("outside-repo")
    ignored_out = _git(top, "check-ignore", "--stdin", stdin="\n".join(cands) + "\n")
    if ignored_out is None:
        raise GitUnavailable(top)
    ignored = {ln.strip().rstrip("/") for ln in ignored_out.splitlines() if ln.strip()}
    rest = [c for c in cands if c not in ignored]
    if not rest:
        return set()
    tracked = set()
    for i in range(0, len(rest), 200):
        chunk = rest[i:i + 200]
        out = _git(top, "ls-files", "-z", "--", *chunk)
        if out is None:
            raise GitUnavailable(top)
        files = [f for f in out.split("\0") if f]
        for c in chunk:
            pre = c.rstrip("/") + "/"
            if any(f == c or f.startswith(pre) for f in files):
                tracked.add(back[c])
    return tracked


def is_tracked(top: str, path: str) -> bool:
    """Whether the file at ``path`` is tracked. Raises GitUnavailable."""
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top))
    if rel == ".." or rel.startswith(".." + os.sep):
        raise GitUnavailable("outside-repo")
    out = _git(top, "ls-files", "-z", "--", rel)
    if out is None:
        raise GitUnavailable(top)
    return any(f for f in out.split("\0"))
