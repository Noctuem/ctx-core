# ctx-core — session handoff

- **Spec:** maintained privately; v0.1.0 implements it in full (all ten
  modules).
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
- **Next:** [human] PyPI publish 0.2.3 + marketplace listing. Engineering
  backlog is empty; new work arrives via dogfood findings or the ctx-build
  sibling (spec staged in the hub `new/` lane, private Gitea first). OQ1
  (cross-repo claims) stays the one open design item — decide before
  ctx-build runs parallel builders.
- **Guard (PUBLIC repo):** never commit the repo owner's personal name or
  absolute local paths; author identity is Noctuem. Grep staged changes before
  every push.
- **Pointers:** `TODO.md` (backlog), `Session_Log.md` (append-only session
  record).

<!-- upstream-reconciled: #17 2026-08-18 -->
<!-- sia-reconciled: #18 2026-08-20 -->
