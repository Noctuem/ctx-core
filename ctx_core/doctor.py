"""doctor: single CI-gateable health-check gate over one ctx-core corpus.

Five checks, three of them HARD (any one failing makes the whole run
non-zero-exit-worthy; the CLI's exact exit-code wrapping is m10's job — this
module only reports) and two SOFT (advisory, never fail the run):

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
  `knobs.pack_budget_tokens`, using the exact same token estimate the packer
  uses (`indexing.build_index`'s `Entry.tokens`, itself `approx_tokens` —
  one seam, not reinvented here).

Every `doctor()` call emits exactly one `DOCTOR_RUN` event, the same
injectable-event-log pattern `packer.pack` and `archive.archive_note` use —
a test points it at an isolated fixture log; the real gate leaves it to the
default derived from `knobs.eventlog_path`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from .archive import SOURCES_KEY, archived_path_for_handle
from .config import Knobs
from .events import EventKind, EventLog, EventLogCorruptError, default_eventlog_path
from .indexing import FRONT_MATTER, Entry, build_index
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
    """Force-loaded L1 (`core/`) must fit inside `knobs.pack_budget_tokens`.
    Token counts come from `entries` (built by `indexing.build_index`, which
    computes every `Entry.tokens` via `indexing.approx_tokens`) — the same
    estimate `packer.pack` budgets against, reused rather than reimplemented
    (one seam).
    """
    core_entries = sorted((e for e in entries if e.tier == "core"), key=lambda e: -e.tokens)
    total = sum(e.tokens for e in core_entries)
    budget = int(knobs.pack_budget_tokens)
    if total <= budget:
        return []
    breakdown = ", ".join(f"{e.path} ({e.tokens:,} tok)" for e in core_entries)
    return [
        f"force-loaded L1 (core/) exceeds pack_budget_tokens: {total:,} tok > "
        f"{budget:,} tok budget. Contributing files: {breakdown}"
    ]


# ==========================================================================
# doctor()
# ==========================================================================


def doctor(layout: Layout, knobs: Knobs, *, event_log: EventLog | None = None) -> DoctorReport:
    """Run all five checks once and report. `event_log`, if given, is used
    in place of the default `EventLog(default_eventlog_path(layout.root,
    knobs.eventlog_path))` — same injectable pattern `packer.pack` and
    `archive.archive_note` use, so a test can point it at an isolated
    fixture log. The index is built exactly once and shared across every
    check that needs it, rather than each check re-walking the filesystem.
    """
    entries, _census = build_index(layout)
    log = event_log if event_log is not None else EventLog(default_eventlog_path(layout.root, knobs.eventlog_path))

    hard_failures: list[str] = []
    hard_failures += _check_corpus_integrity(layout, entries)
    hard_failures += _check_eventlog_chain(log)
    hard_failures += _check_l1_budget(entries, knobs)

    warnings: list[str] = []
    warnings += _check_index_freshness(entries, log)
    warnings += _check_template_state(layout)

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
