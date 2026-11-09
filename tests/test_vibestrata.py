"""End-to-end tests against a throwaway fixture home. Stdlib only:

    python -m unittest discover -s tests -v      (or: pytest)
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_GIT = shutil.which("git") is not None


def write(p: Path, text: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *args],
                   cwd=cwd, check=True, capture_output=True)


class Fixture(unittest.TestCase):
    """Builds a fake $HOME with one project per archetype."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="vibestrata-test-")).resolve()
        h = cls.home = cls.tmp / "home"
        cls.state = cls.tmp / "state"
        cls.claude = cls.tmp / "claude"
        os.environ["VIBESTRATA_USER_HOME"] = str(h)
        os.environ["VIBESTRATA_HOME"] = str(cls.state)
        os.environ["VIBESTRATA_CLAUDE_DIR"] = str(cls.claude)
        cls.codex = cls.tmp / "codex"
        os.environ["VIBESTRATA_CODEX_DIR"] = str(cls.codex)
        day = 86400
        ago = lambda d: time.time() - d * day  # noqa: E731

        # 1. node project with regenerable node_modules
        write(h / "node-app/package.json", '{"dependencies":{"next":"15"}}')
        write(h / "node-app/src/index.js", "console.log(1)")
        write(h / "node-app/node_modules/left-pad/index.js", "x" * 5000)
        write(h / "node-app/.next/cache/blob", "x" * 3000)
        # 2. python venv with proof
        write(h / "py-app/pyproject.toml", "[project]\nname='a'")
        write(h / "py-app/.venv/pyvenv.cfg", "home = /usr/bin")
        write(h / "py-app/.venv/lib/site.py", "x" * 2000)
        write(h / "py-app/pkg/__pycache__/m.cpython-312.pyc", "x")
        # 3. node_modules with no package.json → blocked (no proof)
        write(h / "orphan/node_modules/a/index.js", "x")
        write(h / "orphan/notes.txt", "hello")
        # 4. symlinked node_modules → blocked
        write(h / "linked/package.json", "{}")
        os.symlink(str(h / "node-app/node_modules"), str(h / "linked/node_modules"))
        # 5. group folder with two marked children
        write(h / "stuff/alpha/package.json", "{}")
        write(h / "stuff/beta/pyproject.toml", "")
        # 6. markerless vibe folder
        write(h / "vibe-draft/draft_v2.md", "# thoughts")
        # 7. skipped system folder
        write(h / "Library/whatever/package.json", "{}")
        # 8. lineage siblings
        write(h / "report/README.md", "# Report\n\nfirst take")
        write(h / "report2/README.md", "# Report v2")
        # 9. git repo whose build/ is tracked (a deliverable) and dist/ ignored
        if HAS_GIT:
            r = h / "tracked"
            write(r / "package.json", "{}")
            write(r / "build/site.html", "<h1>ship me</h1>")
            write(r / "dist/bundle.js", "x")
            write(r / ".gitignore", "dist/\n")
            git(r, "init", "-q")
            git(r, "add", ".")
            git(r, "commit", "-qm", "init")

        # 10. .svn internals are never offered
        write(h / "svn-app/package.json", "{}")
        write(h / "svn-app/.svn/pristine/big.bin", "x" * 10)
        if HAS_GIT:
            # 11. repo reached through a symlinked root (~/code -> elsewhere)
            phys = cls.tmp / "physical-code"
            r = phys / "webapp"
            write(r / "package.json", "{}")
            write(r / "build/site.html", "<h1>deliverable</h1>")
            write(r / "node_modules/x/i.js", "x")
            write(r / ".gitignore", "node_modules/\n")
            git(r, "init", "-q")
            git(r, "add", ".")
            git(r, "commit", "-qm", "init")
            os.symlink(str(phys), str(h / "code"))
            # 12. repo whose git config is corrupt → git can't answer → fail closed
            r = h / "broken"
            write(r / "package.json", "{}")
            write(r / "dist/index.html", "tracked")
            git(r, "init", "-q")
            git(r, "add", ".")
            git(r, "commit", "-qm", "init")
            (r / ".git" / "config").write_text("[core\n  this is not valid")

        # 13. a finished content job: hand-off words, a version chain, a harness
        #     _workspace (one deliverable lives only there) and a hand-off note
        d = h / "deck-job"
        write(d / "README.md", "# Seed deck")
        write(d / "HANDOFF.md", "# Handoff\n\n## Status\nv2 sent.\n\n## Next\n- fix the chart on slide 3\n- export a 16:9 PDF\n")
        write(d / "out/deck_v1.pdf", "x" * 300_000)
        write(d / "out/deck_v2.pdf", "x" * 300_000)
        write(d / "_workspace/render.png", "x" * 1_100_000)
        write(d / "_workspace/only_here.pdf", "x" * 8000)
        for i, f in enumerate(sorted(x for x in d.rglob("*"))):
            os.utime(f, (ago(60) + i, ago(60) + i))
        os.utime(d / "out/deck_v2.pdf", (ago(59.9), ago(59.9)))
        # 14. a Codex-only project whose last words say it is still broken
        c = h / "codex-app"
        write(c / "package.json", "{}")
        write(c / "src/index.ts", "export {}")
        for f in [c / "package.json", c / "src/index.ts", c / "src", c]:
            os.utime(f, (ago(30), ago(30)))
        meta = {"type": "session_meta", "payload": {"id": "cx1", "cwd": str(c)}}
        write(cls.codex / "sessions/2026/01/01/rollout-cx1.jsonl", json.dumps(meta) + "\n")
        os.utime(cls.codex / "sessions/2026/01/01/rollout-cx1.jsonl", (ago(30), ago(30)))
        write(cls.codex / "history.jsonl", "\n".join(json.dumps(r) for r in [
            {"session_id": "cx1", "ts": int(ago(30.1)), "text": "build a todo app with offline sync"},
            {"session_id": "cx1", "ts": int(ago(30.05)), "text": "my key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz"},
            {"session_id": "cx1", "ts": int(ago(30)), "text": "the sync still fails, same error"},
        ]) + "\n")
        # 16. a workspace that holds code is listed but never pre-selected; a PDF
        #     under docs/ is an input, not a deliverable; a key in HANDOFF is redacted
        k = h / "kit"
        write(k / "README.md", "# kit")
        write(k / "_workspace/run.py", "print(1)" + "#" * 1_100_000)
        write(k / "docs/guideline.pdf", "x" * 9000)
        write(k / "HANDOFF.md", "## Next\n- rotate OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx1234\n")
        # 15. a registry: ~/CLAUDE.md names report2 (and must not match report)
        write(h / "CLAUDE.md", f"| Report | `~/report2/` |\n")
        for f in (h / "report/README.md", h / "report"):
            os.utime(f, (ago(40), ago(40)))

        # Claude history: one row per prompt, plus a session transcript whose
        # folder uses the *other* naming scheme — we must find it via cwd.
        now_ms = int(time.time() * 1000)
        rows = [
            {"display": "build me a dashboard for my projects", "timestamp": now_ms - 86400000 * 3, "project": str(h / "node-app"), "sessionId": "s1"},
            {"display": "ok", "timestamp": now_ms - 86400000 * 2, "project": str(h / "node-app"), "sessionId": "s1"},
            {"display": "make the chart prettier please", "timestamp": now_ms - 86400000, "project": str(h / "node-app/src"), "sessionId": "s2"},
            {"display": "make a seed deck from my notes", "timestamp": int(ago(60.2) * 1000), "project": str(h / "deck-job"), "sessionId": "s3"},
            {"display": "좋아, 나머지는 내가 할게", "timestamp": int(ago(60) * 1000), "project": str(h / "deck-job"), "sessionId": "s3"},
        ]
        write(cls.claude / "history.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")
        enc = "-" + str(h / "py-app").strip("/").replace("/", "-").replace("_", "-")
        write(cls.claude / "projects" / enc / "abc.jsonl", json.dumps({"type": "user", "cwd": str(h / "py-app")}) + "\n")

        import importlib

        import vibestrata.config
        importlib.reload(vibestrata.config)
        from vibestrata import actions, scan
        importlib.reload(scan)
        importlib.reload(actions)
        cls.scan_mod, cls.actions = scan, actions
        cls.result = scan.full_scan()
        cls.by_name = {p["name"]: p for p in cls.result["projects"]}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def cand(self, project: str, rel: str) -> dict:
        return next(c for c in self.by_name[project]["candidates"] if c["rel"] == rel)


