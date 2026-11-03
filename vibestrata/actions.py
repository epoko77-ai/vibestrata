"""The only code that changes your disk.

Rules, enforced here and nowhere else:

* No delete. Things are *moved* into the system Trash on the same volume (an
  instant rename), with a journal written before the first move. Emptying the
  Trash is yours to do; that is when the space comes back. Where no Trash is
  on the same volume, a quarantine folder on that volume is used instead.
* Every move can be undone from the journal. If the original path has been
  re-created meanwhile, the restored copy is named ``<name>.restored-<ts>``.
* Guards are re-checked at action time, not trusted from the scan.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import gitinfo, rules
from .config import home, locked, read_json, state_dir, write_json
from .segment import WORKSPACE_RE


class GuardError(Exception):
    pass


# ── locations ──────────────────────────────────────────────────────────────

def journal_dir() -> Path:
    d = state_dir() / "journal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mount_of(path: str) -> str:
    """Mount point of the *physical* path (symlinked roots resolved)."""
    p = os.path.realpath(path)
    dev = os.lstat(p).st_dev
    while True:
        parent = os.path.dirname(p)
        if parent == p:
            return p
        try:
            if os.lstat(parent).st_dev != dev:
                return p
        except OSError:
            return p
        p = parent


def trash_dir(path: str) -> Optional[Path]:
    """The system Trash on ``path``'s volume, or None (then: quarantine).

    macOS: ``~/.Trash`` for anything on your home volume, ``<volume>/.Trashes/
    <uid>`` on other disks. Linux: the freedesktop Trash in $XDG_DATA_HOME, or
    ``<mount>/.Trash-<uid>``. Never created on the boot volume's root."""
    real = os.path.realpath(path)
    dev = os.lstat(real).st_dev
    h = home()
    same_home = os.path.isdir(h) and os.lstat(str(h)).st_dev == dev
    mount = _mount_of(real)
    if sys.platform == "darwin":
        cand = h / ".Trash" if same_home else (None if mount in ("/", "/System/Volumes/Data")
                                               else Path(mount) / ".Trashes" / str(os.getuid()))
        if cand is not None and os.path.isdir(cand) and os.lstat(str(cand)).st_dev == dev:
            return cand
        return None
    if same_home:
        cand = Path(os.environ.get("XDG_DATA_HOME") or str(h / ".local" / "share")) / "Trash"
    elif mount != "/":
        cand = Path(mount) / f".Trash-{os.getuid()}"
    else:
        return None
    try:
        (cand / "files").mkdir(parents=True, exist_ok=True)
        (cand / "info").mkdir(parents=True, exist_ok=True)
        if os.lstat(str(cand)).st_dev == dev:
            return cand
    except OSError:
        pass
    return None


def _trash_target(trash: Path, name: str, stamp: str) -> Tuple[Path, Optional[Path]]:
    """A free name in the Trash ("house", else "house (vibestrata 0102-1530)"),
    and on Linux the .trashinfo file that goes with it."""
    files = trash if sys.platform == "darwin" else trash / "files"
    name = name.replace("/", "∕")[:200]
    for n in (name, f"{name} (vibestrata {stamp})", f"{name} (vibestrata {stamp}-{uuid.uuid4().hex[:4]})"):
        if not os.path.lexists(files / n):
            info = None if sys.platform == "darwin" else trash / "info" / f"{n}.trashinfo"
            return files / n, info
    raise GuardError("trash-name")


def quarantine_base(path: str) -> Path:
    """A quarantine folder on the same volume as ``path``, never inside it."""
    real = os.path.realpath(path)
    dev = os.lstat(real).st_dev
    sd = state_dir()
    if os.lstat(str(sd)).st_dev == dev:
        base = sd / "quarantine"
    else:
        base = Path(_mount_of(real)) / ".vibestrata-quarantine"
    if os.path.realpath(str(base)).startswith(real.rstrip(os.sep) + os.sep):
        raise GuardError("cross-volume")
    base.mkdir(parents=True, exist_ok=True)
    if os.lstat(str(base)).st_dev != dev:
        raise GuardError("cross-volume")
    return base


# ── guards ─────────────────────────────────────────────────────────────────

