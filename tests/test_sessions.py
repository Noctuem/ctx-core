"""Tests for `ctx_core.sessions`: the live-session board.

Covers: register/heartbeat/claim/release round-trips, heartbeat-age
liveness + sweep-to-history (never deletes), claim overlap semantics
(structural, not string-prefix -- proven both positive and negative, and
by breaking the check on purpose), the `check()` conflict primitive, event
emission for register/claim/release/expire, and a real two-process
concurrency proof.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ctx_core.config import Knobs
from ctx_core.events import EventLog
from ctx_core.layout import Layout
from ctx_core.sessions import (
    Conflict,
    SESSION_CLAIM,
    SESSION_EXPIRE,
    SESSION_RELEASE,
    SESSION_START,
    SessionBoard,
    SessionNotFoundError,
    _HISTORY_STAMP_RE,
    _normalize_claim_path,
    _paths_overlap,
    generate_session_id,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Bound on the race test's wall-clock time.
RACE_OPS_PER_PROCESS = 15
RACE_PROCESS_COUNT = 2


def _board(tmp_path: Path, **kwargs) -> tuple[SessionBoard, EventLog]:
    layout = Layout(tmp_path)
    log = EventLog(tmp_path / "var" / "log" / "events.jsonl")
    board = SessionBoard(layout, event_log=log, **kwargs)
    return board, log


def _kinds(log: EventLog) -> list[str]:
    if not log.path.exists():
        return []
    lines = log.path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["kind"] for line in lines]


# ==========================================================================
# generate_session_id
# ==========================================================================


def test_generate_session_id_is_unique_and_filesystem_safe() -> None:
    a = generate_session_id()
    b = generate_session_id()
    assert a != b
    assert a.isalnum()  # pure hex -- always a valid filename stem


# ==========================================================================
# knobs= wiring (docs-drift fix: SessionBoard reads Knobs directly)
# ==========================================================================


def test_default_construction_matches_default_knobs(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(tmp_path / "var" / "log" / "events.jsonl")

    board = SessionBoard(layout, event_log=log)

    assert board.stale_seconds == Knobs().session_stale_seconds
    assert board.heartbeat_seconds == Knobs().session_heartbeat_seconds


def test_knobs_param_supplies_stale_and_heartbeat_seconds(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(tmp_path / "var" / "log" / "events.jsonl")
    knobs = Knobs(session_stale_seconds=42.0, session_heartbeat_seconds=7.0)

    board = SessionBoard(layout, knobs=knobs, event_log=log)

    assert board.stale_seconds == 42.0
    assert board.heartbeat_seconds == 7.0


def test_explicit_keyword_wins_over_knobs(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    log = EventLog(tmp_path / "var" / "log" / "events.jsonl")
    knobs = Knobs(session_stale_seconds=42.0, session_heartbeat_seconds=7.0)

    board = SessionBoard(layout, knobs=knobs, stale_seconds=99.0, event_log=log)

    assert board.stale_seconds == 99.0  # explicit keyword, not the knob
    assert board.heartbeat_seconds == 7.0  # falls back to knobs


# ==========================================================================
# register / heartbeat / claim / release round-trip
# ==========================================================================


def test_register_writes_live_file_and_event(tmp_path: Path) -> None:
    board, log = _board(tmp_path)

    board.register("s1", "build the sessions module")

    live_file = board.live_dir / "s1.json"
    assert live_file.is_file()
    data = json.loads(live_file.read_text(encoding="utf-8"))
    assert data["session_id"] == "s1"
    assert data["intent"] == "build the sessions module"
    assert data["claims"] == []
    assert data["pid"] == os.getpid()
    assert data["started_at"] == data["heartbeat_at"]

    assert _kinds(log) == [SESSION_START]


def test_heartbeat_updates_timestamp_no_event(tmp_path: Path) -> None:
    board, log = _board(tmp_path)
    board.register("s1", "intent")
    live_file = board.live_dir / "s1.json"
    before = json.loads(live_file.read_text(encoding="utf-8"))["heartbeat_at"]

    time.sleep(0.01)
    board.heartbeat("s1")

    after = json.loads(live_file.read_text(encoding="utf-8"))["heartbeat_at"]
    assert after != before
    assert _kinds(log) == [SESSION_START]  # heartbeat appends nothing


def test_heartbeat_unknown_session_raises(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    with pytest.raises(SessionNotFoundError):
        board.heartbeat("ghost")


def test_claim_merges_and_dedupes(tmp_path: Path) -> None:
    board, log = _board(tmp_path)
    board.register("s1", "intent")

    board.claim("s1", ["notes/foo", "notes/bar"])
    board.claim("s1", ["notes/bar", "notes/baz"])  # notes/bar repeated

    data = json.loads((board.live_dir / "s1.json").read_text(encoding="utf-8"))
    assert data["claims"] == ["notes/foo", "notes/bar", "notes/baz"]
    assert _kinds(log) == [SESSION_START, SESSION_CLAIM, SESSION_CLAIM]


def test_claim_unknown_session_raises(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    with pytest.raises(SessionNotFoundError):
        board.claim("ghost", ["notes/foo"])


def test_release_moves_file_to_history_and_appends_event(tmp_path: Path) -> None:
    board, log = _board(tmp_path)
    board.register("s1", "intent")
    board.claim("s1", ["notes/foo"])

    board.release("s1")

    assert not (board.live_dir / "s1.json").exists()
    history_file = board.history_dir / "s1.json"
    assert history_file.is_file()
    record = json.loads(history_file.read_text(encoding="utf-8"))
    assert record["ended_reason"] == "released"
    assert record["claims"] == ["notes/foo"]
    assert _kinds(log) == [SESSION_START, SESSION_CLAIM, SESSION_RELEASE]


def test_release_unknown_session_raises(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    with pytest.raises(SessionNotFoundError):
        board.release("ghost")


# ==========================================================================
# liveness / stale sweep
# ==========================================================================


def test_stale_session_is_swept_to_history_and_flagged_in_list(tmp_path: Path) -> None:
    board, log = _board(tmp_path, stale_seconds=0.01)
    board.register("s1", "intent")

    time.sleep(0.05)  # older than stale_seconds

    entries = board.list()
    assert len(entries) == 1
    assert entries[0].session_id == "s1"
    assert entries[0].stale is True

    # swept: nothing left in live/, present (and intact) in history/
    assert not (board.live_dir / "s1.json").exists()
    history_file = board.history_dir / "s1.json"
    assert history_file.is_file()
    record = json.loads(history_file.read_text(encoding="utf-8"))
    assert record["ended_reason"] == "expired"

    assert _kinds(log) == [SESSION_START, SESSION_EXPIRE]

    # a second list() finds nothing more to sweep -- already moved
    assert board.list() == []


def test_fresh_session_is_not_stale(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)  # default stale_seconds -- freshly registered, well within it
    board.register("s1", "intent")

    entries = board.list()
    assert len(entries) == 1
    assert entries[0].stale is False
    assert (board.live_dir / "s1.json").exists()


def test_sweep_never_deletes_only_moves(tmp_path: Path) -> None:
    board, _ = _board(tmp_path, stale_seconds=0.01)
    board.register("s1", "important intent")
    board.claim("s1", ["notes/thing.md"])
    time.sleep(0.05)

    board.list()  # triggers the sweep

    record = json.loads((board.history_dir / "s1.json").read_text(encoding="utf-8"))
    # everything the live file carried survives the move
    assert record["intent"] == "important intent"
    assert record["claims"] == ["notes/thing.md"]


def test_own_stale_session_not_swept_mid_call(tmp_path: Path) -> None:
    """A session heartbeating/claiming against its OWN (now-old-by-clock)
    file must not have itself swept out from under the call -- `exclude`
    protects the caller's own id during register/heartbeat/claim/release.
    """
    board, _ = _board(tmp_path, stale_seconds=0.01)
    board.register("s1", "intent")
    time.sleep(0.05)

    # heartbeat on s1 itself must succeed, not raise SessionNotFoundError
    board.heartbeat("s1")
    assert (board.live_dir / "s1.json").exists()


def test_history_collision_gets_disambiguated_not_overwritten(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    board.register("s1", "first run")
    board.release("s1")
    first_record = json.loads((board.history_dir / "s1.json").read_text(encoding="utf-8"))

    board.register("s1", "second run")  # same id, reused
    board.release("s1")

    # original history entry must be untouched
    still_first = json.loads((board.history_dir / "s1.json").read_text(encoding="utf-8"))
    assert still_first == first_record

    # the second ending landed somewhere else in history/, not lost
    history_files = list(board.history_dir.glob("*.json"))
    assert len(history_files) == 2
    intents = {json.loads(p.read_text(encoding="utf-8"))["intent"] for p in history_files}
    assert intents == {"first run", "second run"}

    # The disambiguated file's stamp suffix matches the documented pattern
    # (TODO Low item 4: `_move_to_history` used to produce `+0000`, not the
    # `Z` both the comment and `_HISTORY_STAMP_RE` name -- fixed by
    # reordering the two `.replace()` calls).
    stamped = [p for p in history_files if p.name != "s1.json"]
    assert len(stamped) == 1
    stem_suffix = stamped[0].stem[len("s1-"):]
    assert _HISTORY_STAMP_RE.fullmatch(stem_suffix), stem_suffix
    assert stem_suffix.endswith("Z")
    assert "+0000" not in stem_suffix


# ==========================================================================
# last_intent
# ==========================================================================


def test_last_intent_none_when_never_ended(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    assert board.last_intent("never-existed") is None

    board.register("s1", "still live")
    assert board.last_intent("s1") is None  # never ended -- no history yet


def test_last_intent_returns_the_single_history_record(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    board.register("s1", "build the sessions module")
    board.release("s1")

    assert board.last_intent("s1") == "build the sessions module"


def test_last_intent_returns_the_newest_of_several_endings(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    board.register("s1", "first run")
    board.release("s1")
    time.sleep(0.01)
    board.register("s1", "second run")
    board.release("s1")
    time.sleep(0.01)
    board.register("s1", "third run")
    board.release("s1")

    assert board.last_intent("s1") == "third run"


def test_last_intent_recovers_after_expiry(tmp_path: Path) -> None:
    """The sweep+reclaim scenario the CLI fix targets: a session expires
    (not a graceful release), and `last_intent` still finds it."""
    board, _ = _board(tmp_path, stale_seconds=0.01)
    board.register("s1", "a genuinely active session")
    time.sleep(0.05)
    board.list()  # triggers the sweep -> moved to history as "expired"

    assert board.last_intent("s1") == "a genuinely active session"


def test_last_intent_does_not_confuse_a_different_session_with_a_shared_prefix(
    tmp_path: Path,
) -> None:
    """`last_intent("a")` must not match `a-b.json` (a real, different
    session id `a-b`, not a repeat ending of `a`) -- the stamp-suffix
    match is structural, not a string prefix."""
    board, _ = _board(tmp_path)
    board.register("a-b", "an unrelated session that happens to share a prefix")
    board.release("a-b")

    assert board.last_intent("a") is None


# ==========================================================================
# claim overlap semantics + check()
# ==========================================================================


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("notes/foo", "notes/foo", True),  # identical
        ("notes/foo", "notes/foo/bar.md", True),  # descendant
        ("notes/foo/bar.md", "notes/foo", True),  # ancestor (symmetric)
        ("notes", "notes/foo/bar.md", True),  # root ancestor
        ("notes/foo", "notes/foobar", False),  # NOT a string prefix match
        ("notes/foo", "notes/bar", False),  # unrelated siblings
        ("notes/foo/bar.md", "notes/foo/baz.md", False),  # siblings under same dir
    ],
)
def test_paths_overlap_is_structural_not_string_prefix(a: str, b: str, expected: bool) -> None:
    assert _paths_overlap(a, b) is expected


def test_normalize_claim_path_strips_slashes_and_backslashes() -> None:
    assert _normalize_claim_path("/notes/foo/") == "notes/foo"
    assert _normalize_claim_path("notes\\foo\\bar.md") == "notes/foo/bar.md"
    assert _normalize_claim_path("./notes/foo") == "notes/foo"


def test_overlap_check_breaks_on_purpose_and_recovers() -> None:
    """Proves the overlap test discriminates: break `_paths_overlap` (the
    naive string-prefix version this module deliberately avoids), rerun the
    parametrized cases above, and confirm the false-positive case flips --
    then leave the real implementation restored (this test only exercises a
    LOCAL broken copy, it never mutates the module).
    """

    def _broken_string_prefix(a: str, b: str) -> bool:
        # The bug this module's real _paths_overlap avoids.
        na, nb = _normalize_claim_path(a), _normalize_claim_path(b)
        return na.startswith(nb) or nb.startswith(na)

    # The real function correctly says NO overlap here...
    assert _paths_overlap("notes/foo", "notes/foobar") is False
    # ...while the naive broken version incorrectly says YES -- proving the
    # test case actually discriminates between the two implementations.
    assert _broken_string_prefix("notes/foo", "notes/foobar") is True


def test_check_finds_conflict_from_a_different_live_session(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    board.register("a", "session A working the notes tree")
    board.claim("a", ["notes/composting"])
    board.register("b", "session B")

    conflict = board.check("notes/composting/basics.md", "b")
    assert conflict == Conflict(
        session_id="a",
        intent="session A working the notes tree",
        claimed_path="notes/composting",
    )


def test_check_ignores_own_claims(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    board.register("a", "intent")
    board.claim("a", ["notes/composting"])

    assert board.check("notes/composting/basics.md", "a") is None


def test_check_returns_none_when_no_overlap(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    board.register("a", "intent")
    board.claim("a", ["notes/composting"])
    board.register("b", "intent")

    assert board.check("notes/watering/schedule.md", "b") is None


def test_check_ignores_expired_claimant(tmp_path: Path) -> None:
    board, _ = _board(tmp_path, stale_seconds=0.01)
    board.register("a", "intent")
    board.claim("a", ["notes/composting"])
    time.sleep(0.05)
    board.register("b", "intent")  # sweeps a's now-stale file first

    assert board.check("notes/composting/basics.md", "b") is None


# ==========================================================================
# list()
# ==========================================================================


def test_list_empty_board(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    assert board.list() == []


def test_list_sorted_by_session_id(tmp_path: Path) -> None:
    board, _ = _board(tmp_path)
    board.register("zebra", "z")
    board.register("alpha", "a")

    entries = board.list()
    assert [e.session_id for e in entries] == ["alpha", "zebra"]


# ==========================================================================
# two-process race: no corruption, one-file-per-writer proven under load
# ==========================================================================


def test_concurrent_two_process_register_claim_heartbeat(tmp_path: Path) -> None:
    """Two real OS processes each register/claim/heartbeat their OWN
    session id against the same board concurrently, then release. Asserts:
    no corrupted/torn JSON files, the event log stays hash-chain-valid, and
    both sessions end up cleanly in history/ with nothing left in live/.
    """
    board_root = tmp_path / "corpus"
    worker = tmp_path / "_session_race_worker.py"
    worker.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from ctx_core.layout import Layout\n"
        "from ctx_core.events import EventLog\n"
        "from ctx_core.sessions import SessionBoard\n"
        "\n"
        "root = Path(sys.argv[1])\n"
        "session_id = sys.argv[2]\n"
        "n = int(sys.argv[3])\n"
        "log = EventLog(root / 'var' / 'log' / 'events.jsonl')\n"
        "board = SessionBoard(Layout(root), event_log=log)\n"
        "board.register(session_id, f'race worker {session_id}')\n"
        "for i in range(n):\n"
        "    board.claim(session_id, [f'notes/{session_id}/{i}.md'])\n"
        "    board.heartbeat(session_id)\n"
        "board.release(session_id)\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    session_ids = [f"race{n}" for n in range(RACE_PROCESS_COUNT)]
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(board_root), sid, str(RACE_OPS_PER_PROCESS)],
            cwd=str(REPO_ROOT),
            env=env,
        )
        for sid in session_ids
    ]

    for proc in procs:
        returncode = proc.wait(timeout=60)
        assert returncode == 0

    layout = Layout(board_root)
    log = EventLog(board_root / "var" / "log" / "events.jsonl")

    # event log integrity -- proves concurrent appends never interleaved
    ok, first_break = log.verify()
    assert (ok, first_break) == (True, None)

    # both sessions ended cleanly: nothing left live, both in history, intact
    board = SessionBoard(layout, event_log=log)
    assert list(board.live_dir.glob("*.json")) == []

    for sid in session_ids:
        history_file = board.history_dir / f"{sid}.json"
        assert history_file.is_file()
        record = json.loads(history_file.read_text(encoding="utf-8"))
        assert record["ended_reason"] == "released"
        assert record["session_id"] == sid
        assert len(record["claims"]) == RACE_OPS_PER_PROCESS

    # expected event count: (start + n*claim + release) per process
    kinds = _kinds(log)
    expected_total = RACE_PROCESS_COUNT * (1 + RACE_OPS_PER_PROCESS + 1)
    assert len(kinds) == expected_total
    assert kinds.count(SESSION_START) == RACE_PROCESS_COUNT
    assert kinds.count(SESSION_RELEASE) == RACE_PROCESS_COUNT
    assert kinds.count(SESSION_CLAIM) == RACE_PROCESS_COUNT * RACE_OPS_PER_PROCESS
