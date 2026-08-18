# TODO — ctx-core

## High

- [ ] [human] Run `git push --force-with-lease origin main` once — local
  history is amended to purge an internal marker string from the pushed tip
  commit; local `main` is the clean canonical state (a deny rule blocks the
  session from pushing it) [2026-08-18]
- [ ] Publish `ctx-core` 0.1.0 to PyPI (maintainer action; README quick-start
  upgrades to `pip install ctx-core` after) [2026-08-18]
- [ ] Live composition check with a real installed `ctx-yield` (stub-tested
  only so far) [2026-08-18]

## Medium

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

- [x] v0.2.0 built: sessions board (m11), intake + layers (m12), stats
  pipeline (m13), wiring/doctor/guard/analyze skill (m14) + gap-closure fix;
  324 tests [2026-08-18]
- [x] v0.1.0 built end-to-end: all 10 spec modules + CI workflow +
  port-fidelity fix pass + corrupt-log hard-fail fix [2026-08-18]
- [x] m1 scaffold — template corpus layout (`Layout`, `Knobs`, `template/`
  tree, `is_template_state`) [2026-08-18]
- [x] Repo scaffolded + public [2026-08-18]
