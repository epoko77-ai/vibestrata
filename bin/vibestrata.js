#!/usr/bin/env node
// npx vibestrata — thin launcher. The engine is a stdlib-only Python package
// shipped inside this npm package, so there is nothing to pip install.
"use strict";
const { spawn, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "..");
const MIN = [3, 9];

function probe(cmd) {
  try {
    const r = spawnSync(cmd, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"], {
      encoding: "utf8", timeout: 8000, stdio: ["ignore", "pipe", "ignore"],
    });
    if (r.status !== 0 || !r.stdout) return null;
    const [maj, min] = r.stdout.trim().split(".").map(Number);
    return maj > MIN[0] || (maj === MIN[0] && min >= MIN[1]) ? `${maj}.${min}` : null;
  } catch (e) {
    return null;
  }
}

function findPython() {
  const candidates = [process.env.VIBESTRATA_PYTHON, "python3", "python",
    "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3", "py"].filter(Boolean);
  for (const c of candidates) {
    if (c.startsWith("/") && !fs.existsSync(c)) continue;
    if (probe(c)) return c;
  }
  return null;
}

const py = findPython();
if (!py) {
  console.error([
    "",
    "  vibestrata needs Python 3.9+ (it uses only the standard library).",
    "",
    process.platform === "darwin"
      ? "  macOS:   xcode-select --install     (or: brew install python)"
      : process.platform === "win32"
        ? "  Windows: https://www.python.org/downloads/"
        : "  Linux:   sudo apt install python3   (or your distro's equivalent)",
    "  Then run:  npx vibestrata",
    "",
  ].join("\n"));
  process.exit(1);
}

const env = Object.assign({}, process.env, {
  PYTHONPATH: [ROOT, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
  PYTHONDONTWRITEBYTECODE: "1",
  VIBESTRATA_LAUNCHER: "npm",
});
const child = spawn(py, ["-m", "vibestrata", ...process.argv.slice(2)], { stdio: "inherit", env });
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => { try { child.kill(sig); } catch (e) { /* gone */ } });
}
child.on("exit", (code, signal) => process.exit(signal ? 1 : code == null ? 1 : code));