class TestDiscovery(Fixture):
    def test_finds_archetypes(self) -> None:
        for name in ("node-app", "py-app", "orphan", "linked", "alpha", "beta", "vibe-draft"):
            self.assertIn(name, self.by_name)

    def test_group_and_skip(self) -> None:
        self.assertEqual(self.by_name["alpha"]["group"], "stuff")
        self.assertNotIn("stuff", self.by_name)
        self.assertNotIn("Library", self.by_name)
        self.assertNotIn("whatever", self.by_name)

    def test_lineage(self) -> None:
        self.assertEqual(self.by_name["report"]["lineage"], "report")
        self.assertEqual(self.by_name["report2"]["lineage"], "report")


class TestGuards(Fixture):
    def test_tier1_with_proof(self) -> None:
        self.assertEqual(self.cand("node-app", "node_modules")["tier"], 1)
        self.assertEqual(self.cand("node-app", ".next")["tier"], 1)
        self.assertEqual(self.cand("py-app", ".venv")["tier"], 1)
        self.assertEqual(self.cand("py-app", "pkg/__pycache__")["tier"], 1)
        self.assertGreater(self.by_name["node-app"]["reclaim"], 0)

    def test_no_proof_blocked(self) -> None:
        c = self.cand("orphan", "node_modules")
        self.assertEqual((c["tier"], c["reason"]), (0, "no-proof"))

    def test_symlink_blocked(self) -> None:
        c = self.cand("linked", "node_modules")
        self.assertEqual((c["tier"], c["reason"]), (0, "symlink"))

    @unittest.skipUnless(HAS_GIT, "git missing")
    def test_git_tracked_blocked_ignored_allowed(self) -> None:
        self.assertEqual(self.cand("tracked", "build")["reason"], "git-tracked")
        self.assertEqual(self.cand("tracked", "dist")["tier"], 2)
        self.assertNotIn("shipped", self.by_name["tracked"]["chips"])  # no remote → not shipped

    @unittest.skipUnless(HAS_GIT, "git missing")
    def test_symlinked_root_still_respects_git(self) -> None:
        self.assertEqual(self.cand("webapp", "build")["reason"], "git-tracked")
        self.assertEqual(self.cand("webapp", "node_modules")["tier"], 1)
        proj = str(self.home / "code/webapp")
        with self.assertRaises(self.actions.GuardError) as e:
            self.actions.check_item(proj, proj + "/build", "candidate")
        self.assertEqual(str(e.exception), "git-tracked")

    @unittest.skipUnless(HAS_GIT, "git missing")
    def test_git_failure_fails_closed(self) -> None:
        self.assertEqual(self.cand("broken", "dist")["reason"], "git-unavailable")
        proj = str(self.home / "broken")
        with self.assertRaises(self.actions.GuardError) as e:
            self.actions.check_item(proj, proj + "/dist", "candidate")
        self.assertEqual(str(e.exception), "git-unavailable")

    def test_vcs_internals_not_offered(self) -> None:
        p = self.by_name["svn-app"]
        self.assertFalse(any(".svn" in b["rel"] for b in p["big"]))
        self.assertFalse(any(".svn" in c["rel"] for c in p["candidates"]))

    def test_never_outside_project(self) -> None:
        with self.assertRaises(self.actions.GuardError):
            self.actions.check_item(str(self.home / "node-app"), str(self.home / "py-app/.venv"), "candidate")
        with self.assertRaises(self.actions.GuardError):
            self.actions.check_item(str(self.home / "node-app"), str(self.home / "node-app/../py-app/.venv"), "candidate")
        with self.assertRaises(self.actions.GuardError):
            self.actions.check_item(str(self.home / "node-app"), str(self.home / "node-app/src"), "candidate")

    def test_refuse_home_as_project(self) -> None:
        with self.assertRaises(self.actions.GuardError):
            self.actions.check_item(str(self.home), str(self.home / "node-app/node_modules"), "candidate")


class TestClaude(Fixture):
    def test_history_mapped_with_subdir(self) -> None:
        c = self.by_name["node-app"]["claude"]
        self.assertEqual(c["prompts"], 3)
        self.assertEqual(c["sessions"], 2)
        self.assertEqual(c["first_prompt"], "build me a dashboard for my projects")
        self.assertEqual(c["last_prompt"], "make the chart prettier please")  # "ok" skipped

    def test_session_found_regardless_of_folder_encoding(self) -> None:
        self.assertIsNotNone(self.by_name["py-app"]["claude"])
        self.assertEqual(self.by_name["py-app"]["claude"]["sessions"], 1)

    def test_status(self) -> None:
        self.assertEqual(self.by_name["node-app"]["status"], "active")


