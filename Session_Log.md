# Session Log — ctx-core

## Session 1 — 2026-08-18 — scaffold

Repo born public: git init, root files (README, VERSION, CLAUDE.md, TODO.md,
LICENSE, .gitignore) seeded mirroring ctx-yield's conventions. No code yet —
next up is module m1 `scaffold` per the build spec.

## Session 2 — 2026-08-18 — v0.1.0 full build (agentic) — ~310K orchestrator + ~3.1M agent tokens

All ten spec modules built via parallel worktree agents and merged: scaffold,
eventlog, init, pack (ported indexer/packer), domains, archive, yield-bridge,
doctor, plugin layer, cli-release. An independent port-fidelity review caught
seven silently-changed tuned constants + a dropped ranking mechanism (fixed);
live negative-path verification caught a doctor crash on a corrupted log tail
(fixed, typed error + graceful report). CI workflow added (2 OS × 2 Python,
live doctor-gate proof). Final: 203 tests green, privacy grep clean, VERSION
aligned to 0.1.0. Next iteration targets: PyPI publish, marketplace listing.

Same session, second release: v0.2.0 built from the increment spec (intent:
the maintainer's context-management model — three sources × four layers +
the statistics mentality). Sessions board (one file per session, heartbeat
liveness, fail-open claim guard), intake front door with source provenance +
routing + doctor checks, zero-token stats pipeline (deterministic products,
analyze skill reads products only). 324 tests green both tiers; live E2E of
the full intake→route→board→stats loop verified. Landing hazard caught: a
worktree merge nearly re-introduced the purged pre-amend commit — landed by
cherry-pick, ancestry verified clean. Publication of both releases rides the
maintainer's pending force-push.

## Session 3 — 2026-08-19/20 — first live dogfood: manager session monitored on the 0.2.0 machinery — ~140K orchestrator + ~830K agent tokens

Ran a real cross-repo manager session (NVS Waves 7-9: ui, e2e, rights-ack
gate — all landed, all 13 NVS modules now built) with this corpus's 0.2.0
machinery as the monitoring layer. Exercised live: doctor (2 clean runs, 1
correct staleness warning), sessions board (claim, expiry sweep, re-claim,
graceful release — full lifecycle), domains registry (2 repos), stats
pipeline (correctly recorded 2 starts / 3 claims / 1 expire / doctor rate).
Three findings filed in TODO Medium, all ergonomics not corruption: no
`sessions heartbeat` CLI subcommand so real sessions >30min sweep to
history as expired; sweep+reclaim silently drops the session intent; Windows
cp1252 stdout mojibakes stats em-dashes (clean under PYTHONUTF8=1). The
board/stats loop held truthful state through the whole arc — the system
earns its keep on first contact.

## Session 4 — 2026-08-20 — second live dogfood: vault-inbox drain run as a board-monitored manager session — ~60K orchestrator + ~71K agent tokens

noctuem's order: "use the new system to drain the inbox and apply all the
items; don't do any work yourself — prompt and monitor." Read as the Session 3
pattern: this corpus's 0.2.0 machinery as the monitoring layer, one worker
dispatched with the hub's `/sia:inbox` procedure and Apply-all pre-approved.
Machinery exercised: doctor (stale-stats warning caught correctly, then clean),
domains (4 target repos registered), sessions board (claim with intent + six
claimed paths → API heartbeat from a background loop → graceful release; the
history file carries the intent), stats (sessions/doctor counts truthful at
generation time). Worker result: 5 items → 5 routed, 0 needs-triage; four
append-only commits (Lexi, Paige, Isolde TODOs; hub `_proposed/` draft spec +
ideas entry + map line), zero legal-name hits in any diff, inbox header-only
with a dated backup. All four pushed. No new engine findings — the heartbeat
gap from Session 3 was worked around via the API (`SessionBoard.heartbeat`
in a 10-min loop), which is exactly the ergonomics the open TODO item should
close.

## Session 5 — 2026-08-20 — third dogfood: a fresh corpus for a non-code domain, manager session end-to-end — ~75K orchestrator + ~690K agent tokens

