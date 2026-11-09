"""Command line entry point.

    vibestrata                 open the Vibestrata window (scans on first run)
    vibestrata install-app     add Vibestrata.app to ~/Applications (Launchpad, Spotlight, Dock)
    vibestrata --tab           open in a normal browser tab instead
    vibestrata --rescan        open the dashboard and rescan now
    vibestrata --demo          try it on synthetic data (touches nothing)
    vibestrata scan [--json]   headless survey (prints a table or JSON)
    vibestrata scan --summary  Markdown summary (what the Claude Code plugin reads)
    vibestrata sweep [--apply] preview / move what each project's state pre-selects
    vibestrata ops             list quarantine operations
    vibestrata undo <op-id>    put an operation's items back where they were
    vibestrata review          how the automatic states did against your verdicts
    vibestrata evidence <p>    the evidence pack the triage skill reads (≤ 4 KB)
    vibestrata label <p> …     store the triage skill's reading of a project
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional

from . import __version__, actions, scan, segment
from .config import state_dir, write_json

STATE_MARK = {"active": "●", "in-use": "◆", "blocked": "✕", "delivered": "✓", "superseded": "↷", "one-shot": "◦",
              "paused": "○", "closed": "·"}


def demo_mod_build() -> dict:
    from . import demo
    return demo.build()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def ago(ts: Optional[float]) -> str:
    if not ts:
        return "—"
    d = (time.time() - ts) / 86400
    return "today" if d < 1 else f"{d:.0f}d"


def load_or_scan(max_age_h: float = 0, quiet: bool = False) -> dict:
    """Reuse the last survey if it is fresh enough, else survey now."""
    from .config import read_json
    from .scan import full_scan

    if max_age_h > 0:
        cached = read_json(state_dir() / "scan.json", {}) or {}
        if cached.get("projects") and cached.get("version", 1) >= scan.SURVEY_VERSION and time.time() - cached.get("scanned_at", 0) < max_age_h * 3600:
            return cached
    if not quiet:
        print("surveying…", file=sys.stderr, flush=True)
    result = full_scan({})
    write_json(state_dir() / "scan.json", result)
    return result


def effective_status(p: dict, st: dict) -> str:
    return segment.effective(p, st)


def state_label(p: dict, st: dict) -> str:
    s_ = segment.effective(p, st)
    if s_ == "paused" and p.get("days") is not None:
        return f"paused · {p['days']:.0f}d"
    return s_


def _quote(why: list) -> str:
    for k, v in why or []:
        if v.get("q"):
            return f" — “{v['q'][:90]}”"
    return ""


def print_summary(result: dict) -> None:
    """Compact Markdown, made for pasting into a chat (or for Claude to read)."""
    from . import brief
    from .config import load_state

    st = load_state()
    t = result["totals"]
    ps = result["projects"]
    eff = {p["id"]: effective_status(p, st) for p in ps}
    groups = {g: sum(1 for p in ps if eff[p["id"]] in ss) for g, ss in segment.GROUPS}
    age = (time.time() - result["scanned_at"]) / 60
    name = lambda p: brief.title(p, st) + (f" (`{p['name']}`)" if brief.title(p, st) != p["name"] else "")  # noqa: E731
    print(f"## vibestrata survey — {t['projects']} projects")
    print(f"_surveyed {age:.0f} min ago_\n")
    print(f"- **In progress:** {groups['now']} · **Done:** {groups['done']} · **Unfinished:** {groups['unfinished']}")
    print("- **States:** " + " · ".join(f"{k} {sum(1 for p in ps if eff[p['id']] == k)}" for k in segment.USER_STATES
                                        if any(eff[p["id"]] == k for p in ps)))
    print(f"- **Disk:** {human(t['bytes'])} · rebuildable {human(t['reclaim'])} (node_modules, .venv, caches) · "
          f"leftovers in finished projects {human(brief.finished_leftovers(ps, st))} (`_workspace`, older versions)")

    def section(title: str, rows: list, line) -> None:
        if rows:
            print(f"\n### {title}\n")
            for p in rows:
                print(line(p))

    last = lambda p: p["activity"]["last"] or 0  # noqa: E731
    building = sorted((p for p in ps if eff[p["id"]] == "active"), key=lambda p: -last(p))
    section("In progress" + (f" (12 most recent of {len(building)})" if len(building) > 12 else ""), building[:12],
            lambda p: f"- **{name(p)}** ({ago(last(p))})" + (f" — now: “{p['claude']['last_prompt'][:90]}”" if (p.get("claude") or {}).get("last_prompt") else ""))
    section("Stuck (the last thing you said was a problem)", sorted((p for p in ps if eff[p["id"]] == "blocked"), key=lambda p: -last(p))[:8],
            lambda p: f"- **{name(p)}** ({ago(last(p))}){_quote(p.get('why'))}")
    forgot = sorted((p for p in ps if eff[p["id"]] == "paused"), key=lambda p: -last(p))
    section("On hold (most recent first)", forgot[:10],
            lambda p: f"- **{name(p)}** ({ago(last(p))})"
                      + (f" — note in `{p['resume']['file']}`: {p['resume']['text'][:110]}" if p.get("resume")
                         else (f" — last asked: “{(p.get('claude') or {}).get('last_prompt', '')[:90]}”"
                               if (p.get('claude') or {}).get('last_prompt') else (f" — {brief.summary(p, st)[:90]}" if brief.summary(p, st) else ""))))
    tools = [p for p in ps if eff[p["id"]] == "in-use"]
    if tools:
        print("\n### Tools in use\n")
        print(", ".join(name(p) for p in tools))
    done = sorted((p for p in ps if eff[p["id"]] in segment.FINISHED and (p.get("byproducts") or {}).get("bytes", 0) > 0),
                  key=lambda p: -p["byproducts"]["bytes"])
    section("Done, with leftovers", done[:8],
            lambda p: f"- **{name(p)}** ({eff[p['id']]}) — {human(p['byproducts']['bytes'])} of leftovers"
                      + (" · unsaved work" if (p.get('flags') or {}).get('unsaved') else ""))
    print("\n_Next: `vibestrata` opens the dashboard · `vibestrata here` shows where you left off in this folder · "
          "`vibestrata sweep` previews a cleanup (it goes to the Trash; `vibestrata undo <op>` brings it back)._")


def load_config_big() -> int:
    from .config import load_config
    return int(load_config().get("big_file_mb", 20))


def sweep_items(result: dict, st: dict, states: Optional[set], byproducts: bool) -> List[dict]:
    """What a sweep would move: tier-1 regenerables the project's state
    pre-selects (or every project in ``states``), plus, if asked, the
    byproducts its state pre-selects (never while work is unsaved)."""
    th = result["thresholds"]
    pins = set(st.get("pins", []))
    items = []
    for p in result["projects"]:
        eff = effective_status(p, st)
        if p["id"] in pins or eff in ("in-use", "active"):
            continue
        plan = segment.plan_for(p, st, th)
        if states is not None:
            if eff not in states:
                continue
            plan = {"regen": True, "byproducts": plan["byproducts"]}
        if plan["regen"]:
            for c in p["candidates"]:
                if c["tier"] == 1 and c["bytes"] > 0:
                    items.append({"project": p["path"], "path": c["path"], "kind": "candidate", "bytes": c["bytes"],
                                  "name": p["name"], "rel": c["rel"], "group": "regen"})
        if byproducts and plan["byproducts"]:
            for b in (p.get("byproducts") or {}).get("items", []):
                if b.get("auto"):
                    items.append({"project": p["path"], "path": b["path"], "kind": b["kind"], "bytes": b["bytes"],
                                  "name": p["name"], "rel": b["rel"], "group": "byproduct", "mtime": b.get("mtime")})
    items.sort(key=lambda i: -i["bytes"])
    return items


def byproducts_withheld(result: dict, st: dict, states: Optional[set] = None) -> str:
    """Why ``--byproducts`` found nothing to add, in one line."""
    th = result["thresholds"]
    pins = set(st.get("pins", []))
    have = [p for p in result["projects"] if (p.get("byproducts") or {}).get("bytes", 0) > 0
            and effective_status(p, st) in segment.FINISHED and (states is None or effective_status(p, st) in states)]
    if not have:
        return "No byproducts eligible: no finished project has leftovers."
    pinned = sum(1 for p in have if p["id"] in pins)
    unsaved = sum(1 for p in have if p["id"] not in pins and not segment.plan_for(p, st, th)["byproducts"])
    kept = len(have) - pinned - unsaved   # every item holds a deliverable or code, or the project's CLAUDE.md names it
    n = len(have)
    if unsaved == n:
        return (f"No byproducts eligible: the {n} finished project{'s' if n > 1 else ''} with leftovers "
                f"{'all have' if n > 1 else 'has'} unsaved work. Commit and push first, or tick them in the dashboard.")
    parts = [f"{unsaved} {'have' if unsaved > 1 else 'has'} unsaved work" if unsaved else "",
             f"{pinned} pinned" if pinned else "",
             f"{kept} keep theirs (they hold deliverables or code, or the project's CLAUDE.md names them)" if kept else ""]
    return (f"No byproducts eligible among the {n} finished project{'s' if n > 1 else ''} with leftovers: "
            + ", ".join(x for x in parts if x) + ".")


def cmd_sweep(args: argparse.Namespace) -> int:
    from .config import load_state

    result = load_or_scan(max_age_h=args.max_age, quiet=args.apply)
    st = load_state()
    wanted = None
    if args.status and args.status != "auto":
        wanted = {s.strip() for s in args.status.split(",") if s.strip()}
        bad = wanted - set(segment.USER_STATES)
        if bad:
            print(f"unknown state: {', '.join(sorted(bad))} (use {', '.join(segment.USER_STATES)})", file=sys.stderr)
            return 2
    items = sweep_items(result, st, wanted, args.byproducts)
    total = sum(i["bytes"] for i in items)
    scope = "+".join(sorted(wanted)) if wanted else "by state"
    why_none = byproducts_withheld(result, st, wanted) if args.byproducts and not any(i["group"] == "byproduct" for i in items) else ""
    if not items:
        print(f"Nothing to sweep ({scope}).")
        if why_none:
            print(why_none)
        return 0
    shown = items if args.all else items[:25]
    for i in shown:
        tag = "  (yours: byproduct)" if i["group"] == "byproduct" else ""
        print(f"  {human(i['bytes']):>9}  {i['name']}/{i['rel']}{tag}")
    if len(items) > len(shown):
        rest = items[len(shown):]
        print(f"  {human(sum(i['bytes'] for i in rest)):>9}  … and {len(rest)} smaller items (--all to list)")
    n_by = sum(1 for i in items if i["group"] == "byproduct")
    print(f"\n{len(items)} items · {human(total)} ({scope}; tier-1 dependencies and caches"
          + (f", plus {n_by} byproducts from finished projects" if n_by else "") + ")")
    if why_none:
        print(why_none)
    if not args.byproducts:
        extra = sweep_items(result, st, wanted, True)
        more = sum(i["bytes"] for i in extra if i["group"] == "byproduct")
        if more:
            print(f"Add --byproducts to include {human(more)} of leftovers in finished projects.")
    if not args.apply:
        print("Dry run. Nothing moved. Re-run with --apply to send these to the Trash (one-step undo).")
        return 0
    op = actions.quarantine([{k: i[k] for k in ("project", "path", "kind", "bytes", "mtime") if k in i} for i in items],
                            f"sweep: {scope}")
    moved = [i for i in op["items"] if i["state"] == "moved"]
    print(f"\nSent {len(moved)} items · {human(sum(i['bytes'] for i in moved))} to the Trash. Empty the Trash to get the space back.")
    if op.get("skipped"):
        print(f"Skipped {len(op['skipped'])}: " + ", ".join(sorted({s['reason'] for s in op['skipped']})))
    if moved:
        print(f"Undo any time:  vibestrata undo {op['id']}")
    return 0


def _find(result: dict, key: str) -> Optional[dict]:
    """By id or path first; a bare name only when it is unambiguous."""
    key = key.rstrip("/")
    full = os.path.normpath(os.path.expanduser(key)) if key else ""
    for p in result["projects"]:
        if key == p["id"] or full == p["path"]:
            return p
    named = [p for p in result["projects"] if p["name"] == key]
    if len(named) > 1:
        print(f"{key!r} is ambiguous: " + ", ".join(f"{p['id']} ({p['path']})" for p in named), file=sys.stderr)
        return None
    return named[0] if named else None


def cmd_evidence(args: argparse.Namespace) -> int:
    from . import evidence

    result = load_or_scan(max_age_h=args.max_age, quiet=True)
    p = _find(result, args.project)
    if not p:
        print(f"no project named {args.project!r}", file=sys.stderr)
        return 1
    json.dump(evidence.build(p, result["projects"]), sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


LABEL_KEYS = {"state", "intent", "confidence", "why", "resume_note", "next_action", "title", "summary"}


def _make_label(p: dict, d: dict) -> dict:
    state = segment.LEGACY_STATES.get(d.get("state"), d.get("state"))
    if state not in segment.USER_STATES:
        raise ValueError(f"state must be one of {', '.join(segment.USER_STATES)}")
    conf = d.get("confidence") or "low"
    if conf not in ("high", "med", "low"):
        raise ValueError("confidence must be high, med or low")
    nxt = d.get("next_action") or "ask"
    if nxt not in ("keep", "archive_byproducts", "archive_whole", "push_first", "ask"):
        raise ValueError("next_action must be keep, archive_byproducts, archive_whole, push_first or ask")
    unsaved = (p.get("flags") or {}).get("unsaved")
    if unsaved and nxt.startswith("archive"):
        nxt = "push_first" if not unsaved.get("no_vcs") else "ask"   # the gate holds for the skill too
    return {"state": state, "intent": d.get("intent") or "", "confidence": conf, "why": str(d.get("why") or "")[:300],
            "resume_note": str(d.get("resume_note") or "")[:400], "next_action": nxt, "auto": p.get("state"),
            "title": " ".join(str(d.get("title") or "").split())[:120],
            "summary": " ".join(str(d.get("summary") or "").split())[:300],
            "source": "triage", "ts": time.time()}


def cmd_label(args: argparse.Namespace) -> int:
    from .config import update_state

    result = load_or_scan(max_age_h=24, quiet=True)
    if args.stdin:
        try:
            data = json.loads(sys.stdin.read())
        except ValueError as e:
            print(f"bad JSON on stdin: {e}", file=sys.stderr)
            return 2
        rows = data if isinstance(data, list) else [data]
    else:
        if not args.project:
            print("label needs a project (or --stdin)", file=sys.stderr)
            return 2
        rows = [{"project": args.project, "state": args.state, "intent": args.intent, "confidence": args.confidence,
                 "why": args.why, "resume_note": args.resume_note, "next_action": args.next_action, "clear": args.clear,
                 "title": args.title, "summary": args.summary}]
    todo, rc = [], 0
    for d in rows:
        p = _find(result, str(d.get("project") or ""))
        if not p:
            print(f"no project named {d.get('project')!r}", file=sys.stderr)
            rc = 1
            continue
        if d.get("clear"):
            todo.append((p, None))
            continue
        try:
            todo.append((p, _make_label(p, d)))
        except ValueError as e:
            print(f"{p['name']}: {e}", file=sys.stderr)
            rc = 2

    def put(st: dict) -> None:
        for p, label in todo:
            if label is None:
                st["labels"].pop(p["id"], None)
            else:
                st["labels"][p["id"]] = label
    if todo:
        update_state(put)
    for p, label in todo:
        print(f"cleared the triage label on {p['name']}" if label is None
              else f"{p['name']}: {p.get('state')} → {label['state']} ({label['confidence']}, next: {label['next_action']})")
    return rc


def cmd_review(args: argparse.Namespace) -> int:
    """Projects worth a closer look by the triage skill: idle a while, enough
    history to judge, no label or state from you yet, unnamed ones first."""
    from .config import load_state

    result = load_or_scan(max_age_h=args.max_age, quiet=True)
    st = load_state()
    th = result["thresholds"]
    q = [p for p in result["projects"]
         if p["id"] not in st.get("labels", {}) and p["id"] not in st.get("overrides", {})
         and segment.effective(p, st) in ("paused", "blocked", "one-shot", "delivered")
         and (p.get("days") or 0) >= th["cooling"]
         and ((p.get("claude") or {}).get("meaningful", 0) >= args.min_prompts)]
    q.sort(key=lambda p: (p.get("title_src") != "folder", bool(p.get("resume")), -((p.get("claude") or {}).get("meaningful", 0))))
    rows = [{"id": p["id"], "name": p["name"], "title": p.get("title"), "state": p.get("state"), "idle_days": round(p["days"] or 0),
             "prompts": (p.get("claude") or {}).get("meaningful", 0), "has_note": bool(p.get("resume")),
             "unnamed": p.get("title_src") == "folder"}
            for p in q[: args.limit]]
    if args.json:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        for r in rows:
            print(f"{r['id']}  {r['name']:<24} {r['state']:<10} {r['idle_days']:>4}d {r['prompts']:>4} prompts"
                  + ("  (unnamed)" if r["unnamed"] else "") + ("  (has note)" if r["has_note"] else ""))
    return 0


def survey_in_background() -> bool:
    """Refresh the survey in a detached, low-priority process, one at a time.
    The SessionStart hook calls it, so the plugin works without ever opening
    the dashboard. False if a survey is already running."""
    import subprocess
    from pathlib import Path

    lock = state_dir() / "survey.lock"
    try:
        if time.time() - lock.stat().st_mtime > 900:  # a survey that died
            lock.unlink()
    except OSError:
        pass
    try:
        os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError:
        return False
    pkg_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(x for x in (pkg_root, os.environ.get("PYTHONPATH")) if x))
    try:
        subprocess.Popen([sys.executable, "-m", "vibestrata", "scan", "--background"], cwd=os.path.expanduser("~"), env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        lock.unlink()
        return False
    return True


def cmd_here(args: argparse.Namespace) -> int:
    """Where you left off in this folder's project (or a named one). With
    --hook: the Claude Code SessionStart hook, which must stay quiet and fast."""
    from . import brief
    from .config import load_config, load_state, read_json

    if args.hook:
        try:
            data = json.loads(sys.stdin.read() or "{}") if not sys.stdin.isatty() else {}
        except ValueError:
            data = {}
        try:
            cfg = load_config()
            if not cfg.get("session_brief", True):
                return 0
            cwd = str(data.get("cwd") or os.getcwd())
            path = state_dir() / "scan.json"
            had = path.exists()
            result = read_json(path, {}) or {}
            usable = bool(result.get("projects")) and result.get("version", 1) >= scan.SURVEY_VERSION
            # no dashboard needed: keep the survey fresh from here, in the background
            age_h = (time.time() - (result.get("scanned_at") or 0)) / 3600
            root = os.path.realpath(result.get("home") or os.path.expanduser("~"))
            new_folder = usable and brief.owner(result["projects"], cwd) is None \
                and os.path.realpath(cwd).startswith(root + os.sep) and age_h > 1
            if not usable or new_folder or age_h > float(cfg.get("session_survey_hours", 12)):
                started = survey_in_background()
                if not usable:
                    if started and not had:
                        json.dump(brief.first_run(brief.history_lang()), sys.stdout, ensure_ascii=False)
                    return 0
            out = brief.hook_payload(result, load_state(), cwd, float(cfg.get("session_brief_days", 3)))
            if out:
                json.dump(out, sys.stdout, ensure_ascii=False)
        except Exception:  # a hook never gets in the way of starting a session
            pass
        return 0
    result = load_or_scan(max_age_h=args.max_age, quiet=True)
    st = load_state()
    p = _find(result, args.project) if args.project else brief.owner(result["projects"], os.getcwd())
    if not p:
        print("This folder isn't one of your surveyed projects." if not args.project else f"no project named {args.project!r}",
              file=sys.stderr)
        return 1
    lang = args.lang or brief.guess_lang(result["projects"])
    lines = brief.situation(p, st, lang)
    if args.json:
        json.dump({"id": p["id"], "title": brief.title(p, st), "summary": brief.summary(p, st), "path": p["path"],
                   "state": segment.effective(p, st), "situation": lines, "links": p.get("links") or {},
                   "note": ((st.get("notes") or {}).get(p["id"]) or {}).get("text", "")}, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return 0
    print(f"\n  {brief.title(p, st)}" + (f"  ({p['name']})" if p["name"] not in brief.title(p, st) else ""))
    if brief.summary(p, st):
        print(f"  {brief.summary(p, st)}")
    print()
    for x in lines:
        print(f"  · {x}")
    links = p.get("links") or {}
    if links:
        print("\n  " + "  ".join(f"{k}: {v}" for k, v in links.items()))
    print()
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from .config import load_state

    if args.background:  # started by the SessionStart hook
        try:
            os.nice(10)
        except (AttributeError, OSError):
            pass
        try:
            load_or_scan(quiet=True)
        finally:
            try:
                (state_dir() / "survey.lock").unlink()
            except OSError:
                pass
        return 0
    result = load_or_scan(max_age_h=args.max_age, quiet=args.json)
    if args.summary:
        print_summary(result)
        return 0
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return 0
    st = load_state()
    t = result["totals"]
    print(f"\n{t['projects']} projects · {human(t['bytes'])} total · "
          f"{human(t['reclaim'])} safely reclaimable (+{human(t['reclaim_optional'])} optional, "
          f"{human(t.get('byproducts', 0))} byproducts) · scanned in {result['scan_seconds']}s\n")
    rows = sorted(result["projects"], key=lambda p: -p["reclaim"])[: args.top]
    w = max([len(p["name"]) for p in rows] + [8])
    print(f"  {'project':<{w}}  {'size':>9}  {'reclaim':>9}  {'last':>6}  state")
    for p in rows:
        eff = effective_status(p, st)
        print(f"{STATE_MARK.get(eff, ' ')} {p['name']:<{w}}  {human(p['bytes']):>9}  "
              f"{human(p['reclaim']):>9}  {ago(p['activity']['last']):>6}  {state_label(p, st)}")
    print("\nOpen the dashboard with `vibestrata` to act on these.")
    return 0


def cmd_ops(_args: argparse.Namespace) -> int:
    ops = actions.list_ops()
    if not ops:
        print("no operations yet.")
    for op in ops:
        n = sum(1 for i in op["items"] if i.get("state") == "moved")
        size = sum(i.get("bytes", 0) for i in op["items"])
        where = "in the Trash" if op.get("to", "quarantine") == "trash" else "in quarantine"
        print(f"{op['id']}  {op['state']:<18} {len(op['items'])} items ({n} {where}) {human(size)}  {op.get('label','')}")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    try:
        op = actions.restore(args.op)
    except actions.GuardError as e:
        print(f"cannot undo: {e}", file=sys.stderr)
        return 1
    for i in op["items"]:
        extra = f" → {i['restored_as']}" if i.get("restored_as") else ""
        print(f"{i['state']:<9} {i['src']}{extra}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="vibestrata", description="A local field survey of your vibe-coded projects.")
    ap.add_argument("--version", action="version", version=f"vibestrata {__version__}")
    ap.add_argument("--port", type=int, default=7444)
    ap.add_argument("--no-browser", action="store_true", help="don't open any window (server only)")
    ap.add_argument("--tab", action="store_true", help="open in a normal browser tab; keep running until Ctrl+C")
    ap.add_argument("--app-launch", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--rescan", action="store_true", help="rescan on launch")
    ap.add_argument("--demo", action="store_true", help="synthetic data, nothing on disk is read or touched")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", help="headless survey")
    s.add_argument("--json", action="store_true")
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--summary", action="store_true", help="compact Markdown summary")
    s.add_argument("--max-age", type=float, default=0, metavar="HOURS", help="reuse the last survey if younger than this")
    s.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    w = sub.add_parser("sweep", help="preview (or --apply) what each project's state pre-selects")
    w.add_argument("--status", default="auto", help="'auto' (default: follow each project's state) or a comma list of states")
    w.add_argument("--byproducts", action="store_true", help="also move byproducts of finished projects (_workspace, old versions)")
    w.add_argument("--apply", action="store_true", help="actually send items to the Trash (undo: vibestrata undo <op>)")
    w.add_argument("--all", action="store_true", help="list every item, not just the largest 25")
    w.add_argument("--max-age", type=float, default=6, metavar="HOURS", help="reuse a survey younger than this (default 6)")
    r = sub.add_parser("review", help="projects worth a closer look by the triage skill")
    r.add_argument("--json", action="store_true")
    r.add_argument("--queue", action="store_true", help=argparse.SUPPRESS)  # the only mode now
    r.add_argument("--limit", type=int, default=12)
    r.add_argument("--min-prompts", type=int, default=10)
    r.add_argument("--max-age", type=float, default=6, metavar="HOURS")
    e = sub.add_parser("evidence", help="print the evidence pack for one project (what the triage skill reads)")
    e.add_argument("project", help="name, id or path")
    e.add_argument("--max-age", type=float, default=6, metavar="HOURS")
    lb = sub.add_parser("label", help="store a reading of a project (used by the triage skill)")
    lb.add_argument("project", nargs="?", help="name, id or path")
    lb.add_argument("--stdin", action="store_true", help="read one label or a list of labels as JSON from stdin")
    lb.add_argument("--state", choices=segment.USER_STATES)
    lb.add_argument("--intent", choices=["closed", "resume", "handoff", ""], default="")
    lb.add_argument("--confidence", choices=["high", "med", "low"], default="low")
    lb.add_argument("--why", default="")
    lb.add_argument("--resume-note", default="")
    lb.add_argument("--next-action", choices=["keep", "archive_byproducts", "archive_whole", "push_first", "ask"], default="ask")
    lb.add_argument("--clear", action="store_true", help="remove the label")
    lb.add_argument("--title", default="", help="a readable name, when the folder name says nothing")
    lb.add_argument("--summary", default="", help="one line about what it is")
    hr = sub.add_parser("here", help="where you left off in this folder's project (or a named one)")
    hr.add_argument("project", nargs="?", help="name, id or path (default: the project this folder belongs to)")
    hr.add_argument("--json", action="store_true")
    hr.add_argument("--lang", choices=["en", "ko"])
    hr.add_argument("--hook", action="store_true", help=argparse.SUPPRESS)  # Claude Code SessionStart
    hr.add_argument("--max-age", type=float, default=24, metavar="HOURS")
    sub.add_parser("ops", help="list past cleanups (each can be undone)")
    sub.add_parser("install-app", help="install Vibestrata.app (macOS) or a desktop entry (Linux)")
    u = sub.add_parser("undo", help="restore an operation")
    u.add_argument("op")
    args = ap.parse_args(argv)
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "sweep":
        return cmd_sweep(args)
    if args.cmd == "ops":
        return cmd_ops(args)
    if args.cmd == "review":
        return cmd_review(args)
    if args.cmd == "evidence":
        return cmd_evidence(args)
    if args.cmd == "label":
        if not args.stdin and not args.clear and not args.state:
            ap.error("label needs --state (or --clear, or --stdin)")
        return cmd_label(args)
    if args.cmd == "undo":
        return cmd_undo(args)
    if args.cmd == "here":
        return cmd_here(args)
    if args.cmd == "install-app":
        from .app import install_app
        where = install_app()
        print(f"Installed: {where}\nOpen it from Launchpad / Spotlight (\"Vibestrata\"), or drag it to the Dock.")
        return 0
    from .server import serve

    if not args.app_launch and sys.stdout.isatty():
        # the map in the terminal first, from the last survey; the window follows
        try:
            from . import brief
            from .config import load_state, read_json
            cached = demo_mod_build() if args.demo else read_json(state_dir() / "scan.json", {}) or {}
            if cached.get("projects") and cached.get("version", 1) >= scan.SURVEY_VERSION:
                import shutil as _sh
                print(brief.overview(cached, {} if args.demo else load_state(), width=min(110, _sh.get_terminal_size().columns - 2),
                                     color=True))
        except Exception:
            pass
    window = "none" if args.no_browser else "tab" if args.tab else "auto"
    serve(args.port, window=window, rescan=args.rescan, demo=args.demo)
    return 0
