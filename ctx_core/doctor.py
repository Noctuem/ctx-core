"""doctor: single CI-gateable health-check gate over one ctx-core corpus.

Eight checks (v0.2 added three): four HARD (any one failing makes the whole
run non-zero-exit-worthy; the CLI's exact exit-code wrapping is m10's job —
this module only reports) and four SOFT (advisory, never fail the run):

- Corpus integrity (HARD): every note carrying a `sources:` front-matter
  handle (see `ctx_core.archive`'s stub format) must resolve to a live
  content-addressed file under `var/archive/`. A dangling stub means the
  original text is unrecoverable — the sharpest possible corpus defect.
- Event-log chain validation (HARD): `EventLog.verify()` on the corpus's
  event log must pass. A missing/empty log verifies trivially (nothing to
  break yet), so a fresh corpus is never penalized for not having run
  anything yet.
- Index freshness (SOFT): has any indexed file changed since the last
  recorded `CONTEXT_ASSEMBLED` pack? A corpus that was never packed has
  nothing to compare against and is never warned about — only a corpus that
  WAS packed and has since drifted gets the advisory nudge to re-pack.
- Still-template check (SOFT): `Layout.is_template_state()` — a nudge, not a
  defect; a fresh clone legitimately starts here.
- Budget check (HARD): force-loaded L1 (`core/`) must fit inside
  `knobs.core_budget_tokens` (defaults to `knobs.pack_budget_tokens` when
  unset — see `_check_l1_budget`), using the exact same token estimate the
  packer uses (`indexing.build_index`'s `Entry.tokens`, itself
  `approx_tokens` —
  one seam, not reinvented here).
- Unrouted-New check (SOFT always, escalating to HARD): every item still
  sitting in `notes/intake/` (m12) is a warning the moment it exists --
  visible, never a silent leak -- and becomes a hard failure once its
  age-in-days passes `knobs.intake_max_age_days`.
- Stale-session-files advisory (SOFT): a `var/sessions/live/*.json` file (m11)
  whose heartbeat has already aged past `knobs.session_stale_seconds` but
  hasn't been swept to history yet by any real `SessionBoard` call. Read-only
  -- this check does NOT sweep (unlike `SessionBoard.list()`), matching
  doctor's report-only contract.
- Stats-product staleness advisory (SOFT): `var/stats/summary.json` (m13)
  older than the most recent event already in the log -- products are cheap
  to regenerate, so this is a nudge to run `ctx stats` again, never a defect.

Every `doctor()` call emits exactly one `DOCTOR_RUN` event, the same
injectable-event-log pattern `packer.pack` and `archive.archive_note` use —
a test points it at an isolated fixture log; the real gate leaves it to the
default derived from `knobs.eventlog_path`.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from .archive import SOURCES_KEY, archived_path_for_handle
from .config import Knobs
from .events import EventKind, EventLog, EventLogCorruptError, default_eventlog_path
from .indexing import FRONT_MATTER, Entry, build_index
from .intake import intake_list
from .layout import Layout

# ==========================================================================
# Constants
# ==========================================================================

#: Keys may contain colons or dots (namespaced front-matter keys) — mirrors
#: `indexing._FRONT_MATTER_KV` exactly, but that pattern is private to that
#: module, and this check only ever needs ONE key (`sources`) rather than
#: the full front-matter parse `indexing`/`archive` already do internally —
#: not worth importing a private symbol for.
_FRONT_MATTER_KV = re.compile(r"^\s*([\w:.-]+)\s*:\s*(.*)$")

#: How many changed-since-last-pack file names the freshness warning names
#: outright before collapsing the rest into a "+N more" tail — mirrors
#: `packer.Manifest.render`'s own drop-list cap in spirit (named, bounded,
#: never silently truncated without saying so).
FRESHNESS_MAX_NAMED = 5


# ==========================================================================
# DoctorReport
# ==========================================================================


@dataclass
class DoctorReport:
    """Result of one `doctor()` run.

    `ok` is a computed property, never a separately-stored field, so the
    contract ("`.ok` is False iff `hard_failures` is non-empty") holds by
    construction — there is no way to build a `DoctorReport` where the two
    disagree.
    """

    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard_failures


# ==========================================================================
# Corpus integrity (HARD)
# ==========================================================================


def _extract_sources_handle(raw: str) -> str | None:
    """The `sources:` front-matter value, if this file's front matter has
    one (i.e. it's an archive stub per `ctx_core.archive`'s stub format) —
    `None` for an ordinary note. Uses `indexing.FRONT_MATTER` (the one
    exported front-matter boundary regex) so this stays in lockstep with
    how `build_index` itself finds the front-matter block.
    """
    m = FRONT_MATTER.match(raw)
    if not m:
        return None
    for line in m.group(1).splitlines():
        kv = _FRONT_MATTER_KV.match(line)
        if not kv:
            continue
        key, value = kv.group(1).strip(), kv.group(2).strip()
        if key == SOURCES_KEY:
            return value
    return None


def _check_corpus_integrity(layout: Layout, entries: list[Entry]) -> list[str]:
    """Every note claiming a `sources:` handle must resolve to a live file
    under `var/archive/` — the reversibility guarantee `archive.py` promises,
    checked here rather than trusted. Names the specific note path, the
    handle it claims, and the path it failed to resolve to, so a CI log
    reader can act without re-running locally.
    """
    failures: list[str] = []
    for entry in entries:
        path = layout.root / entry.path
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # unreadable is `build_index`'s own census concern, not this check's
        handle = _extract_sources_handle(raw)
        if handle is None:
            continue
        archived_path = archived_path_for_handle(layout, handle)
        if not archived_path.is_file():
            failures.append(
                f"{entry.path}: dangling archive stub — `{SOURCES_KEY}: {handle}` "
                f"does not resolve to a file at {archived_path}"
            )
    return failures


# ==========================================================================
# Event-log chain validation (HARD)
# ==========================================================================


def _check_eventlog_chain(event_log: EventLog) -> list[str]:
    """`EventLog.verify()` — a missing/empty log verifies trivially (`True,
    None`), so a corpus that has never run anything is never penalized here.
    """
    ok, first_break = event_log.verify()
    if ok:
        return []
    return [
        f"event log chain broken at line {first_break} in {event_log.path} — "
        "a record was edited, reordered, inserted, or removed outside the append path"
    ]


# ==========================================================================
# Index freshness (SOFT)
# ==========================================================================


def _last_context_assembled_ts(event_log: EventLog) -> float | None:
    """Epoch timestamp of the most recent `CONTEXT_ASSEMBLED` event in
    `event_log`, or `None` if the log is missing/empty or has never recorded
    one. A malformed line is skipped rather than raising — this is an
    advisory check, not the chain-integrity one (`_check_eventlog_chain`
    already owns reporting corruption as a hard failure).
    """
    if not event_log.path.is_file():
        return None
    text = event_log.path.read_text(encoding="utf-8")
    if not text:
        return None
    last_ts: float | None = None
    for line in text.split("\n"):
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("kind") != EventKind.CONTEXT_ASSEMBLED.value:
            continue
        try:
            last_ts = datetime.fromisoformat(record["ts"]).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
    return last_ts


def _check_index_freshness(entries: list[Entry], event_log: EventLog) -> list[str]:
    """Has any indexed file changed since the last recorded pack? A corpus
    that has never been packed has nothing to compare against and is not
    warned about — only drift AFTER a known-good pack is "stale," never the
    mere absence of one. Advisory only: `pack()` rebuilds its index fresh on
    every call (see `indexing.build_index`'s own docstring), so this never
    reflects a genuinely stale on-disk index — only a nudge that the working
    set a session already loaded may not reflect the latest edits.
    """
    last_pack_ts = _last_context_assembled_ts(event_log)
    if last_pack_ts is None:
        return []
    stale = sorted(
        (e for e in entries if e.mtime > last_pack_ts),
        key=lambda e: e.mtime,
        reverse=True,
    )
    if not stale:
        return []
    named = ", ".join(e.path for e in stale[:FRESHNESS_MAX_NAMED])
    if len(stale) > FRESHNESS_MAX_NAMED:
        named += f" (+{len(stale) - FRESHNESS_MAX_NAMED} more)"
    return [
        f"corpus changed since the last recorded pack — {len(stale)} file(s) modified: "
        f"{named} — the packer's working set may be stale; run `ctx pack` again"
    ]


# ==========================================================================
# Still-template check (SOFT)
# ==========================================================================


def _check_template_state(layout: Layout) -> list[str]:
    """`Layout.is_template_state()` — a nudge, never a failure. A fresh
    clone legitimately starts here.
    """
    if layout.is_template_state():
        return [
            "corpus is in template state (core/profile.md is still the shipped "
            "placeholder and notes/ has no domain content) — expected for a fresh "
            "clone; run `ctx init` to customize it"
        ]
    return []


# ==========================================================================
# Budget check (HARD)
# ==========================================================================


def _check_l1_budget(entries: list[Entry], knobs: Knobs) -> list[str]:
    """Force-loaded L1 (`core/`) must fit inside `knobs.core_budget_tokens`
    -- its OWN ceiling, un-conflated from `knobs.pack_budget_tokens` (review
    pass item 3, 2026-08-21: reusing the pack budget for both meant
    worst-case context was core + a full pack, up to 2x the one knob an
    operator actually tunes). `core_budget_tokens` defaults to `None`,
    meaning "same as pack_budget_tokens" -- resolved HERE, at check time,
    rather than baked into `Knobs`'s own default, so a corpus that has
    never heard of `core_budget_tokens` (default OR a `.ctxrc.toml` that
    only sets `pack_budget_tokens`) sees zero behavior change.

    Token counts come from `entries` (built by `indexing.build_index`, which
    computes every `Entry.tokens` via `indexing.approx_tokens`) — the same
    estimate `packer.pack` budgets against, reused rather than reimplemented
    (one seam).
    """
    core_entries = sorted((e for e in entries if e.tier == "core"), key=lambda e: -e.tokens)
    total = sum(e.tokens for e in core_entries)
    budget = int(
        knobs.core_budget_tokens if knobs.core_budget_tokens is not None else knobs.pack_budget_tokens
    )
    if total <= budget:
        return []
    breakdown = ", ".join(f"{e.path} ({e.tokens:,} tok)" for e in core_entries)
    return [
        f"force-loaded L1 (core/) exceeds core_budget_tokens: {total:,} tok > "
        f"{budget:,} tok budget. Contributing files: {breakdown}"
    ]


# ==========================================================================
# Unrouted-New check (SOFT always, HARD past intake_max_age_days)
# ==========================================================================


def _check_unrouted_intake(layout: Layout, knobs: Knobs) -> tuple[list[str], list[str]]:
    """New-layer front-door check (m12). Every unrouted item under
    `notes/intake/` is a warning the moment it exists -- a visible defect-
    in-waiting, never a silent leak -- and escalates to a HARD failure once
    ANY item's age passes `knobs.intake_max_age_days`, per the build spec's
    "warn immediately; hard fail past intake_max_age_days" rule.

    Returns `(hard_failures, warnings)`. A missing `notes/intake/` (m12 not
    in use, or nothing filed yet) reports neither -- absence is not a
    defect, same posture every other advisory check here takes.
    """
    items = intake_list(layout)
    if not items:
        return [], []

    oldest = items[0]  # intake_list() sorts oldest-received first
    warnings = [
        f"{len(items)} unrouted intake item(s) in notes/intake/ -- oldest "
        f"{oldest.age_days:.1f}d old ({oldest.path}); route them with "
        "`ctx intake route` or `ctx intake list` to see the full queue"
    ]

    overdue = [it for it in items if it.age_days > knobs.intake_max_age_days]
    if not overdue:
        return [], warnings

    named = ", ".join(f"{it.path} ({it.age_days:.1f}d)" for it in overdue[:FRESHNESS_MAX_NAMED])
    if len(overdue) > FRESHNESS_MAX_NAMED:
        named += f" (+{len(overdue) - FRESHNESS_MAX_NAMED} more)"
    hard_failures = [
        f"{len(overdue)} intake item(s) unrouted past intake_max_age_days "
        f"({knobs.intake_max_age_days}d): {named} -- route them with `ctx intake route`"
    ]
    return hard_failures, warnings


# ==========================================================================
# Stale-session-files advisory (SOFT)
# ==========================================================================


def _check_stale_sessions(layout: Layout, knobs: Knobs) -> list[str]:
    """Advisory only. Reads `var/sessions/live/*.json` (m11) directly --
    deliberately NOT through `SessionBoard`, whose own `list()`/`claim()`/
    etc. sweep stale entries to history as a side effect (by design, per
    m11's own docs); a report-only health check must never mutate the
    corpus, so this re-implements the same heartbeat-age comparison
    read-only rather than reusing `SessionBoard.list()`.
    """
    live_dir = layout.var / "sessions" / "live"
    if not live_dir.is_dir():
        return []

    now = time.time()
    stale_ids: list[str] = []
    for path in sorted(live_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            age = now - datetime.fromisoformat(data.get("heartbeat_at", "")).timestamp()
        except (TypeError, ValueError):
            age = float("inf")  # missing/malformed heartbeat -- treat as stale
        if age > knobs.session_stale_seconds:
            stale_ids.append(str(data.get("session_id", path.stem)))

    if not stale_ids:
        return []
    return [
        f"{len(stale_ids)} session live-file(s) past session_stale_seconds "
        f"({knobs.session_stale_seconds:.0f}s) and not yet swept to history: "
        f"{', '.join(sorted(stale_ids))} -- clears on the next real sessions-board call"
    ]


# ==========================================================================
# Stats-product staleness advisory (SOFT)
# ==========================================================================


def _last_event_ts(event_log: EventLog) -> float | None:
    """Epoch timestamp of the most recent event of ANY kind in `event_log`.
    Generalizes `_last_context_assembled_ts` above (which filters to one
    kind) -- kept as a separate, small function rather than adding a `kind`
    parameter to the existing one, so the freshness check's already-tested
    behavior is untouched.
    """
    if not event_log.path.is_file():
        return None
    text = event_log.path.read_text(encoding="utf-8")
    if not text:
        return None
    last_ts: float | None = None
    for line in text.split("\n"):
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        try:
            ts = datetime.fromisoformat(record["ts"]).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
        if last_ts is None or ts > last_ts:
            last_ts = ts
    return last_ts


def _check_stats_staleness(layout: Layout, event_log: EventLog) -> list[str]:
    """Advisory only. Compares `var/stats/summary.json`'s (m13) own
    `generated_at` against the most recent event of any kind already in the
    (same) event log -- "products are cheap, regenerate them" per the build
    spec, never a defect. No products yet, or no events yet, is not warned
    about -- nothing to compare against, same posture `_check_index_freshness`
    takes for a corpus that has never been packed.
    """
    summary_path = layout.var / "stats" / "summary.json"
    if not summary_path.is_file():
        return []
    try:
        product = json.loads(summary_path.read_text(encoding="utf-8"))
        generated_ts = datetime.fromisoformat(product["generated_at"]).timestamp()
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return []

    last_event_ts = _last_event_ts(event_log)
    if last_event_ts is None or last_event_ts <= generated_ts:
        return []

    age = last_event_ts - generated_ts
    return [
        f"stats products (var/stats/summary.md|json) are stale -- "
        f"{age:.0f}s of newer event-log activity since they were last "
        "generated; run `ctx stats` to refresh (cheap, zero tokens)"
    ]


# ==========================================================================
# doctor()
# ==========================================================================


def doctor(layout: Layout, knobs: Knobs, *, event_log: EventLog | None = None) -> DoctorReport:
    """Run all eight checks once and report. `event_log`, if given, is used
    in place of the default `EventLog(default_eventlog_path(layout.root,
    knobs.eventlog_path))` -- same injectable pattern `packer.pack` and
    `archive.archive_note` use, so a test can point it at an isolated
    fixture log. The index is built exactly once and shared across every
    check that needs it, rather than each check re-walking the filesystem.
    """
    entries, _census = build_index(layout)
    log = event_log if event_log is not None else EventLog(default_eventlog_path(layout.root, knobs.eventlog_path))

    intake_hard, intake_warn = _check_unrouted_intake(layout, knobs)

    hard_failures: list[str] = []
    hard_failures += _check_corpus_integrity(layout, entries)
    hard_failures += _check_eventlog_chain(log)
    hard_failures += _check_l1_budget(entries, knobs)
    hard_failures += intake_hard

    warnings: list[str] = []
    warnings += _check_index_freshness(entries, log)
    warnings += _check_template_state(layout)
    warnings += intake_warn
    warnings += _check_stale_sessions(layout, knobs)
    warnings += _check_stats_staleness(layout, log)

    report = DoctorReport(hard_failures=hard_failures, warnings=warnings)

    # The DOCTOR_RUN emission below is best-effort reporting on top of an
    # already-computed report, never a precondition for one -- a corrupt
    # log tail (already named in hard_failures by `_check_eventlog_chain`
    # above) must not turn a clean report into a crash. `EventLog.append`
    # raises `EventLogCorruptError` rather than repairing/extending a
    # broken chain, so catch exactly that and downgrade to a warning.
    try:
        log.append(
            EventKind.DOCTOR_RUN,
            {
                "ok": report.ok,
                "n_hard_failures": len(hard_failures),
                "n_warnings": len(warnings),
                "hard_failures": hard_failures,
                "warnings": warnings,
            },
        )
    except EventLogCorruptError as exc:
        report.warnings.append(
            f"DOCTOR_RUN event emission skipped: {exc}"
        )

    return report
