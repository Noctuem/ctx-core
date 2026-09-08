# TODO — ctx-core

## High

- [ ] [human] Publish `ctx-core` 0.2.5 to PyPI (maintainer action — no PyPI token in the session env; README quick-start
  upgrades to `pip install ctx-core` after) [2026-08-18]

## Medium

- [ ] [human] Plugin-marketplace + community listing per docs/ADOPTION.md checklist
  (who submits: maintainer's call — outward-facing publication) [2026-08-18]
- [ ] `ctx pack` misses the one note the task is about when relevance rests on a
  project-name token, and always-include notes can eat the whole budget. Observed on
  a private corpus: `ctx pack "<Project> commercialization milestone spec"` spent all
  45k on pinned intake notes, the corpus TODO (14.5k) and two unrelated specs, then
  dropped the project's own spec and the two spec-writing standards as over-budget —
  the three files the task needed; the project spec never ranked at all. Candidate
  fixes: (a) boost notes whose path/title matches a task token; (b) cap the share of
  the budget always-include intake + TODO may take, so ranked candidates keep a floor.
  [SIA hub dogfood 2026-09-07]

## Low

- [ ] Budget knobs (`core_budget_tokens`, pack budget) are denominated in the engine's
  `approx_tokens` (`len//4`) estimator units, not real tokens — real usage runs ~×1.63
  higher (n=24 hub measurement), and any future estimator change silently reprices every
  corpus's budgets. Consider: document the unit explicitly in Knobs, or accept a
  real-token mode with a calibration factor. [dogfood SIA-v4 seed, 2026-08-24]
- [ ] doctor's stats-staleness warning fires at "0s of newer activity" when `ctx stats`
  and `ctx doctor` run back-to-back — the stats run's own event (or doctor's) is what
  makes the products "stale". Exclude the stats/doctor bookkeeping events from the
  staleness clock, or add a small grace window. Ergonomics, not corruption.
  [dogfood Session 10, 2026-08-23]

## Shelved
- [ ] Hook upgrade path: switch .claude/hooks event append from package
  import to the `ctx` console script now that the CLI exists [2026-08-18] — **SHELVED 2026-08-21 [noctuem]: the import path stays; a subprocess per tool call taxes every tool use and the hooks already fail open if the import fails. Reopen only if the package ever can't be imported from the hook env.**
