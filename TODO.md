# TODO — ctx-core

## High

- [ ] Publish `ctx-core` 0.2.1 to PyPI (maintainer action; README quick-start
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

- [ ] Docs drift: `sessions.py` module docstring says `Knobs` does not yet carry the
  session knobs / awaits m14 wiring — they exist (config.py) and every call site (CLI
  `_session_board`, all four hooks) threads them through; fix the comment, optionally
  let `SessionBoard.__init__` read Knobs directly [2026-08-21, state-report survey]
- [ ] `EventKind.INIT_RUN` is defined and never emitted (`init.py` never touches the
  log) — wire `init` to emit it (stats could report "initialized N days ago") or delete
  it [2026-08-21, state-report survey]
- [ ] Stats `_classify_drop_reason` misses the intake-cap reason ("unrouted intake item
  over the … cap") — it always folds into `other`; add the prefix (one line)
  [2026-08-21, state-report survey]
- [ ] `INTAKE_ROUTE_AGE_KEYS` hedges four key names; the emitter writes `age_days` only —
  pin one, delete three [2026-08-21, state-report survey]
- [ ] Pack the engine's own corpus once so this repo's stats products stop reporting an
  empty packs section (0 `context_assembled` in its own log) and the index-freshness
  check has a baseline [2026-08-21, state-report survey]

- [ ] Cosmetic: `_move_to_history`'s stamp suffix never yields the `Z` the comment
  promises (first `.replace` already turns `+00:00` into `+0000`); files stay unique
  and parse fine — tidy the comment or the replace chain [2026-08-20, found landing the
  intent-recovery fix]

## Shelved

## Fleeting Ideas

## Done
- [x] `ctx pack` has no per-invocation `--budget N` override; the only knobs
  are the 8000 default (2800 under `--summary`) or editing `.ctxrc.toml`.
  Found dogfooding a fresh corpus whose three load-bearing notes ran 3.8k /
  3.8k / 6.7k tokens: `--summary` dropped all three as `oversized` and packed
  low-relevance filler to 100% of budget -- a truthful manifest, but the
  wrong working set. Add `--budget N` (and consider a doctor/pack warning
  when the top-ranked file is itself oversized) [2026-08-20, second dogfood:
  first non-ctx-core corpus] — **FIXED 2026-08-20 (2dc8feb): `--budget N` literal + manifest note when the top-ranked file is oversized**
- [x] Sessions CLI has no `heartbeat` subcommand (only list/claim/release), so
  a CLI-driven session can't refresh liveness and gets swept `expired` at
  stale_seconds=1800 while genuinely active; found dogfooding a ~40-min live
  manager session. Add `ctx sessions heartbeat <id>` (API `.heartbeat()`
  already exists) or make long-lived CLI use ergonomic some other way.
  Consequence observed: after the sweep, re-claiming the same session_id
  registers fresh with intent='' — the sweep+reclaim path silently drops the
  session's intent [2026-08-19, first live dogfood session] — **FIXED 2026-08-20 (d2d0a8f + bcf210e): `ctx sessions heartbeat`, automatic heartbeat from the PostToolUse hook, intent recovered on re-claim via `SessionBoard.last_intent`**
- [x] Windows console mojibake: `ctx stats` markdown (em-dashes) goes through
  cp1252 stdout and renders `?` in UTF-8 terminals; clean under PYTHONUTF8=1.
  Force UTF-8 stdout/stderr in the CLI entry point
  [2026-08-19, first live dogfood session] — **FIXED 2026-08-20 (fa56e1b): UTF-8 stdio forced in `main()`**

- [x] [human] force-push purge run by maintainer; remote history clean, CI green on published 0.2.0 [2026-08-18]
- [x] v0.2.0 built: sessions board (m11), intake + layers (m12), stats
  pipeline (m13), wiring/doctor/guard/analyze skill (m14) + gap-closure fix;
  324 tests [2026-08-18]
- [x] v0.1.0 built end-to-end: all 10 spec modules + CI workflow +
  port-fidelity fix pass + corrupt-log hard-fail fix [2026-08-18]
- [x] m1 scaffold — template corpus layout (`Layout`, `Knobs`, `template/`
  tree, `is_template_state`) [2026-08-18]
- [x] Repo scaffolded + public [2026-08-18]
