"""Make vibestrata feel like an app, without shipping a browser engine.

* ``vibestrata`` opens a chrome-less window: a native WKWebView/WebView2 window
  when the optional ``pywebview`` is installed, otherwise an installed
  Chromium-family browser in ``--app`` mode (own window, no tabs, no URL
  bar), otherwise a normal browser tab.
* One instance: a second launch re-opens a window on the running server.
* Closing the window ends the server (heartbeat watchdog), like quitting an app.
* ``vibestrata install-app`` puts a Vibestrata.app in ~/Applications (macOS) or a
  .desktop entry (Linux) so it opens from Launchpad / Spotlight / Dock.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .config import home, read_json, state_dir

MAC_BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
    "/Applications/Arc.app/Contents/MacOS/Arc",
]
LINUX_BROWSERS = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                  "microsoft-edge", "brave-browser", "vivaldi"]


# ── single instance ────────────────────────────────────────────────────────

def instance_file() -> Path:
    return state_dir() / "server.json"


def write_instance(port: int, token: str) -> None:
    path = instance_file()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"pid": os.getpid(), "port": port, "token": token}, fh)


def clear_instance() -> None:
    info = read_json(instance_file(), {})
    if info.get("pid") == os.getpid():
        try:
            instance_file().unlink()
        except OSError:
            pass


def running_instance() -> Optional[dict]:
    """The live server from a previous launch, if it still answers."""
    info = read_json(instance_file(), None)
    if not info or not info.get("port"):
        return None
    try:
        c = http.client.HTTPConnection("127.0.0.1", int(info["port"]), timeout=1.5)
        c.request("GET", "/api/health", headers={"Host": f"127.0.0.1:{info['port']}"})
        r = c.getresponse()
        ok = r.status == 200 and json.loads(r.read() or b"{}").get("app") == "vibestrata"
        c.close()
    except (OSError, ValueError):
        return None
    return info if ok else None


# ── windows ────────────────────────────────────────────────────────────────

def has_webview() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except Exception:
        return False


def find_app_browser() -> Optional[str]:
    if os.environ.get("VIBESTRATA_BROWSER"):
        return os.environ["VIBESTRATA_BROWSER"]
    if sys.platform == "darwin":
        for p in MAC_BROWSERS:
            if os.path.exists(p):
                return p
        user = home() / "Applications"
        for p in MAC_BROWSERS:
            alt = str(user / p[len("/Applications/"):])
            if os.path.exists(alt):
                return alt
        return None
    if sys.platform.startswith("win"):
        for base in (os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", ""), os.environ.get("LOCALAPPDATA", "")):
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                p = os.path.join(base, rel)
                if base and os.path.exists(p):
                    return p
        return None
    for name in LINUX_BROWSERS:
        p = shutil.which(name)
        if p:
            return p
    return None


def open_app_window(url: str) -> str:
    """Open ``url`` in a chrome-less window. Returns how it was opened."""
    exe = find_app_browser()
    if exe:
        profile = state_dir() / "window-profile"  # separate profile = separate app window
        args = [exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1440,960",
                "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return "app-window"
        except OSError:
            pass
    import webbrowser
    webbrowser.open(url)
    return "browser-tab"


def run_native_window(url: str, on_closed) -> None:
    """Blocks on the main thread until the native window closes."""
    import webview
    from . import __version__

    webview.create_window(f"vibestrata {__version__}", url, width=1440, height=960, min_size=(900, 640),
                          background_color="#F1ECE2", text_select=True)
    webview.start()
    on_closed()


# ── install as an app ──────────────────────────────────────────────────────

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Vibestrata</string>
  <key>CFBundleDisplayName</key><string>Vibestrata</string>
  <key>CFBundleIdentifier</key><string>io.github.vibestrata</string>
  <key>CFBundleVersion</key><string>{version}</string>
  <key>CFBundleShortVersionString</key><string>{version}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>vibestrata</string>
  <key>CFBundleIconFile</key><string>vibestrata</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><{ui_element}/>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
"""


def _runtime() -> Path:
    """Copy this (stdlib-only) package to ~/.vibestrata/runtime/<version>/ so the
    installed app keeps working no matter how vibestrata was launched — npx and
    uvx caches come and go; this copy does not."""
    from . import __version__

    src = Path(__file__).resolve().parent
    root = state_dir() / "runtime" / __version__
    dest = root / "vibestrata"
    if not dest.exists() or dest.resolve() != src:
        tmp = root / f".incoming-{os.getpid()}"
        shutil.copytree(str(src), str(tmp / "vibestrata"), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if dest.exists():
            shutil.move(str(dest), str(root / f".old-{os.getpid()}"))  # keep, never delete
        os.replace(str(tmp / "vibestrata"), str(dest))
        try:
            tmp.rmdir()
        except OSError:
            pass
    return root


def _python() -> str:
    """A long-lived interpreter (the base of any venv, not a cache path)."""
    base = getattr(sys, "_base_executable", None) or sys.executable
    return os.path.realpath(base) if os.path.exists(base) else sys.executable


def install_app(target: Optional[Path] = None) -> Path:
    from . import __version__

    py = _python()
    runtime = _runtime()
    if sys.platform == "darwin":
        app = target or (home() / "Applications" / "Vibestrata.app")
        macos, res = app / "Contents" / "MacOS", app / "Contents" / "Resources"
        macos.mkdir(parents=True, exist_ok=True)
        res.mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "Info.plist").write_text(INFO_PLIST.format(
            version=__version__, ui_element="false" if has_webview() else "true"))
        launcher = macos / "vibestrata"
        launcher.write_text(
            "#!/bin/sh\n"
            "# Vibestrata launcher — generated by `vibestrata install-app`\n"
            'mkdir -p "$HOME/.vibestrata"\n'
            f'export PYTHONPATH="{runtime}"\n'
            f'exec "{py}" -m vibestrata --app-launch >> "$HOME/.vibestrata/app.log" 2>&1\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        icns = runtime / "vibestrata" / "static" / "vibestrata.icns"
        if icns.exists():
            shutil.copyfile(icns, res / "vibestrata.icns")
        subprocess.run(["touch", str(app)], check=False)  # refresh Finder's icon cache
        return app
    if sys.platform.startswith("linux"):
        apps = Path(os.environ.get("XDG_DATA_HOME") or home() / ".local" / "share") / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        desktop = apps / "vibestrata.desktop"
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=Vibestrata\n"
            "Comment=Field survey of your projects — reclaim disk, keep the undo\n"
            f'Exec=env PYTHONPATH="{runtime}" "{py}" -m vibestrata --app-launch\nIcon={runtime}/vibestrata/static/icon-512.png\n'
            'Terminal=false\nCategories=Development;Utility;\n')
        return desktop
    raise SystemExit("install-app supports macOS and Linux for now.")