- [ ] Claim-guard limit: Bash-issued writes are still unguarded — the guard's
  `PreToolUse` deny path stays Edit/MultiEdit/Write/NotebookEdit only; a
  reliable pre-write path signal for Bash still doesn't exist (a shell
  command's targets can sit in arbitrary argv positions). **2026-08-21
  (review pass item 5):** added an observe-only advisory instead —
  `ctx_eventlog_hook.py` mtime-checks other live sessions' claimed files
  after a Bash call and appends `claim_overlap_observed` (windowed count in
  `ctx stats`' Sessions section); never denies. Named limit stays documented
  in `.claude/hooks/README.md`; revisit the guard gap itself if a reliable
  path signal ever appears [2026-08-18] — **SHELVED 2026-08-21: the observe-only Bash claim-overlap advisory landed (bfa9446); the guard gap itself stays open by design until the harness exposes a reliable path signal.**
- [ ] Intake completion interview (file-and-track shipped; completion prompts
  deferred — revisit if filing-without-completing dominates) [2026-08-18] — **SHELVED 2026-08-21: no evidence yet that filing-without-completing dominates; reopen on that signal.**

## Fleeting Ideas

## Done
- [x] `ctx doctor` hook-silence advisory lets one historical
  `hook_post_tool_use` event mask later silent runs, and manual hookless
  sessions have no explicit event marker; bound the check to the recent
  threshold-sized doctor window and mark hookless session records generically.
  **FIXED 2026-09-08 (0.2.5): the knob now bounds consecutive
  hook-expected doctor runs since the last hook event; `session_start`
  accepts `hookless: true`, with absent/false preserving the old
  hook-expected meaning.**
  [Codex integration audit 2026-09-08]
- [x] `ctx stats` counts routed/archive stubs still present under
  `notes/intake/` as unrouted even though the canonical `ctx intake list`
  queue admits only `ctx:layer: new`; make the stats fold use that queue.
  **FIXED 2026-09-08 (0.2.5): `_fold_intake` now consumes `intake_list`;
  output schema unchanged.**
  [Codex integration audit 2026-09-08]
- [x] Live composition check with a real installed `ctx-yield` (stub-tested
  only so far) [2026-08-18] — **FIXED 2026-08-21: `ctx-yield` 0.1.0 was
  already installed and on PATH (`pip show ctx-yield`) — ran `ctx stats
  --root examples/corpus`; `var/stats/summary.json`'s `yield` section
  reports `ran: true`, `degraded: false`, `warning: null` (1 composition
  run recorded) — live end-to-end composition confirmed, no `--exact` /
  API key used**
- [x] `yield-bridge`: consider emitting an eventlog line per composition run
  (design note in build records) [2026-08-18] — **FIXED 2026-08-21
  (16b2589): `yield_scan` gets an optional `event_log` param and appends
  one `yield_scan` event per call ({ran, degraded, warning, n_entries,
  timeout_s}) for every outcome; `compute_stats` threads its own optional
  `event_log` through (stats is the caller) and folds a windowed
  `yield_runs_in_window` count into the yield section**
- [x] Docs drift: `sessions.py` module docstring says `Knobs` does not yet carry the
  session knobs / awaits m14 wiring — they exist (config.py) and every call site (CLI
  `_session_board`, all four hooks) threads them through; fix the comment, optionally
  let `SessionBoard.__init__` read Knobs directly [2026-08-21, state-report survey] —
  **FIXED 2026-08-21 (93c2c85): docstring corrected; `SessionBoard.__init__` now
  accepts `knobs: Knobs | None` and reads `session_stale_seconds`/
  `session_heartbeat_seconds` from it whenever the plain keyword params are left at
  their new `None` default (an explicit keyword still wins)**
- [x] `EventKind.INIT_RUN` is defined and never emitted (`init.py` never touches the
  log) — wire `init` to emit it (stats could report "initialized N days ago") or delete
  it [2026-08-21, state-report survey] — **FIXED 2026-08-21 (781db24): `run_interview`
  emits one `INIT_RUN` event per profile write (`{customized, fields}`); `ctx stats`
  folds the newest into `window.initialized_at` (null when absent) and renders one line**
- [x] `INTAKE_ROUTE_AGE_KEYS` hedges four key names; the emitter writes `age_days` only —
  pin one, delete three [2026-08-21, state-report survey] — **FIXED 2026-08-21
  (9c815f2): pinned to `INTAKE_ROUTE_AGE_KEY = "age_days"` (the emitter's only key);
  the other three deleted, with a regression test proving a retired key name is no
  longer silently honored**
- [x] The engine repo is NOT a corpus instance (no core/ or notes/ trees), so "pack the
  engine's own corpus" was mis-framed — the review pass's acceptance `ctx pack` run
  (2026-08-21) produced the first `context_assembled` event here but had zero rankable
  candidates. Decide: ship a small example corpus (e.g. `examples/corpus/`) that CI
  packs as a live fixture, or retire this item [2026-08-21, amended at review-pass
  landing] — **FIXED 2026-08-21 (f6b9153): shipped `examples/corpus/` (fictional,
  realistic-shaped — pinned note, decisions-tagged note, one note deliberately
  oversized for its own small `pack_budget_tokens`); CI runs `ctx doctor`/`ctx pack`
  against it as a live fixture distinct from the template-built scratch corpus;
  README quick-start points to it**
- [x] Cosmetic: `_move_to_history`'s stamp suffix never yields the `Z` the comment
  promises (first `.replace` already turns `+00:00` into `+0000`); files stay unique
  and parse fine — tidy the comment or the replace chain [2026-08-20, found landing the
  intent-recovery fix] — **FIXED 2026-08-21 (4f50af0): reordered the two `.replace()`
  calls so the stamp actually ends in `Z`; `_HISTORY_STAMP_RE` updated to match the
  real pattern, and a test asserts the disambiguated filename fits it**
- [x] Stats `_classify_drop_reason` misses the intake-cap reason ("unrouted intake item
  over the … cap") — it always folds into `other`; add the prefix (one line)
  [2026-08-21, state-report survey] — **FIXED 2026-08-21: `_DROP_REASON_PREFIXES` gained
  `intake_cap` alongside a new `age_gated` label for `packer.py`'s distinct age-gate
  drop reason (external review pass, item 1)**
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
