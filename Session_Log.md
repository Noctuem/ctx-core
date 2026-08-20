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
