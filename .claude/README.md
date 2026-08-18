# `.claude/` — the ctx-core plugin layer

Native Claude Code surfaces wrap the `ctx` engine so a session (or a hook)
can use it without shelling out by hand every time.

## `skills/ctx-pack/`

A Claude Code Skill. Claude loads it when a task looks like it needs a
context manifest, and it tells Claude to run `ctx pack "<task>"` and read
back the manifest it prints. See `skills/ctx-pack/SKILL.md`.

## `skills/ctx-analyze/`

A Claude Code Skill -- the stage-3 **active analysis** layer of the
Statistics Mentality (gathering and aggregation are automatic and cost
zero tokens; analysis is the only stage that spends any). Reads
`var/stats/summary.md`/`summary.json` (never the raw event log) and gives
propose-only recommendations: what's never packed, why things get dropped,
budget utilization, doctor pass rate, intake backlog, session conflicts,
ctx-yield dead weight. See `skills/ctx-analyze/SKILL.md`.

## `commands/ctx-doctor.md`

A slash command (`/ctx-doctor`). Running it tells Claude to run `ctx
doctor` and report the pass/fail verdict — hard failures listed plainly,
warnings called out separately.

## `hooks/`

Four hooks, all non-blocking and fail-open by design:

- `hooks/ctx_eventlog_hook.py` (`PostToolUse`) — appends one line to this
  corpus's event log (`var/log/events.jsonl`) recording that a tool ran.
- `hooks/ctx_session_start_hook.py` (`SessionStart`) — registers this
  session on the live-session board and prints who else is live.
- `hooks/ctx_session_guard_hook.py` (`PreToolUse`) — blocks a write into a
  path a DIFFERENT live session already claims, naming that session and
  its intent; fails open on anything unexpected.
- `hooks/ctx_session_end_hook.py` (`SessionEnd`/`Stop`) — releases this
  session's board claim and refreshes the stats products.

If `ctx-core` isn't installed, or the directory doesn't look like a
ctx-core corpus, each hook logs the problem to `var/log/ctx-hook-errors.log`
and exits cleanly rather than interrupting your session. See
`hooks/README.md` for the exact `settings.json` snippet that wires all
four into a domain repo, and for what each one needs to actually have
something to act on.

## Wiring this into your own domain repo

If you scaffolded from the ctx-core template, this is already here. If
you're adding ctx-core's plugin layer to an existing repo by hand: copy
this whole `.claude/` directory in, `pip install ctx-core` (or point your
environment at a vendored copy) so both `ctx` and `ctx_core` are on your
Python environment, and add the hook snippets from `hooks/README.md` to
your own `.claude/settings.json`.

None of these surfaces read or store anything beyond this one corpus — the
hooks only ever write to this repo's own `var/`, and the skills/command
only ever shell out to (or, for `ctx-analyze`, read the products of) the
`ctx` command your own environment already has installed.