def processes_using(paths: List[str]) -> Optional[Dict[str, List[str]]]:
    """Map each path to processes whose cwd, executable or command line
    lives inside it. One ``lsof`` + one ``ps`` call for the whole batch.

    Returns None when the check could not be completed (timeout, error):
    callers must then refuse to move anything.
    """
    hits: Dict[str, List[str]] = {p: [] for p in paths}
    # match both the given and the physical spelling of each path
    prefixes = []
    for p in paths:
        for q in {p, os.path.realpath(p)}:
            prefixes.append((p, q, q.rstrip(os.sep) + os.sep))
    lines: List[Tuple[str, str]] = []
    lsof = shutil.which("lsof")
    if lsof:
        try:
            out = subprocess.run([lsof, "-nP", "-w", "-d", "cwd,txt", "-F", "pcn"],
                                 capture_output=True, text=True, timeout=30, errors="replace").stdout
            pid_, cmd = "", ""
            for ln in out.splitlines():
                if ln.startswith("p"):
                    pid_ = ln[1:]
                elif ln.startswith("c"):
                    cmd = ln[1:]
                elif ln.startswith("n"):
                    lines.append((f"{cmd}[{pid_}]", ln[1:]))
        except (OSError, subprocess.SubprocessError):
            return None
    ps = shutil.which("ps")
    if ps:
        try:
            out = subprocess.run([ps, "-axo", "pid=,command="], capture_output=True, text=True,
                                 timeout=15, errors="replace").stdout
            for ln in out.splitlines():
                pid_, _, cmd = ln.strip().partition(" ")
                if str(os.getpid()) == pid_:
                    continue
                lines.append((f"{os.path.basename(cmd.split(' ')[0])}[{pid_}]", cmd))
        except (OSError, subprocess.SubprocessError):
            return None
    else:
        return None  # no ps at all: we cannot vouch for anything
    for who, where in lines:
        for p, q, pre in prefixes:
            if where == q or pre in where or where.endswith(" " + q) or f" {q} " in where:
                if who not in hits[p]:
                    hits[p].append(who)
    return {p: v for p, v in hits.items() if v}


def _inside(child: str, parent: str) -> bool:
    c, p = os.path.realpath(child), os.path.realpath(parent)
    return c != p and c.startswith(p.rstrip(os.sep) + os.sep)


def check_item(project: str, path: str, kind: str, allow_tier2: bool = True) -> Dict[str, Any]:
    """Re-validate one item right before moving it. Raises GuardError."""
    path = os.path.normpath(path)
    project = os.path.normpath(project)
    if kind == "project":
        # the whole folder: it must be the project itself
        if path != project or not os.path.isabs(path):
            raise GuardError("outside-project")
        real = os.path.realpath(path)
        if real in (os.path.realpath(str(home())), "/") or real.count(os.sep) < 2:
            raise GuardError("refuse-home")
        if os.path.islink(path):
            raise GuardError("symlink")
        if not os.path.isdir(path):
            raise GuardError("missing")
        if rules.is_cloud_path(path):
            raise GuardError("cloud-synced")
        sd = os.path.realpath(str(state_dir()))
        if sd == real or sd.startswith(real + os.sep):
            raise GuardError("protected")
        return {"tier": 0, "dev": os.lstat(path).st_dev}
    if not os.path.isabs(path) or not _inside(path, project):
        raise GuardError("outside-project")
    if os.path.realpath(project) in (os.path.realpath(str(home())), "/"):
        raise GuardError("refuse-home")
    if not os.path.lexists(path):
        raise GuardError("missing")
    st = os.lstat(path)
    if os.path.islink(path):
        raise GuardError("symlink")
    parts = set(Path(os.path.relpath(path, project)).parts)
    if parts & rules.NEVER:
        raise GuardError("protected")
    if rules.is_cloud_path(path) or getattr(st, "st_flags", 0) & rules.SF_DATALESS:
        raise GuardError("cloud-synced")
    if _inside(str(state_dir()), path) or os.path.realpath(path) == os.path.realpath(str(state_dir())):
        raise GuardError("protected")
    tier = 0
    if kind == "candidate":
        rule = rules.match(os.path.basename(path))
        if not rule or not os.path.isdir(path):
            raise GuardError("not-regenerable")
        if not rule.proof(os.path.dirname(path), path):
            raise GuardError("no-proof")
        tier = rule.tier
        if tier == 2 and not allow_tier2:
            raise GuardError("tier2")
    elif kind == "file":
        if not os.path.isfile(path):
            raise GuardError("not-a-file")
        if os.path.basename(path).startswith(".env"):
            raise GuardError("protected")
    elif kind == "workspace":
        # a harness scratch folder (_workspace, _workspace_v2…): your files, not
        # regenerable, so only ever moved when you ask for it
        if not os.path.isdir(path) or not WORKSPACE_RE.match(os.path.basename(path)):
            raise GuardError("not-a-workspace")
    else:
        raise GuardError("bad-kind")
    try:
        top = gitinfo.toplevel(os.path.dirname(path))
        if top:
            if kind in ("candidate", "workspace") and path in gitinfo.blocked_paths(top, [path]):
                raise GuardError("git-tracked")
            if kind == "file" and gitinfo.is_tracked(top, path):
                raise GuardError("git-tracked")
    except gitinfo.GitUnavailable:
        raise GuardError("git-unavailable")
    return {"tier": tier, "dev": st.st_dev}


