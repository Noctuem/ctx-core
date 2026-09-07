# AGENTS.md -- how any AI agent works in this repository

This repository is a **ctx-core corpus**: the durable context for one
domain, kept as Markdown under `core/` and `notes/` and managed by the
`ctx` command-line tool (https://github.com/Noctuem/ctx-core). This file is
the tool-neutral bootstrap; `CLAUDE.md` imports it, and other tools should
read it directly.

## Read first, every session

1. `core/card.md` -- the standing rules for working here.
2. `core/map.md` -- what lives where; every note has a line.
3. `core/profile.md` -- who this corpus is for and how they work.

Then run `ctx pack "<what you are about to do>"` and load the files the
manifest names -- those and only those. Everything else is on demand.

## Session contract

```
ctx sessions start <id> --intent "..."   # begin (any filename-safe id)
ctx sessions claim <id> <path>...        # before editing a file
ctx sessions heartbeat <id>              # during long work
ctx doctor                               # before every commit
ctx sessions release <id>                # end
```

New context that has no home yet goes through `ctx intake add "<text>"`,
never into a tool's private memory. Moving or adding a note edits
`core/map.md` in the same commit.

## Handoff

Rewrite the section below at the end of every session: where things stand
and what comes next. Keep it short; the backlog lives in `TODO.md` if the
repository keeps one.

### Where we are

(fresh corpus -- run `ctx init`)

### Next

(nothing yet)
