# `.claude/` — the ctx-core plugin layer

Three native Claude Code surfaces wrap the `ctx` engine so a session (or
a hook) can use it without shelling out by hand every time.

## `skills/ctx-pack/`

A Claude Code Skill. Claude loads it when a task looks like it needs a
context manifest, and it tells Claude to run `ctx pack "<task>"` and read
back the manifest it prints. See `skills/ctx-pack/SKILL.md`.

## `commands/ctx-doctor.md`

A slash command (`/ctx-doctor`). Running it tells Claude to run `ctx
doctor` and report the pass/fail verdict — hard failures listed plainly,
warnings called out separately.

## `hooks/ctx_eventlog_hook.py`

A `PostToolUse` hook: after every tool call, it appends one line to this
corpus's event log (`var/log/events.jsonl`) recording that a tool ran. It
is **non-blocking and fail-open** — if `ctx-core` isn't installed, or the
directory doesn't look like a ctx-core corpus, it logs the problem to
`var/log/ctx-hook-errors.log` and exits cleanly rather than interrupting
your session. See `hooks/README.md` for the exact `settings.json` snippet
that wires it into a domain repo, and for what it needs to actually have
something to log to.

## Wiring this into your own domain repo

If you scaffolded from the ctx-core template, this is already here. If
you're adding ctx-core's plugin layer to an existing repo by hand: copy
this whole `.claude/` directory in, `pip install ctx-core` (or point your
environment at a vendored copy) so both `ctx` and `ctx_core` are on your
Python environment, and add the hook snippet from `hooks/README.md` to
your own `.claude/settings.json`.

None of these three surfaces read or store anything beyond this one
corpus — the hook only ever writes to this repo's own `var/log/`, and the
skill/command only ever shell out to the `ctx` command your own
environment already has installed.
