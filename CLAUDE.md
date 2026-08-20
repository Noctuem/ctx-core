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
  a board-monitored manager session — 5/5 routed, no new engine findings.
- **Next:** PyPI publish, marketplace listing (TODO); fix the three dogfood
  findings (small, all scoped in TODO Medium).
- **Guard (PUBLIC repo):** never commit the repo owner's personal name or
  absolute local paths; author identity is Noctuem. Grep staged changes before
  every push.
- **Pointers:** `TODO.md` (backlog), `Session_Log.md` (append-only session
  record).

<!-- upstream-reconciled: #17 2026-08-18 -->
<!-- sia-reconciled: #18 2026-08-20 -->
