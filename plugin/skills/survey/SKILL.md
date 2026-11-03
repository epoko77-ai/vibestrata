---
name: survey
description: Show the user every project on this machine at a glance with vibestrata — what each one is (a readable name even for folders called "0614"), what's in progress, which are done or still in use as tools, which got stuck on a problem and on what words, which they forgot, which have no backup, and what can be cleaned up (node_modules, .venv, caches, leftover _workspace folders and old versions). Use when the user asks "what projects do I have", "show me my projects", "what was I working on", "which projects did I abandon", "what was I stuck on", "what did I forget", "how much disk are my projects using", "clean up node_modules", "free up space", "내 프로젝트 보여줘", "프로젝트 현황", "진행 상황 한눈에", "하다 만 프로젝트", "막힌 프로젝트", "잊고 있던 프로젝트", "프로젝트 정리", "용량 정리".
argument-hint: "[sweep]"
allowed-tools: Bash(npx -y vibestrata scan --summary --max-age 6) Bash(npx -y vibestrata scan --summary) Bash(npx -y vibestrata sweep) Bash(npx -y vibestrata sweep --all) Bash(npx -y vibestrata sweep --byproducts) Bash(npx -y vibestrata ops)
---

# vibestrata survey

vibestrata is a local map of the user's projects, built from their files, git and their own Claude Code / Codex prompts. It reads only. Cleanup sends things to the system Trash, and every removal can be undone until the user empties the Trash.

## 1. Survey

Run:

```bash
npx -y vibestrata scan --summary --max-age 6
```

(`--max-age 6` reuses a survey from the last 6 hours; drop it if the user asks for a fresh one. A fresh survey takes ~10–30 s.)

Then answer the user's actual question from the Markdown it prints. Projects are named by readable titles (the folder name follows in backticks when it differs) and grouped:

- **In progress** (building): touched in the last two weeks, with what they asked most recently.
- **Done**: *in use* (a finished tool a skill, launch agent, MCP server or alias points at), *done* (handed over or shipped), *moved on* (a newer folder took over).
- **Unfinished**: *stuck* (the last thing they said was a problem that never got fixed; quote it, it's where they'd pick up), *on hold* (stopped mid-way, with the hand-off note when one exists), *tried once*.

Lead with what matters to them: what's stuck and on what words, what they forgot, what has no backup. Keep it short; don't paste the whole list back unless asked. The states are a first guess; the user corrects them in the dashboard.

For one project in depth, use the `resume` skill. If the user wants Claude to read the evidence and name or classify the unclear ones, suggest `/vibestrata:triage`.

## 2. Cleanup (only when the user wants it)

Preview first. This moves nothing:

```bash
npx -y vibestrata sweep
```

It follows each project's state: dependencies and caches proven rebuildable by a lockfile/manifest next to them (not git-tracked, not in use) from finished projects and from anything idle for 45+ days. Projects in progress, in use or pinned are skipped. Show the user the total and the biggest items. If it mentions leftovers in finished projects, `npx -y vibestrata sweep --byproducts` previews those too (never from projects with unsaved work).

To remove whole projects, point the user to the dashboard (`/vibestrata:open`): they tick projects and see the warnings (no backup, a skill depends on it) before anything moves.

**Never run `--apply` without the user's explicit go-ahead in this conversation.** When they confirm, run the same command with `--apply` added:

```bash
npx -y vibestrata sweep --apply
```

Report what moved, and give them the undo command it prints (`npx -y vibestrata undo <op-id>`).

## 3. Visual dashboard

If the user wants to browse the map, rename projects, leave notes, correct states, open a project in Claude where they left off, or tick projects to clean up, suggest opening the app window with the `/vibestrata:open` skill.

## Notes

- Everything runs locally; nothing is uploaded. Prompts shown come from the user's own `~/.claude/history.jsonl` and `~/.codex/history.jsonl`, with anything that looks like a credential redacted.
- The plugin also says where the user left off when they open Claude Code in a project they've been away from for 3+ days. It keeps the survey fresh on its own (a read-only background survey when the last one is missing or over 12 hours old), so the dashboard never has to be opened first. They can turn that off with `"session_brief": false` in `~/.vibestrata/config.json`.
- Requires Python 3.9+ (standard library only). If `npx` reports Python is missing, relay its install hint.
- `npx -y vibestrata ops` lists past cleanups; `npx -y vibestrata undo <op-id>` reverses one.