class TestQuarantine(Fixture):
    def test_roundtrip_and_collision(self) -> None:
        proj = str(self.home / "node-app")
        nm = os.path.join(proj, "node_modules")
        op = self.actions.quarantine([{"project": proj, "path": nm, "kind": "candidate"}], "t")
        self.assertEqual(op["state"], "quarantined")
        self.assertFalse(os.path.exists(nm))
        self.assertTrue(os.path.exists(op["items"][0]["dst"]))
        journal = self.state / "journal" / f"{op['id']}.json"
        self.assertTrue(journal.exists())
        # user reinstalls deps meanwhile → restore must not clobber
        write(Path(nm) / "fresh.js", "new")
        back = self.actions.restore(op["id"])
        self.assertEqual(back["state"], "restored")
        self.assertTrue((Path(nm) / "fresh.js").exists())
        self.assertTrue(os.path.exists(back["items"][0]["restored_as"]))
        with self.assertRaises(self.actions.GuardError):
            self.actions.empty_to_trash(op["id"])

    def test_pending_journal_entry_is_recoverable(self) -> None:
        """Crash/ENOSPC after rename but before the journal update."""
        proj = str(self.home / "py-app")
        venv = os.path.join(proj, ".venv")
        op = self.actions.quarantine([{"project": proj, "path": venv, "kind": "candidate"}])
        jp = self.state / "journal" / f"{op['id']}.json"
        data = json.loads(jp.read_text())
        data["items"][0]["state"] = "pending"
        jp.write_text(json.dumps(data))
        back = self.actions.restore(op["id"])
        self.assertEqual(back["state"], "restored")
        self.assertTrue(os.path.exists(os.path.join(venv, "pyvenv.cfg")))

    def test_pending_with_recreated_src_and_lost_items(self) -> None:
        proj = str(self.home / "node-app")
        nm = os.path.join(proj, ".next")
        op = self.actions.quarantine([{"project": proj, "path": nm, "kind": "candidate"}])
        jp = self.state / "journal" / f"{op['id']}.json"
        data = json.loads(jp.read_text())
        data["items"][0]["state"] = "pending"
        jp.write_text(json.dumps(data))
        write(Path(nm) / "rebuilt", "x")  # user rebuilt meanwhile
        back = self.actions.restore(op["id"])
        self.assertEqual(back["state"], "restored")
        self.assertTrue(os.path.exists(back["items"][0]["restored_as"]))
        # an op whose quarantined item vanished is terminal, not "quarantined"
        lost = {"id": "20990101-000000-abcdef", "ts": 1, "label": "", "state": "quarantined",
                "items": [{"src": nm + "-x", "dst": str(self.tmp / "nowhere/x"), "project": proj,
                           "kind": "candidate", "bytes": 1, "state": "pending"}], "skipped": []}
        (self.state / "journal" / f"{lost['id']}.json").write_text(json.dumps(lost))
        listed = next(o for o in self.actions.list_ops() if o["id"] == lost["id"])
        self.assertEqual(listed["state"], "lost")

    def test_busy_check_failure_refuses(self) -> None:
        """If lsof/ps time out we can't vouch for anything: move nothing."""
        orig = self.actions.processes_using
        self.actions.processes_using = lambda paths: None
        try:
            proj = str(self.home / "node-app")
            op = self.actions.quarantine([{"project": proj, "path": os.path.join(proj, ".next"), "kind": "candidate"}])
        finally:
            self.actions.processes_using = orig
        self.assertEqual(op["state"], "empty")
        self.assertEqual(op["skipped"][0]["reason"], "busy-check-failed")
        self.assertTrue(os.path.exists(os.path.join(proj, ".next")))

    def test_busy_check_detects_cwd(self) -> None:
        d = self.home / "node-app" / ".next"
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], cwd=str(d))
        try:
            time.sleep(0.5)
            hits = self.actions.processes_using([str(d)])
            if shutil.which("lsof"):
                self.assertIsNotNone(hits)
                self.assertIn(str(d), hits)
        finally:
            p.kill()
            p.wait()

    def test_file_quarantine_and_trash(self) -> None:
        proj = str(self.home / "vibe-draft")
        f = os.path.join(proj, "draft_v2.md")
        # quarantine first (on Linux a Trash folder always exists, so ask for it), then hand it to the Trash
        op = self.actions.quarantine([{"project": proj, "path": f, "kind": "file"}], to="quarantine")
        self.assertFalse(os.path.exists(f))
        old = self.actions.sys.platform
        os.environ["XDG_DATA_HOME"] = str(self.home / "files/share")  # "/files" in path on purpose
        self.actions.sys.platform = "linux"
        try:
            done = self.actions.empty_to_trash(op["id"])
        finally:
            self.actions.sys.platform = old
        self.assertEqual(done["state"], "trashed")
        self.assertTrue(any((self.home / "files/share/Trash/files").iterdir()))
        self.assertTrue(any((self.home / "files/share/Trash/info").iterdir()))
        with self.assertRaises(self.actions.GuardError):
            self.actions.restore(op["id"])

    def test_skips_are_reported_not_moved(self) -> None:
        proj = str(self.home / "orphan")
        op = self.actions.quarantine([{"project": proj, "path": os.path.join(proj, "node_modules"), "kind": "candidate"}])
        self.assertEqual(op["state"], "empty")
        self.assertEqual(op["skipped"][0]["reason"], "no-proof")
        self.assertTrue(os.path.exists(os.path.join(proj, "node_modules")))

    @unittest.skipUnless(HAS_GIT, "git missing")
    def test_tracked_dir_refused_at_action_time(self) -> None:
        proj = str(self.home / "tracked")
        op = self.actions.quarantine([{"project": proj, "path": os.path.join(proj, "build"), "kind": "candidate"}])
        self.assertEqual(op["skipped"][0]["reason"], "git-tracked")
        self.assertTrue(os.path.exists(os.path.join(proj, "build/site.html")))


class TestSegmentation(Fixture):
    def test_content_job_is_delivered_with_leftovers(self) -> None:
        p = self.by_name["deck-job"]
        self.assertEqual(p["state"], "delivered")
        self.assertEqual(p["why"][0][0], "handoff")
        items = {b["rel"]: b for b in p["byproducts"]["items"]}
        self.assertEqual(items["out/deck_v1.pdf"]["why"], "older-version")
        self.assertNotIn("out/deck_v2.pdf", items)  # the newest is kept
        self.assertEqual(items["_workspace"]["holds"], 1)  # only_here.pdf
        self.assertIsNone(p["flags"]["unsaved"])
        self.assertTrue(p["plan"]["byproducts"])
        self.assertIn("fix the chart", p["resume"]["text"])
        self.assertEqual(p["resume"]["file"], "HANDOFF.md")

    def test_codex_project_blocked_and_redacted(self) -> None:
        p = self.by_name["codex-app"]
        self.assertEqual(p["claude"]["sources"], {"codex": 3})
        self.assertEqual(p["state"], "blocked")
        self.assertIn("still fails", p["why"][0][1]["q"])
        self.assertNotIn("sk-ant", json.dumps(p, ensure_ascii=False))
        self.assertEqual(p["flags"]["unsaved"], {"no_vcs": True})

    def test_registry_and_superseded(self) -> None:
        self.assertEqual(self.by_name["report2"]["flags"]["registered"], ["~/CLAUDE.md"])
        self.assertEqual(self.by_name["report"]["flags"]["registered"], [])
        self.assertEqual(self.by_name["report"]["state"], "superseded")

    def test_workspace_with_code_not_auto_and_inputs_ignored(self) -> None:
        p = self.by_name["kit"]
        ws = next(b for b in p["byproducts"]["items"] if b["rel"] == "_workspace")
        self.assertEqual((ws["code"], ws["auto"]), (1, False))
        self.assertEqual(p["deliverables"]["count"], 0)  # docs/guideline.pdf is an input
        self.assertNotIn("sk-abc", p["resume"]["text"])
        self.assertIn("[redacted]", p["resume"]["text"])

    def test_changed_since_scan_refuses(self) -> None:
        proj = str(self.home / "deck-job")
        f = os.path.join(proj, "out/deck_v1.pdf")
        seen = os.path.getmtime(f)
        os.utime(f, (seen + 3600, seen + 3600))  # edited after the survey
        try:
            op = self.actions.quarantine([{"project": proj, "path": f, "kind": "file", "mtime": seen}])
            self.assertEqual(op["skipped"][0]["reason"], "changed-since-scan")
            self.assertTrue(os.path.exists(f))
        finally:
            os.utime(f, (seen, seen))

    def test_workspace_quarantine_roundtrip(self) -> None:
        proj = str(self.home / "deck-job")
        ws = os.path.join(proj, "_workspace")
        with self.assertRaises(self.actions.GuardError):
            self.actions.check_item(proj, os.path.join(proj, "out"), "workspace")
        op = self.actions.quarantine([{"project": proj, "path": ws, "kind": "workspace"}])
        self.assertEqual(op["state"], "quarantined")
        self.assertFalse(os.path.exists(ws))
        self.actions.restore(op["id"])
        self.assertTrue(os.path.exists(os.path.join(ws, "only_here.pdf")))

    def test_evidence_pack_is_small(self) -> None:
        from vibestrata import evidence
        pack = evidence.build(self.by_name["codex-app"], self.result["projects"])
        raw = json.dumps(pack, ensure_ascii=False)
        self.assertLessEqual(len(raw.encode()), 4096)
        self.assertEqual(pack["basis"], "prompts")
        self.assertNotIn("sk-ant", raw)
        self.assertEqual(pack["last_prompts"][-1][1], "the sync still fails, same error")