Stood up a second, private ctx-core corpus from `template/` for a grad-school
domain (no code, all PDFs/docx/xlsx) and drove a real task through the whole
0.2.0 surface: `init --answers`, `sessions claim` + a hand-rolled re-claim
heartbeat loop (the Session 3 gap, still open; kept the session live for the
full ~45 min run, 0 expires), `intake add` → `route`, four subagents writing
notes, `pack` feeding the final planning worker, `doctor` before every commit,
`sessions release`, `stats` truthful at every read (2 packs, 3 oversized
drops, 3/3 doctor, intake latency, 1 start / 3 claims / 1 release). One new
finding filed in TODO Medium: `ctx pack` has no `--budget N` override; with
`--summary` (2,800) the three load-bearing notes were dropped as `oversized`
and the manifest filled with low-relevance filler — fixed per-corpus via
`.ctxrc.toml pack_budget_tokens = 20000`. The corpus itself is private and
lives outside this repo.

### Session 4, second pass (same session as the Session 4 entry above; landed after
### Session 5's entry because that parallel session pushed first)

noctuem's correction: "I wanted those items done,
not just put in a todo." Board re-claimed (`inbox-build-20260820`, API
heartbeat loop, ~45 min live — longest session the board has held without
sweeping), three Opus builders in parallel, one per repo: Lexi (old-card
variance: proven parameterization inferred from each card's own arithmetic,
one tap per deck via the existing PATCH; 219/106 green, `7458054`), Paige
(completed tasks fold into a collapsed section, storage untouched; web 53
green, `425b007`), Isolde (header→live workout clock `2a87e51`; deviation
detection + daily/weekly workload + trends with migration 009 and `API.md`
minted `44c6857`; weekly review with Discord yes/no sign-off via the hub
helper, dry-run by default, migration 010 `ef42fa8`; server 294 / client 281).
All pushed; Isolde Drift filed in the hub spec (`deb7d5a`). Redeploys NOT run:
SSH to the server is denied in this permission mode — three documented
deploy one-liners handed back. Engine held truthful state through a 45-min
three-worker arc; no new findings. Lesson saved to memory: "apply the items"
means build them.

### Session 4, third pass (same session) — rulings applied

noctuem's rulings: Isolde "go with the most recent ruling" (the signed-off
review stands, Saturday refresh timer retires) + SSH granted; Lexi "do the
pass yourself"; Paige "ssh and redeploy"; hub fork "don't care". Board
re-claimed (`inbox-deploy-20260820`), Paige redeployed by the manager (one
documented command, container up at `bb7b105`), two workers in parallel:
Isolde (installer arms `isolde-weekly-review.timer` and disables the refresh
timer first; 0.2.1 deployed via deploy-guard with DB backup, migrations
009+010 applied, probes green; first real post Sat 2026-08-22; `1c1b11e`,
`93e4270`) and Lexi (the worker IS the LLM: 34 live cards → 5 already
varied, 0 arithmetic-inferrable, 10 hand-parameterized and verified over 500
draws each, 19 static on purpose; FSRS history byte-identical; `0e7c8cc`).
Two stale "not deployed" claims in project handoffs were found false by
looking (Lexi live = the Anki app since 08-10; Isolde 08-18 features live
since 08-18) and corrected in those repos. Hub Drift appended (`8bb4d8c`).
Board: 3 sessions this arc, API-heartbeat loop each time, 0 expires; stats
and doctor truthful throughout; no new engine findings.


## Session 6 — 2026-08-20 — the four dogfood findings fixed (manager session; one Sonnet worker) — ~60K orchestrator + ~180K agent tokens

Landed as four commits, tests in each, 324→345 green, hygiene grep zero:
d2d0a8f `ctx sessions heartbeat` + automatic heartbeat from the PostToolUse
hook (fail-open; SessionStart's one-shot was never enough for a long
session); bcf210e re-claim after expiry recovers intent via
`SessionBoard.last_intent` (history lookup, prefix-collision guarded);
fa56e1b UTF-8 stdio forced in `main()` (stats em-dashes now clean on
Windows without PYTHONUTF8 — smoke-verified); 2dc8feb `pack --budget N`
used literally + a manifest note when the top-ranked file itself was
dropped as oversized (the Session-5 wrong-working-set failure now announces
itself). README command surface updated. One cosmetic finding filed Low
(history stamp suffix never yields `Z`). VERSION 0.2.1. Board session for
this arc claimed/heartbeated/released through the new subcommand.
