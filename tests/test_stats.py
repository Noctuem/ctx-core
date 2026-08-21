"""Tests for `ctx_core.stats`: zero-token aggregation + products.

Self-contained `tmp_path` corpora throughout (same pattern as
`tests/test_doctor.py`/`tests/test_packer.py`). Events are synthesized
through the real `EventLog.append` API (never hand-written raw JSONL) except
where a test needs to control a record's `ts` precisely for window-filtering
— there, a real record is appended first and only its `ts` field is
rewritten afterward (mirrors `test_doctor.py`'s own tamper-the-line
technique; `stats.py` never checks the hash chain, so this is a legitimate
way to control time without a real sleep).

Covers: top-packed/never-packed + drop-reason histogram + budget-utilization
folded from crafted `CONTEXT_ASSEMBLED` payloads; a real end-to-end
`pack()`/`doctor()` round trip; window filtering (`since_days`); byte-identity
of `to_json()`/`render_md()` across two runs with a pinned `now`; every
absence-stated case (no yield installed, no `var/sessions/`, no
`notes/intake/`, empty log) reads as "not measured," never as "clean";
session claim-conflict detection from the on-disk `var/sessions/live/` shape;
intake backlog + latency folding from `notes/intake/` front matter and
`INTAKE_ROUTE` events; `ctx-yield` findings via the shared fake-ctx-yield
fixture; and `write_products`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ctx_core.config import Knobs
from ctx_core.doctor import doctor
from ctx_core.events import EventKind, EventLog, default_eventlog_path
from ctx_core.layout import Layout
from ctx_core.packer import pack
from ctx_core.stats import (
    CLAIM_OVERLAP_OBSERVED_KIND,
    INTAKE_ROUTE_KIND,
    SCHEMA_VERSION,
    SESSION_CLAIM_KIND,
    SESSION_START_KIND,
    StatsReport,
    compute_stats,
    write_products,
)
from ctx_core.yield_bridge import NOT_INSTALLED_HINT

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "fake-ctx-yield"

SECONDS_PER_DAY = 86_400
FIXED_NOW = 1_800_000_000.0  # arbitrary, pinned epoch — never wall-clock


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def no_ctx_yield_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Deterministic "not installed" outcome regardless of the host
    machine's real PATH — see `test_yield_bridge.py`'s identical fixture.
    """
    empty_dir = tmp_path_factory.mktemp("empty-path")
    monkeypatch.setenv("PATH", str(empty_dir))


@pytest.fixture
def fake_ctx_yield_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(FIXTURE_DIR) + os.pathsep + os.environ.get("PATH", ""))


def _pack_payload(*, budget_tokens: int, total_tokens: int, items: list[dict]) -> dict:
    return {
        "task": "x",
        "config_hash": "deadbeef",
        "budget_tokens": budget_tokens,
        "total_tokens": total_tokens,
        "n_candidates": len(items),
        "n_dropped": sum(1 for i in items if i.get("dropped")),
        "items": items,
    }


def _chosen(path: str) -> dict:
    return {
        "item_id": path,
        "tier": "notes",
        "tokens": 100,
        "age_days": 1.0,
        "relevance": 0.9,
        "pinned": False,
        "handle": "h",
        "path": path,
        "dropped": False,
    }


def _dropped(path: str, reason: str) -> dict:
    return {
        "item_id": path,
        "tier": "notes",
        "tokens": 100,
        "age_days": 1.0,
        "relevance": 0.1,
        "pinned": False,
        "handle": "h",
        "path": path,
        "dropped": True,
        "drop_reason": reason,
        "redundant": reason.startswith("near-duplicate"),
    }