class TestServer(Fixture):
    def test_auth(self) -> None:
        from http.server import ThreadingHTTPServer

        from vibestrata import server
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)  # type: ignore[arg-type]
        port = httpd.server_address[1]
        app = server.App(port)
        httpd.RequestHandlerClass = server.make_handler(app)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            def req(method, path, headers=None, body=None):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
                c.request(method, path, body=body, headers=h)
                r = c.getresponse()
                out = r.status, dict(r.getheaders()), r.read()
                c.close()
                return out

            self.assertEqual(req("GET", "/api/scan")[0], 401)
            self.assertEqual(req("GET", "/", {"Host": f"evil.com:{port}"})[0], 421)
            self.assertEqual(req("GET", "/?t=wrong")[0], 403)
            st, hdrs, _ = req("GET", f"/?t={app.token}")
            self.assertEqual(st, 302)
            cookie = hdrs["Set-Cookie"].split(";")[0]
            self.assertEqual(req("GET", "/api/scan", {"Cookie": cookie})[0], 200)
            st, _, body = req("GET", "/", {"Cookie": cookie})
            self.assertIn(app.token.encode(), body)
            # mutation without header token → forbidden even with cookie
            self.assertEqual(req("POST", "/api/pin", {"Cookie": cookie, "Content-Type": "application/json"}, b"{}")[0], 403)
            ok = {"Cookie": cookie, "X-Vibestrata-Token": app.token, "Content-Type": "application/json"}
            self.assertEqual(req("POST", "/api/pin", {**ok, "Origin": "http://evil.com"}, b"{}")[0], 403)
            self.assertEqual(req("POST", "/api/pin", ok, b'{"id":"abc","pinned":true}')[0], 200)
            # unknown project paths are refused before any guard runs
            body = json.dumps({"items": [{"project": "/etc", "path": "/etc/hosts", "kind": "file"}]}).encode()
            self.assertEqual(req("POST", "/api/quarantine", ok, body)[0], 404)
            self.assertEqual(req("GET", "/static/../server.py", {"Cookie": cookie})[0], 404)
            # names and notes are yours; empty clears them
            app.scanner.result = self.result
            pid_ = self.by_name["deck-job"]["id"]
            body = json.dumps({"id": pid_, "title": "  Deck  for ACME ", "note": "send v2 on Monday"}).encode()
            st, _, raw = req("POST", "/api/meta", ok, body)
            self.assertEqual(st, 200)
            got = json.loads(raw)["state"]
            self.assertEqual(got["names"][pid_], {"title": "Deck for ACME"})
            self.assertEqual(got["notes"][pid_]["text"], "send v2 on Monday")
            st, _, raw = req("POST", "/api/meta", ok, json.dumps({"id": pid_, "title": "", "note": ""}).encode())
            got = json.loads(raw)["state"]
            self.assertNotIn(pid_, got["names"])
            self.assertNotIn(pid_, got["notes"])
            self.assertEqual(req("POST", "/api/meta", ok, json.dumps({"id": "nope", "note": "x"}).encode())[0], 404)
            # states: "keep" is gone, "in-use" took its place
            self.assertEqual(req("POST", "/api/override", ok, json.dumps({"id": pid_, "status": "keep"}).encode())[0], 400)
            self.assertEqual(req("POST", "/api/override", ok, json.dumps({"id": pid_, "status": "in-use"}).encode())[0], 200)
            req("POST", "/api/override", ok, json.dumps({"id": pid_, "status": None}).encode())
            # a whole project goes only as itself
            body = json.dumps({"items": [{"project": str(self.home / "deck-job"), "path": str(self.home), "kind": "project"}]}).encode()
            self.assertEqual(req("POST", "/api/remove", ok, body)[0], 409)
            # your own files are moved only if the survey listed them
            body = json.dumps({"items": [{"project": str(self.home / "deck-job"), "path": str(self.home / "deck-job/README.md"), "kind": "file"}]}).encode()
            self.assertEqual(req("POST", "/api/quarantine", ok, body)[0], 409)
            # hostile bytes never crash the handler
            self.assertEqual(req("GET", "/api/scan", {"Cookie": "vibestrata=\u00e9; " + cookie})[0], 200)
            self.assertEqual(req("GET", "/?t=%C3%A9")[0], 403)
            self.assertEqual(req("POST", "/api/pin", {**ok, "Content-Length": "-1"}, b"")[0], 400)
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestCli(Fixture):
    def run_cli(self, *argv: str) -> str:
        import contextlib
        import io

        from vibestrata import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(list(argv)), 0)
        return buf.getvalue()

    def test_summary_and_sweep_roundtrip(self) -> None:
        out = self.run_cli("scan", "--summary")
        self.assertIn("## vibestrata survey", out)
        self.assertIn("**Seed deck (`deck-job`)**", out)          # readable name, folder alongside
        self.assertIn("**codex-app** (30d) — “the sync still fails, same error”", out)
        self.assertIn("leftovers in finished projects", out)
        # make py-app look fossil so the sweep has something to do
        from vibestrata.config import update_state
        pid = self.by_name["py-app"]["id"]
        update_state(lambda st: st["overrides"].__setitem__(pid, "closed"))
        dry = self.run_cli("sweep", "--max-age", "0")
        self.assertIn("py-app/.venv", dry)
        self.assertIn("Dry run", dry)
        self.assertTrue((self.home / "py-app/.venv").exists())
        applied = self.run_cli("sweep", "--apply", "--max-age", "1")
        self.assertIn("Undo any time:", applied)
        self.assertFalse((self.home / "py-app/.venv").exists())
        op_id = applied.strip().split()[-1]
        self.run_cli("undo", op_id)
        self.assertTrue((self.home / "py-app/.venv/pyvenv.cfg").exists())
        update_state(lambda st: st["overrides"].pop(pid, None))

    def test_label_and_review(self) -> None:
        from vibestrata.config import update_state
        self.run_cli("scan", "--max-age", "0")
        out = self.run_cli("label", "codex-app", "--state", "paused", "--confidence", "med", "--why", "waiting on an API key")
        self.assertIn("blocked → paused", out)
        from vibestrata.config import load_state
        self.assertEqual(load_state()["labels"][self.by_name["codex-app"]["id"]]["state"], "paused")
        self.assertIn("paused", self.run_cli("scan", "--max-age", "1"))
        self.run_cli("label", "codex-app", "--clear")
        # the triage skill writes JSON on stdin; the unsaved-work gate holds for it too
        import io
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps([{"project": "codex-app", "state": "closed", "confidence": "high",
                                             "why": "$(rm -rf ~) is just text", "next_action": "archive_whole"}]))
        try:
            out = self.run_cli("label", "--stdin")
        finally:
            sys.stdin = old_stdin
        self.assertIn("next: ask", out)  # code without git → never archive
        lab = load_state()["labels"][self.by_name["codex-app"]["id"]]
        self.assertEqual((lab["state"], lab["why"]), ("closed", "$(rm -rf ~) is just text"))
        self.run_cli("label", "codex-app", "--clear")
        # 0.1's age statuses are dropped, not silently half-honoured
        update_state(lambda st: st["overrides"].__setitem__("legacy", "fossil"))
        self.assertNotIn("legacy", load_state()["overrides"])
        q = json.loads(self.run_cli("review", "--queue", "--json", "--min-prompts", "0", "--max-age", "1"))
        self.assertTrue(all("id" in r for r in q))
        update_state(lambda st: st["overrides"].__setitem__("legacy2", "keep"))
        self.assertEqual(load_state()["overrides"]["legacy2"], "in-use")
        update_state(lambda st: st["overrides"].pop("legacy2", None))
        out = self.run_cli("label", "report", "--state", "delivered", "--title", "Quarterly report", "--summary", "Q3 numbers for the board")
        lab = load_state()["labels"][self.by_name["report"]["id"]]
        self.assertEqual((lab["title"], lab["summary"]), ("Quarterly report", "Q3 numbers for the board"))
        self.run_cli("label", "report", "--clear")
        ev = json.loads(self.run_cli("evidence", "deck-job", "--max-age", "1"))
        self.assertEqual(ev["rules"]["state"], "delivered")


