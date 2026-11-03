"""``vibestrata --demo``: a synthetic survey for screenshots and test-drives.

No disk is read or touched. Actions are simulated in memory.
"""

from __future__ import annotations

import copy
import random
import time
import uuid
from typing import Any, Dict, List

DAY = 86400
GB = 1 << 30
MB = 1 << 20

NAMES = [
    ("habit-tracker", "Next.js", "A tiny habit tracker with streaks and a heatmap", "make the streak counter reset at local midnight"),
    ("recipe-ai", "Next.js", "Turn a fridge photo into three dinner ideas", "add a shopping list export"),
    ("pdf-chat", "Python", "Chat with a folder of PDFs, local embeddings", "why is retrieval returning the same chunk twice"),
    ("portfolio-2025", "Vite", "Personal site, take four", "use a serif for the headings"),
    ("portfolio-2026", "Next.js", "Personal site, take five (the real one)", "deploy this to vercel"),
    ("slack-digest-bot", "Node", "Daily digest of starred Slack threads", "summaries are too long, cap at 5 bullets"),
    ("landing-v3", "Next.js", "Landing page, now with pricing table", "add annual pricing toggle"),
    ("landing-v2", "Static", "Landing page experiment for the newsletter", "make the CTA bigger"),
    ("invoice-parser", "Python", "Extract line items from supplier invoices", "handle the scanned ones with OCR"),
    ("kanban-clone", "Vite", "Drag-and-drop board, offline first", "persist to indexeddb"),
    ("tts-podcast", "Python", "Blog posts to a weekly podcast feed", "the voice sounds robotic on numbers"),
    ("market-scraper", "Python", "Track prices of 40 SKUs every morning", "rotate user agents"),
    ("rag-eval", "Jupyter", "Notebook comparing chunking strategies", "plot recall@k by chunk size"),
    ("chrome-tab-saver", "Node", "Extension: save and name tab groups", "publish to the web store"),
    ("wedding-site", "Static", "RSVP page for the wedding", "add a dietary restrictions field"),
    ("video-shorts", "Python", "Cut long talks into vertical shorts", "subtitles drift after 10 minutes"),
    ("ai-flashcards", "Next.js", "Flashcards from lecture notes with spaced repetition", "the SM-2 intervals look wrong"),
    ("budget-dashboard", "Vite", "Personal budget from bank CSVs", "categorize transfers between my own accounts"),
    ("discord-dnd-bot", "Node", "Dice and initiative tracker for Discord", "support advantage rolls"),
    ("photo-dedupe", "Python", "Find near-duplicate photos", "use perceptual hashes instead of md5"),
    ("resume-builder", "Next.js", "Resume from a YAML file, three themes", "export to PDF with proper page breaks"),
    ("api-mock-server", "Node", "Mock any OpenAPI spec locally", ""),
    ("game-jam-48h", "Static", "Canvas platformer for the game jam", "jump feels floaty"),
    ("weather-widget", "Vite", "Menubar weather", "show the feels-like temperature"),
    ("notion-export", "Python", "Notion workspace to Markdown", "keep the database relations as links"),
    ("pitch-deck-2026", "Deck", "Seed deck drafts", "make slide 7 less dense"),
    ("llm-router", "Python", "Route prompts to the cheapest model that passes", "add a fallback when the eval fails"),
    ("meal-planner", "Next.js", "Weekly meal plans from a pantry list", ""),
    ("sql-tutor", "Next.js", "Interactive SQL lessons with a sandbox", "lesson 4 query times out"),
    ("receipt-ocr", "Python", "Receipts to a spreadsheet", ""),
    ("todo-cli", "Rust", "A todo CLI to learn Rust", "borrow checker hates my linked list"),
    ("blog-astro", "Node", "Blog migration attempt", ""),
    ("hackathon-ar", "Static", "WebXR furniture preview", ""),
    ("crypto-alerts", "Node", "Price alerts to Telegram", "alert only on 5% moves"),
    ("thesis-figures", "Jupyter", "Figures for chapter 3", "use the colorblind palette"),
    ("mobile-app-expo", "Node", "Expo app for the gym", "the build fails on iOS 19"),
    ("dataset-cleaner", "Python", "Clean the survey export", ""),
    ("prompt-library", "Next.js", "Searchable library of my prompts", "add tags"),
    # folder names that say nothing; the deliverable names them
    ("0614", "Deck", "", "make the comparison table fit on one page"),
    ("udo", "Deck", "", "add the ferry times to page 2"),
]
# what you first asked for, when the folder has no README to say it
ASKS = {"0614": "review the three ministries' AI strategies for the board offsite and compare them",
        "udo": "plan a 3-day family trip to Udo island with an 8-year-old, as a PDF guide"}
