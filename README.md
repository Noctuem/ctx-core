# ctx-core

ctx-core is an open-source context system for [Claude Code](https://claude.com/claude-code)
(and compatible agents): a **git-native, human-readable markdown corpus** plus a
**zero-dependency Python CLI** that packs the right context for a task under a token
budget, checks its own health, and manages the corpus's lifecycle without ever
silently losing anything.

It ships as a **GitHub template repo** — clone it, run one interview, and you have a
working context corpus wired into Claude Code in one session.

Sibling of [ctx-yield](https://github.com/Noctuem/ctx-yield), which measures whether an
*existing* context system is earning its tokens. ctx-core is the system itself; the two
compose (`ctx pack --report`) but neither absorbs the other.

## What it is

Most of what a good context system needs — progressive-disclosure skills, hooks,
memory, plugin marketplaces — already exists natively in Claude Code. ctx-core doesn't
reinvent any of that. It exists for the handful of things that genuinely aren't native
yet:

| Gap | What ctx-core does about it |
|---|---|
| A packer that respects a token budget | `ctx pack` scores and ranks your notes, fits as much as the budget allows, and tells you exactly what it dropped and why |
| Oversized notes silently bloating context | `ctx archive` content-addresses a note's full text and replaces it with a short stub — nothing is ever truncated or lost, only relocated |
| A health check CI can actually gate on | `ctx doctor` exits non-zero on a real defect (a dangling archive stub, a broken event log, a budget blown) instead of being a warning nobody reads |
| No durable record of what a session actually did | An append-only, hash-chained event log — tamper-evident, single-writer by construction |
| No visibility into whether force-loaded context is even used | `ctx pack --report` composes with `ctx-yield`, when installed, to flag context that's never recalled |

Everything else in the repo (the init interview, the Claude Code plugin layer, the
multi-domain registry) is glue holding those five pieces together — not machinery this
project invented for its own sake.

## The three layers

```
core/      -- L1, force-loaded    -- who you are and how you work; read every session
notes/     -- L2, on-demand       -- the corpus; the packer selects from this per task
var/       -- runtime state       -- the event log and the content-addressed archive
```

- **L1 (`core/`)** is small and always loaded — your working profile (`ctx init` writes
  it), the interaction contract, and the map rule that keeps `notes/` organized.
- **L2 (`notes/`)** is everything else: as much as you want, because nothing here is
  force-loaded. `ctx pack` is what decides which of it a given task actually needs.
- **L3** isn't a directory, it's the harness around the other two: the packer, the
  budgets and knobs it reads (`.ctxrc.toml`), and `ctx doctor` as the gate that catches
  a corpus quietly going wrong.

A corpus lives entirely in git-tracked markdown plus a couple of runtime files under
`var/` — no database, no server, no daemon. Every `ctx` command is a one-shot process
that reads the filesystem, does its job, and exits.

## Quick start

```sh
# 1. Clone from the template (or click "Use this template" on GitHub)
git clone https://github.com/Noctuem/ctx-core.git my-context
cd my-context

# 2. Install the engine
# (PyPI package ctx-core coming; then: pip install ctx-core)
pip install -e .

# 3. Answer a short interview -- writes core/profile.md
ctx init

# 4. Pack a budgeted manifest for a real task
ctx pack "figure out why the build is failing"

# 5. Check corpus health -- exit code is CI-gateable
ctx doctor
```

`ctx init` also accepts `--answers answers.json` for non-interactive/scripted setup
(see `docs/ADOPTION.md`).

Want to see the commands above run against real, already-populated content instead
of a fresh clone's empty `notes/`? `examples/corpus/` is a small, committed corpus
(fictional domain, realistic shape — a pinned note, a decisions log, and one
note deliberately oversized for its own small budget) you can point `--root` at:

```sh
ctx pack "help a new teammate ramp up on the product" --root examples/corpus
```

See `examples/corpus/README.md` for what it demonstrates.

## Command surface

```
ctx pack "<task>" [--summary] [--last N] [--decisions] [--budget N] [--report]
ctx doctor
ctx archive sweep
ctx archive stub <path>
ctx init [--answers FILE]
ctx domains list | register <name> <path> | forget <name>
ctx sessions list | claim <session-id> <path>... [--intent TEXT] | release <session-id> | heartbeat <session-id>
ctx intake add "<text>" [--source user|project|research] [--title T]
ctx intake route <item> --to core|notes[/<subpath>]|archive
ctx intake list
ctx stats [--since N]
ctx --version
```

Every command takes `--root PATH` to point at a corpus other than the current
directory (default: cwd). Run `ctx <command> --help` for the full flag list.

## Sessions, intake, stats

Three additions on top of the three-layer model above, all governed by the same
semi-automatic posture: machinery gathers, aggregates, and flags automatically —
it never applies anything on its own.

- **Sessions** (`ctx sessions`) — a live-session board, one JSON file per session
  under `var/sessions/live/`, so concurrent work on one corpus is coordinated by
  construction rather than convention. `ctx sessions claim` registers a session (if
  it isn't already) and records what paths it's touching; a stale session (past
  `session_stale_seconds`, crashed or otherwise abandoned) stops blocking on its
  own and its claim is swept to `var/sessions/history/` — nothing is ever deleted.
  The plugin layer wires this in automatically: a `SessionStart` hook registers and
  shows who else is active, and a `PreToolUse` guard hook blocks a write into
  another live session's claim (fail-open — a broken guard never bricks a session).
- **Intake** (`ctx intake`) — the missing fourth lifecycle stage: **New**. Context
  that just arrived (`ctx intake add`) is filed under `notes/intake/` with
  provenance (`user`/`project`/`research`) and a received timestamp, until a
  session or a human routes it (`ctx intake route`) into `core/`, `notes/`, or the
  archive. `ctx doctor` makes an unrouted item visible immediately and a hard
  failure once it's sat past `intake_max_age_days` — filing something is never a
  silent leak.
- **Stats** (`ctx stats`) — the zero-token half of the Statistics Mentality: a
  deterministic fold over the event log, the corpus index, sessions, and intake
  into `var/stats/summary.md`/`summary.json` — what gets packed, what never does,
  why things get dropped, budget utilization, doctor pass rate, intake latency,
  session activity, and ctx-yield dead weight when it's installed. No LLM call
  anywhere in gathering or aggregation; the `ctx-analyze` skill is the one place
  that spends tokens, and it reads only these products, never the raw event log.

## Package name vs. command name

The PyPI package is **`ctx-core`**; the console script it installs is the shorter
**`ctx`**. That split is deliberate — a package literally named `ctx` was hijacked once
before (a 2022 supply-chain incident on a small utility package of that name), so the
distribution keeps the descriptive name and only the installed *command* claims the
short verb.

If `ctx` already resolves to something else on your `PATH`, you have two fallbacks that
need no reinstall:

```sh
python -m ctx_core.cli pack "a task"    # always works if the package is installed
pipx install ctx-core                    # isolates the console script in its own venv
```

## Composing with ctx-yield

[ctx-yield](https://github.com/Noctuem/ctx-yield) answers "is this context actually
earning its tokens" for whatever you point it at. ctx-core never bundles it — install
it separately, and `ctx pack --report` will pick it up automatically:

```sh
pip install ctx-yield
ctx pack "a task" --report
```

If `ctx-yield` isn't installed, `--report` prints a short notice and exits `0` — it
never fails your pipeline for a missing optional tool.

**Token counts are estimated, not exact.** Every budget ctx-core checks against
(`pack_budget_tokens`, `--budget N`, `core_budget_tokens`) is measured with
`approx_tokens = len(text) // 4` (`ctx_core/indexing.py`) — a cheap heuristic, not a
real tokenizer, and it can drift 20-30% from an actual token count. `ctx-yield
--exact` above is the precise path when that drift matters.

## Secrets

**ctx-core itself needs none.** The only credential anywhere in this stack is
`ctx-yield`'s optional `--exact` tokenizer path, which reads `ANTHROPIC_API_KEY` from
*its own* environment — ctx-core never reads, stores, or passes that key itself; it
only shells out to an already-configured `ctx-yield` binary. See `.env.example`.

## Running the tests

```sh
pip install -e ".[dev]"
python -m pytest
```

`tests/test_cli.py` drives the real, installed `ctx` entry point via subprocess (not
just an in-process function call) for at least one case per subcommand — that tier is
slower, so set `CTX_CORE_FAST=1` to skip it during the day-to-day edit loop:

```sh
CTX_CORE_FAST=1 python -m pytest       # fast tier only
python -m pytest                       # full suite -- the merge gate
```

## Status

Version 0.1.0 shipped 2026-08-18 — all ten modules (layout/config, packer+indexer,
doctor, archive, event log, yield-bridge, init, plugin layer, domains, CLI) are built
and tested, with 203 tests passing in a CI matrix across Linux and Windows on Python
3.11 and 3.13.

Version 0.2.0 adds the live-session board (`ctx sessions`), the New-layer intake
front door (`ctx intake`), and the zero-token stats pipeline (`ctx stats` + the
`ctx-analyze` skill), plus three new `ctx doctor` checks and two new plugin hooks
(a `SessionStart` board hook and a fail-open `PreToolUse` claim guard).

Version 0.2.2 adds `ctx sessions heartbeat`, automatic session heartbeat from the
plugin's `PostToolUse` hook, intent recovery on session re-claim, forced UTF-8
stdio, and `ctx pack --budget N` (a literal per-invocation override) — 360 tests
passing locally, both suite tiers. PyPI publication and the plugin-marketplace
listing (see `docs/ADOPTION.md` for the checklist) are still the next iteration
targets.

## License

MIT — see `LICENSE`.
