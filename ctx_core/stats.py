"""stats: zero-token aggregation + products.

Stage 2 of the Statistics Mentality (gathering / aggregation / analysis):
`compute_stats` is a pure, deterministic fold over data that already exists
on disk — the hash-chained event log, the corpus index (`build_index`), the
`var/sessions/` dirs, `notes/intake/`, and (optionally) `ctx-yield`'s scan
result. No LLM call, no network call this module makes itself, no wall-clock
read anywhere inside the fold — `now` is always the caller-supplied instant.

Determinism is the whole contract: the same inputs (same event log, same
corpus files, same `since_days`/`now`) must produce byte-identical
`summary.md` / `summary.json` on every run. That means: every iteration is
sorted (never bare dict/set order), every float is rounded to a fixed
precision (`ROUND_PRECISION`), and every number in the rendered products
names its own scope (the window, whether it covers the whole corpus, and
whether ctx-yield actually ran) rather than being read as unscoped truth.

`sessions.py` (m11) and `intake.py` (m12) are sibling modules built in the
same wave as this one and may not exist yet in a given checkout — this
module never imports them. It reads their ON-DISK CONTRACT directly (the
`var/sessions/live|history/*.json` file shape and `notes/intake/*.md` front
matter, both specified in the build spec's Module 11/12 sections) and folds
`SESSION_*`/`INTAKE_*` event kinds by the plain string names their spec
sections name (this codebase's `EventKind` already documents that any
non-empty string is a valid `kind` — the enum is a minimum vocabulary, not a
closed set). See `_build/stats/done.md` for the exact assumption and its
graceful-degradation behavior if a landed m11/m12 ever uses different names.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Knobs
from .events import EventKind, EventLog, default_eventlog_path
from .indexing import FRONT_MATTER, Entry, build_index
from .layout import Layout
from .yield_bridge import NOT_INSTALLED_HINT, YIELD_SCAN_KIND, dead_weight_map, yield_scan

# ==========================================================================
# Named constants — no bare literals below.
# ==========================================================================

#: Where products are written, relative to `layout.var`.
STATS_DIRNAME = "stats"
SUMMARY_MD_FILENAME = "summary.md"
SUMMARY_JSON_FILENAME = "summary.json"

#: Bumped only if the JSON product's shape changes incompatibly.
SCHEMA_VERSION = 1

#: Fixed rounding precision for every float this module emits — the
#: deterministic-engine rule that a different-but-equal summation order
#: must still compare equal, and that regenerating from the same inputs is
#: byte-identical.
ROUND_PRECISION = 4

#: How many top-packed files the md render names outright before relying on
#: the JSON product for the rest (mirrors `packer.Manifest.render`'s own
#: capped-list convention).
TOP_PACKED_LIMIT = 10

#: Cap on how many never-packed paths the md render names outright. The
#: JSON product always carries the full list — this only bounds the
#: human-readable render.
NEVER_PACKED_MD_CAP = 50

#: Cap on how many claim-conflict pairs the md render names outright.
CLAIM_CONFLICTS_MD_CAP = 20

#: Bucket upper edges for the budget-utilization histogram (fraction of
#: budget consumed). The last bucket ("over budget") catches anything past
#: the final edge.
BUDGET_UTILIZATION_BUCKET_EDGES = (0.25, 0.5, 0.75, 1.0)
BUDGET_UTILIZATION_BUCKET_LABELS = ("<25%", "25-50%", "50-75%", "75-100%", ">100%")

#: `kind` strings this module folds from the event log. `EventKind` (m5,
#: frozen 0.1.0) only defines the four kinds that shipped in 0.1.0; none of
#: the v0.2 modules (m11 sessions, m12 intake, m13 stats) own `events.py`,
#: so the SESSION_*/INTAKE_* kinds this build spec calls for have nowhere
#: to live as enum members. Named here as plain strings, following the
#: existing convention exactly (snake_case of the documented event name —
#: e.g. `CONTEXT_ASSEMBLED = "context_assembled"`). If a landed m11/m12
#: emits different literal strings, these counts silently read zero rather
#: than raising — stated in `_build/stats/done.md`, not hidden.
SESSION_START_KIND = "session_start"
SESSION_CLAIM_KIND = "session_claim"
SESSION_RELEASE_KIND = "session_release"
SESSION_EXPIRE_KIND = "session_expire"
INTAKE_ADD_KIND = "intake_add"
INTAKE_ROUTE_KIND = "intake_route"

#: `kind` the `PostToolUse` hook's Bash claim-overlap OBSERVER appends
#: (`.claude/hooks/ctx_eventlog_hook.py`, review pass item 5, 2026-08-21) --
#: advisory only, never a guard; folded here as a windowed count in the
#: Sessions section. Same plain-string convention as the SESSION_*/INTAKE_*
#: kinds above.
CLAIM_OVERLAP_OBSERVED_KIND = "claim_overlap_observed"

#: `var/sessions/live/` and `var/sessions/history/` directory names, per
#: the m11 build spec's on-disk contract.
SESSIONS_SUBDIR = "sessions"
SESSIONS_LIVE_DIRNAME = "live"
SESSIONS_HISTORY_DIRNAME = "history"

#: `notes/intake/` directory name, per the m12 build spec's on-disk
#: contract. An item is "unrouted" for this module's purposes as long as
#: its note is still physically present here — `intake route` (m12) moves
#: the note out (git-visible rename) as part of routing, so presence is a
#: direct, unambiguous ground-truth signal that does not depend on any
#: front-matter field being parsed correctly.
INTAKE_SUBDIR = "intake"

#: Front-matter keys this module reads from an intake note, per the m12
#: build spec's documented front matter (`ctx:layer`, `ctx:source`,
#: `ctx:received`).
INTAKE_SOURCE_KEY = "ctx:source"
INTAKE_RECEIVED_KEY = "ctx:received"
#: Default source when a note's front matter omits `ctx:source` — mirrors
#: `intake_add`'s own documented default (`source="user"`).
INTAKE_DEFAULT_SOURCE = "user"

#: Payload key an `INTAKE_ROUTE` event carries for its "age at routing"
#: figure. The build spec's m12 section said the event "carries source,
#: age-at-routing, and destination" without pinning an exact key name (m12
#: had not landed at build time), so this module originally hedged with
#: four candidate names; `intake.py`'s landed `intake_route` (the only
#: emitter) writes `age_days` and nothing else — pinned to the one real
#: key (TODO Low, state-report survey item 3, 2026-08-21).
INTAKE_ROUTE_AGE_KEY = "age_days"

#: Drop-reason prefixes this module classifies against, matching
#: `packer.py`'s exact wording (`pack()`'s `dropped` list). A reason this
#: module doesn't recognize falls into `"other"` rather than raising —
#: this histogram is read-side and must survive `packer.py`'s wording
#: changing without stats.py knowing about it in lockstep.
_DROP_REASON_PREFIXES = (
    ("larger than the whole working budget", "oversized"),
    ("near-duplicate of an included file", "near_duplicate"),
    ("over budget", "over_budget"),
    ("below relevance/recency cutoff", "below_cutoff"),
    ("older than", "age_gated"),
    ("unrouted intake item over the", "intake_cap"),
)

#: Mirrors `indexing._FRONT_MATTER_KV` — that pattern is private to
#: `indexing.py`, and this module only ever needs a handful of known keys
#: (not the full front-matter parse `indexing`/`archive`/`doctor` each do
#: internally), so it is not worth importing a private symbol for.
_FRONT_MATTER_KV = re.compile(r"^\s*([\w:.-]+)\s*:\s*(.*)$")


# ==========================================================================
# Small pure helpers
# ==========================================================================


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, ROUND_PRECISION)


def _iso(ts: float) -> str:
    """UTC ISO-8601 for a given epoch timestamp — the one timestamp
    convention this codebase uses (see `events.py`).
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_iso(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return None


def _read_events(path: Path) -> list[dict]:
    """Raw event records off disk, best-effort. Malformed lines are
    skipped (never raised) — `doctor()`'s chain-integrity check is the one
    place that treats a broken log as a defect; this is a read-side fold
    over whatever is actually there, same posture `doctor.py`'s own
    `_last_context_assembled_ts` takes. Splits strictly on `"\\n"`, same
    reasoning as `events._read_raw_lines`.
    """
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    if not text:
        return []
    out: list[dict] = []
    for line in text.split("\n"):
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _in_window(record: dict, window_start: float | None) -> bool:
    if window_start is None:
        return True
    ts = _parse_iso(record.get("ts", ""))
    if ts is None:
        return False
    return ts >= window_start


def _classify_drop_reason(reason: str) -> str:
    for prefix, label in _DROP_REASON_PREFIXES:
        if reason.startswith(prefix):
            return label
    return "other"


def _bucket_label(fill: float) -> str:
    for edge, label in zip(BUDGET_UTILIZATION_BUCKET_EDGES, BUDGET_UTILIZATION_BUCKET_LABELS):
        if fill < edge:
            return label
    return BUDGET_UTILIZATION_BUCKET_LABELS[-1]


def _paths_overlap(a: str, b: str) -> bool:
    """Same path, or one is an ancestor/descendant of the other — the claim
    overlap semantics the m11 build spec names for `SessionBoard.check`.
    Path-segment-boundary aware: `notes/foo` does not overlap `notes/foobar`.
    """
    if a == b:
        return True
    pa, pb = a.strip("/").split("/"), b.strip("/").split("/")
    shorter, longer = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    return longer[: len(shorter)] == shorter


def _read_front_matter(raw: str) -> dict[str, str]:
    m = FRONT_MATTER.match(raw)
    if not m:
        return {}
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        mm = _FRONT_MATTER_KV.match(line)
        if mm:
            meta[mm.group(1).strip()] = mm.group(2).strip()
    return meta


def _mean_median(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return statistics.fmean(values), statistics.median(values)


def _last_init_run_ts(events: list[dict]) -> str | None:
    """The `ts` (ISO-8601 string, as recorded) of the newest `INIT_RUN`
    event in `events`, or `None` if there is none. Read over the FULL,
    un-windowed event list rather than `windowed` -- "when was this corpus
    last initialized" is a corpus-wide fact, not scoped to `--since`, the
    same posture `doctor.py`'s `_last_context_assembled_ts` takes for the
    same reason (a corpus initialized before the window start must not read
    as never-initialized).
    """
    best_epoch: float | None = None
    best_iso: str | None = None
    for record in events:
        if record.get("kind") != EventKind.INIT_RUN.value:
            continue
        epoch = _parse_iso(record.get("ts", ""))
        if epoch is None:
            continue
        if best_epoch is None or epoch > best_epoch:
            best_epoch = epoch
            best_iso = record.get("ts")
    return best_iso


# ==========================================================================
# StatsReport
# ==========================================================================


@dataclass
class StatsReport:
    """One `compute_stats()` result. Every section names its own scope so a
    reader (human or the stage-3 `ctx-analyze` skill) never mistakes a
    windowed figure for a corpus-wide one, or an absent signal for a clean
    one.
    """

    generated_at: str
    window: dict[str, Any]
    packs: dict[str, Any]
    drop_reasons: dict[str, int]
    budget_utilization: dict[str, Any]
    doctor: dict[str, Any]
    intake: dict[str, Any]
    sessions: dict[str, Any]
    yield_info: dict[str, Any]

    def to_json(self) -> dict:
        """The machine-readable product. Stable, sorted-key schema —
        `write_products` is what actually serializes it (with
        `sort_keys=True`), this just assembles the nested dict.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "window": self.window,
            "packs": self.packs,
            "drop_reasons": self.drop_reasons,
            "budget_utilization": self.budget_utilization,
            "doctor": self.doctor,
            "intake": self.intake,
            "sessions": self.sessions,
            "yield": self.yield_info,
        }

    def render_md(self) -> str:
        w = self.window
        lines = [
            "# ctx-core stats",
            "",
            f"Window: {w['since_days']!r} day(s)"
            if w["since_days"] is not None
            else "Window: all time",
            f"From `{w['start'] or '(unbounded)'}` through `{w['end']}`.",
            f"Generated `{self.generated_at}`.",
            f"Initialized `{w['initialized_at']}`."
            if w.get("initialized_at")
            else "Not yet initialized — no `ctx init` run recorded.",
            "",
            "## Packs",
            "",
            f"{self.packs['count_in_window']:,} pack(s) recorded in this window.",
            "",
            "**Top-packed files (this window):**",
            "",
        ]
        top = self.packs["top_packed"][:TOP_PACKED_LIMIT]
        if top:
            for path, count in top:
                lines.append(f"- `{path}` — {count:,}x")
        else:
            lines.append("- (none — no packs recorded in this window)")

        never = self.packs["never_packed"]
        lines += ["", f"**Never packed in this window ({len(never):,}):**", ""]
        if never:
            for path in never[:NEVER_PACKED_MD_CAP]:
                lines.append(f"- `{path}`")
            if len(never) > NEVER_PACKED_MD_CAP:
                lines.append(f"- ...and {len(never) - NEVER_PACKED_MD_CAP:,} more (listing capped at {NEVER_PACKED_MD_CAP})")
        else:
            lines.append("- (none)")

        lines += ["", "## Drop-reason histogram", ""]
        if self.drop_reasons:
            for reason, count in sorted(self.drop_reasons.items()):
                lines.append(f"- `{reason}`: {count:,}")
        else:
            lines.append("- (no drops recorded in this window)")

        bu = self.budget_utilization
        lines += ["", "## Budget utilization (this window)", ""]
        if bu["count"]:
            lines.append(
                f"{bu['count']:,} pack(s) — min {bu['min']:.0%}, mean {bu['mean']:.0%}, "
                f"median {bu['median']:.0%}, max {bu['max']:.0%}."
            )
            for label in BUDGET_UTILIZATION_BUCKET_LABELS:
                lines.append(f"- {label}: {bu['buckets'].get(label, 0):,}")
        else:
            lines.append("- (no packs recorded in this window — not measured)")

        d = self.doctor
        lines += ["", "## Doctor pass rate (this window)", ""]
        if d["n_runs"]:
            lines.append(f"{d['n_pass']:,}/{d['n_runs']:,} clean ({d['pass_rate']:.0%}).")
        else:
            lines.append("- no `ctx doctor` runs recorded in this window — not measured")

        ik = self.intake
        lines += ["", "## Intake", ""]
        if not ik["dir_present"]:
            lines.append("- `notes/intake/` absent — 0 unrouted (module m12 not in use, or nothing filed yet)")
        else:
            lines.append(f"Unrouted backlog: {ik['unrouted_total']:,} item(s).")
            for source, count in sorted(ik["unrouted_by_source"].items()):
                lines.append(f"- {source}: {count:,}")
            if ik["unrouted_no_timestamp"]:
                lines.append(f"- ({ik['unrouted_no_timestamp']:,} item(s) missing/unparsable `{INTAKE_RECEIVED_KEY}`)")
        lat = ik["latency"]
        lines += ["", "**Intake latency (add -> route), this window:**", ""]
        if lat["n_measured"]:
            lines.append(
                f"{lat['n_measured']:,} measured (+ {lat['n_unmeasured']:,} route event(s) with no readable "
                f"latency field) — min {lat['min_days']:g}d, mean {lat['mean_days']:g}d, "
                f"median {lat['median_days']:g}d, max {lat['max_days']:g}d."
            )
        else:
            lines.append(
                f"- not measured ({lat['n_unmeasured']:,} route event(s) in window, none with a readable latency field)"
                if lat["n_unmeasured"]
                else "- not measured (no `ctx intake route` events recorded in this window)"
            )

        s = self.sessions
        lines += ["", "## Sessions", ""]
        if not s["dir_present"]:
            lines.append("- `var/sessions/` absent — 0 sessions (module m11 not in use, or none yet)")
        else:
            lines.append(
                f"Live: {s['n_live']:,}. History: {s['n_history']:,}. This window — "
                f"starts: {s['starts_in_window']:,}, claims: {s['claims_in_window']:,}, "
                f"releases: {s['releases_in_window']:,}, expires: {s['expires_in_window']:,}, "
                f"Bash claim-overlaps observed: {s['claim_overlaps_observed_in_window']:,}."
            )
            conflicts = s["claim_conflicts"]
            lines += ["", f"**Claim conflicts (current live snapshot, {len(conflicts):,}):**", ""]
            if conflicts:
                for a, b, path in conflicts[:CLAIM_CONFLICTS_MD_CAP]:
                    lines.append(f"- `{a}` <-> `{b}` over `{path}`")
                if len(conflicts) > CLAIM_CONFLICTS_MD_CAP:
                    lines.append(f"- ...and {len(conflicts) - CLAIM_CONFLICTS_MD_CAP:,} more")
            else:
                lines.append("- (none)")

        y = self.yield_info
        lines += ["", "## ctx-yield dead weight", ""]
        lines.append(
            f"{y['yield_runs_in_window']:,} composition run(s) recorded in this window."
        )
        if not y["ran"]:
            lines.append(f"- not measured — {y['warning']}")
        elif y["degraded"]:
            lines.append(f"- ctx-yield ran but its report could not be read: {y['warning']}")
        elif y["dead_weight_files"]:
            for path in y["dead_weight_files"]:
                lines.append(f"- `{path}` — never recalled")
        else:
            lines.append("- ctx-yield ran, clean — nothing force-loaded is going unrecalled")

        return "\n".join(lines) + "\n"


# ==========================================================================
# compute_stats()
# ==========================================================================


def compute_stats(
    layout: Layout,
    knobs: Knobs,
    *,
    since_days: int | float | None = None,
    now: float | None = None,
    event_log: EventLog | None = None,
) -> StatsReport:
    """Fold the event log, the corpus index, `var/sessions/`, `notes/intake/`,
    and (if installed) `ctx-yield`'s scan into one `StatsReport`.

    `now` is read from `time.time()` exactly once, right here, if omitted —
    every computation below uses this one captured value, never a fresh
    wall-clock read, so the whole fold is a pure function of
    `(layout's on-disk state, knobs, since_days, now)`. Pass `now` explicitly
    from a test (or a caller wanting a reproducible replay) to get
    byte-identical products across runs.

    `since_days=None` means an unbounded window (the whole event log /
    corpus history) — the CLI (m14) is expected to supply
    `knobs.stats_window_days` as its own default before calling this when
    `--since` is omitted; this function does not read that knob itself
    (nothing in the v0.2 module set owns `config.py`'s `Knobs` — see
    `_build/stats/done.md`).

    `event_log`, if given, is threaded straight through to
    `yield_bridge.yield_scan` (see `_fold_yield`) so a `ctx stats` run logs
    its own ctx-yield composition run for a LATER `ctx stats` call to fold
    into `yield.yield_runs_in_window` (TODO Medium, "yield-bridge eventlog
    line"). This is the one deliberate exception to the "pure fold" framing
    above: passing `event_log` makes this call a write as well as a read,
    the same way `packer.pack` always writes one event as a side effect of
    an otherwise read-heavy call. `event_log=None` (the default) keeps
    `compute_stats` a pure read.
    """
    effective_now = now if now is not None else time.time()
    window_start = effective_now - float(since_days) * 86_400 if since_days is not None else None

    entries, _census = build_index(layout)
    all_paths = sorted(e.path for e in entries)

    log_path = default_eventlog_path(layout.root, knobs.eventlog_path)
    events = _read_events(log_path)
    windowed = [e for e in events if _in_window(e, window_start)]

    packs = _fold_packs(windowed, all_paths)
    drop_reasons = _fold_drop_reasons(windowed)
    budget_utilization = _fold_budget_utilization(windowed)
    doctor = _fold_doctor(windowed)
    intake = _fold_intake(layout, windowed, effective_now)
    sessions = _fold_sessions(layout, windowed)
    yield_info = _fold_yield(layout, knobs, entries, windowed, event_log=event_log)

    return StatsReport(
        generated_at=_iso(effective_now),
        window={
            "since_days": since_days,
            "start": _iso(window_start) if window_start is not None else None,
            "end": _iso(effective_now),
            "initialized_at": _last_init_run_ts(events),
        },
        packs=packs,
        drop_reasons=drop_reasons,
        budget_utilization=budget_utilization,
        doctor=doctor,
        intake=intake,
        sessions=sessions,
        yield_info=yield_info,
    )


# --- packs / top-packed / never-packed -----------------------------------


def _fold_packs(windowed: list[dict], all_paths: list[str]) -> dict[str, Any]:
    pack_events = [e for e in windowed if e.get("kind") == EventKind.CONTEXT_ASSEMBLED.value]
    counts: dict[str, int] = {}
    for e in pack_events:
        for item in e.get("payload", {}).get("items", []) or []:
            if not item.get("dropped", False):
                path = item.get("path")
                if path:
                    counts[path] = counts.get(path, 0) + 1
    top_packed = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    never_packed = sorted(p for p in all_paths if p not in counts)
    return {
        "count_in_window": len(pack_events),
        "top_packed": [[p, c] for p, c in top_packed],
        "never_packed": never_packed,
    }


def _fold_drop_reasons(windowed: list[dict]) -> dict[str, int]:
    pack_events = [e for e in windowed if e.get("kind") == EventKind.CONTEXT_ASSEMBLED.value]
    histogram: dict[str, int] = {}
    for e in pack_events:
        for item in e.get("payload", {}).get("items", []) or []:
            if item.get("dropped", False):
                label = _classify_drop_reason(str(item.get("drop_reason", "")))
                histogram[label] = histogram.get(label, 0) + 1
    return histogram


def _fold_budget_utilization(windowed: list[dict]) -> dict[str, Any]:
    pack_events = [e for e in windowed if e.get("kind") == EventKind.CONTEXT_ASSEMBLED.value]
    fills: list[float] = []
    for e in pack_events:
        payload = e.get("payload", {})
        budget = payload.get("budget_tokens") or 0
        total = payload.get("total_tokens") or 0
        fills.append(total / budget if budget else 0.0)
    fills.sort()
    mean, median = _mean_median(fills)
    buckets: dict[str, int] = {label: 0 for label in BUDGET_UTILIZATION_BUCKET_LABELS}
    for fill in fills:
        buckets[_bucket_label(fill)] += 1
    return {
        "count": len(fills),
        "min": _round(fills[0]) if fills else None,
        "max": _round(fills[-1]) if fills else None,
        "mean": _round(mean),
        "median": _round(median),
        "buckets": buckets,
    }


def _fold_doctor(windowed: list[dict]) -> dict[str, Any]:
    runs = [e for e in windowed if e.get("kind") == EventKind.DOCTOR_RUN.value]
    n_runs = len(runs)
    n_pass = sum(1 for e in runs if e.get("payload", {}).get("ok") is True)
    return {
        "n_runs": n_runs,
        "n_pass": n_pass,
        "pass_rate": _round(n_pass / n_runs) if n_runs else None,
    }


# --- intake -----------------------------------------------------------


def _fold_intake(layout: Layout, windowed: list[dict], effective_now: float) -> dict[str, Any]:
    intake_dir = layout.notes / INTAKE_SUBDIR
    dir_present = intake_dir.is_dir()
    unrouted_by_source: dict[str, int] = {}
    no_timestamp = 0
    if dir_present:
        for p in sorted(intake_dir.glob("*.md")):
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta = _read_front_matter(raw)
            source = meta.get(INTAKE_SOURCE_KEY) or INTAKE_DEFAULT_SOURCE
            unrouted_by_source[source] = unrouted_by_source.get(source, 0) + 1
            if _parse_iso(meta.get(INTAKE_RECEIVED_KEY, "")) is None:
                no_timestamp += 1

    route_events = [e for e in windowed if e.get("kind") == INTAKE_ROUTE_KIND]
    latencies: list[float] = []
    n_unmeasured = 0
    for e in route_events:
        payload = e.get("payload", {})
        value = None
        if INTAKE_ROUTE_AGE_KEY in payload:
            try:
                value = float(payload[INTAKE_ROUTE_AGE_KEY])
            except (TypeError, ValueError):
                value = None
        if value is None:
            n_unmeasured += 1
        else:
            latencies.append(value)
    latencies.sort()
    mean, median = _mean_median(latencies)

    return {
        "dir_present": dir_present,
        "unrouted_total": sum(unrouted_by_source.values()),
        "unrouted_by_source": unrouted_by_source,
        "unrouted_no_timestamp": no_timestamp,
        "latency": {
            "n_measured": len(latencies),
            "n_unmeasured": n_unmeasured,
            "min_days": _round(latencies[0]) if latencies else None,
            "max_days": _round(latencies[-1]) if latencies else None,
            "mean_days": _round(mean),
            "median_days": _round(median),
        },
    }


# --- sessions -----------------------------------------------------------


def _read_session_files(dir_path: Path) -> list[dict]:
    if not dir_path.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(dir_path.glob("*.json")):
        try:
            record = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _fold_sessions(layout: Layout, windowed: list[dict]) -> dict[str, Any]:
    sessions_root = layout.var / SESSIONS_SUBDIR
    dir_present = sessions_root.is_dir()
    live = _read_session_files(sessions_root / SESSIONS_LIVE_DIRNAME)
    history = _read_session_files(sessions_root / SESSIONS_HISTORY_DIRNAME)

    conflicts: list[tuple[str, str, str]] = []
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            sid_a = str(live[i].get("session_id", f"?{i}"))
            sid_b = str(live[j].get("session_id", f"?{j}"))
            claims_a = live[i].get("claims") or []
            claims_b = live[j].get("claims") or []
            found: str | None = None
            for pa in sorted(claims_a):
                for pb in sorted(claims_b):
                    if _paths_overlap(str(pa), str(pb)):
                        found = str(pa) if pa == pb or len(str(pa)) <= len(str(pb)) else str(pb)
                        break
                if found:
                    break
            if found:
                a, b = sorted((sid_a, sid_b))
                conflicts.append((a, b, found))
    conflicts.sort()

    return {
        "dir_present": dir_present,
        "n_live": len(live),
        "n_history": len(history),
        "starts_in_window": sum(1 for e in windowed if e.get("kind") == SESSION_START_KIND),
        "claims_in_window": sum(1 for e in windowed if e.get("kind") == SESSION_CLAIM_KIND),
        "releases_in_window": sum(1 for e in windowed if e.get("kind") == SESSION_RELEASE_KIND),
        "expires_in_window": sum(1 for e in windowed if e.get("kind") == SESSION_EXPIRE_KIND),
        "claim_conflicts": [[a, b, p] for a, b, p in conflicts],
        "claim_overlaps_observed_in_window": sum(
            1 for e in windowed if e.get("kind") == CLAIM_OVERLAP_OBSERVED_KIND
        ),
    }


# --- ctx-yield dead weight ------------------------------------------------


def _fold_yield(
    layout: Layout,
    knobs: Knobs,
    entries: list[Entry],
    windowed: list[dict],
    *,
    event_log: EventLog | None = None,
) -> dict[str, Any]:
    yield_runs_in_window = sum(1 for e in windowed if e.get("kind") == YIELD_SCAN_KIND)
    result = yield_scan(layout, knobs, event_log=event_log)
    if result is None:
        return {
            "ran": False,
            "degraded": False,
            "warning": NOT_INSTALLED_HINT,
            "dead_weight_files": [],
            "yield_runs_in_window": yield_runs_in_window,
        }
    if result.warning is not None:
        return {
            "ran": True,
            "degraded": True,
            "warning": result.warning,
            "dead_weight_files": [],
            "yield_runs_in_window": yield_runs_in_window,
        }
    joined = dead_weight_map(entries, result)
    dead = sorted(path for path, ye in joined.items() if ye.never_recalled)
    return {
        "ran": True,
        "degraded": False,
        "warning": None,
        "dead_weight_files": dead,
        "yield_runs_in_window": yield_runs_in_window,
    }


# ==========================================================================
# write_products()
# ==========================================================================


def write_products(report: StatsReport, layout: Layout) -> list[Path]:
    """Write `summary.md` + `summary.json` under `var/stats/`. Returns the
    two paths written, `[md_path, json_path]`, creating `var/stats/` if
    needed. Overwrites whatever was there — products are cheap to
    regenerate (never hand-edited), so there is no merge/append concern.
    """
    stats_dir = layout.var / STATS_DIRNAME
    stats_dir.mkdir(parents=True, exist_ok=True)

    md_path = stats_dir / SUMMARY_MD_FILENAME
    json_path = stats_dir / SUMMARY_JSON_FILENAME

    md_path.write_text(report.render_md(), encoding="utf-8", newline="\n")
    json_text = json.dumps(report.to_json(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    json_path.write_text(json_text, encoding="utf-8", newline="\n")

    return [md_path, json_path]