LINKS = {"portfolio-2026": {"repo": "https://github.com/you/portfolio", "site": "https://you.dev"},
         "landing-v3": {"repo": "https://github.com/you/landing", "site": "https://landing-v3.vercel.app"},
         "prompt-library": {"repo": "https://github.com/you/prompt-library"},
         "llm-router": {"repo": "https://github.com/you/llm-router"},
         "habit-tracker": {"repo": "https://github.com/you/habit-tracker", "site": "https://habits.vercel.app"}}


def _proj(i: int, name: str, stack: str, desc: str, last_prompt: str, rnd: random.Random, now: float) -> Dict[str, Any]:
    days = [0.3, 2, 9, 140, 5, 66, 21, 320, 38, 180, 95, 12, 250, 47, 75, 460, 30, 110, 210, 1.2,
            16, 380, 55, 160, 270, 27, 88, 600, 135, 3.5, 230, 70, 41, 510, 190, 104, 290, 680, 142, 63][i % 40]
    days *= rnd.uniform(0.85, 1.2)
    last = now - days * DAY
    js = stack in ("Next.js", "Vite", "Node")
    py = stack in ("Python", "Jupyter")
    source = int(rnd.uniform(2, 60) * MB) + (int(rnd.uniform(0.2, 1.8) * GB) if rnd.random() < 0.3 else 0)
    if stack in ("Deck",) or name.startswith(("video", "tts", "thesis")):
        source += int(rnd.uniform(0.6, 4.5) * GB)
    cands: List[Dict[str, Any]] = []
    path = f"/Users/you/{name}"

    def add(rel: str, cat: str, tier: int, b: int, proof: str, reason: str = "") -> None:
        cands.append({"path": f"{path}/{rel}", "rel": rel, "name": rel.split("/")[-1], "category": cat, "tier": tier,
                      "reason": reason, "proof": proof, "bytes": b, "mtime": last})

    if js:
        add("node_modules", "deps", 1, int(rnd.uniform(0.12, 0.7) * GB), "package.json next to it")
        if stack == "Next.js":
            add(".next", "build", 1, int(rnd.uniform(40, 600) * MB), "Next.js project")
        if rnd.random() < 0.4:
            add("dist", "output", 2, int(rnd.uniform(3, 90) * MB), "build config next to it")
    if py:
        add(".venv", "deps", 1, int(rnd.uniform(0.15, 1.6) * GB), "pyvenv.cfg inside")
        add("__pycache__", "cache", 1, int(rnd.uniform(0.1, 3) * MB), "bytecode cache")
    if stack == "Rust":
        add("target", "build", 1, int(2.1 * GB), "Cargo.toml next to it")
    has_git = i % 4 != 3
    if i % 11 == 4 and has_git:
        add("build", "output", 0, int(30 * MB), "", "git-tracked")
    cands.sort(key=lambda c: -c["bytes"])
    by_cat: Dict[str, int] = {}
    for c in cands:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + c["bytes"]
    git_bytes = int(rnd.uniform(0.2, 40) * MB)
    big = []
    if source > GB:
        for j, (rel, kind) in enumerate([("renders/final_v3.mp4", "video"), ("renders/final_v2.mp4", "video"), ("drafts/deck_v5.pptx", "doc")]):
            big.append({"path": f"{path}/{rel}", "rel": rel, "bytes": int(source * (0.35 - j * 0.1)), "mtime": last - j * DAY * 3,
                        "kind": kind, "sprawl": True})
    has_git = i % 4 != 3
    dirty = rnd.choice([0, 0, 0, 2, 7, 14]) if has_git else 0
    unpushed = rnd.choice([0, 0, 1, 3, 12]) if has_git else 0
    remote = has_git and rnd.random() < 0.7
    status = "active" if days < 14 else "cooling" if days < 45 else "dormant" if days < 120 else "fossil"
    chips = []
    if has_git:
        if status != "active" and (dirty or unpushed or not remote):
            chips.append("half-finished")
        if remote and not dirty and not unpushed:
            chips.append("shipped")
    else:
        chips.append("no-git")
    today = int(now // DAY)
    pulse = sorted({int(days) + k for k in range(0, rnd.randint(1, 14), rnd.randint(1, 3)) if int(days) + k < 90})
    if days < 90:
        pulse = sorted(set(pulse) | {int(days + rnd.uniform(0, 40)) for _ in range(rnd.randint(2, 12))} - {x for x in range(90, 200)})
    pulse = [x for x in pulse if 0 <= x < 90]
    total = source + git_bytes + sum(by_cat.values())
    prompts = rnd.randint(8, 900)
    return {
        "id": f"demo{i:03d}", "name": name, "path": path, "root": "/Users/you", "group": None,
        "stack": [stack, "Claude"] if i % 3 else [stack],
        "desc": desc, "lineage": None, "bytes": total, "source_bytes": source, "git_bytes": git_bytes,
        "junk_bytes": sum(by_cat.values()), "by_category": by_cat,
        "reclaim": sum(c["bytes"] for c in cands if c["tier"] == 1),
        "reclaim_optional": sum(c["bytes"] for c in cands if c["tier"] == 2),
        "candidates": cands, "big": big, "big_total": sum(b["bytes"] for b in big), "big_count": len(big),
        "files": rnd.randint(12, 4000),
        "git": {"root": path, "nested": False, "branch": "main", "dirty": dirty, "remote": remote, "unpushed": unpushed,
                "commits": rnd.randint(1, 240), "last_commit": last - rnd.uniform(0, 3) * DAY, "last_subject": "wip"} if has_git else None,
        "claude": {"prompts": prompts, "sessions": max(1, prompts // 25), "first": last - rnd.uniform(5, 60) * DAY, "last": last,
                   "first_prompt": ASKS.get(name) or f"Let's build this: {desc[0].lower() + desc[1:]}.", "last_prompt": last_prompt,
                   "asks": [[last - 30 * DAY, ASKS.get(name) or f"Let's build this: {desc[0].lower() + desc[1:]}."]],
                   "session": {"id": f"demo-session-{i:03d}", "cwd": path, "source": "claude", "ts": last}} if last_prompt else None,
        "doc_title": "", "doc_para": desc, "links": LINKS.get(name, {}),
        "code_last": last - (40 * DAY if name in ("market-scraper", "llm-router") else 0),
        "activity": {"last": last, "source": "claude" if last_prompt else "files", "commit": last - DAY if has_git else None,
                     "claude": last if last_prompt else None, "files": last},
        "days": days, "status": status, "chips": chips, "pulse": pulse, "cloud": False, "scan_ms": 40,
    }


# How each demo session ended. Read by the same rules as a real survey.
ENDINGS: Dict[str, List[str]] = {
    "pdf-chat": ["dedupe the chunks before ranking", "still returning the same chunk twice"],
    "video-shorts": ["shift the subtitle track by the audio offset", "subtitles still drift after 10 minutes"],
    "mobile-app-expo": ["bump expo to 54", "the build still fails on iOS 19, same error"],
    "sql-tutor": ["lesson 4 query times out", "it doesn't work on the sandbox either"],
    "wedding-site": ["add a dietary restrictions field", "looks good, that's it. ship it"],
    "resume-builder": ["export to PDF with proper page breaks", "perfect. I'll take it from here"],
    "thesis-figures": ["use the colorblind palette", "looks good, these are final"],
    "pitch-deck-2026": ["make slide 7 less dense", "great, I'll take it from here and do the last edits myself"],
    "habit-tracker": ["make the streak counter reset at local midnight", "save progress, continue tomorrow"],
    "tts-podcast": ["the voice sounds robotic on numbers", "save progress, we'll continue later"],
    "0614": ["make the comparison table fit on one page", "perfect, I'll take it from here"],
}
ONE_SHOT = {"weather-widget", "crypto-alerts", "hackathon-ar", "receipt-ocr"}
NOTES = {
    "invoice-parser": ("HANDOFF.md", "Next: OCR fallback for scanned invoices · supplier B uses a two-column layout · tests in tests/fixtures/scanned"),
    "tts-podcast": ("_workspace/CHECKPOINT.md", "Remaining: number normalisation (\"1,200\" → \"twelve hundred\") · try the newer voice · RSS feed works"),
    "llm-router": ("CHANGELOG.md", "2026-07-02 — fallback chain when the eval fails; cost table refreshed"),
}
REGISTERED = {"prompt-library": "~/CLAUDE.md", "llm-router": "skill: llm-router", "market-scraper": "launch agent: com.you.market-scraper"}


def _enrich(p: Dict[str, Any], rnd: random.Random, now: float) -> None:
    from . import segment

    name, last = p["name"], p["activity"]["last"]
    c = p.get("claude")
    if c:
        if name in ONE_SHOT:
            n = rnd.randint(3, 7)
            tail = [f"build {p['desc'].lower()}", c["last_prompt"] or "make it nicer"][-n:]
            c["first"] = last - 0.4 * DAY
        else:
            n = min(c["prompts"], rnd.randint(12, 400))
            tail = ENDINGS.get(name, [c["first_prompt"], c["last_prompt"]])
        recent = [[last - (len(tail) - k) * 600, txt] for k, txt in enumerate(tail) if txt]
        c.update({"meaningful": n, "prompts": max(c["prompts"], n), "p_first": c["first"], "p_last": last,
                  "sources": {"claude": n} if p["id"][-1] in "02468" else {"claude": n - n // 3, "codex": n // 3},
                  "recent": recent, "last_prompt": recent[-1][1] if recent else c["last_prompt"]})
        p["signals"] = segment.prompt_signals([(ts, txt) for ts, txt in recent])
    else:
        p["signals"] = {}
    p["code_files"] = 0 if p["stack"][0] in ("Deck", "Static") else rnd.randint(4, 120)
    deliv: List[Dict[str, Any]] = []
    byp: List[Dict[str, Any]] = []
    path = p["path"]
    if name in ("pitch-deck-2026", "thesis-figures", "resume-builder", "video-shorts", "0614", "udo"):
        ext = {"pitch-deck-2026": "pptx", "thesis-figures": "pdf", "resume-builder": "pdf", "video-shorts": "mp4", "0614": "pdf", "udo": "pdf"}[name]
        stem = {"pitch-deck-2026": "seed_deck", "thesis-figures": "chapter3_figures", "resume-builder": "resume", "video-shorts": "talk_short",
                "0614": "Board_Offsite_AI_Strategy_Review", "udo": "Udo_Family_Trip_Guide"}[name]
        for v in range(1, 6):
            deliv.append({"rel": f"out/{stem}_v{v}.{ext}", "mtime": last - (5 - v) * DAY * 0.5, "bytes": int(rnd.uniform(4, 60) * MB)})
        for d in deliv[:-1]:
            byp.append({"path": f"{path}/{d['rel']}", "rel": d["rel"], "bytes": d["bytes"], "mtime": d["mtime"], "why": "older-version",
                        "kind": "file", "keep": deliv[-1]["rel"], "blocked": "", "holds": 0, "auto": True})
    if name in ("pitch-deck-2026", "tts-podcast", "thesis-figures", "rag-eval", "video-shorts"):
        b = int(rnd.uniform(0.2, 2.4) * GB)
        held = ["draft_final.pdf"] if name == "rag-eval" else []
        byp.append({"path": f"{path}/_workspace", "rel": "_workspace", "bytes": b, "mtime": last, "why": "workspace",
                    "kind": "workspace", "files": rnd.randint(40, 900), "blocked": "", "holds": len(held), "held": held,
                    "code": 0, "referenced": False, "auto": not held})
        p["source_bytes"] += b
        p["bytes"] += b
    byp.sort(key=lambda x: -x["bytes"])
    free = [x for x in byp if not x["blocked"]]
    p["byproducts"] = {"bytes": sum(x["bytes"] for x in free), "count": len(free),
                       "safe_bytes": sum(x["bytes"] for x in free if x["auto"]), "items": byp}
    p["deliverables"] = segment.evidence(deliv) if deliv else {"count": 0, "bytes": 0, "newest": None, "recent": []}
    note = NOTES.get(name)
    p["resume"] = {"file": note[0], "ts": last - 0.1 * DAY, "text": note[1], "kind": "handoff"} if note else None
    p["stem"] = segment_stem(name)
    if name == "portfolio-2025":
        p["git"] = None
        p["chips"] = [c for c in p["chips"] if c != "shipped"]
    if name in ("pitch-deck-2026", "thesis-figures", "resume-builder") and p["git"]:
        p["git"].update(dirty=0, unpushed=0, remote=True)
    from . import identity
    p.update(identity.name(p))


def segment_stem(name: str) -> str:
    from .scan import lineage_stem
    return lineage_stem(name)


def build(seed: int = 7) -> Dict[str, Any]:
    from . import segment

    rnd = random.Random(seed)
    now = time.time()
    projects = [_proj(i, n, s, d, lp, rnd, now) for i, (n, s, d, lp) in enumerate(NAMES)]
    for p in projects:
        _enrich(p, rnd, now)
    th = {"active": 14, "cooling": 45, "dormant": 120}
    sources = [(label, " ".join(f"/Users/you/{n}" for n, lab in REGISTERED.items() if lab == label)) for label in set(REGISTERED.values())]
    segment.apply_all(projects, th, sources)
    projects.sort(key=lambda p: -p["bytes"])
    from .scan import totals
    toolchain = {".npm": int(9.3 * GB), ".cache": int(21 * GB), "Library/pnpm": int(3.1 * GB)}
    from .scan import SURVEY_VERSION
    return {"version": SURVEY_VERSION, "scanned_at": now, "scan_seconds": 11.4, "home": "/Users/you",
            "thresholds": th, "projects": projects,
            "toolchain": toolchain, "totals": totals(projects, toolchain), "demo": True}


class DemoActions:
    """In-memory stand-ins for actions.quarantine/restore/empty_to_trash."""

    def __init__(self, result: Dict[str, Any]) -> None:
        self.result = result
        self.ops: List[Dict[str, Any]] = []
        self.stash: Dict[str, Dict[str, Any]] = {}

    def _project(self, path: str) -> Dict[str, Any]:
        return next(p for p in self.result["projects"] if p["path"] == path)

    def _recompute(self, p: Dict[str, Any]) -> None:
        by_cat: Dict[str, int] = {}
        for c in p["candidates"]:
            by_cat[c["category"]] = by_cat.get(c["category"], 0) + c["bytes"]
        p["by_category"] = by_cat
        p["junk_bytes"] = sum(by_cat.values())
        p["big_total"] = sum(b["bytes"] for b in p["big"])
        p["big_count"] = len(p["big"])
        p["reclaim"] = sum(c["bytes"] for c in p["candidates"] if c["tier"] == 1)
        p["reclaim_optional"] = sum(c["bytes"] for c in p["candidates"] if c["tier"] == 2)
        p["bytes"] = p["source_bytes"] + p["git_bytes"] + p["junk_bytes"]
        free = [x for x in p["byproducts"]["items"] if not x["blocked"]]
        p["byproducts"].update(bytes=sum(x["bytes"] for x in free), count=len(free),
                               safe_bytes=sum(x["bytes"] for x in free if x["auto"]))

    def _refresh(self) -> None:
        from .scan import totals
        self.result["totals"] = totals(self.result["projects"], self.result["toolchain"])

    def quarantine(self, items: List[Dict[str, Any]], label: str = "") -> Dict[str, Any]:
        op_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        moved, skipped = [], []
        for it in items:
            p = self._project(it["project"])
            kind = it.get("kind", "candidate")
            if kind == "project":
                self.result["projects"].remove(p)
                self.stash[p["path"]] = copy.deepcopy({"item": p, "kind": kind, "pool": "projects"})
                moved.append({"src": p["path"], "dst": f"/Users/you/.Trash/{p['name']}", "project": p["path"], "kind": kind,
                              "bytes": p["bytes"], "state": "moved", "in": "trash"})
                continue
            pools = {"candidates": p["candidates"], "big": p["big"], "byproducts": p["byproducts"]["items"]}
            where, hit = None, None
            for k in (["candidates"] if kind == "candidate" else ["byproducts", "big"]):
                hit = next((x for x in pools[k] if x["path"] == it["path"]), None)
                if hit:
                    where = k
                    break
            if not hit or (kind == "candidate" and not hit["tier"]):
                skipped.append({"path": it["path"], "reason": hit["reason"] if hit else "missing"})
                continue
            pools[where].remove(hit)
            if kind != "candidate":
                p["source_bytes"] -= hit["bytes"]
            self.stash[hit["path"]] = copy.deepcopy({"item": hit, "kind": kind, "pool": where})
            moved.append({"src": hit["path"], "dst": f"/Users/you/.Trash/{p['name']} — {hit['path'].split('/')[-1]}",
                          "project": p["path"], "kind": kind, "bytes": hit["bytes"], "state": "moved", "in": "trash"})
            self._recompute(p)
        op = {"id": op_id, "ts": time.time(), "label": label, "state": "quarantined" if moved else "empty",
              "items": moved, "skipped": skipped, "to": "trash"}
        if moved:
            self.ops.insert(0, op)
        self._refresh()
        return op

    def restore(self, op_id: str) -> Dict[str, Any]:
        op = next(o for o in self.ops if o["id"] == op_id)
        for i in op["items"]:
            if i["state"] != "moved":
                continue
            saved = self.stash.pop(i["src"])
            if saved["kind"] == "project":
                self.result["projects"].append(saved["item"])
                i["state"] = "restored"
                continue
            p = self._project(i["project"])
            pool = {"candidates": p["candidates"], "big": p["big"], "byproducts": p["byproducts"]["items"]}[saved.get("pool", "candidates")]
            pool.append(saved["item"])
            pool.sort(key=lambda b: -b["bytes"])
            if saved["kind"] != "candidate":
                p["source_bytes"] += saved["item"]["bytes"]
            i["state"] = "restored"
            self._recompute(p)
        op["state"] = "restored"
        self._refresh()
        return op

    def empty_to_trash(self, op_id: str) -> Dict[str, Any]:
        op = next(o for o in self.ops if o["id"] == op_id)
        for i in op["items"]:
            if i["state"] == "moved":
                i["state"] = "trashed"
                self.stash.pop(i["src"], None)
        op["state"] = "trashed"
        return op