class TestIdentity(unittest.TestCase):
    """Readable names for folders called 0614 or udo."""

    def test_titles_from_deliverables(self) -> None:
        import unicodedata

        from vibestrata import identity
        nfd = unicodedata.normalize("NFD", "out/우도_가족여행_가이드북_v2.pdf")  # how macOS hands it over
        self.assertEqual(identity.title_from_file(nfd, "udo"), "우도 가족여행 가이드북")
        self.assertEqual(identity.title_from_file("AI_전략_워크숍_보고서_20260614.pdf", "0614"), "AI 전략 워크숍 보고서")
        for junk in ("sample-seoul-2020.pdf", "s17.mp4", "deck.pdf", "udo.pdf", "v3_part1.pptx"):
            self.assertEqual(identity.title_from_file(junk, "udo"), "", junk)

    def test_docs_skip_boilerplate_and_meta(self) -> None:
        from vibestrata import identity
        docs = [("CLAUDE.md", "@AGENTS.md\n"),
                ("README.md", "This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](x).\n\n## Getting Started\n"),
                ("AGENTS.md", "# 하네스: 우도 여행 — 프로젝트 SSOT\n\n이 파일이 본 프로젝트의 SSOT다.\n\n**목표**: 가족 여행 가이드북을 PDF로 만든다.\n")]
        title, para = identity.describe(docs)
        self.assertEqual(para, "가족 여행 가이드북을 PDF로 만든다.")
        self.assertEqual(identity.title_from_doc(title, "udo"), "우도 여행")
        self.assertEqual(identity.title_from_doc("Install", "x"), "")

    def test_html_readme(self) -> None:
        from vibestrata import identity
        readme = ('<p align="center"><img src="https://x/shot.png" alt="the dashboard" width="100%"></p>\n\n'
                  '<h1 align="center"><i>vibestrata</i></h1>\n'
                  '<p align="center"><b>Your AI-made projects, at a glance.</b><br>\n'
                  'What each one is &amp; where you left off,<br>\n'
                  'on a local dashboard.</p>\n\n'
                  '<p align="center"><a href="README.ko.md">한국어</a> · <a href="#install">Install</a></p>\n\n'
                  '<!--\nbadges go here\n-->\n\n## Install\n\nnpx vibestrata\n')
        title, para = identity.describe([("README.md", readme)])
        self.assertEqual(title, "vibestrata")
        self.assertEqual(para, "Your AI-made projects, at a glance. What each one is & where you left off, on a local dashboard.")
        self.assertEqual(identity.name({"name": "cleanup-project", "doc_title": title, "doc_para": para})["title"], "vibestrata")
        # comparisons in plain Markdown are not tags
        self.assertEqual(identity.describe([("README.md", "# T\n\nruns if a < b and c > d, fast\n")])[1], "runs if a < b and c > d, fast")

    def test_first_real_ask(self) -> None:
        from vibestrata import identity
        asks = [[1, "claude logout"], [2, "쓸수 있는 스킬 확인"], [3, "codex cli 설치해"],
                [4, "[Pasted text #1 +3 lines] 이걸로 내 이력서를 영어로 만들어줘"]]
        self.assertEqual(identity.first_ask(asks), "이걸로 내 이력서를 영어로 만들어줘")

    def test_links(self) -> None:
        from vibestrata import gitinfo, identity
        self.assertEqual(gitinfo.web_url("git@github.com:me/site.git"), "https://github.com/me/site")
        self.assertEqual(gitinfo.web_url("https://me:tok@github.com/me/site.git"), "https://github.com/me/site")
        self.assertEqual(gitinfo.web_url("/srv/git/site.git"), "")
        docs = [("README.md", "Deploy your app on [Vercel](https://vercel.com/new?utm_source=x).\n\n- 배포: https://site-abc.vercel.app\n")]
        self.assertEqual(identity.links("/nonexistent", docs, {"remote_url": "https://github.com/me/site"}),
                         {"repo": "https://github.com/me/site", "site": "https://site-abc.vercel.app"})


