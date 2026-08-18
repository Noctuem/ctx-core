"""Tests for `ctx_core.doctor`: the single CI-gateable health-check gate.

Self-contained `tmp_path` corpora throughout (same pattern as
`tests/test_archive.py`/`tests/test_packer.py`). Every hard-fail and
soft-warning path is exercised BOTH broken (must go red) and healthy (must
stay clean) — a check that has never been watched fail proves nothing.

Covers:
- Corpus integrity: dangling stub -> hard fail naming the note/handle/path;
  a properly archived stub -> no failure.
- Event-log chain validation: tampered line -> hard fail naming the line and
  log path; a clean/missing log -> no failure.
- Index freshness (soft): a file touched after the last recorded pack ->
  warning naming it; nothing packed yet, or nothing changed since -> no
  warning.
- Still-template check (soft): template-state corpus -> ok=True with the
  warning present; a customized corpus -> no warning.
- Budget check: L1 (core/) over `pack_budget_tokens` -> hard fail naming the
  contributing files; under budget -> no failure.
- `DoctorReport.ok` reflects `hard_failures` by construction; `DOCTOR_RUN`
  event emission, including the injectable `event_log` pattern.
- v0.2 additions (m14): unrouted-New intake check (soft always, hard past
  `intake_max_age_days`), stale-session-files advisory (soft), stats-product
  staleness advisory (soft) -- each broken on purpose and watched go red
  before the healthy/clean counterpart is asserted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ctx_core.archive import HANDLE_PREFIX, archive_note
from ctx_core.config import Knobs
from ctx_core.doctor import DoctorReport, doctor
from ctx_core.events import EventKind, EventLog
from ctx_core.intake import intake_add
from ctx_core.layout import PROFILE_PLACEHOLDER, Layout
from ctx_core.packer import pack
from ctx_core.sessions import SessionBoard
from ctx_core.stats import compute_stats, write_products

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "template"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


LONG_BODY = "This note explains composting in great detail. " * 40
FAKE_HANDLE = HANDLE_PREFIX + "a" * 64


# ==========================================================================
# Clean corpus baseline
# ==========================================================================


def test_doctor_ok_true_for_clean_corpus(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "A short, customized profile.")
    _write(layout.notes / "gardening.md", "Notes about gardening.")

    report = doctor(layout, Knobs())

    assert isinstance(report, DoctorReport)
    assert report.ok is True
    assert report.hard_failures == []


def test_doctor_report_ok_property_reflects_hard_failures() -> None:
    assert DoctorReport().ok is True
    assert DoctorReport(hard_failures=["something broke"]).ok is False
    assert DoctorReport(warnings=["just a nudge"]).ok is True


# ==========================================================================
# Corpus integrity (HARD)
# ==========================================================================


def test_doctor_dangling_stub_is_hard_failure(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(
        layout.notes / "orphan.md",
        f"---\nsources: {FAKE_HANDLE}\n---\n\nA stub whose archive was deleted.\n",
    )

    report = doctor(layout, Knobs())

    assert report.ok is False
    assert any("orphan.md" in f and "dangling archive stub" in f for f in report.hard_failures)
    assert any(FAKE_HANDLE in f for f in report.hard_failures)


def test_doctor_properly_archived_stub_passes(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)
    archive_note("notes/big.md", layout)

    report = doctor(layout, Knobs())

    assert report.ok is True
    assert report.hard_failures == []


# ==========================================================================
# Event-log chain validation (HARD)
# ==========================================================================


def test_doctor_corrupted_eventlog_is_hard_failure(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "Fine.")
    log = EventLog(tmp_path / "events.jsonl")
    log.append("some_event", {"a": 1})
    log.append("some_event", {"a": 2})

    # Tamper with the first line's payload without recomputing its hash --
    # the chain-integrity check this proves is "an edit outside the append
    # path is detected," not "append() validates its own input."
    lines = log.path.read_text(encoding="utf-8").split("\n")
    record = json.loads(lines[0])
    record["payload"]["a"] = 999  # mutate content, leave the stale `hash` field
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    log.path.write_text("\n".join(lines), encoding="utf-8", newline="")

    report = doctor(layout, Knobs(), event_log=log)

    assert report.ok is False
    assert any("event log chain broken at line 0" in f and str(log.path) in f for f in report.hard_failures)


def test_doctor_garbage_tail_returns_clean_report(tmp_path: Path) -> None:
    """The live repro: a log whose TAIL line is garbage (undecodable JSON,
    or valid JSON missing `"seq"`) used to crash `doctor()` with an
    unhandled `KeyError`/`JSONDecodeError` out of `EventLog.append`'s
    unconditional `DOCTOR_RUN` emission -- the existing corruption tests
    above only tamper with hash-covered FIELDS (still valid, seq-bearing
    JSON), which never exercised this path. `doctor()` must instead return
    a normal, clean `DoctorReport`: ok False, the chain break named among
    hard_failures (from `_check_eventlog_chain`/`verify()`, unchanged), and
    the `DOCTOR_RUN` emission itself downgraded to a warning rather than
    raising.
    """
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "Fine.")
    log = EventLog(tmp_path / "events.jsonl")
    log.append("some_event", {"a": 1})

    with open(log.path, "a", encoding="utf-8", newline="") as f:
        f.write('{"garbage":"tamper"}\n')  # valid JSON, no "seq" -> append() would crash

    report = doctor(layout, Knobs(), event_log=log)  # must not raise

    assert isinstance(report, DoctorReport)
    assert report.ok is False
    assert any("event log chain broken at line 1" in f for f in report.hard_failures)
    assert any("DOCTOR_RUN event emission skipped" in w for w in report.warnings)


def test_doctor_subprocess_garbage_tail_exits_1_no_traceback(tmp_path: Path) -> None:
    """Drives the REAL `ctx doctor` entry point (not the in-process
    `doctor()` call) against the exact live-repro sequence: build a corpus
    from `template/`, run `ctx pack` once (creates `var/log/events.jsonl`
    with one event), tamper the tail with a garbage line outside the
    append path, then run `ctx doctor`. Before this fix that crashed with
    an unhandled traceback at exit code 1; the requirement is exit code 1
    with a clean report and NO traceback on stderr -- unit tests calling
    `doctor()` directly cannot prove the shipped CLI entry point behaves,
    only a subprocess drive of the real command can (me-code.md: "verify
    the real entry point").
    """
    root = tmp_path / "corpus"
    shutil.copytree(TEMPLATE_DIR, root)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    pack_result = subprocess.run(
        [sys.executable, "-m", "ctx_core.cli", "pack", "x", "--root", str(root)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert pack_result.returncode == 0, pack_result.stderr

    log_path = root / "var" / "log" / "events.jsonl"
    assert log_path.is_file()
    lines_before = [l for l in log_path.read_text(encoding="utf-8").split("\n") if l]
    assert len(lines_before) == 1

    with open(log_path, "a", encoding="utf-8", newline="") as f:
        f.write('{"garbage":"tamper"}\n')

    doctor_result = subprocess.run(
        [sys.executable, "-m", "ctx_core.cli", "doctor", "--root", str(root)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert doctor_result.returncode == 1
    assert "Traceback" not in doctor_result.stderr
    assert "Traceback" not in doctor_result.stdout
    assert "ctx doctor: FAILED" in doctor_result.stdout


def test_doctor_missing_eventlog_verifies_trivially(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "Fine.")

    report = doctor(layout, Knobs())

    assert report.ok is True


def test_doctor_healthy_eventlog_no_failure(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    log.append("some_event", {"a": 1})
    log.append("some_event", {"a": 2})

    report = doctor(layout, Knobs(), event_log=log)

    assert not any("chain broken" in f for f in report.hard_failures)


# ==========================================================================
# Index freshness (SOFT)
# ==========================================================================


def test_doctor_warns_when_file_changed_after_last_pack(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "gardening.md"
    _write(note, "Notes about gardening.")
    log = EventLog(tmp_path / "events.jsonl")
    pack("gardening", layout, Knobs(), event_log=log)

    last_line = log.path.read_text(encoding="utf-8").strip().splitlines()[-1]
    last_ts = datetime.fromisoformat(json.loads(last_line)["ts"]).timestamp()

    future = last_ts + 1000
    os.utime(note, (future, future))

    report = doctor(layout, Knobs(), event_log=log)

    assert report.ok is True  # soft warning only, never a hard failure
    assert any("gardening.md" in w and "may be stale" in w for w in report.warnings)


def test_doctor_no_freshness_warning_when_never_packed(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.notes / "gardening.md", "Notes about gardening.")

    report = doctor(layout, Knobs())

    assert not any("may be stale" in w for w in report.warnings)


def test_doctor_no_freshness_warning_when_nothing_changed_since_pack(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "gardening.md"
    _write(note, "Notes about gardening.")
    # Backdate the note well clear of "now" so its mtime is unambiguously
    # before the pack event's timestamp -- without this, a note written
    # microseconds before `pack()` records its event can occasionally race
    # against clock-resolution skew between the filesystem mtime clock and
    # `datetime.now()`, producing a flaky false warning. Caught by running
    # this test repeatedly, not by inspection: it failed intermittently
    # before this backdating was added.
    past = datetime.now().timestamp() - 3600
    os.utime(note, (past, past))
    log = EventLog(tmp_path / "events.jsonl")
    pack("gardening", layout, Knobs(), event_log=log)

    report = doctor(layout, Knobs(), event_log=log)

    assert not any("may be stale" in w for w in report.warnings)


# ==========================================================================
# Still-template check (SOFT)
# ==========================================================================


def test_doctor_template_state_corpus_ok_true_with_warning(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", PROFILE_PLACEHOLDER)

    report = doctor(layout, Knobs())

    assert report.ok is True
    assert any("template state" in w for w in report.warnings)


def test_doctor_customized_corpus_no_template_warning(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "A real, customized profile.")

    report = doctor(layout, Knobs())

    assert not any("template state" in w for w in report.warnings)


# ==========================================================================
# Budget check (HARD)
# ==========================================================================


def test_doctor_l1_over_tiny_budget_is_hard_failure(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", LONG_BODY)
    knobs = Knobs(pack_budget_tokens=5)

    report = doctor(layout, knobs)

    assert report.ok is False
    assert any(
        "force-loaded L1" in f and "core/profile.md" in f and "pack_budget_tokens" in f
        for f in report.hard_failures
    )


def test_doctor_l1_under_budget_no_failure(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "A short profile.")
    knobs = Knobs(pack_budget_tokens=8000)

    report = doctor(layout, knobs)

    assert not any("force-loaded L1" in f for f in report.hard_failures)


# ==========================================================================
# DOCTOR_RUN event emission
# ==========================================================================


def test_doctor_emits_doctor_run_event_by_default(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "Fine.")

    report = doctor(layout, Knobs())

    log = EventLog(layout.var / "log" / "events.jsonl")
    ok, _ = log.verify()
    assert ok is True
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == EventKind.DOCTOR_RUN.value
    assert record["payload"]["ok"] == report.ok


def test_doctor_accepts_injected_event_log(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "Fine.")
    isolated_log = EventLog(tmp_path / "isolated.jsonl")

    doctor(layout, Knobs(), event_log=isolated_log)

    ok, _ = isolated_log.verify()
    assert ok is True
    default_log_path = layout.var / "log" / "events.jsonl"
    assert not default_log_path.exists()


def test_doctor_run_event_records_failures_and_warnings(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", PROFILE_PLACEHOLDER)  # template-state warning
    knobs = Knobs(pack_budget_tokens=1)  # forces a hard failure too
    log = EventLog(tmp_path / "events.jsonl")

    report = doctor(layout, knobs, event_log=log)

    record = json.loads(log.path.read_text(encoding="utf-8").splitlines()[0])
    assert record["payload"]["n_hard_failures"] == len(report.hard_failures)
    assert record["payload"]["n_warnings"] == len(report.warnings)
    assert record["payload"]["hard_failures"] == report.hard_failures
    assert record["payload"]["warnings"] == report.warnings


# ==========================================================================
# Unrouted-New check (v0.2, m14) -- soft always, hard past intake_max_age_days
# ==========================================================================


def test_doctor_unrouted_intake_item_is_soft_warning_only(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    intake_add(layout, "A fresh note that just arrived.", source="user")

    report = doctor(layout, Knobs())

    assert report.ok is True  # soft warning only, never a hard failure
    assert any("unrouted intake item" in w for w in report.warnings)
    assert not any("intake_max_age_days" in f for f in report.hard_failures)


def test_doctor_no_intake_warning_when_notes_intake_absent(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "Fine.")

    report = doctor(layout, Knobs())

    assert not any("unrouted intake item" in w for w in report.warnings)


def test_doctor_intake_item_past_max_age_is_hard_failure(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    knobs = Knobs(intake_max_age_days=14)
    old_received = datetime.now(timezone.utc) - timedelta(days=30)
    intake_add(layout, "An old, forgotten note.", source="user", now=old_received)

    report = doctor(layout, knobs)

    assert report.ok is False
    assert any(
        "intake_max_age_days" in f and "unrouted past" in f for f in report.hard_failures
    )
    # Still carries the soft warning too -- escalation adds to, never replaces it.
    assert any("unrouted intake item" in w for w in report.warnings)


def test_doctor_intake_item_within_max_age_is_not_hard_failure(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    knobs = Knobs(intake_max_age_days=14)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    intake_add(layout, "A recent note.", source="user", now=recent)

    report = doctor(layout, knobs)

    assert report.ok is True
    assert not any("intake_max_age_days" in f for f in report.hard_failures)


# ==========================================================================
# Stale-session-files advisory (v0.2, m14) -- soft only, never sweeps
# ==========================================================================


def test_doctor_stale_session_file_is_soft_warning(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    knobs = Knobs(session_stale_seconds=1800.0)
    old_heartbeat = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    live_dir = layout.var / "sessions" / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    _write(
        live_dir / "abc123.json",
        json.dumps(
            {
                "session_id": "abc123",
                "started_at": old_heartbeat,
                "heartbeat_at": old_heartbeat,
                "intent": "a stale build",
                "claims": [],
                "pid": 1,
            }
        ),
    )

    report = doctor(layout, knobs)

    assert report.ok is True  # advisory only
    assert any("abc123" in w and "session_stale_seconds" in w for w in report.warnings)
    # Report-only: doctor() must not have swept the file to history.
    assert (live_dir / "abc123.json").is_file()
    assert not (layout.var / "sessions" / "history" / "abc123.json").exists()


def test_doctor_no_stale_session_warning_for_fresh_heartbeat(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    board = SessionBoard(layout, event_log=EventLog(tmp_path / "events.jsonl"))
    board.register("fresh1", "an active build")

    report = doctor(layout, Knobs())

    assert not any("session_stale_seconds" in w for w in report.warnings)


def test_doctor_no_stale_session_warning_when_sessions_dir_absent(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "Fine.")

    report = doctor(layout, Knobs())

    assert not any("session_stale_seconds" in w for w in report.warnings)


# ==========================================================================
# Stats-product staleness advisory (v0.2, m14) -- soft only
# ==========================================================================


def test_doctor_stale_stats_products_is_soft_warning(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")

    stats_dir = layout.var / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)
    old_generated = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _write(
        stats_dir / "summary.json",
        json.dumps({"schema_version": 1, "generated_at": old_generated}),
    )
    log.append("some_event", {"a": 1})  # newer than the product's generated_at

    report = doctor(layout, Knobs(), event_log=log)

    assert report.ok is True  # advisory only
    assert any("stats products" in w and "stale" in w for w in report.warnings)


def test_doctor_no_staleness_warning_when_products_are_current(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")
    log.append("some_event", {"a": 1})

    report = compute_stats(layout, Knobs())
    write_products(report, layout)

    result = doctor(layout, Knobs(), event_log=log)

    assert not any("stats products" in w and "stale" in w for w in result.warnings)


def test_doctor_no_staleness_warning_when_no_products_yet(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "Fine.")

    report = doctor(layout, Knobs())

    assert not any("stats products" in w for w in report.warnings)