def changed_since(path: str, ts: float) -> bool:
    """True if anything you made under ``path`` is newer than ``ts`` (the
    survey's record). Dependency folders and VCS internals are skipped, as
    the survey skips them. Unreadable folders count as changed."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_mtime > ts + 1
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            return True
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in rules.NEVER and not rules.match(e.name):
                        stack.append(e.path)
                elif e.name != ".DS_Store" and e.stat(follow_symlinks=False).st_mtime > ts + 1:
                    return True
            except OSError:
                continue
    return False


# ── journal ops ────────────────────────────────────────────────────────────
#
# Item states: pending → moved → restored | trashed, or pending → failed.
# A crash (or ENOSPC) can leave an item "pending" although the rename already
# happened, so every reader resolves "pending" against the disk.

def _op_path(op_id: str) -> Path:
    if not op_id.replace("-", "").isalnum():
        raise GuardError("bad-op")
    return journal_dir() / f"{op_id}.json"


def _settle(item: Dict[str, Any]) -> str:
    """Resolve an item's real state from the disk when the journal is unsure."""
    if item.get("state") == "pending":
        # dst lives in a per-op folder only vibestrata creates: if it exists, the
        # rename happened (even if the user has since re-created src).
        if os.path.lexists(item["dst"]):
            item["state"] = "moved"
        elif os.path.lexists(item["src"]):
            item["state"] = "failed"
        else:
            item["state"] = "lost"
    return item.get("state", "")


def _derive_state(op: Dict[str, Any]) -> str:
    """Op state from its items. Terminal when nothing is left in quarantine."""
    states = [_settle(i) for i in op.get("items", [])]
    if not states:
        return op.get("state", "empty")
    if "moved" in states:
        if "restored" in states:
            return "partially-restored"
        if "trashed" in states:
            return "partially-trashed"
        return "quarantined"
    if "restored" in states and "trashed" in states:
        return "partially-trashed"
    for s in ("restored", "trashed"):
        if s in states:
            return s
    return "lost" if "lost" in states else "failed"


def _try_write(path: Path, op: Dict[str, Any]) -> bool:
    try:
        write_json(path, op)
        return True
    except OSError:
        return False


def list_ops() -> List[Dict[str, Any]]:
    ops = []
    for f in journal_dir().glob("*.json"):
        op = read_json(f, None)
        if op:
            if op.get("items"):
                op["state"] = _derive_state(op)
            ops.append(op)
    ops.sort(key=lambda o: -o.get("ts", 0))
    return ops