class TestManage(Fixture):
    """Tools in use, names on the survey, whole projects to the Trash and back."""

    def test_survey_names_and_kinds(self) -> None:
        deck = self.by_name["deck-job"]
        self.assertEqual((deck["title"], deck["title_src"], deck["kind"]), ("Seed deck", "doc", "doc"))
        cx = self.by_name["codex-app"]
        self.assertEqual(cx["summary"], "build a todo app with offline sync")
        self.assertEqual((cx["claude"]["session"]["id"], cx["claude"]["session"]["source"]), ("cx1", "codex"))

    def test_in_use(self) -> None:
        from vibestrata import segment
        th = {"active": 14, "cooling": 45, "dormant": 120}
        now = time.time()
        base = {"name": "tool", "path": "/x/tool", "chips": [], "flags": {}, "signals": {}, "claude": {}, "git": None}
        idle_tool = {**base, "days": 90, "tool": "skill: tool"}
        self.assertEqual(segment.classify(idle_tool, th, None)[0], "in-use")
        stuck_tool = {**idle_tool, "signals": {"complaint": {"ts": now - 90 * 86400, "text": "still broken", "kind": "persist"}}}
        self.assertEqual(segment.classify(stuck_tool, th, None)[0], "blocked")
        # outputs keep coming while the code sits still: a pipeline in use
        pipeline = {**base, "days": 1, "code_files": 40, "code_last": now - 60 * 86400,
                    "deliverables": {"count": 9, "newest": now - 86400}}
        self.assertEqual(segment.classify(pipeline, th, None)[0], "in-use")
        self.assertEqual(segment.classify({**pipeline, "code_last": now - 86400}, th, None)[0], "active")

    def test_skill_symlink_marks_a_tool(self) -> None:
        from vibestrata import segment
        skills = self.claude / "skills"
        skills.mkdir(parents=True, exist_ok=True)
        write(self.home / "kit/skills/kit-skill/SKILL.md", "---\nname: kit-skill\n---\n")
        os.symlink(str(self.home / "kit/skills/kit-skill"), str(skills / "kit-skill"))
        try:
            p = {**self.by_name["kit"], "flags": {}}
            p["flags"]["registered"] = segment.references(p["path"], segment.registry_sources())
            self.assertIn("skill: kit-skill", p["flags"]["registered"])
            self.assertEqual(segment.is_tool(p), "skill: kit-skill")
        finally:
            os.unlink(skills / "kit-skill")

    def test_project_to_trash_and_back(self) -> None:
        trash = self.home / ".Trash"
        trash.mkdir(exist_ok=True)
        proj = self.home / "old-site"
        write(proj / "index.html", "<h1>old</h1>")
        write(trash / "old-site/already.txt", "a different old-site is already in the Trash")
        p = str(proj)
        with self.assertRaises(self.actions.GuardError):
            self.actions.check_item(p, str(self.home), "project")
        with self.assertRaises(self.actions.GuardError):
            self.actions.check_item(str(self.home), str(self.home), "project")
        op = self.actions.quarantine([{"project": p, "path": p, "kind": "project", "bytes": 10}], "t")
        if sys.platform == "darwin":
            self.assertEqual(op["to"], "trash")
            dst = Path(op["items"][0]["dst"])
            self.assertEqual(dst.parent, trash)
            self.assertTrue(dst.name.startswith("old-site (vibestrata"))
            self.assertTrue((dst / "index.html").exists())
        self.assertFalse(proj.exists())
        self.actions.restore(op["id"])
        self.assertTrue((proj / "index.html").exists())
        self.assertTrue((trash / "old-site/already.txt").exists())  # never touched

    def test_overview_and_situation(self) -> None:
        from vibestrata import brief
        out = brief.overview(self.result, {}, lang="en", width=100)
        self.assertIn(f"{len(self.result['projects'])} projects", out)
        self.assertIn("Stuck on a problem", out)
        self.assertIn("“the sync still fails, same error”", out)
        lines = brief.situation(self.by_name["deck-job"], {"notes": {self.by_name["deck-job"]["id"]: {"text": "send v3"}}}, "en")
        self.assertTrue(any("wrapped it up" in x for x in lines))
        self.assertIn("Your note: send v3", lines)
        self.assertEqual(brief._fit("가나다라", 5), "가나…")

    def test_session_hook(self) -> None:
        import contextlib
        import io

        from vibestrata import brief, cli
        from vibestrata.config import state_dir, write_json
        write_json(state_dir() / "scan.json", self.result)
        away = brief.hook_payload(self.result, {}, str(self.home / "deck-job" / "out"))
        self.assertIn("Seed deck", away["systemMessage"])
        self.assertEqual(away["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIsNone(brief.hook_payload(self.result, {}, str(self.home / "node-app")))  # you were here yesterday
        self.assertIsNone(brief.hook_payload(self.result, {}, str(self.tmp)))                # not a project
        buf, old = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO(json.dumps({"cwd": str(self.home / "deck-job"), "source": "startup"}))
        try:
            with contextlib.redirect_stdout(buf):
                self.assertEqual(cli.main(["here", "--hook"]), 0)
        finally:
            sys.stdin = old
        self.assertIn("Seed deck", json.loads(buf.getvalue())["systemMessage"])
        sys.stdin = io.StringIO("not json")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.assertEqual(cli.main(["here", "--hook"]), 0)  # never in the way
        finally:
            sys.stdin = old

    def test_session_hook_surveys_on_its_own(self) -> None:
        import contextlib
        import io
        from unittest import mock

        from vibestrata import cli
        from vibestrata.config import state_dir, write_json
        scan_json, lock = state_dir() / "scan.json", state_dir() / "survey.lock"

        def hook(cwd: Path) -> str:
            buf, old = io.StringIO(), sys.stdin
            sys.stdin = io.StringIO(json.dumps({"cwd": str(cwd)}))
            try:
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(cli.main(["here", "--hook"]), 0)
            finally:
                sys.stdin = old
            return buf.getvalue()

        saved = scan_json.read_bytes() if scan_json.exists() else None
        try:
            scan_json.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
            # plugin installed, dashboard never opened: survey in the background, say so once
            with mock.patch("subprocess.Popen") as popen:
                out = hook(self.home / "deck-job")
                self.assertIn("npx vibestrata", json.loads(out)["systemMessage"])
                self.assertIn("--background", popen.call_args[0][0])
                self.assertTrue(lock.exists())
                self.assertEqual(hook(self.home / "deck-job"), "")  # one survey at a time
                self.assertEqual(popen.call_count, 1)
            # the background survey writes the cache and frees the lock
            self.assertEqual(cli.main(["scan", "--background"]), 0)
            self.assertFalse(lock.exists())
            self.assertTrue(json.loads(scan_json.read_text())["projects"])
            write_json(scan_json, self.result)
            with mock.patch("subprocess.Popen") as popen:
                self.assertIn("Seed deck", json.loads(hook(self.home / "deck-job"))["systemMessage"])
                popen.assert_not_called()  # fresh enough
                write_json(scan_json, dict(self.result, scanned_at=time.time() - 13 * 3600))
                self.assertIn("Seed deck", json.loads(hook(self.home / "deck-job"))["systemMessage"])
                self.assertEqual(popen.call_count, 1)  # stale: answer from the cache, refresh behind
        finally:
            lock.unlink(missing_ok=True)
            if saved is not None:
                scan_json.write_bytes(saved)

    def test_open_file_stays_inside(self) -> None:
        with self.assertRaises(self.actions.GuardError):
            self.actions.open_in(str(self.home / "deck-job"), "file", rel="../kit/HANDOFF.md")
        with self.assertRaises(self.actions.GuardError):
            self.actions.open_in(str(self.home / "deck-job"), "nope")


class TestApp(Fixture):
    def test_instance_roundtrip(self) -> None:
        from vibestrata import app
        self.assertIsNone(app.running_instance())
        app.write_instance(1, "tok")
        self.assertEqual(oct(os.stat(app.instance_file()).st_mode & 0o777), "0o600")
        self.assertIsNone(app.running_instance())  # nothing listens on port 1
        app.clear_instance()
        self.assertFalse(app.instance_file().exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS bundle")
    def test_install_app_bundle(self) -> None:
        from vibestrata import app
        target = self.tmp / "Vibestrata.app"
        app.install_app(target)
        launcher = target / "Contents/MacOS/vibestrata"
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertIn("-m vibestrata --app-launch", launcher.read_text())
        self.assertTrue((target / "Contents/Resources/vibestrata.icns").exists())
        r = subprocess.run(["plutil", "-lint", str(target / "Contents/Info.plist")], capture_output=True)
        self.assertEqual(r.returncode, 0)


class TestRules(unittest.TestCase):
    """The segmentation rules on their own, no disk."""

    def test_prompt_signals(self) -> None:
        from vibestrata.segment import prompt_signals
        t0 = 1_700_000_000
        sig = lambda *ps: prompt_signals([(t0 + i * 60, x) for i, x in enumerate(ps)])  # noqa: E731
        self.assertEqual(sig("pdf 가 또 안열려", "여전히 꺠져")["complaint"]["kind"], "persist")
        self.assertEqual(sig("여전히 깨져", "중간저장해")["complaint"]["kind"], "persist")  # ritual skipped
        self.assertNotIn("complaint", sig("슬라이드8 글씨가 깨져 다시 만들어줘", "아니 8번째", "중간저장해"))  # a fix request mid-way
        self.assertEqual(sig("홈버튼 안눌려", "깃허브 썸네일 안뜨자나")["complaint"]["kind"], "report")
        self.assertIn("handoff_words", sig("좋아, 고생했어, 나머진 편집은 내가할게"))
        self.assertNotIn("handoff_words", sig("만족해. 일단 중간저장하고 추후에 따로 하자"))
        self.assertTrue(sig("고생했어. 내일 계속하자.").get("continue"))
        self.assertIn("satisfied_words", sig("지금 맘에 들어"))
        for req_ in ("작업 완료되면 커밋해줘", "나머지 슬라이드도 내가 준 스타일로 해줘", "작업끝? readme 도?", "만족하는데, 더 강화해야할것 같아"):
            got = sig(req_)
            self.assertNotIn("handoff_words", got, req_)
            self.assertNotIn("satisfied_words", got, req_)
        self.assertIn("handoff_words", sig("지금 맘에 들어. 이 스킬 저장해줘."))

    def test_classify_order_and_gate(self) -> None:
        from vibestrata import segment
        th = {"active": 14, "cooling": 45, "dormant": 120}
        base = {"id": "x", "name": "x", "days": 60, "chips": [], "git": None, "claude": {"meaningful": 30, "p_first": 1, "p_last": 2},
                "flags": {"registered": []}, "deliverables": {}, "signals": {}}
        st = lambda **kw: segment.classify({**base, **kw}, th, None)[0]  # noqa: E731
        self.assertEqual(st(days=3), "active")
        self.assertEqual(st(), "paused")
        comp = {"complaint": {"ts": 100, "text": "still broken", "kind": "persist"}}
        self.assertEqual(st(signals=comp), "blocked")
        self.assertEqual(st(signals=comp, git={"last_commit": 100 + 3600}), "paused")  # fixed later
        self.assertEqual(st(chips=["shipped"], git={"branch": "main"}), "delivered")
        self.assertEqual(st(chips=["shipped"], git={"branch": "main"}, signals={"continue": True}), "paused")
        self.assertEqual(segment.classify(base, th, {"name": "x2"})[0], "superseded")
        self.assertEqual(segment.classify({**base, "flags": {"registered": ["~/CLAUDE.md"]}}, th, {"name": "x2"})[0], "paused")
        self.assertEqual(st(claude={"meaningful": 4, "p_first": 0, "p_last": 3600}), "one-shot")
        p = {**base, "flags": {"unsaved": {"dirty": 2}}}
        self.assertFalse(segment.plan("delivered", p, th)["byproducts"])
        self.assertTrue(segment.plan("delivered", base, th)["byproducts"])
        self.assertEqual(segment.plan("paused", {**base, "days": 20}, th), {"regen": False, "byproducts": False})
        self.assertEqual(segment.plan("paused", base, th), {"regen": True, "byproducts": False})

    def test_version_chains(self) -> None:
        from vibestrata.segment import version_chains
        f = lambda n, m, b=500_000: {"path": "/p/" + n, "mtime": m, "bytes": b}  # noqa: E731
        out = version_chains([f("r_v1.pdf", 1), f("r_v2.pdf", 2), f("r_v3.pdf", 3), f("r.pdf", 0), f("slide-1.png", 1), f("slide-2.png", 2),
                              f("tiny_v1.pdf", 1, 10), f("tiny_v2.pdf", 2, 10), f("deck.pptx", 5), f("deck_regen2.pptx", 4)], "/p")
        # newest (deck.pptx) and highest-numbered (deck_regen2) are both kept
        self.assertEqual(sorted(o["rel"] for o in out), ["r.pdf", "r_v1.pdf", "r_v2.pdf"])
        self.assertEqual({o["keep"] for o in out if o["rel"].startswith("r")}, {"r_v3.pdf"})

    def test_resume_snippets(self) -> None:
        from vibestrata.segment import _snippet
        log = "# Changelog\n\n## [1.1.0] - 2026-07-18\n### Added\n- catalog of terms\n\n## [1.0.0] - 2026-06-01\n- first\n"
        self.assertIn("catalog of terms", _snippet("CHANGELOG.md", log))
        self.assertIn("2026-07-18", _snippet("CHANGELOG.md", log))
        table = "# 변경 이력\n\n| 날짜 | 내용 |\n|---|---|\n| 2026-08-01 | 첫 배포 |\n| 2026-09-04 | robots.txt 추가 |\n"
        self.assertIn("robots.txt", _snippet("CHANGELOG.md", table))
        ho = "# Status\nall good\n\n## 다음 할 일\n- 렌더\n- 업로드\n"
        self.assertTrue(_snippet("HANDOFF.md", ho).startswith("다음 할 일: 렌더"))

    def test_redact(self) -> None:
        from vibestrata.claude import redact
        self.assertEqual(redact("AIzaSyDb1PhhzMjWOs-jzsBwUj0gdtI1bFy9Wj4"), "[redacted]")
        self.assertEqual(redact("see verifiable-graphrag-manuscript-ko-v1.pdf"), "see verifiable-graphrag-manuscript-ko-v1.pdf")


class TestSessionWork(unittest.TestCase):
    """A session opened in one folder that worked in another belongs to the other."""

    def setUp(self) -> None:
        from unittest import mock
        self.tmp = Path(tempfile.mkdtemp(prefix="vibestrata-work-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"VIBESTRATA_CLAUDE_DIR": str(self.tmp / "claude"), "VIBESTRATA_HOME": str(self.tmp / "state"),
                                           "VIBESTRATA_CODEX_DIR": str(self.tmp / "no-codex")})
        env.start()
        self.addCleanup(env.stop)
        self.launch, self.target = str(self.tmp / "home/launcher"), str(self.tmp / "home/studio")
        now = time.time()
        rows = []

        def session(sid: str, edits_in: list, cds: int = 0, prompts: tuple = ()) -> None:
            lines = [{"type": "user", "cwd": self.launch, "sessionId": sid}]
            for i, d in enumerate(edits_in):
                lines.append({"type": "assistant", "cwd": self.launch, "message": {"content": [
                    {"type": "tool_use", "id": f"t{i}", "name": ["Edit", "Write"][i % 2],
                     "input": ({"replace_all": False} if i % 2 == 0 else {}) | {"file_path": f"{d}/f{i}.py", "content": "x" * 50}}]}})
            for i in range(cds):
                lines.append({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": f"c{i}", "name": "Bash", "input": {"command": f"cd {self.target}/videos && make", "description": "render"}}]}})
            write(self.tmp / "claude/projects/-enc-launcher" / f"{sid}.jsonl",
                  "\n".join(json.dumps(x, separators=(",", ":")) for x in lines) + "\n")
            for k, text in enumerate(prompts):
                rows.append({"display": text, "timestamp": int((now - 3600 + k) * 1000), "project": self.launch, "sessionId": sid})

        # worked in the studio: 6 writes there, 1 at home base, plus cd's
        session("moved", [self.target + "/tools"] * 6 + [self.launch], cds=3, prompts=("render episode 7 and upload it",))
        # glanced at the studio: 2 writes there, 10 at home base
        session("stayed", [self.target] * 2 + [self.launch + "/src"] * 10, prompts=("fix the launcher config file",))
        write(self.tmp / "claude/history.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")

    def place(self):
        from vibestrata import claude, scan
        idx = claude.read_all()
        self.assertEqual(set(idx.movers), {"moved"})                   # 2 writes elsewhere is a glance
        self.assertIn(self.launch, idx.hints())
        out = claude.relocate(idx, [self.launch, self.target])
        return (scan._claude_for(self.launch, out, [self.launch, self.target]),
                scan._claude_for(self.target, out, [self.launch, self.target]))

    def test_credited_where_the_work_happened(self) -> None:
        here, there = self.place()
        self.assertEqual(there.last_prompt[1], "render episode 7 and upload it")
        self.assertEqual(there.sessions, {"moved"})
        self.assertEqual(there.last_session[2], self.launch)            # resume still opens where it started
        self.assertEqual(there.links(), {"from": [[self.launch, 1]]})
        self.assertEqual(here.last_prompt[1], "fix the launcher config file")
        self.assertEqual(here.sessions, {"stayed"})
        self.assertEqual(here.links(), {"to": [[self.target, 1]]})

    def test_chunk_boundaries(self) -> None:
        from unittest import mock

        from vibestrata import claude
        f = self.tmp / "claude/projects/-enc-launcher/moved.jsonl"
        with open(f, "rb") as fh:
            whole = claude._work(fh, fh.read(16384))
        with mock.patch.object(claude, "CHUNK", 37), mock.patch.object(claude, "OVERLAP", 400), open(f, "rb") as fh:
            tiny = claude._work(fh, fh.read(64))
        self.assertEqual(whole, tiny)
        self.assertEqual(whole, {self.target + "/tools": 6, self.launch: 1, self.target + "/videos": 3})

    def test_shown_both_ways(self) -> None:
        from vibestrata import brief
        here, there = self.place()
        self.assertEqual(brief.link_lines({"ai_links": there.links()}),
                         ["Worked on from launcher (sessions opened there: 1)."])
        self.assertEqual(brief.link_lines({"ai_links": here.links()}, "ko"),
                         ["여기서 연 세션은 실제로 studio 폴더에서 작업했어요 (세션 1개)."])
        # a folder whose every session moved away keeps the link, not an empty AI history
        from vibestrata.claude import Activity
        only = Activity()
        only.work_in[self.target] = 1
        self.assertTrue(only.empty())
        self.assertFalse(here.empty())
        from concurrent.futures import ThreadPoolExecutor

        from vibestrata import scan
        from vibestrata.config import load_config
        write(Path(self.launch) / "README.md", "# Launcher")
        with ThreadPoolExecutor(2) as pool:
            got = scan.survey(self.launch, str(self.tmp / "home"), None, load_config(), only, pool)
        self.assertIsNone(got["claude"])
        self.assertEqual(got["ai_links"], {"to": [[self.target, 1]]})


class TestLeftovers(unittest.TestCase):
    """The leftovers figure and --byproducts say what sweep can actually take."""

    @staticmethod
    def proj(i: int, state: str, nbytes: int, unsaved: bool = False) -> dict:
        return {"id": f"p{i}", "name": f"p{i}", "path": f"/p{i}", "state": state, "days": 90, "candidates": [],
                "byproducts": {"bytes": nbytes, "items": [{"path": f"/p{i}/_workspace", "rel": "_workspace", "bytes": nbytes,
                                                          "kind": "workspace", "auto": True}]},
                "flags": {"unsaved": {"dirty": 3}} if unsaved else {}}

    def test_header_counts_finished_projects_only(self) -> None:
        from vibestrata import brief
        st = {"overrides": {}, "labels": {}, "pins": []}
        ps = [self.proj(1, "delivered", 100), self.proj(2, "paused", 10_000), self.proj(3, "superseded", 20)]
        self.assertEqual(brief.finished_leftovers(ps, st), 120)
        res = {"projects": ps, "totals": {"bytes": 1, "reclaim": 0, "byproducts": 10_120}}
        self.assertIn("120 B leftovers in finished projects", brief.overview(res, st, "en"))

    def test_byproducts_withheld(self) -> None:
        from vibestrata import cli
        th = {"active": 14, "cooling": 45, "dormant": 120}
        st = {"overrides": {}, "labels": {}, "pins": []}
        res = {"thresholds": th, "projects": [self.proj(1, "delivered", 100, True), self.proj(2, "superseded", 50, True)]}
        self.assertEqual(cli.sweep_items(res, st, None, True), [])
        self.assertEqual(cli.byproducts_withheld(res, st),
                         "No byproducts eligible: the 2 finished projects with leftovers all have unsaved work. "
                         "Commit and push first, or tick them in the dashboard.")
        st["pins"] = ["p2"]
        self.assertIn("1 has unsaved work, 1 pinned", cli.byproducts_withheld(res, st))
        self.assertEqual(cli.byproducts_withheld({"thresholds": th, "projects": [self.proj(3, "paused", 9)]}, st),
                         "No byproducts eligible: no finished project has leftovers.")
        # once the work is saved, it is offered
        res["projects"][0]["flags"] = {}
        self.assertEqual([i["path"] for i in cli.sweep_items(res, st, None, True) if i["group"] == "byproduct"], ["/p1/_workspace"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
