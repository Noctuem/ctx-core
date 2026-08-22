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


## Session 7 — 2026-08-21 — external-review worker brief landed; state report published; forge draft staged — ~90K orchestrator + ~410K agent tokens

State report "ctx-core at 0.2.1" authored from a fresh code survey (published
artifact + PDF); the survey itself surfaced five small items (filed Low). Then
noctuem handed two files: a six-item worker brief from an external review, and
the forge v0.1 draft (autonomous build loop over ctx-core). Brief landed by one
Sonnet worker in five commits, 345→360 green, hygiene zero: d0a24ec distinct
age-gate drop reason + `age_gated`/`intake_cap` stats labels (fixture manifest
updated); 646f76f age-gate exemption checks unpadded coverage so 1–2-term
tasks can clear 0.75; 0cfb302 `core_budget_tokens` (None → resolves to
pack_budget_tokens at check time; doctor message names it); 4af6c8e README
status + token-estimator caveat (quick-start NOT flipped — PyPI publish hasn't
happened); bfa9446 Bash claim-overlap advisory in the PostToolUse hook —
observe-only, ≤50 stats, one `claim_overlap_observed` per (session, path),
fail-open incl. corrupt log, surfaced in stats' Sessions line; TODO Medium item
amended not ticked (the guard gap stays open by design). Acceptance `ctx pack`
run gave this repo its first `context_assembled` event but had zero rankable
candidates — the engine repo has no core/notes trees; TODO item re-framed
(example corpus vs retire). Report's doctor arithmetic fixed (3 hard + 4 soft +
1 escalating), republished. forge draft staged at hub
notes/projects/specs/new/_proposed/forge.spec.md with map line (hub 55d3800);
both inbox files handled from a tmp spot, never staged, deleted after. VERSION
0.2.2.


## Session 8 — 2026-08-21 — TODO backlog cleared through the system; ctx-build spec staged — ~70K orchestrator + ~370K agent tokens

noctuem: "using the system go through the todos and do them" then start
ctx-build (forge renamed into the ctx family). Board claimed for the arc;
doctor green. One Sonnet worker, seven commits, 360→381: 93c2c85 sessions
docstring + SessionBoard reads Knobs; 781db24 `init_run` emitted by
run_interview, stats `window.initialized_at` (full-log read); 9c815f2
INTAKE_ROUTE_AGE_KEY pinned to `age_days`; 4f50af0 history stamp: replace
order root-caused, real `Z` suffix, regex `Z$`; 16b2589 `yield_scan` event per
composition run (every outcome) + windowed `yield_runs_in_window`; f6b9153
examples/corpus (fictional domain, pinned + decisions-tagged + oversized notes,
3,000-token .ctxrc) doctored + packed in CI, README quick-start points at it;
e7851dd live ctx-yield check — already installed (0.1.0), ran/not degraded, no
API key. Shelved with reasons [noctuem]: hook-upgrade path (import stays;
subprocess-per-tool-call latency), Bash guard gap (advisory landed; awaits a
path signal), intake completion interview (no signal). PyPI + marketplace stay
[human]. VERSION 0.2.3. In parallel an Opus spec author converted forge v0.1 →
ctx-build spec with a 10-module graph (hub new/ctx-build.spec.md, c4064d2);
noctuem ruled private Gitea first, mirror later. Next: /sia:new-spec →
/sia:new-project → /sia:build for ctx-build.

## Session 9 — 2026-08-21 — manager session: ctx-build Waves 1–5 built through /sia:build — ~100K orchestrator + ~2.0M agent tokens

No engine change in this repo. /sia:build invoked here; ctx-core's own graph
is fully built (14/14 done.md, both specs implemented), so the live target was
the sibling ctx-build (its Wave 0 was managed from Session 8). Board claimed
(Session 9, CLAUDE.md/Session_Log.md/TODO.md) for the arc; released at close.
Agentic full run: ten Sonnet workers + three fix workers, one merge gate per
wave in-session; all ten modules landed (ledger, roles, router, verify-env,
cli, runner, proof-manual, proof-auto + fixes), final gate 265 passed / 9
skipped / 1 xfail, hygiene clean, pushed to private Gitea. Dogfood signals
for THIS repo from the run: (1) `ctx init --root T --answers {}` is a clean
non-interactive bootstrap — ctx-build's `init` relies on it; (2) `ctx pack` on
a young corpus returned 230 tokens (profile + README) — thin manifest for a
builder brief, expected but worth watching as intake accrues; (3) the
sessions board worked as a presence signal for a cross-repo manager session
again — OQ1 unchanged. Drift written to the hub spec; ctx-build handoff +
TODO carry the rest.
