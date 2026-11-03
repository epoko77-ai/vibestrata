# Launch kit

Drafts are ready to adapt. Post them yourself; tone matters more than polish. The numbers below are from the author's own machine; check yours before posting.

## Positioning (one line)

> **Your AI-made projects, at a glance.** What each one is (even the folder called `0614`), where you left off, what's stuck and what you forgot, on a local dashboard and inside Claude Code. Cleanup goes to the Trash, always undoable.

ccusage shows what you *spent* with Claude. vibestrata shows what you *made*. Lead with the first screen: the overview sentence (*"87 projects made with AI over 7 months: 7 in progress · 36 done · 44 unfinished"*) and the three columns under it (what you're building and what you're on, what's stuck and on which words, what you forgot), then the project page of a *stuck* project. Cleanup is the third act, not the hook: npkill and Mole already own "delete node_modules".

The second hook is Claude Code itself: open a project you left months ago and the first line says where it stopped.

## Before launch (checklist)

- [ ] `npx vibestrata` works from a clean machine (publish to npm first; see RELEASING.md)
- [ ] `/plugin install vibestrata --marketplace epoko77-ai/vibestrata` works, and the "you're back" line shows in a project idle for 3+ days
- [ ] Your own share card exported, with names hidden
- [ ] README images load fast; repo topics set: `claude-code`, `vibe-coding`, `codex`, `project-management`, `developer-tools`, `macos`
- [ ] Social preview image set (Settings → Social preview → `docs/card.png`)
- [ ] Two or three friends have tried it and filed issues, so the tracker isn't empty

## Timing

Year-end is the season for "my year in…" cards. Launching in late November or early December gives the share card a reason to travel.

| Day | Where |
|---|---|
| D-0, Tue/Wed around 8–9am PT | Show HN, X thread, r/ClaudeAI |
| D-0, evening KST | GeekNews, your column, YouTube community post |
| D+1 | r/ChatGPTCoding, r/SideProject, Threads, LinkedIn |
| D+3 | Product Hunt (optional), with any fixes from the first wave |

On launch day, reply to every comment within the hour. Turn bug reports into issues while people are still watching.

---

## Show HN

**Title:** Show HN: Vibestrata – what you made with Claude Code, at a glance, and where you left off

**Body:**

I build a lot of small things with Claude Code and Codex: reports, decks, sites, little tools. Seven months in I had 87 folders, and the names stopped meaning anything: `0614`, `udo`, `test`, `assist2`. Some were tools I use every week, some were finished jobs, some had broken on the last prompt and I never came back.

vibestrata (`npx vibestrata`, stdlib Python + vanilla JS, local only) reads what you already left behind and draws the map:

- a readable name for every folder: the README title, or the document it produced (`AI_전략_워크숍_보고서.pdf`), plus one line from the first thing you asked for
- a state: building; done (a tool a skill or launch agent still uses, a job you handed over, a v1 a newer folder replaced); unfinished (stuck: your last words were "still broken"; on hold; tried once)
- where you left off, in plain sentences: when it started, your last words, your hand-off note, what it produced, whether there's any backup. One click reopens the conversation in Claude
- a Claude Code plugin: open a project you left months ago and the first line says where it stopped, so "let's continue" just works
- cleanup when you want it: tick projects, send them (or just their node_modules / drafts) to the Trash, with warnings like "no git: this folder is the only copy", and undo

Getting the states right was mostly about what *not* to trust. In my own history, "save progress" ends most sessions whatever the state, "thanks!" sits next to "let's continue tomorrow" half the time, and "cancel that" usually scraps one run, not the project. So it never decides you're *done* from closing words alone, and every state is one click to correct.

No network, no telemetry, 127.0.0.1 only. `npx vibestrata --demo` runs on synthetic data if you want to look first. What would you want to see about each project at a glance?

## X / Threads (thread)

1/ I've made 87 things with Claude Code in 7 months. I couldn't tell you what half the folders were. `0614`? `udo`? `assist2`?

So I built vibestrata: everything you made with AI, at a glance. [screenshot]

2/ Every folder gets a name and a state, from the evidence you already left:

✕ Stuck: your last words were "the build still fails on iOS 19, same error"
○ On hold: with the hand-off note you left
◆ In use: a skill still points at it
✓ Done: "I'll take it from here"

[project page screenshot]

3/ The part I use most: open Claude Code in a project I left in August and the first line is
"On hold · away 76 days · your last words: 'still broken'"
Then "let's continue" just works.

4/ Cleanup is there when you want it: tick projects → Trash, with warnings first ("no git: this folder is the only copy"). One-click undo.

5/ Local only, MIT, `npx vibestrata`. `--demo` if you're nervous.
github.com/epoko77-ai/vibestrata

Post your card. How many of yours are stuck?

## Reddit (r/ClaudeAI)

**Title:** I made a map of everything I've built with Claude Code: what each folder is, where I left off, and a "you're back" line when I reopen one

Use the Show HN body, shortened, with the share card as the post image. End with: *"how many of your projects are stuck on the last prompt?"*

## GeekNews (한국어)

**제목:** Vibestrata – AI와 만든 내 프로젝트들, 뭐였고 어디까지 했는지 한눈에

**본문:**
Claude Code와 Codex로 보고서, 발표자료, 사이트, 작은 도구를 만들다 보니 7개월 만에 폴더가 87개가 됐습니다. `0614`, `udo`, `test`처럼 이름만 봐서는 뭔지 모르는 폴더가 절반이었습니다. 매주 쓰는 도구도, 넘겨준 작업도, 마지막 프롬프트에서 깨진 채 멈춘 사이트도 섞여 있었습니다.

vibestrata는 로컬에서만 도는 프로젝트 지도입니다. `npx vibestrata` 한 줄로 실행됩니다.
- 모든 폴더에 읽을 수 있는 이름을 붙입니다. README 제목이나 만들어 낸 문서 이름(`AI_전략_워크숍_보고서.pdf`)을 쓰고, 처음 요청한 말로 한 줄 설명을 붙입니다.
- 상태를 나눕니다. 만드는 중, 다 된 것(스킬이 지금도 쓰는 도구, 넘겨준 작업, 새 폴더로 옮겨 간 옛 버전), 하다 만 것(마지막 말이 "여전히 안 돼"였던 막혀서 멈춤, 하다 멈춤, 잠깐 해 본 것).
- 어디까지 했는지 몇 문장으로 보여 줍니다. 언제 시작했는지, 마지막에 한 말, 인계 메모, 결과물, 백업 여부. 버튼 하나로 그 대화를 Claude에서 다시 엽니다.
- Claude Code 플러그인: 몇 달 전에 멈춘 프로젝트에서 Claude를 켜면 첫 줄에 어디서 멈췄는지 나옵니다.
- 정리는 필요할 때만 합니다. 체크한 프로젝트를 휴지통으로 보내고(통째로, 또는 node_modules·초안만), "git이 없어서 이 폴더가 유일한 사본"같은 경고를 먼저 보여 주며, 언제든 되돌릴 수 있습니다.

MIT 라이선스이고, 네트워크와 사용 데이터 수집은 일절 없습니다. `npx vibestrata --demo`로 가상 데이터를 먼저 볼 수 있습니다.

## Your own channels (column / YouTube)

Angle: *"AI와 7개월, 87개를 만들었다. 그중 5개는 마지막 말이 '여전히 깨져'였다"*. Use your real share card with names hidden. The story is the last words you find in the stuck ones, and the folders you couldn't name until the tool named them.
