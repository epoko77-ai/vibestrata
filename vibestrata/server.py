"""Local web server. Binds to 127.0.0.1 only.

Security model:
* A random token is minted per launch. The browser is opened with
  ``?t=<token>``; the server swaps it for a SameSite=Strict cookie.
* Every request must carry the cookie; every mutation must also carry the
  token in an ``X-Vibestrata-Token`` header (injected into the page), so other
  sites cannot forge requests.
* The ``Host`` header must be ``127.0.0.1:<port>`` or ``localhost:<port>``
  (blocks DNS-rebinding).
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__, actions, scan, segment
from .config import DEFAULT_CONFIG, load_config, load_state, update_state
from .scan import Scanner

STATIC = Path(__file__).parent / "static"
MAX_BODY = 2 * 1024 * 1024


def _same(a: str, b: str) -> bool:
    """Constant-time compare that never raises on odd (non-ASCII) input."""
    return hmac.compare_digest(a.encode("utf-8", "surrogateescape"), b.encode("utf-8"))


class App:
    def __init__(self, port: int, demo: bool = False) -> None:
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.scanner = Scanner(load=not demo)
        self.cfg = dict(DEFAULT_CONFIG) if demo else load_config()
        self.op_lock = threading.Lock()
        self.demo = None
        self.demo_state: Dict[str, Any] = {"pins": [], "overrides": {}, "labels": {}, "names": {}, "notes": {}}
        self.app_mode = False          # window lifetime == server lifetime
        self.last_ping = 0.0
        self.on_quit = lambda: None
        if demo:
            from . import demo as demo_mod
            self.scanner.result = demo_mod.build()
            self.demo = demo_mod.DemoActions(self.scanner.result)

    def state(self) -> Dict[str, Any]:
        return self.demo_state if self.demo else load_state()

    def set_state(self, fn) -> Dict[str, Any]:
        if self.demo:
            fn(self.demo_state)
            return self.demo_state
        return update_state(fn)

    def known_project(self, path: str) -> Optional[Dict[str, Any]]:
        path = os.path.normpath(path or "")
        return next((p for p in self.scanner.result.get("projects", []) if p["path"] == path), None)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"vibestrata/{__version__}"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            if os.environ.get("VIBESTRATA_DEBUG"):
                super().log_message(fmt, *args)

        # ── helpers ──
        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data: Any, code: int = 200) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _err(self, code: int, msg: str) -> None:
            self._json({"error": msg}, code)

        def _host_ok(self) -> bool:
            host = self.headers.get("Host", "")
            return host in (f"127.0.0.1:{app.port}", f"localhost:{app.port}")

        def _cookie_ok(self) -> bool:
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "vibestrata" and _same(v, app.token):
                    return True
            return False

        def _body(self) -> Dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > MAX_BODY:
                raise ValueError("too-large")
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("bad-body")
            return data

        # ── GET ──
        def do_GET(self) -> None:  # noqa: N802
            self._safe(self._get)

        def do_POST(self) -> None:  # noqa: N802
            self._safe(self._post)

        def _safe(self, fn) -> None:
            try:
                fn()
            except Exception as exc:  # never drop the connection silently
                if os.environ.get("VIBESTRATA_DEBUG"):
                    import traceback
                    traceback.print_exc()
                try:
                    self._err(500, f"internal: {type(exc).__name__}")
                except Exception:
                    pass

        def _get(self) -> None:
            if not self._host_ok():
                return self._err(421, "bad host")
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            if url.path == "/api/health":  # unauthenticated, reveals nothing
                return self._json({"app": "vibestrata", "version": __version__})
            if url.path == "/" and "t" in qs:
                if _same(qs["t"][0], app.token):
                    return self._send(302, b"", "text/plain", {
                        "Location": "/",
                        "Set-Cookie": f"vibestrata={app.token}; Path=/; HttpOnly; SameSite=Strict",
                    })
                return self._err(403, "bad token")
            if not self._cookie_ok():
                msg = ("<!doctype html><meta charset=utf-8><title>vibestrata</title>"
                       "<body style='font:16px/1.6 ui-monospace,monospace;padding:4rem;background:#F1ECE2'>"
                       "This vibestrata session is locked. Open the link printed in your terminal "
                       "(it contains a one-time token).</body>")
                return self._send(401, msg.encode(), "text/html; charset=utf-8")
            if url.path == "/":
                html = (STATIC / "index.html").read_text(encoding="utf-8")
                html = html.replace("__VIBESTRATA_TOKEN__", app.token).replace("__VIBESTRATA_VERSION__", __version__)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if url.path == "/api/scan":
                st = app.state()
                return self._json({
                    "result": app.scanner.result, "progress": app.scanner.progress,
                    "state": st, "version": __version__, "app_mode": app.app_mode,
                    "config": {k: app.cfg.get(k) for k in ("thresholds", "roots", "big_file_mb", "editor_command")},
                })
            if url.path == "/api/progress":
                return self._json(app.scanner.progress)
            if url.path == "/api/ops":
                return self._json({"ops": app.demo.ops if app.demo else actions.list_ops()})
            if url.path.startswith("/static/"):
                name = url.path[len("/static/"):]
                f = (STATIC / name).resolve()
                if STATIC.resolve() not in f.parents or not f.is_file():
                    return self._err(404, "not found")
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                return self._send(200, f.read_bytes(), ctype)
            return self._err(404, "not found")

        do_HEAD = do_GET

        # ── POST ──
        def _post(self) -> None:
            if not self._host_ok():
                return self._err(421, "bad host")
            if not self._cookie_ok() or not _same(self.headers.get("X-Vibestrata-Token", ""), app.token):
                return self._err(403, "forbidden")
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{app.port}", f"http://localhost:{app.port}"):
                return self._err(403, "bad origin")
            try:
                body = self._body()
            except (ValueError, UnicodeDecodeError) as e:
                return self._err(400, str(e))
            path = urlparse(self.path).path
            try:
                return self._route(path, body)
            except actions.GuardError as e:
                return self._err(409, str(e))

        def _route(self, path: str, body: Dict[str, Any]) -> None:
            if path == "/api/quarantine":
                path = "/api/remove"  # older name
            if app.demo and path in ("/api/scan", "/api/rescan", "/api/remove", "/api/restore", "/api/trash", "/api/open"):
                return self._demo_route(path, body)
            if path == "/api/ping":
                app.last_ping = time.time()
                return self._json({"ok": True})
            if path == "/api/quit":
                threading.Timer(0.3, app.on_quit).start()
                return self._json({"ok": True})
            if path == "/api/scan":
                started = app.scanner.start()
                return self._json({"started": started, "progress": app.scanner.progress})
            if path == "/api/rescan":
                if not app.known_project(body.get("path")):
                    return self._err(404, "unknown project")
                return self._json({"project": app.scanner.rescan_one(os.path.normpath(body["path"]))})
            if path == "/api/remove":
                items = []
                projects = app.scanner.result.get("projects") or []
                whole = set()
                for it in body.get("items") or []:
                    proj = app.known_project(it.get("project"))
                    if not proj:
                        return self._err(404, "unknown project")
                    item = {"project": proj["path"], "path": os.path.normpath(str(it.get("path", ""))),
                            "kind": it.get("kind", "candidate"), "bytes": it.get("bytes", 0)}
                    if item["kind"] == "project":
                        if item["path"] != proj["path"]:
                            return self._err(409, "unknown-item")
                        if any(q["path"].startswith(proj["path"] + os.sep) for q in projects):
                            return self._err(409, "contains-projects")
                        # as the survey saw it: anything you changed since then stops the move
                        item["mtime"] = (proj.get("activity") or {}).get("files") or app.scanner.result.get("scanned_at")
                        item["bytes"] = proj.get("bytes", 0)
                        whole.add(proj["path"])
                    elif item["kind"] != "candidate":
                        # your own files: only what the survey listed, as it saw them
                        known = [*((proj.get("byproducts") or {}).get("items") or []), *(proj.get("big") or [])]
                        hit = next((k for k in known if k["path"] == item["path"]), None)
                        if not hit or (item["kind"] == "workspace" and hit.get("kind") != "workspace"):
                            return self._err(409, "unknown-item")
                        item["mtime"] = hit.get("mtime")
                    items.append(item)
                # a project going whole takes its parts with it
                items = [i for i in items if i["kind"] == "project" or i["project"] not in whole]
                with app.op_lock:
                    op = actions.quarantine(items, str(body.get("label", ""))[:120])
                gone = {i["project"] for i in op.get("items", []) if i["kind"] == "project" and i["state"] == "moved"}
                if gone:
                    app.scanner.drop(gone)
                touched = sorted({i["project"] for i in op.get("items", [])} - gone)
                fresh = [app.scanner.rescan_one(p) for p in touched]
                return self._json({"op": op, "projects": [p for p in fresh if p], "gone": sorted(gone),
                                   "totals": app.scanner.result.get("totals")})
            if path == "/api/restore":
                with app.op_lock:
                    op = actions.restore(str(body.get("id", "")))
                back = {i.get("restored_as") or i["src"] for i in op.get("items", []) if i["kind"] == "project" and i["state"] == "restored"}
                fresh = [app.scanner.readd(b) for b in sorted(back)]
                touched = sorted({i["project"] for i in op.get("items", []) if i["kind"] != "project"})
                fresh += [app.scanner.rescan_one(p) for p in touched if app.known_project(p)]
                return self._json({"op": op, "projects": [p for p in fresh if p], "totals": app.scanner.result.get("totals")})
            if path == "/api/trash":
                with app.op_lock:
                    return self._json({"op": actions.empty_to_trash(str(body.get("id", "")))})
            if path == "/api/pin":
                pid_, on = str(body.get("id", "")), bool(body.get("pinned"))

                def f(st: Dict[str, Any]) -> None:
                    pins = [p for p in st["pins"] if p != pid_]
                    if on:
                        pins.append(pid_)
                    st["pins"] = pins
                return self._json({"state": app.set_state(f)})
            if path == "/api/override":
                pid_, status = str(body.get("id", "")), body.get("status")
                if status not in (None,) + segment.USER_STATES:
                    return self._err(400, "bad status")

                def g(st: Dict[str, Any]) -> None:
                    if status:
                        st["overrides"][pid_] = status
                    else:
                        st["overrides"].pop(pid_, None)
                return self._json({"state": app.set_state(g)})
            if path == "/api/meta":
                pid_ = str(body.get("id", ""))
                if not any(p["id"] == pid_ for p in app.scanner.result.get("projects", [])):
                    return self._err(404, "unknown project")

                def m(st: Dict[str, Any]) -> None:
                    if "title" in body or "summary" in body:
                        cur = dict(st["names"].get(pid_) or {})
                        for k in ("title", "summary"):
                            if k in body:
                                v = " ".join(str(body.get(k) or "").split())[:200 if k == "title" else 400]
                                cur.pop(k, None) if not v else cur.__setitem__(k, v)
                        st["names"].pop(pid_, None) if not cur else st["names"].__setitem__(pid_, cur)
                    if "note" in body:
                        v = str(body.get("note") or "").strip()[:2000]
                        st["notes"].pop(pid_, None) if not v else st["notes"].__setitem__(pid_, {"text": v, "ts": time.time()})
                return self._json({"state": app.set_state(m)})
            if path == "/api/open":
                proj = app.known_project(body.get("path"))
                if not proj:
                    return self._err(404, "unknown project")
                actions.open_in(proj["path"], str(body.get("how", "finder")), app.cfg.get("editor_command", ""),
                                session=(proj.get("claude") or {}).get("session"), rel=str(body.get("rel") or ""),
                                lang="ko" if body.get("lang") == "ko" else "en")
                return self._json({"ok": True})
            return self._err(404, "not found")

        def _demo_route(self, path: str, body: Dict[str, Any]) -> None:
            d = app.demo
            res = app.scanner.result
            if path == "/api/scan":
                return self._json({"started": False, "progress": app.scanner.progress})
            if path in ("/api/rescan", "/api/open"):
                return self._json({"ok": True, "project": app.known_project(body.get("path"))})
            if path == "/api/remove":
                op = d.quarantine(body.get("items") or [], str(body.get("label", "")))
            elif path == "/api/restore":
                op = d.restore(str(body.get("id", "")))
            else:
                op = d.empty_to_trash(str(body.get("id", "")))
            touched = {i["project"] for i in op.get("items", [])}
            gone = sorted({i["project"] for i in op.get("items", []) if i["kind"] == "project" and i["state"] == "moved"})
            return self._json({"op": op, "projects": [p for p in res["projects"] if p["path"] in touched],
                               "gone": gone, "totals": res["totals"]})

    return Handler


def serve(port: int = 7444, window: str = "auto", rescan: bool = False, demo: bool = False) -> None:
    """Run the dashboard.

    window: "auto"  chrome-less app window; closing it quits (default)
            "tab"   a normal browser tab; runs until Ctrl+C
            "none"  no window; runs until Ctrl+C
    """
    import webbrowser

    from . import app as appmod

    if not demo and window != "none":
        inst = appmod.running_instance()
        if inst:  # already running: just bring up another window
            url = f"http://127.0.0.1:{inst['port']}/?t={inst['token']}"
            how = appmod.open_app_window(url) if window == "auto" else (webbrowser.open(url) and "browser-tab")
            print(f"  vibestrata is already running (pid {inst['pid']}) — opened a {how or 'window'}.")
            return

    httpd = None
    for p in (port, 0):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), None)  # type: ignore[arg-type]
            break
        except OSError:
            continue
    assert httpd is not None
    real_port = httpd.server_address[1]
    app = App(real_port, demo=demo)
    httpd.RequestHandlerClass = make_handler(app)
    httpd.daemon_threads = True
    if not demo:
        appmod.write_instance(real_port, app.token)
        if rescan or not app.scanner.result.get("projects") or app.scanner.result.get("version", 1) < scan.SURVEY_VERSION:
            app.scanner.start()
    url = f"http://127.0.0.1:{real_port}/?t={app.token}"
    stop = threading.Event()

    def quit_() -> None:
        if not stop.is_set():
            stop.set()
            threading.Thread(target=httpd.shutdown, daemon=True).start()
    app.on_quit = quit_

    native = window == "auto" and appmod.has_webview()
    if window == "auto":
        app.app_mode = True
        started = time.time()

        def watchdog() -> None:
            # Window closed (no heartbeat) → quit, like closing an app.
            while not stop.wait(3):
                last = app.last_ping or started
                limit = 15 if app.last_ping else 90
                if time.time() - last > limit and not app.scanner.progress.get("running"):
                    quit_()
        if not native:
            threading.Thread(target=watchdog, daemon=True).start()

    print(f"\n  vibestrata {__version__} — local only, nothing leaves this machine" + ("  [DEMO: synthetic data]" if demo else ""))
    if window == "auto":
        print("  Opening the Vibestrata window. Close it to quit (or Ctrl+C here).\n", flush=True)
    else:
        print(f"  ▸ {url}\n  Ctrl+C to stop.\n", flush=True)

    try:
        if native:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            appmod.run_native_window(url, on_closed=quit_)
        else:
            if window == "auto":
                threading.Timer(0.3, lambda: appmod.open_app_window(url)).start()
            elif window == "tab":
                threading.Timer(0.3, lambda: webbrowser.open(url)).start()
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  bye.")
    finally:
        stop.set()
        httpd.server_close()
        if not demo:
            appmod.clear_instance()
