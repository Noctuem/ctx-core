# TODO — ctx-core

## High

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

## Low

## Shelved

## Fleeting Ideas

## Done

- [x] v0.1.0 built end-to-end: all 10 spec modules + CI workflow +
  port-fidelity fix pass + corrupt-log hard-fail fix [2026-08-18]
- [x] m1 scaffold — template corpus layout (`Layout`, `Knobs`, `template/`
  tree, `is_template_state`) [2026-08-18]
- [x] Repo scaffolded + public [2026-08-18]
