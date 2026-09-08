# ctx-core — session handoff

- **Spec:** maintained privately; v0.1.0 implements it in full (all ten
  modules).
- **Latest (2026-09-08, Session 13):** v0.2.5 fixes two integration-audit
  regressions. Hook silence now counts consecutive hook-expected doctor runs
  since the last hook event, manual CLI sessions carry a generic
  `hookless: true` marker, and mixed/legacy sessions remain hook-expected.
  Intake stats now reuse the canonical New-layer queue, so routed and archive
  stubs are excluded while the JSON/Markdown schema stays unchanged. Full
  suite: 403 passed. Not pushed; publication remains a maintainer action.
- **Where we are:** v0.2.0 built 2026-08-18 (sessions board, intake layers,
  stats pipeline; 324 tests); v0.1.0+v0.2.0 PUBLISHED 2026-08-18,
  remote history clean, CI matrix green. First live dogfood 2026-08-19/20
  (Session 3): board/domains/stats/doctor all held truthful state through a
  real cross-repo manager session; three ergonomics findings filed in TODO
  Medium (heartbeat CLI gap, intent lost on sweep+reclaim, Windows stdout
  encoding). Second dogfood 2026-08-20 (Session 4): vault-inbox drain run as
  a board-monitored manager session — 5/5 routed, then built by three
  parallel workers (Lexi/Paige/Isolde, all pushed; Paige + Isolde redeployed,
  Lexi variance pass applied to the live cards); no new engine findings.
  Third dogfood 2026-08-20 (Session 5): a fresh PRIVATE corpus for a non-code
  domain driven end-to-end (init/claim/intake/pack/doctor/release/stats); one
  new finding (`pack --budget`).
  Fourth arc 2026-08-20 (Session 6): the four dogfood findings FIXED in
  one worker pass (324→345 tests) — `ctx sessions heartbeat` + automatic
  heartbeat from the PostToolUse hook, intent recovered on re-claim,
  UTF-8 stdio, `pack --budget` (literal) + oversized-top-hit note;
  VERSION 0.2.1.
  Fifth arc 2026-08-21 (Session 7): the external review's six-item worker
  brief landed in one pass (345→360 tests) — distinct age-gate drop reason +
  stats labels, age-gate exemption reachable for 1–2-term tasks (unpadded
  coverage), `core_budget_tokens` knob un-conflating doctor's L1 ceiling,
  README status/estimator line, and a Bash claim-overlap ADVISORY in the
  PostToolUse hook (observe-only; the guard's deny path stays singular);
  VERSION 0.2.2.
  Sixth arc 2026-08-21 (Session 8): TODO backlog CLEARED through the system
  (360→381 tests) — `init_run` emitted + `initialized_at` in stats,
  `yield_scan` event per composition run, example corpus at
  `examples/corpus/` packed+doctored in CI, live ctx-yield composition
  confirmed (0.1.0 on PATH, ran/not degraded), sessions docstring/Knobs,
  age-key pin, history stamp fixed; three items shelved with reasons.
  VERSION 0.2.3. Open: only the two [human] items (PyPI, marketplace).
  Session 9 (2026-08-21) was a manager session only: ctx-build Waves 1–5
  built through /sia:build from here (all ten modules, 265 tests, pushed to
  private Gitea); no engine change; two soft dogfood notes in the log
  (`ctx init --answers {}` bootstrap path, thin `ctx pack` on a young corpus).
  Same arc 2026-08-23: ctx-build's loop closed LIVE from here (sweeps 5–7,
  router oscillation-resolution fix, terminate + handoff reached; ctx-build
  Session 4). Its ship gate: two real-target builds.
  Session 10 (2026-08-23): manager session on the board again — Isolde inbox
  drained + built (0.2.2) and a weekly-review engine defect found by the
  read-through and fixed (0.2.3), three workers, no engine change here; one
  Low ergonomics note filed (doctor's 0s-stale warning after `ctx stats`).
  Session 11 (2026-08-23): manager session on the board — Lexi inbox drained
  + built (0.1.3 shipped + deployed, live cards repaired, Khan auto-card lane
  confirmed live); board/heartbeat held again, no new engine findings.
  Session 12 (2026-08-24): **THE PHASE-2 MIGRATION — noctuem's SIA hub now
  RUNS ON THIS ENGINE.** SIA-v4 spec authored (plan-spec, 4 scouts), approved,
  scaffolded, built agentic (7 modules incl. a defect-fixed converter, 339-file
  corpus seed, doctor clean, verification 7/7 mutation-tested), and CUT OVER
  attended the same arc (v3 frozen, path swapped, global config repointed,
  server timer swapped, auto-memory retired machine-wide). Engine held the
  whole way — zero engine defects hit during the migration; one Low finding
  filed (budget knobs denominated in estimator units), and Session 10's
  0s-stale doctor warning reproduced in the new hub day one (strengthens that
  item). OQ1 RESOLVED by hub ruling: per-repo boards, no cross-repo bridge.
- **Next:** [human] PyPI publish 0.2.5 + marketplace listing (existing local
  users run on the local editable install until then). Engineering backlog:
  the pack-relevance Medium item and two Low ergonomics items. New work arrives via dogfood (now including the hub
  itself) or ctx-build (ship gate: two real-target builds — the SIA-v4 build
  arguably qualifies as one; noctuem's call).
- **Guard (PUBLIC repo):** never commit the repo owner's personal name or
  absolute local paths; author identity is Noctuem. Grep staged changes before
  every push.
- **Pointers:** `TODO.md` (backlog), `Session_Log.md` (append-only session
  record).

<!-- upstream-reconciled: #17 2026-08-18 -->
<!-- sia-reconciled: #18 2026-08-20 -->
