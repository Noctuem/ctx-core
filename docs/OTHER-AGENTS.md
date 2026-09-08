# Using a ctx-core corpus from any agent

ctx-core is a Markdown corpus plus a stdlib Python CLI. Claude Code gets
hooks, a skill and a slash command on top (`.claude/`), but nothing in the
corpus depends on them. Any tool that can read files and run a command --
Codex, Gemini CLI, Cursor, aider, Copilot, a plain shell script -- works
against the same corpus with the same guarantees, by following the contract
the hooks automate.

## 1. Bootstrap: `AGENTS.md`

The template ships a root `AGENTS.md`: what the repo is, which three files
to read first (`core/card.md`, `core/map.md`, `core/profile.md`), and the
session contract below. `CLAUDE.md` is a thin file that `@`-imports it, so
Claude Code and everything else read one source. Tools that look for a
different filename (`GEMINI.md`, `.cursorrules`, `CONVENTIONS.md`) can be
pointed at `AGENTS.md` or given a one-line file that says "read AGENTS.md".

Write the bootstrap without tool-specific syntax: no `@`-imports, no slash
commands, no assumptions about hooks. State paths explicitly.

## 2. The session contract (what the hooks do for Claude Code)

```
ctx sessions start <id> --intent "what this session is for"   # begin
ctx pack "<task>"                                             # working set
ctx sessions claim <id> <path>...                             # before editing
ctx sessions heartbeat <id>                                   # every ~2 min of long work
ctx doctor                                                    # before committing
ctx sessions release <id>                                     # end
```

`<id>` is any filename-safe string the tool can keep for the whole session
(a UUID, a run id). `start` is idempotent: calling it on a live id refreshes
the heartbeat instead of re-registering. A session that dies without
`release` is swept to history after `session_stale_seconds` (default 30 min)
by the next board call on the same host -- never deleted.

`ctx sessions start` and the implicit registration performed by
`ctx sessions claim` record `hookless: true`, so their doctor runs do not
count toward the hook-silence advisory. A harness with hooks should register through
`SessionBoard.register`'s default (`hookless=False`) and emit
`hook_post_tool_use` after tool calls. Older session records with no `hookless`
field keep that hook-expected interpretation.

## 3. Several machines

Set `eventlog_per_writer = true` when more than one clone appends to a
committed `var/`. Each clone gets its own chain file; readers fold them all.
See the README's "Several machines, several agents" section.

## 4. Windows interpreter note

Hooks and scripts that invoke `python` by name can silently hit the
Microsoft Store stub (exit 9009) when the machine PATH lists
`WindowsApps` first. Prefer `py -3` in any command line a harness runs, or
remove the stub aliases. The hook-silence advisory exists because this
failure is otherwise invisible.
