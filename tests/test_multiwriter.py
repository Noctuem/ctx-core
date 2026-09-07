"""Tests for the 0.2.4 increment: per-writer event logs, the host-aware
session board, the index/ranking/intake knobs, and doctor's hook-silence
and map-coverage advisories.

Same `tmp_path` corpus pattern as the rest of the suite. Every new check
is watched go red on a broken input before the clean counterpart is
asserted.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ctx_core import cli
from ctx_core.config import Knobs
from ctx_core.doctor import HOOK_POST_TOOL_USE_KIND, doctor
from ctx_core.events import (
    EVENTS_FILENAME,
    EventKind,
    EventLog,
    default_eventlog_path,
    eventlog_for,
    eventlog_paths,
    read_events,
    writer_id,
)
from ctx_core.intake import intake_add
from ctx_core.layout import Layout
from ctx_core.packer import pack
from ctx_core.sessions import HOST_FIELD, SessionBoard, local_host
from ctx_core.stats import compute_stats


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _corpus(tmp_path: Path, *, with_map: bool = False) -> Layout:
    layout = Layout(tmp_path)
    _write(layout.core / "card.md", "# Card\nRules.\n")
    _write(layout.core / "profile.md", "# Profile\nA real profile.\n")
    if with_map:
        _write(layout.core / "map.md", "# Map\n- [notes/alpha.md] alpha\n- [notes/gone.md] gone\n")
    _write(layout.notes / "alpha.md", "# Alpha\ncomposting soil worms compost bin.\n")
    _write(layout.notes / "beta.md", "# Beta\nbicycle chain lube tyre pressure.\n")
    return layout


# ==========================================================================
# writer id + per-writer paths
# ==========================================================================


def test_writer_id_is_deterministic_and_filename_safe(tmp_path: Path) -> None:
    a = writer_id(tmp_path)
    assert a == writer_id(tmp_path)
    assert a != writer_id(tmp_path / "other")
    host = socket.gethostname().lower()
    assert a.startswith(host.split(".")[0][:1])  # same machine, folded
    assert all(c.isalnum() or c == "-" for c in a)


def test_per_writer_path_and_fold_order(tmp_path: Path) -> None:
    shared = default_eventlog_path(tmp_path, "var/log/")
    own = default_eventlog_path(tmp_path, "var/log/", per_writer=True)
    assert shared.name == EVENTS_FILENAME
    assert own.name == f"events-{writer_id(tmp_path)}.jsonl"
    assert eventlog_paths(tmp_path, "var/log/") == []

    EventLog(own).append("x", {"i": 1})
    EventLog(shared).append("x", {"i": 0})
    other = own.with_name("events-aaaa-000000.jsonl")
    EventLog(other).append("x", {"i": 2})

    paths = eventlog_paths(tmp_path, "var/log/")
    assert paths[0] == shared  # shared first, then writers by name
    assert set(paths[1:]) == {own, other}
    merged = read_events(paths)
    assert len(merged) == 3
    assert [r["ts"] for r in merged] == sorted(r["ts"] for r in merged)  # merged by ts


def test_eventlog_for_honors_knob(tmp_path: Path) -> None:
    assert eventlog_for(tmp_path, Knobs()).path.name == EVENTS_FILENAME
    assert eventlog_for(tmp_path, Knobs(eventlog_per_writer=True)).path.name.startswith("events-")


def test_knobs_load_reads_new_keys(tmp_path: Path) -> None:
    _write(
        tmp_path / ".ctxrc.toml",
        'eventlog_per_writer = true\nindex_exclude = ["Session_Log.md"]\n'
        "retrieval_k = 24\nintake_always_include_max_items = 3\n"
        "doctor_map_check = false\n",
    )
    knobs = Knobs.load(tmp_path)
    assert knobs.eventlog_per_writer is True
    assert knobs.index_exclude == ["Session_Log.md"]
    assert knobs.retrieval_k == 24
    assert knobs.intake_always_include_max_items == 3
    assert knobs.doctor_map_check is False


# ==========================================================================
# stats + doctor fold every chain
# ==========================================================================


def test_doctor_verifies_every_chain_and_stats_folds_them(tmp_path: Path) -> None:
    layout = _corpus(tmp_path)
    knobs = Knobs(eventlog_per_writer=True, hook_silence_min_doctor_runs=None)
    own = eventlog_for(tmp_path, knobs)
    other_path = own.path.with_name("events-other-000000.jsonl")
    other = EventLog(other_path)
    other.append(EventKind.DOCTOR_RUN.value, {"ok": True})
    other.append(EventKind.DOCTOR_RUN.value, {"ok": False})

    report = doctor(layout, knobs)
    assert report.ok, report.hard_failures
    assert own.path.is_file()  # doctor's own DOCTOR_RUN landed in the writer file

    stats = compute_stats(layout, knobs, now=datetime.now(timezone.utc).timestamp() + 60)
    assert stats.doctor["n_runs"] == 3  # 2 in `other` + doctor's own

    # Tamper with the OTHER chain: the corpus-wide gate must go red.
    lines = other_path.read_text(encoding="utf-8").split("\n")
    lines[0] = lines[0].replace('"ok": true', '"ok": false').replace('"ok":true', '"ok":false')
    other_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    report = doctor(layout, knobs)
    assert not report.ok
    assert any("events-other-000000.jsonl" in f for f in report.hard_failures)


# ==========================================================================
# packer knobs
# ==========================================================================


def test_index_exclude_and_retrieval_k(tmp_path: Path) -> None:
    layout = _corpus(tmp_path)
    for i in range(20):
        # Distinct filler per note so the near-duplicate filter leaves them alone.
        stem = "abcdefghijklmnopqrst"[i]
        filler = " ".join(f"{stem * 3}{'xyzuvw'[k]}" for k in range(6))
        _write(layout.notes / f"n{i:02d}.md", f"# n{i}\ncompost worms soil note {i} {filler}.\n")
    log = EventLog(tmp_path / "scratch.jsonl")

    wide = pack("compost worms soil", layout, Knobs(retrieval_k=30, pack_budget_tokens=100_000), event_log=log)
    narrow = pack("compost worms soil", layout, Knobs(retrieval_k=3, pack_budget_tokens=100_000), event_log=log)
    assert len([e for e in wide.entries if not e.pinned]) > len([e for e in narrow.entries if not e.pinned])
    assert len([e for e in narrow.entries if not e.pinned]) == 3

    excluded = pack(
        "compost worms soil",
        layout,
        Knobs(retrieval_k=30, pack_budget_tokens=100_000, index_exclude=["notes/n0"]),
        event_log=log,
    )
    assert not any(e.path.startswith("notes/n0") for e in excluded.entries)
    assert not any(e.path.startswith("notes/n0") for e, _ in excluded.dropped)
    assert excluded.census.get("excluded by caller-supplied prefix") == 10


def test_intake_always_include_max_items_pins_only_the_newest(tmp_path: Path) -> None:
    layout = _corpus(tmp_path)
    log = EventLog(tmp_path / "scratch.jsonl")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    paths = [
        intake_add(layout, f"note {i} about nothing relevant", title=f"item-{i}", now=base + timedelta(days=i), event_log=log)
        for i in range(4)
    ]
    everything = pack("bicycle chain", layout, Knobs(pack_budget_tokens=100_000), event_log=log)
    assert sum(1 for e in everything.entries if e.path.startswith("notes/intake/")) == 4

    capped = pack("bicycle chain", layout, Knobs(pack_budget_tokens=100_000, intake_always_include_max_items=2), event_log=log)
    pinned = {e.path for e in capped.entries if e.pinned and e.path.startswith("notes/intake/")}
    newest_two = {p.relative_to(tmp_path).as_posix() for p in paths[-2:]}
    assert pinned == newest_two


# ==========================================================================
# host-aware board
# ==========================================================================


def _foreign_live_file(board: SessionBoard, session_id: str, *, age_seconds: float, claims: list[str]) -> Path:
    hb = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    path = board.live_dir / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "started_at": hb,
                "heartbeat_at": hb,
                "intent": "elsewhere",
                "claims": claims,
                "pid": 1,
                HOST_FIELD: "some-other-box",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_register_records_host_and_foreign_sessions_are_never_swept(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "var" / "log" / "events.jsonl")
    board = SessionBoard(Layout(tmp_path), event_log=log, stale_seconds=60)
    board.register("mine", "local work")
    mine = json.loads((board.live_dir / "mine.json").read_text(encoding="utf-8"))
    assert mine[HOST_FIELD] == local_host()

    foreign = _foreign_live_file(board, "theirs", age_seconds=10_000, claims=["notes/alpha.md"])
    entries = board.list()  # sweeps stale LOCAL files only
    assert foreign.is_file(), "a foreign live file must survive the local sweep"
    by_id = {e.session_id: e for e in entries}
    assert by_id["theirs"].foreign is True and by_id["theirs"].stale is True
    assert by_id["mine"].foreign is False
    # A stale foreign file blocks nothing; a fresh one still does.
    assert board.check("notes/alpha.md", "mine") is None
    _foreign_live_file(board, "fresh", age_seconds=1, claims=["notes/beta.md"])
    conflict = board.check("notes/beta.md", "mine")
    assert conflict is not None and conflict.session_id == "fresh"


def test_legacy_live_file_without_host_is_treated_as_local(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "var" / "log" / "events.jsonl")
    board = SessionBoard(Layout(tmp_path), event_log=log, stale_seconds=60)
    path = _foreign_live_file(board, "old", age_seconds=10_000, claims=[])
    data = json.loads(path.read_text(encoding="utf-8"))
    del data[HOST_FIELD]
    path.write_text(json.dumps(data), encoding="utf-8")
    board.list()
    assert not path.exists()
    assert (board.history_dir / "old.json").is_file()


def test_unlink_permission_error_is_retried_then_left_for_next_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = EventLog(tmp_path / "var" / "log" / "events.jsonl")
    board = SessionBoard(Layout(tmp_path), event_log=log, stale_seconds=60)
    board.register("s", "x")
    calls = {"n": 0}
    real_unlink = Path.unlink

    def flaky_unlink(self, *a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(32, "in use")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    board.release("s")
    assert calls["n"] == 3
    assert not (board.live_dir / "s.json").exists()
    assert (board.history_dir / "s.json").is_file()


# ==========================================================================
# doctor: hook silence + map coverage
# ==========================================================================


def test_hook_silence_warns_only_after_threshold_and_never_with_hook_events(tmp_path: Path) -> None:
    layout = _corpus(tmp_path)
    knobs = Knobs(hook_silence_min_doctor_runs=3)
    assert not any("hook silence" in w for w in doctor(layout, knobs).warnings)
    assert not any("hook silence" in w for w in doctor(layout, knobs).warnings)
    # doctor() judges BEFORE appending its own DOCTOR_RUN: run 3 sees two
    # prior runs (under the threshold), run 4 sees three (at it).
    third = doctor(layout, knobs)
    assert not any("hook silence" in w for w in third.warnings), third.warnings
    fourth = doctor(layout, knobs)
    assert any("hook silence" in w for w in fourth.warnings), fourth.warnings

    eventlog_for(tmp_path, knobs).append(HOOK_POST_TOOL_USE_KIND, {"tool_name": "Bash"})
    assert not any("hook silence" in w for w in doctor(layout, knobs).warnings)

    silent = Knobs(hook_silence_min_doctor_runs=None)
    other = _corpus(tmp_path / "quiet")
    for _ in range(5):
        report = doctor(other, silent)
    assert not any("hook silence" in w for w in report.warnings)


def test_map_coverage_names_uncovered_files_and_dangling_pointers(tmp_path: Path) -> None:
    layout = _corpus(tmp_path, with_map=True)
    knobs = Knobs(hook_silence_min_doctor_runs=None)
    report = doctor(layout, knobs)
    coverage = [w for w in report.warnings if w.startswith("map coverage")]
    pointers = [w for w in report.warnings if w.startswith("map pointers")]
    assert coverage and "notes/beta.md" in coverage[0] and "alpha.md" not in coverage[0]
    assert pointers and "notes/gone.md" in pointers[0]

    # Intake items are pre-map by definition -- never counted as uncovered.
    intake_add(layout, "fresh thing", title="fresh", event_log=EventLog(tmp_path / "scratch.jsonl"))
    report = doctor(layout, knobs)
    assert not any("intake" in w for w in report.warnings if w.startswith("map coverage"))

    _write(layout.core / "map.md", "# Map\n- [notes/alpha.md] alpha\n- beta.md is the bike note\n")
    report = doctor(layout, knobs)
    assert not any(w.startswith("map") for w in report.warnings), report.warnings

    assert not any(w.startswith("map") for w in doctor(layout, Knobs(doctor_map_check=False, hook_silence_min_doctor_runs=None)).warnings)


def test_root_files_are_excludable_and_never_map_uncovered(tmp_path: Path) -> None:
    layout = _corpus(tmp_path, with_map=True)
    _write(tmp_path / "Session_Log.md", "# Log\nsession one two three.\n")
    _write(layout.core / "map.md", "# Map\n- alpha.md\n- beta.md\n")
    knobs = Knobs(hook_silence_min_doctor_runs=None)
    report = doctor(layout, knobs)
    assert not any(w.startswith("map coverage") for w in report.warnings), report.warnings
    log = EventLog(tmp_path / "scratch.jsonl")
    m = pack("session one two", layout, Knobs(index_exclude=["Session_Log.md"]), event_log=log)
    assert not any(e.path == "Session_Log.md" for e in m.entries)
    assert not any(e.path == "Session_Log.md" for e, _ in m.dropped)


def test_no_map_no_warning(tmp_path: Path) -> None:
    layout = _corpus(tmp_path)
    report = doctor(layout, Knobs(hook_silence_min_doctor_runs=None))
    assert not any(w.startswith("map") for w in report.warnings)


# ==========================================================================
# cli: sessions start (the hookless front door)
# ==========================================================================


def test_sessions_start_registers_then_heartbeats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _corpus(tmp_path)
    assert cli.main(["sessions", "start", "agent-1", "--intent", "gemini run", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Registered 'agent-1'" in out and local_host() in out
    assert cli.main(["sessions", "start", "agent-1", "--root", str(tmp_path)]) == 0
    assert "already live" in capsys.readouterr().out
    assert cli.main(["sessions", "list", "--root", str(tmp_path)]) == 0
    listing = capsys.readouterr().out
    assert "agent-1" in listing and f"host={local_host()}" in listing
    assert cli.main(["sessions", "release", "agent-1", "--root", str(tmp_path)]) == 0
