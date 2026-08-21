# `.claude/hooks/`

Four hook scripts ship here, all **non-blocking and fail-open by design**:
if `ctx-core` isn't installed in the Python environment the hook runs
with, the working directory doesn't look like a ctx-core corpus, or
anything else goes wrong, a script writes one line to
`var/log/ctx-hook-errors.log` and exits `0` (the guard hook's one
exception is documented below). None of them ever raise into Claude Code
or block your session on their own account — a broken hook must not break
your session.

## `ctx_eventlog_hook.py` — `PostToolUse`

Appends one line to this corpus's event log (`var/log/events.jsonl`, see
`ctx_core.events`) every time a tool runs, so the corpus keeps a durable,
hash-chained record of what happened without anyone remembering to log it
by hand.

Also heartbeats this session on the live-session board (`ctx_core.sessions`),
if it's already registered there -- since this hook fires on every tool
call, it doubles as the liveness pulse a genuinely long-lived session needs
between `SessionStart` events (which only fire once per session, or on
resume/clear) to avoid the `session_stale_seconds` sweep. A session the
board has never seen (`SessionNotFoundError`) is a no-op, not a failure --
same fail-open contract as everything else here.

## `ctx_session_start_hook.py` — `SessionStart`

Registers this session on the live-session board (`var/sessions/live/`,
see `ctx_core.sessions`) -- or heartbeats it, if it's already there from
before -- and prints who else is live and what they claim, so a session
opens already knowing about the rest of the fleet.

## `ctx_session_guard_hook.py` — `PreToolUse`

Blocks an `Edit`/`MultiEdit`/`Write`/`NotebookEdit` call whose target path
is already claimed by a **different** live session on the board. This is
the one hook that can actually stop a tool call:

- **Deny:** exits `2`, with a stderr message naming the claiming session's
  id and its registered intent -- Claude Code feeds that back to the model
  as the reason.
- **Permit:** exits `0` -- including every fail-open case (`ctx-core` not
  importable, a malformed payload, a tool this guard doesn't cover, a path
  outside the corpus, the session's own claim, a claim that's gone stale
  past `session_stale_seconds`, or any internal error). A broken guard
  must never brick a session, so anything unexpected permits rather than
  denies.

## `ctx_session_end_hook.py` — `SessionEnd` (or `Stop`)

Releases this session's board claim (moves its live file to
`var/sessions/history/`) and refreshes the stats products
(`var/stats/summary.md`/`summary.json`, see `ctx_core.stats`) -- a pure
`ctx_core` package call, zero tokens, same fail-open contract as the other
three.

## Wiring these into a domain repo

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
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/ctx_session_start_hook.py\""
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|MultiEdit|Write|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/ctx_session_guard_hook.py\""
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/ctx_session_end_hook.py\""
          }
        ]
      }
    ]
  }
}
```

`$CLAUDE_PROJECT_DIR` is set by Claude Code to the project root at hook
run time, so this works regardless of where a session's current
directory happens to be when a hook fires. If your Claude Code version
exposes `Stop` instead of `SessionEnd`, wire `ctx_session_end_hook.py`
under `Stop` instead -- the script itself doesn't care which event name
fired it.

## What they need to actually do something

Every hook here reads `ctx_core` as an ordinary installed Python package —
`pip install ctx-core` (or an editable/vendored install) into the same
Python environment the hook's `python` resolves to. If `ctx_core` isn't
importable there, each hook still runs (and still exits `0`, except the
guard's deny path which only ever fires on a real conflict it *could*
resolve); it just has nothing to register/check/append with, and says so
in `var/log/ctx-hook-errors.log`.
