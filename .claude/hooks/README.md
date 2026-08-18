# `.claude/hooks/`

One hook script ships here: `ctx_eventlog_hook.py`. It appends one line
to this corpus's event log (`var/log/events.jsonl`, see `ctx_core.events`)
every time a tool runs in a Claude Code session, so the corpus keeps a
durable, hash-chained record of what happened without anyone remembering
to log it by hand.

**Non-blocking and fail-open by design.** If `ctx-core` isn't installed
in the Python environment the hook runs with, or the working directory
doesn't look like a ctx-core corpus, or anything else goes wrong, the
script writes one line to `var/log/ctx-hook-errors.log` and exits `0`. It
never raises into Claude Code and never blocks a tool call — a broken
hook must not break your session.

## Wiring it into a domain repo

Add this to your project's `.claude/settings.json` (create the file if
your repo doesn't already have one):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/ctx_eventlog_hook.py\""
          }
        ]
      }
    ]
  }
}
```

`$CLAUDE_PROJECT_DIR` is set by Claude Code to the project root at hook
run time, so this works regardless of where a session's current
directory happens to be when the tool call fires.

## What it needs to actually log something

The hook reads `ctx_core` as an ordinary installed Python package — `pip
install ctx-core` (or an editable/vendored install) into the same Python
environment the hook's `python` resolves to. If `ctx_core` isn't
importable there, the hook still runs (and still exits `0`); it just has
nothing to append with, and says so in `var/log/ctx-hook-errors.log`.
