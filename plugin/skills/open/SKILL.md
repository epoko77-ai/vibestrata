---
name: open
description: Open the vibestrata dashboard window, the map of all the user's projects (what each is, where it stopped, what's stuck or forgotten), with rename, notes, open-in-Claude and reversible cleanup.
disable-model-invocation: true
allowed-tools: Bash(npx -y vibestrata --rescan)
---

Open the vibestrata app window in the background so this session stays free:

```bash
npx -y vibestrata --rescan
```

Run it with the Bash tool's background option (it keeps running until the user closes the window). Then tell the user the window is opening, that closing it quits vibestrata, and that anything they remove goes to the Trash and can be undone from the Log. For a permanent app icon (Launchpad / Spotlight / Dock) they can run `npx -y vibestrata install-app` once.