def _set_ts(log_path: Path, index: int, iso_ts: str) -> None:
    """Rewrite the `ts` field of line `index` in place. `stats.py` never
    checks the hash chain, so this does not need to stay hash-valid — only
    `EventLog.verify()` (not exercised here) would notice.
    """
    lines = log_path.read_text(encoding="utf-8").split("\n")
    lines = [l for l in lines if l]
    record = json.loads(lines[index])
    record["ts"] = iso_ts
    lines[index] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _iso_days_ago(now: float, days: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(now - days * SECONDS_PER_DAY, tz=timezone.utc).isoformat()


# ==========================================================================
# Empty corpus / absence-stated
# ==========================================================================


def test_empty_corpus_everything_absent_stated(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert isinstance(report, StatsReport)
    assert report.packs["count_in_window"] == 0
    assert report.packs["top_packed"] == []
    assert report.packs["never_packed"] == []
    assert report.drop_reasons == {}
    assert report.budget_utilization["count"] == 0
    assert report.doctor["n_runs"] == 0
    assert report.doctor["pass_rate"] is None
    assert report.intake["dir_present"] is False
    assert report.intake["unrouted_total"] == 0
    assert report.sessions["dir_present"] is False
    assert report.sessions["n_live"] == 0
    assert report.yield_info["ran"] is False
    assert report.yield_info["warning"] == NOT_INSTALLED_HINT
    assert report.yield_info["dead_weight_files"] == []

    md = report.render_md()
    assert "not measured" in md
    json_out = report.to_json()
    assert json_out["schema_version"] == SCHEMA_VERSION


# ==========================================================================
# Crafted CONTEXT_ASSEMBLED payloads: top-packed / never-packed / drop
# reasons / budget utilization
# ==========================================================================


def test_top_packed_never_packed_and_drop_reason_histogram(
    no_ctx_yield_on_path: None, tmp_path: Path
) -> None:
    layout = Layout(tmp_path)
    _write(layout.notes / "a.md", "A")
    _write(layout.notes / "b.md", "B")
    _write(layout.notes / "c.md", "C")  # never chosen

    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(
        EventKind.CONTEXT_ASSEMBLED,
        _pack_payload(
            budget_tokens=1000,
            total_tokens=200,
            items=[
                _chosen("notes/a.md"),
                _dropped("notes/b.md", "below relevance/recency cutoff"),
                _dropped("notes/c.md", "over budget (500 tok, 100 remaining)"),
            ],
        ),
    )
    log.append(
        EventKind.CONTEXT_ASSEMBLED,
        _pack_payload(
            budget_tokens=1000,
            total_tokens=100,
            items=[
                _chosen("notes/a.md"),
                _dropped("notes/b.md", "near-duplicate of an included file"),
            ],
        ),
    )

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.packs["count_in_window"] == 2
    assert report.packs["top_packed"] == [["notes/a.md", 2]]
    assert report.packs["never_packed"] == ["notes/b.md", "notes/c.md"]  # both only ever dropped, never chosen
    assert report.drop_reasons == {"below_cutoff": 1, "over_budget": 1, "near_duplicate": 1}

    bu = report.budget_utilization
    assert bu["count"] == 2
    assert bu["min"] == 0.1  # 100/1000
    assert bu["max"] == 0.2  # 200/1000
    assert bu["buckets"]["<25%"] == 2


def test_unrecognized_drop_reason_falls_back_to_other(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(
        EventKind.CONTEXT_ASSEMBLED,
        _pack_payload(
            budget_tokens=100,
            total_tokens=0,
            items=[_dropped("notes/z.md", "some brand-new reason packer.py might invent later")],
        ),
    )

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.drop_reasons == {"other": 1}


def test_age_gated_and_intake_cap_drop_reasons_are_classified(
    no_ctx_yield_on_path: None, tmp_path: Path
) -> None:
    """`packer.py`'s two distinct drop reasons (age-gate, intake-cap) --
    added in the 2026-08-21 review pass -- must classify into their own
    labels rather than folding into `other`."""
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(
        EventKind.CONTEXT_ASSEMBLED,
        _pack_payload(
            budget_tokens=100,
            total_tokens=0,
            items=[
                _dropped(
                    "notes/ancient.md",
                    "older than 40d and below the age-gate relevance exemption",
                ),
                _dropped(
                    "notes/intake/big.md",
                    "unrouted intake item over the 2,000-token intake-always-include "
                    "cap; read it directly",
                ),
            ],
        ),
    )

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.drop_reasons == {"age_gated": 1, "intake_cap": 1}


# ==========================================================================
# Doctor pass rate
# ==========================================================================


def test_doctor_pass_rate_folds_ok_and_failed_runs(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(EventKind.DOCTOR_RUN, {"ok": True, "n_hard_failures": 0, "n_warnings": 0})
    log.append(EventKind.DOCTOR_RUN, {"ok": True, "n_hard_failures": 0, "n_warnings": 1})
    log.append(EventKind.DOCTOR_RUN, {"ok": False, "n_hard_failures": 1, "n_warnings": 0})

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.doctor == {"n_runs": 3, "n_pass": 2, "pass_rate": 0.6667}


# ==========================================================================
# initialized_at (INIT_RUN fold, TODO Low state-report survey item 2)
# ==========================================================================


def test_initialized_at_is_null_with_no_init_run(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.window["initialized_at"] is None
    assert "Not yet initialized" in report.render_md()


def test_initialized_at_is_newest_init_run_ts(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(EventKind.INIT_RUN, {"customized": True, "fields": ["name"]})
    log.append(EventKind.INIT_RUN, {"customized": True, "fields": ["name", "domain_purpose"]})

    _set_ts(log.path, 0, _iso_days_ago(FIXED_NOW, 10))
    _set_ts(log.path, 1, _iso_days_ago(FIXED_NOW, 2))  # newer -- must win

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.window["initialized_at"] == _iso_days_ago(FIXED_NOW, 2)
    assert f"Initialized `{_iso_days_ago(FIXED_NOW, 2)}`." in report.render_md()


def test_initialized_at_is_not_window_scoped(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    """A corpus initialized before the `--since` window start must not read
    as never-initialized -- `initialized_at` folds the whole log, mirroring
    `doctor._last_context_assembled_ts`'s own posture (see stats.py's
    `_last_init_run_ts` docstring).
    """
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(EventKind.INIT_RUN, {"customized": True, "fields": ["name"]})
    _set_ts(log.path, 0, _iso_days_ago(FIXED_NOW, 40))  # outside a 7-day window

    report = compute_stats(layout, Knobs(), since_days=7, now=FIXED_NOW)

    assert report.window["initialized_at"] == _iso_days_ago(FIXED_NOW, 40)


# ==========================================================================
# Window filtering
# ==========================================================================


def test_since_days_window_excludes_older_events(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(EventKind.CONTEXT_ASSEMBLED, _pack_payload(budget_tokens=100, total_tokens=10, items=[]))
    log.append(EventKind.CONTEXT_ASSEMBLED, _pack_payload(budget_tokens=100, total_tokens=20, items=[]))

    _set_ts(log.path, 0, _iso_days_ago(FIXED_NOW, 40))  # outside a 7-day window
    _set_ts(log.path, 1, _iso_days_ago(FIXED_NOW, 1))  # inside

    windowed = compute_stats(layout, Knobs(), since_days=7, now=FIXED_NOW)
    unbounded = compute_stats(layout, Knobs(), since_days=None, now=FIXED_NOW)

    assert windowed.packs["count_in_window"] == 1
    assert unbounded.packs["count_in_window"] == 2
    assert windowed.window == {
        "since_days": 7,
        "start": _iso_days_ago(FIXED_NOW, 7),
        "end": windowed.window["end"],
        "initialized_at": None,
    }
    assert unbounded.window["start"] is None


# ==========================================================================
# Determinism: byte-identical on repeat run with pinned `now`
# ==========================================================================


def test_byte_identical_on_repeat_run_with_pinned_now(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "A short, customized profile.")
    _write(layout.notes / "gardening.md", "Notes about gardening.")
    knobs = Knobs()
    log = EventLog(default_eventlog_path(layout.root, knobs.eventlog_path))
    pack("gardening", layout, knobs, event_log=log)
    doctor(layout, knobs, event_log=log)

    report1 = compute_stats(layout, knobs, since_days=30, now=FIXED_NOW)
    report2 = compute_stats(layout, knobs, since_days=30, now=FIXED_NOW)

    assert report1.to_json() == report2.to_json()
    assert report1.render_md() == report2.render_md()

    paths1 = write_products(report1, layout)
    bytes1 = [p.read_bytes() for p in paths1]
    paths2 = write_products(report2, layout)
    bytes2 = [p.read_bytes() for p in paths2]
    assert bytes1 == bytes2


# ==========================================================================
# Real pack()/doctor() end-to-end fold
# ==========================================================================


def test_real_pack_and_doctor_events_fold_end_to_end(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "A short, customized profile.")
    _write(layout.notes / "gardening.md", "Notes about composting kitchen scraps.")
    knobs = Knobs()

    pack("composting", layout, knobs)
    doctor(layout, knobs)

    report = compute_stats(layout, knobs, now=FIXED_NOW + 10)  # after both real (wall-clock) events

    assert report.packs["count_in_window"] == 1
    assert report.doctor["n_runs"] == 1
    assert "notes/gardening.md" in dict(report.packs["top_packed"]) or "notes/gardening.md" in report.packs[
        "never_packed"
    ]  # folded without crashing either way


# ==========================================================================
# Sessions: live/history counts + claim-conflict detection
# ==========================================================================


def _session_file(session_id: str, claims: list[str]) -> dict:
    return {
        "session_id": session_id,
        "started_at": "2026-08-18T00:00:00+00:00",
        "heartbeat_at": "2026-08-18T00:05:00+00:00",
        "intent": "testing",
        "claims": claims,
        "pid": 1234,
    }


def test_sessions_absent_dir_reads_zero(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.sessions["dir_present"] is False
    assert report.sessions["n_live"] == 0
    assert report.sessions["claim_conflicts"] == []


def test_sessions_live_history_counts_and_claim_conflict(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    live_dir = layout.var / "sessions" / "live"
    history_dir = layout.var / "sessions" / "history"
    live_dir.mkdir(parents=True)
    history_dir.mkdir(parents=True)

    _write(live_dir / "sess-a.json", json.dumps(_session_file("sess-a", ["notes/foo.md"])))
    _write(live_dir / "sess-b.json", json.dumps(_session_file("sess-b", ["notes/foo.md", "notes/bar.md"])))
    _write(live_dir / "sess-c.json", json.dumps(_session_file("sess-c", ["notes/baz.md"])))  # no conflict
    _write(history_dir / "sess-old.json", json.dumps(_session_file("sess-old", [])))

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.sessions["dir_present"] is True
    assert report.sessions["n_live"] == 3
    assert report.sessions["n_history"] == 1
    assert report.sessions["claim_conflicts"] == [["sess-a", "sess-b", "notes/foo.md"]]


def test_sessions_ancestor_descendant_claims_conflict(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    live_dir = layout.var / "sessions" / "live"
    live_dir.mkdir(parents=True)
    _write(live_dir / "sess-a.json", json.dumps(_session_file("sess-a", ["notes"])))
    _write(live_dir / "sess-b.json", json.dumps(_session_file("sess-b", ["notes/deep/file.md"])))

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert len(report.sessions["claim_conflicts"]) == 1


def test_session_event_kinds_counted_in_window(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(SESSION_START_KIND, {"session_id": "sess-a"})
    log.append(SESSION_CLAIM_KIND, {"session_id": "sess-a", "paths": ["notes/foo.md"]})
    log.append(SESSION_CLAIM_KIND, {"session_id": "sess-a", "paths": ["notes/bar.md"]})

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.sessions["starts_in_window"] == 1
    assert report.sessions["claims_in_window"] == 2


def test_claim_overlap_observed_events_counted_in_window(
    no_ctx_yield_on_path: None, tmp_path: Path
) -> None:
    """`claim_overlap_observed` (the Bash-write advisory the PostToolUse
    hook emits, review pass item 5) is windowed and folded into the
    Sessions section -- never surfaced as a `claim_conflicts` entry, which
    is a different, live-snapshot-only signal."""
    layout = Layout(tmp_path)
    live_dir = layout.var / "sessions" / "live"
    live_dir.mkdir(parents=True)
    _write(live_dir / "sess-a.json", json.dumps(_session_file("sess-a", [])))

    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(
        CLAIM_OVERLAP_OBSERVED_KIND,
        {"session_id": "sess-a", "other_session": "sess-b", "path": "notes/foo.md"},
    )
    log.append(
        CLAIM_OVERLAP_OBSERVED_KIND,
        {"session_id": "sess-a", "other_session": "sess-c", "path": "notes/bar.md"},
    )

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.sessions["claim_overlaps_observed_in_window"] == 2
    assert "claim-overlaps observed: 2" in report.render_md()


# ==========================================================================
# Intake: unrouted backlog + latency
# ==========================================================================


def test_intake_absent_dir_reads_zero(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.intake["dir_present"] is False
    assert report.intake["unrouted_total"] == 0
    assert report.intake["unrouted_by_source"] == {}


def test_intake_unrouted_backlog_by_source(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    intake_dir = layout.notes / "intake"
    _write(
        intake_dir / "2026-08-18-idea-one.md",
        "---\nctx:layer: new\nctx:source: user\nctx:received: 2026-08-18T00:00:00+00:00\n---\n\nAn idea.\n",
    )
    _write(
        intake_dir / "2026-08-17-note-two.md",
        "---\nctx:layer: new\nctx:source: research\nctx:received: 2026-08-17T00:00:00+00:00\n---\n\nA finding.\n",
    )
    _write(
        intake_dir / "2026-08-16-no-source.md",
        "---\nctx:layer: new\nctx:received: not-a-timestamp\n---\n\nNo source given.\n",
    )

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.intake["dir_present"] is True
    assert report.intake["unrouted_total"] == 3
    assert report.intake["unrouted_by_source"] == {"user": 2, "research": 1}  # 3rd note has no ctx:source -> defaults "user"
    assert report.intake["unrouted_no_timestamp"] == 1


def test_intake_latency_from_route_events(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(INTAKE_ROUTE_KIND, {"source": "user", "destination": "notes/x.md", "age_days": 2.5})
    log.append(INTAKE_ROUTE_KIND, {"source": "user", "destination": "notes/y.md", "age_days": 0.5})
    log.append(INTAKE_ROUTE_KIND, {"source": "user", "destination": "notes/z.md"})  # no readable latency field

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    lat = report.intake["latency"]
    assert lat["n_measured"] == 2
    assert lat["n_unmeasured"] == 1
    assert lat["min_days"] == 0.5
    assert lat["max_days"] == 2.5
    assert lat["mean_days"] == 1.5


def test_intake_latency_ignores_retired_hedge_keys(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    """`INTAKE_ROUTE_AGE_KEYS` used to hedge four candidate key names;
    `intake_route` (the only emitter) writes `age_days` only, so the hedge
    was pinned down to `INTAKE_ROUTE_AGE_KEY` (TODO Low, state-report
    survey item 3). A payload carrying one of the retired names and no
    `age_days` must read as unmeasured, not silently pick up the old key.
    """
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))
    log.append(
        INTAKE_ROUTE_KIND,
        {"source": "user", "destination": "notes/x.md", "age_at_routing_days": 3.0},
    )

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    lat = report.intake["latency"]
    assert lat["n_measured"] == 0
    assert lat["n_unmeasured"] == 1


def test_intake_latency_not_measured_when_no_route_events(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    lat = report.intake["latency"]
    assert lat["n_measured"] == 0
    assert lat["n_unmeasured"] == 0
    assert lat["mean_days"] is None
    assert "not measured" in report.render_md()


# ==========================================================================
# ctx-yield: not installed vs. findings
# ==========================================================================


def test_yield_not_installed_is_stated_not_clean(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.yield_info["ran"] is False
    assert report.yield_info["dead_weight_files"] == []
    assert report.yield_info["warning"] == NOT_INSTALLED_HINT


def test_yield_findings_produces_dead_weight_list(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FAKE_CTX_YIELD_MODE", "findings")
    layout = Layout(tmp_path)
    # The fixture's "findings" mode reports on core/card.md (never_recalled)
    # and core/map.md (recalled) — see tests/test_yield_bridge.py.
    _write(layout.core / "card.md", "Card.")
    _write(layout.core / "map.md", "Map.")

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.yield_info["ran"] is True
    assert report.yield_info["degraded"] is False
    assert report.yield_info["dead_weight_files"] == ["core/card.md"]


def test_yield_malformed_json_is_degraded_not_clean(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FAKE_CTX_YIELD_MODE", "malformed")
    layout = Layout(tmp_path)

    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    assert report.yield_info["ran"] is True
    assert report.yield_info["degraded"] is True
    assert report.yield_info["dead_weight_files"] == []
    assert report.yield_info["warning"]


# ==========================================================================
# ctx-yield: event_log threading + yield_runs_in_window fold
# (TODO Medium, "yield-bridge eventlog line")
# ==========================================================================


def test_no_event_log_means_no_yield_scan_event_emitted(
    no_ctx_yield_on_path: None, tmp_path: Path
) -> None:
    layout = Layout(tmp_path)
    log_path = default_eventlog_path(layout.root, Knobs().eventlog_path)

    compute_stats(layout, Knobs(), now=FIXED_NOW)  # no event_log=

    assert not log_path.exists()


def test_event_log_gets_exactly_one_yield_scan_event_per_compute_stats_call(
    no_ctx_yield_on_path: None, tmp_path: Path
) -> None:
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))

    compute_stats(layout, Knobs(), now=FIXED_NOW, event_log=log)

    lines = [l for l in log.path.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "yield_scan"
    assert record["payload"]["ran"] is False  # no ctx-yield on PATH in this fixture


def test_yield_runs_in_window_folds_prior_runs_not_this_ones(
    no_ctx_yield_on_path: None, tmp_path: Path
) -> None:
    """A `compute_stats` call's OWN `yield_scan` event (emitted at the end
    of this same call) must not count towards its own report -- `windowed`
    is computed from the log as it stood before this call ran. A SECOND
    call sees the first call's event.
    """
    layout = Layout(tmp_path)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))

    first = compute_stats(layout, Knobs(), now=FIXED_NOW, event_log=log)
    assert first.yield_info["yield_runs_in_window"] == 0

    second = compute_stats(layout, Knobs(), now=FIXED_NOW, event_log=log)
    assert second.yield_info["yield_runs_in_window"] == 1
    assert "1 composition run(s) recorded in this window." in second.render_md()


# ==========================================================================
# write_products()
# ==========================================================================


def test_write_products_creates_md_and_json(no_ctx_yield_on_path: None, tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    report = compute_stats(layout, Knobs(), now=FIXED_NOW)

    paths = write_products(report, layout)

    assert len(paths) == 2
    md_path, json_path = paths
    assert md_path == layout.var / "stats" / "summary.md"
    assert json_path == layout.var / "stats" / "summary.json"
    assert md_path.is_file()
    assert json_path.is_file()

    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded == report.to_json()
    assert loaded["schema_version"] == SCHEMA_VERSION
    assert "# ctx-core stats" in md_path.read_text(encoding="utf-8")
