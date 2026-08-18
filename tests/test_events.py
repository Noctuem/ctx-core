"""Tests for ctx_core.events: the hash-chained append-only event log.

Covers: append/verify round-trip, chain-field correctness, tamper
detection, and the concurrency guarantee (two OS processes hammering the
same log never interleave writes).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ctx_core.events import (
    EventKind,
    EventLog,
    EventLogCorruptError,
    GENESIS_HASH,
    _hash_line,
    _hash_record,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Bound on the race test's wall-clock time; keeps it well under the ~10s
# ceiling asked for even accounting for process-spawn overhead on CI.
RACE_APPENDS_PER_PROCESS = 25
RACE_PROCESS_COUNT = 2


def test_append_creates_parent_dir_and_file(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "events.jsonl"
    log = EventLog(log_path)

    log.append(EventKind.INIT_RUN, {"note": "first"})

    assert log_path.exists()
    ok, first_break = log.verify()
    assert (ok, first_break) == (True, None)


def test_empty_or_missing_log_verifies_true(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    assert log.verify() == (True, None)


def test_append_multiple_and_verify(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")

    log.append(EventKind.INIT_RUN, {"i": 0})
    log.append(EventKind.CONTEXT_ASSEMBLED, {"i": 1})
    log.append(EventKind.ARCHIVE_STUB, {"i": 2})
    log.append(EventKind.DOCTOR_RUN, {"i": 3})
    log.append("some_future_kind", {"i": 4})  # non-EventKind strings allowed

    ok, first_break = log.verify()
    assert (ok, first_break) == (True, None)

    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5

    records = [json.loads(line) for line in lines]

    # seq is monotonic starting at 0
    assert [r["seq"] for r in records] == [0, 1, 2, 3, 4]

    # kind/payload round-trip
    assert records[1]["kind"] == "context_assembled"
    assert records[1]["payload"] == {"i": 1}

    # timestamps are ISO-8601 with an explicit UTC offset
    for r in records:
        assert r["ts"].endswith("+00:00")

    # genesis line chains from GENESIS_HASH
    assert records[0]["prev_hash"] == GENESIS_HASH

    # each subsequent line's prev_hash is the sha256 of the previous raw line
    for i in range(1, len(lines)):
        assert records[i]["prev_hash"] == _hash_line(lines[i - 1])

    # each line's own hash covers everything except "hash" itself
    for record in records:
        assert _hash_record(record) == record["hash"]


def test_verify_detects_edited_line(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    for i in range(4):
        log.append(EventKind.CONTEXT_ASSEMBLED, {"i": i})

    # sanity: untouched chain verifies clean
    assert log.verify() == (True, None)

    lines = log.path.read_text(encoding="utf-8").splitlines()
    tampered_index = 2

    record = json.loads(lines[tampered_index])
    record["payload"] = {"i": "tampered"}  # content changed, hash left stale
    lines[tampered_index] = json.dumps(record, sort_keys=True, separators=(",", ":"))

    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, first_break = log.verify()
    assert (ok, first_break) == (False, tampered_index)


def test_verify_detects_deleted_line(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    for i in range(4):
        log.append(EventKind.CONTEXT_ASSEMBLED, {"i": i})

    lines = log.path.read_text(encoding="utf-8").splitlines()
    del lines[1]  # drop a line from the middle -> breaks the chain at its successor
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, first_break = log.verify()
    assert ok is False
    assert first_break == 1  # the line now sitting at index 1 has a stale prev_hash


def test_append_rejects_bad_args(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    with pytest.raises(ValueError):
        log.append("", {"i": 0})
    with pytest.raises(TypeError):
        log.append(EventKind.INIT_RUN, ["not", "a", "dict"])  # type: ignore[arg-type]


def test_append_on_undecodable_tail_raises_typed_error(tmp_path: Path) -> None:
    """A tail line that isn't even valid JSON (e.g. a tampered/garbage line
    appended outside `append()`) must raise `EventLogCorruptError`, never a
    bare `json.JSONDecodeError` -- and must not extend the log as if the
    tail were sound.
    """
    log = EventLog(tmp_path / "events.jsonl")
    log.append(EventKind.INIT_RUN, {"i": 0})

    with open(log.path, "a", encoding="utf-8", newline="") as f:
        f.write('{"garbage":"tamper"\n')  # unbalanced -> not valid JSON

    lines_before = log.path.read_text(encoding="utf-8").split("\n")
    lines_before = [l for l in lines_before if l]

    with pytest.raises(EventLogCorruptError) as exc_info:
        log.append(EventKind.INIT_RUN, {"i": 1})

    assert str(log.path) in str(exc_info.value)

    lines_after = log.path.read_text(encoding="utf-8").split("\n")
    lines_after = [l for l in lines_after if l]
    assert lines_after == lines_before  # nothing was appended on the failed attempt


def test_append_on_tail_missing_seq_raises_typed_error(tmp_path: Path) -> None:
    """A tail line that IS valid JSON but lacks the `"seq"` field (e.g.
    `{"garbage":"tamper"}` -- valid JSON, missing the chain field `append`
    needs) must also raise `EventLogCorruptError`, not a bare `KeyError`.
    """
    log = EventLog(tmp_path / "events.jsonl")
    log.append(EventKind.INIT_RUN, {"i": 0})

    with open(log.path, "a", encoding="utf-8", newline="") as f:
        f.write('{"garbage":"tamper"}\n')

    with pytest.raises(EventLogCorruptError) as exc_info:
        log.append(EventKind.INIT_RUN, {"i": 1})

    assert str(log.path) in str(exc_info.value)
    assert "seq" in str(exc_info.value)


def test_concurrent_appends_never_interleave(tmp_path: Path) -> None:
    """Two separate OS processes hammer the same log concurrently. The
    advisory lock must serialize every append: no interleaved/corrupted
    lines, the chain still verifies end to end, and the line count equals
    the total number of appends across both processes.
    """
    log_path = tmp_path / "events.jsonl"
    worker = tmp_path / "_race_worker.py"
    worker.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from ctx_core.events import EventLog, EventKind\n"
        "\n"
        "log = EventLog(Path(sys.argv[1]))\n"
        "n = int(sys.argv[2])\n"
        "tag = sys.argv[3]\n"
        "for i in range(n):\n"
        "    log.append(EventKind.CONTEXT_ASSEMBLED, {'tag': tag, 'i': i})\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    procs = [
        subprocess.Popen(
            [
                sys.executable,
                str(worker),
                str(log_path),
                str(RACE_APPENDS_PER_PROCESS),
                f"proc{n}",
            ],
            cwd=str(REPO_ROOT),
            env=env,
        )
        for n in range(RACE_PROCESS_COUNT)
    ]

    for proc in procs:
        returncode = proc.wait(timeout=60)
        assert returncode == 0

    log = EventLog(log_path)
    ok, first_break = log.verify()
    assert (ok, first_break) == (True, None)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == RACE_APPENDS_PER_PROCESS * RACE_PROCESS_COUNT

    # seq values are a contiguous, gap-free, duplicate-free run -- proof
    # that no two appends ever read the same tail and raced past the lock.
    records = [json.loads(line) for line in lines]
    seqs = sorted(r["seq"] for r in records)
    assert seqs == list(range(len(lines)))
