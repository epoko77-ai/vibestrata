#!/bin/sh
# Where you left off, when you come back to a project after a few days.
# Answers from the last survey; if there is none yet or it is over 12 hours
# old, starts one read-only survey in the background and returns at once.
# Prints nothing if you were here recently or this folder isn't one of your
# projects. Turn it off with "session_brief": false in ~/.vibestrata/config.json.
if command -v vibestrata >/dev/null 2>&1; then
  exec vibestrata here --hook
fi
exec npx -y --prefer-offline vibestrata here --hook 2>/dev/null