def quarantine(items: List[Dict[str, str]], label: str = "", to: str = "trash") -> Dict[str, Any]:
    """Move items to the Trash (or ``to="quarantine"``). ``items``:
    [{project, path, kind}]; kind is candidate, file, workspace or project."""
    if not items:
        raise GuardError("empty")
    checked, skipped = [], []
    for it in items:
        try:
            info = check_item(it["project"], it["path"], it.get("kind", "candidate"))
            # your own files move only as the survey saw them
            if it.get("kind") in ("file", "workspace", "project") and it.get("mtime") is not None \
                    and changed_since(os.path.normpath(it["path"]), float(it["mtime"])):
                raise GuardError("changed-since-scan")
            checked.append({**it, **info})
        except GuardError as e:
            skipped.append({"path": it.get("path"), "reason": str(e)})
        except (KeyError, OSError) as e:
            skipped.append({"path": it.get("path"), "reason": f"error:{e}"})
    busy = processes_using([c["path"] for c in checked]) if checked else {}
    ready = []
    for c in checked:
        if busy is None:
            skipped.append({"path": c["path"], "reason": "busy-check-failed"})
        elif c["path"] in busy:
            skipped.append({"path": c["path"], "reason": "in-use", "by": busy[c["path"]][:4]})
        else:
            ready.append(c)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    op_id = f"{stamp}-{uuid.uuid4().hex[:6]}"
    op = {"id": op_id, "ts": time.time(), "label": label, "state": "quarantined",
          "items": [], "skipped": skipped, "to": to}
    path = _op_path(op_id)
    if not ready:
        op["state"] = "empty"
        return op
    with locked(path):
        short = op_id[4:13].replace("-", "")
        for i, c in enumerate(ready):
            info: Optional[Path] = None
            try:
                trash = trash_dir(c["path"]) if to == "trash" else None
                if trash is not None:
                    base_name = os.path.basename(c["path"]) if c.get("kind") == "project" \
                        else f"{os.path.basename(c['project'])} — {os.path.basename(c['path'])}"
                    dst, info = _trash_target(trash, base_name, short)
                else:
                    dst = quarantine_base(c["path"]) / op_id / f"{i:03d}__{os.path.basename(c['path'])}"
            except (GuardError, OSError) as e:
                skipped.append({"path": c["path"], "reason": str(e) if isinstance(e, GuardError) else "cross-volume"})
                continue
            op["items"].append({"src": c["path"], "dst": str(dst), "project": c["project"],
                                "kind": c.get("kind", "candidate"), "bytes": int(c.get("bytes") or 0),
                                "state": "pending", "in": "trash" if trash is not None else "quarantine",
                                **({"info": str(info)} if info else {})})
        if not op["items"]:
            op["state"] = "empty"
            return op
        write_json(path, op)  # journal first (must succeed), then move
        for item in op["items"]:
            try:
                os.makedirs(os.path.dirname(item["dst"]), exist_ok=True)
                if item.get("info"):  # freedesktop Trash: say where it came from
                    Path(item["info"]).write_text(
                        f"[Trash Info]\nPath={urllib.parse.quote(item['src'])}\n"
                        f"DeletionDate={_dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
                os.rename(item["src"], item["dst"])
                item["state"] = "moved"
            except OSError as e:
                item["state"] = "failed"
                item["error"] = str(e)
                if item.get("info"):
                    try:
                        os.unlink(item["info"])
                    except OSError:
                        pass
            _try_write(path, op)  # best effort; readers re-settle from disk
        op["state"] = "quarantined" if any(i["state"] == "moved" for i in op["items"]) else "failed"
        if not _try_write(path, op):
            op["journal_warning"] = "journal write failed; states are re-derived from disk on undo"
    return op


def restore(op_id: str) -> Dict[str, Any]:
    path = _op_path(op_id)
    with locked(path):
        op = read_json(path, None)
        if not op:
            raise GuardError("unknown-op")
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        for item in op["items"]:
            if _settle(item) != "moved":
                continue
            src, dst = item["src"], item["dst"]
            if not os.path.lexists(dst):
                item["state"] = "lost"
                continue
            target = src
            if os.path.lexists(target):
                target = f"{src}.restored-{stamp}"
                item["restored_as"] = target
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.rename(dst, target)
                item["state"] = "restored"
                if item.get("info"):
                    try:
                        os.unlink(item["info"])
                    except OSError:
                        pass
            except OSError as e:
                item["error"] = str(e)
        states = [_settle(i) for i in op["items"]]
        if "restored" not in states and "trashed" in states and "moved" not in states:
            raise GuardError("already-trashed")
        op["state"] = _derive_state(op)
        op["restored_at"] = time.time()
        write_json(path, op)
        for d in {os.path.dirname(i["dst"]) for i in op["items"] if i.get("in") != "trash"}:
            try:
                os.rmdir(d)  # only removes if empty
            except OSError:
                pass
    return op


def _to_trash(path: str) -> str:
    if sys.platform == "darwin" and shutil.which("osascript"):
        script = 'on run argv\ntell application "Finder" to delete (POSIX file (item 1 of argv) as alias)\nend run'
        try:
            r = subprocess.run(["osascript", "-e", script, path], capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                return "finder"
        except (OSError, subprocess.SubprocessError):
            pass
        trash = home() / ".Trash"
        info = None
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(home() / ".local" / "share")
        trash = Path(xdg) / "Trash" / "files"
        info = Path(xdg) / "Trash" / "info"
        info.mkdir(parents=True, exist_ok=True)
    trash.mkdir(parents=True, exist_ok=True)
    if os.lstat(str(trash)).st_dev != os.lstat(path).st_dev:
        raise GuardError("trash-cross-volume")
    name = f"vibestrata-{os.path.basename(path)}"
    target = trash / name
    if info is not None:
        (info / f"{name}.trashinfo").write_text(
            f"[Trash Info]\nPath={urllib.parse.quote(path)}\nDeletionDate={_dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
    os.rename(path, str(target))
    return "trash-folder"


def empty_to_trash(op_id: str) -> Dict[str, Any]:
    """Hand quarantined folders to the system Trash, journaling per folder so
    a failure halfway leaves an accurate record."""
    path = _op_path(op_id)
    with locked(path):
        op = read_json(path, None)
        if not op:
            raise GuardError("unknown-op")
        moved = [i for i in op["items"] if _settle(i) == "moved" and i.get("in") != "trash"]
        if not moved:
            raise GuardError("nothing-to-trash")
        by_folder: Dict[str, List[Dict[str, Any]]] = {}
        for i in moved:
            by_folder.setdefault(os.path.dirname(i["dst"]), []).append(i)
        via, errors = [], []
        for folder, its in sorted(by_folder.items()):
            try:
                if os.path.isdir(folder):
                    via.append(_to_trash(folder))
                for i in its:
                    i["state"] = "trashed"
            except (GuardError, OSError) as e:
                errors.append(str(e))
            _try_write(path, op)
        op["state"] = _derive_state(op)
        op["trashed_at"] = time.time()
        op["trashed_via"] = sorted(set(via))
        if errors:
            op["trash_errors"] = errors
        write_json(path, op)
        if errors and op["state"] == "quarantined":
            raise GuardError(errors[0])
    return op


# ── open in … ──────────────────────────────────────────────────────────────

RESUME_PROMPT = {
    "ko": "이 프로젝트를 다시 보려고 해. README·CLAUDE.md·HANDOFF 같은 메모와 최근 커밋, 결과물을 살펴보고 "
          "지금 어디까지 됐는지 요약한 다음, 이어서 하거나 보강하면 좋을 점을 제안해줘. 아직 아무것도 고치지 마.",
    "en": "I'm coming back to this project. Read its notes (README, CLAUDE.md, any HANDOFF), recent commits and "
          "outputs, summarise where it stands, then suggest what to continue or improve. Don't change anything yet.",
}


def _terminal(cmd: str, cwd: str, *args: str) -> List[str]:
    """Run ``cd <cwd> && <cmd> <args…>`` in a new Terminal window (macOS),
    every argument shell-quoted by AppleScript."""
    if sys.platform != "darwin":
        raise GuardError("unsupported")
    parts = " & \" \" & ".join(f"quoted form of (item {i + 2} of argv)" for i in range(len(args)))
    script = ('on run argv\ntell application "Terminal"\nactivate\n'
              f'do script "cd " & quoted form of (item 1 of argv) & " && {cmd}"'
              + (f' & " " & {parts}' if args else "") + "\nend tell\nend run")
    return ["osascript", "-e", script, cwd, *args]


def open_in(path: str, how: str, editor_command: str = "", session: Optional[Dict[str, Any]] = None,
            rel: str = "", lang: str = "en") -> None:
    """Open a project: in Finder, a terminal, an editor, one of its files, or
    in Claude Code / Codex (continuing the last conversation, or starting a
    fresh one that first reads where things stand)."""
    if not os.path.isdir(path):
        raise GuardError("missing")
    s = session or {}
    cwd = s.get("cwd") if s.get("cwd") and os.path.isdir(s["cwd"]) and (s["cwd"] == path or s["cwd"].startswith(path + os.sep)) else path
    sid = s.get("id") if re.fullmatch(r"[\w-]{8,80}", str(s.get("id") or "")) else ""
    if how == "finder":
        cmd = ["open", path] if sys.platform == "darwin" else ["xdg-open", path]
    elif how == "file":
        f = os.path.realpath(os.path.join(path, rel))
        if not rel or not f.startswith(os.path.realpath(path) + os.sep) or not os.path.isfile(f):
            raise GuardError("not-a-file")
        cmd = ["open", f] if sys.platform == "darwin" else ["xdg-open", f]
    elif how == "terminal":
        if sys.platform == "darwin":
            cmd = ["open", "-a", "Terminal", path]
        else:
            term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal")
            if not term:
                raise GuardError("no-terminal")
            cmd = [term, f"--working-directory={path}"]
    elif how == "claude":  # pick up the last conversation
        cmd = _terminal("claude --resume", cwd, sid) if sid and s.get("source") == "claude" else _terminal("claude --continue", path)
    elif how == "codex":
        cmd = _terminal("codex resume", cwd, sid) if sid else _terminal("codex resume --last", path)
    elif how == "claude-new":  # a fresh look, for something finished
        cmd = _terminal("claude", path, RESUME_PROMPT.get(lang, RESUME_PROMPT["en"]))
    elif how == "editor":
        if not editor_command:
            raise GuardError("no-editor")
        cmd = [a.replace("{path}", path) for a in shlex.split(editor_command)]
        if "{path}" not in editor_command:
            cmd.append(path)
    else:
        raise GuardError("bad-open")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
