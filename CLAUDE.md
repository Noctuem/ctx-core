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
- **Next:** PyPI publish, marketplace listing (TODO); fix the four dogfood
  findings (small, all scoped in TODO Medium — heartbeat CLI, intent on
  sweep+reclaim, Windows stdout encoding, `pack --budget` override; the last
  from Session 5's third dogfood on a fresh non-code corpus, 2026-08-20).
- **Guard (PUBLIC repo):** never commit the repo owner's personal name or
  absolute local paths; author identity is Noctuem. Grep staged changes before
  every push.
- **Pointers:** `TODO.md` (backlog), `Session_Log.md` (append-only session
  record).

<!-- upstream-reconciled: #17 2026-08-18 -->
<!-- sia-reconciled: #18 2026-08-20 -->
