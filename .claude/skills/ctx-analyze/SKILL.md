---
name: ctx-analyze
description: Use when you want to understand how well this corpus's context system is working -- what gets packed, what never does, drop reasons, budget utilization, doctor pass rate, intake latency, session activity -- and propose improvements from the already-computed stats products.
---

# ctx-analyze

This is the **stage-3 active layer** of the Statistics Mentality: gathering
(hooks, the event log) and aggregation (`ctx stats`) are automatic and cost
zero tokens; analysis is the only stage that spends any, because it's the
only stage that needs judgment rather than arithmetic.

## The one rule this skill exists to enforce

**Read only `var/stats/summary.md` and `var/stats/summary.json`. Never read
the raw event log (`var/log/events.jsonl`) directly, and never fold/parse it
yourself.** The event log is stage-1 gathering — a firehose of individual
records meant for `ctx stats` to aggregate, not for an LLM to re-derive
aggregates from by hand. If the products look stale or incomplete, the fix
is to run `ctx stats` again (cheap, zero tokens, regenerates the products
from the same log) — not to open `events.jsonl` as a workaround.

## What to do

1. Check whether the products exist and are current:
   - `var/stats/summary.md` / `var/stats/summary.json` — if either is
     missing, or `ctx doctor` flagged a stats-staleness warning, run
     `ctx stats` first and re-read the fresh products.
2. Read `var/stats/summary.md` (human-readable) for the narrative, and
   `var/stats/summary.json` (machine-readable, stable schema) for exact
   numbers when you need to compute something across sections (e.g.
   comparing drop-reason counts against pack count).
3. Look across the sections for a story, not just isolated numbers:
   - **Packs / top-packed / never-packed** — is anything force-loaded via
     `always_include`/pinning that never actually gets used? That's a
     candidate for `ctx-yield`'s dead-weight signal (see the yield section)
     or for un-pinning.
   - **Drop-reason histogram** — a corpus dominated by `over_budget` drops
     may need a higher `pack_budget_tokens`, tighter notes, or better
     relevance tagging; `oversized` drops name notes worth archiving
     (`ctx archive stub`) or splitting.
   - **Budget utilization** — packs consistently near 0% fill waste a
     budget that could admit more; packs consistently at 100%+ are
     starving something relevant.
   - **Doctor pass rate** — a low pass rate across recent runs means the
     corpus is spending more time broken than healthy; look at what's
     recurring, not just the latest run.
   - **Intake** — a growing unrouted backlog, or high add-to-route latency,
     means New-layer context is being filed and forgotten rather than
     completed and routed (see the build spec's Open Question 3).
   - **Sessions** — repeated claim conflicts on the same paths suggest a
     board convention or a corpus layout that needs adjusting, not just a
     one-off collision.
   - **ctx-yield dead weight** — `ran: false` means "not measured," never
     "clean." Only read the dead-weight list as a real signal when
     `ran: true` and `degraded: false`.
4. **Propose, never apply.** This skill's output is analysis and
   recommendations — routing an intake item, changing a knob, archiving a
   note, re-pinning something. Per the v0.2 spec's semi-automatic stance,
   every one of those actions is a human or a ratified-verifier call, not
   something this skill executes on its own. Say what you'd do and why;
   don't do it.
5. Every number you cite should carry the scope the products already state
   (window, whether the corpus is unbounded, whether ctx-yield ran) — don't
   round an absent signal (`ran: false`) up to "clean."
