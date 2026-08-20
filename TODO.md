# TODO — ctx-core

## High

- [ ] Publish `ctx-core` 0.2.0 to PyPI (maintainer action; README quick-start
  upgrades to `pip install ctx-core` after) [2026-08-18]
- [ ] Live composition check with a real installed `ctx-yield` (stub-tested
  only so far) [2026-08-18]

## Medium

- [ ] Sessions CLI has no `heartbeat` subcommand (only list/claim/release), so
  a CLI-driven session can't refresh liveness and gets swept `expired` at
  stale_seconds=1800 while genuinely active; found dogfooding a ~40-min live
  manager session. Add `ctx sessions heartbeat <id>` (API `.heartbeat()`
  already exists) or make long-lived CLI use ergonomic some other way.
  Consequence observed: after the sweep, re-claiming the same session_id
  registers fresh with intent='' — the sweep+reclaim path silently drops the
  session's intent [2026-08-19, first live dogfood session]
- [ ] Windows console mojibake: `ctx stats` markdown (em-dashes) goes through
  cp1252 stdout and renders `?` in UTF-8 terminals; clean under PYTHONUTF8=1.
  Force UTF-8 stdout/stderr in the CLI entry point
  [2026-08-19, first live dogfood session]
- [ ] Plugin-marketplace + community listing per docs/ADOPTION.md checklist
  (who submits: maintainer's call) [2026-08-18]
- [ ] Hook upgrade path: switch .claude/hooks event append from package
  import to the `ctx` console script now that the CLI exists [2026-08-18]
- [ ] `yield-bridge`: consider emitting an eventlog line per composition run
  (design note in build records) [2026-08-18]
- [ ] Claim-guard limit: Bash-issued writes are unguarded (named limit in
  .claude/hooks/README.md); revisit if a reliable path signal appears
  [2026-08-18]
- [ ] Intake completion interview (file-and-track shipped; completion prompts
  deferred — revisit if filing-without-completing dominates) [2026-08-18]

## Low

## Shelved

## Fleeting Ideas

## Done

- [x] [human] force-push purge run by maintainer; remote history clean, CI green on published 0.2.0 [2026-08-18]
- [x] v0.2.0 built: sessions board (m11), intake + layers (m12), stats
  pipeline (m13), wiring/doctor/guard/analyze skill (m14) + gap-closure fix;
  324 tests [2026-08-18]
- [x] v0.1.0 built end-to-end: all 10 spec modules + CI workflow +
  port-fidelity fix pass + corrupt-log hard-fail fix [2026-08-18]
- [x] m1 scaffold — template corpus layout (`Layout`, `Knobs`, `template/`
  tree, `is_template_state`) [2026-08-18]
- [x] Repo scaffolded + public [2026-08-18]
