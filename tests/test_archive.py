"""Tests for `ctx_core.archive`: content-addressed stubbing + lifecycle sweep.

Self-contained `tmp_path` corpora throughout (same pattern as
`tests/test_packer.py`'s hand-built cases) -- no shared fixtures/ directory
needed for this module. Covers:

- `archive_note`: content-addressing, stub shape, reversibility, idempotency,
  relative/absolute paths, summary vs. naive truncation, event emission.
- `stub_oversized_notes`: the full size-triggered policy over a corpus.
- `sweep`: age-based PROPOSALS -- never touches a note, writes the queue
  file, skips core/orientation files and already-archived notes.
- Never-deletes: original bytes always recoverable after a stub operation.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ctx_core.archive import (
    ARCHIVE_DIRNAME,
    HANDLE_PREFIX,
    PROPOSALS_BASENAME,
    SHARD_LEN,
    SOURCES_KEY,
    ArchiveProposal,
    archive_note,
    archived_path_for_handle,
    read_archived,
    stub_oversized_notes,
    sweep,
)
from ctx_core.config import Knobs
from ctx_core.events import EventKind, EventLog
from ctx_core.layout import Layout

DAY = 86_400
FIXED_NOW = 1_700_000_000.0  # 2023-11-14T22:13:20Z -- arbitrary, frozen


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _age(path: Path, age_days: float, now: float = FIXED_NOW) -> None:
    mtime = now - age_days * DAY
    os.utime(path, (mtime, mtime))


def _freeze_time(monkeypatch: pytest.MonkeyPatch, now: float = FIXED_NOW) -> None:
    monkeypatch.setattr(time, "time", lambda: now)


LONG_BODY = "This note explains composting in great detail. " * 40  # well over any small threshold


# ==========================================================================
# archive_note
# ==========================================================================


def test_archive_note_returns_sha256_handle_with_prefix(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    handle = archive_note("notes/big.md", layout)

    assert handle.startswith(HANDLE_PREFIX)
    hex_digest = handle[len(HANDLE_PREFIX):]
    assert len(hex_digest) == 64
    assert all(c in "0123456789abcdef" for c in hex_digest)


def test_archive_note_shards_by_first_two_hex_chars(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    handle = archive_note("notes/big.md", layout)
    hex_digest = handle[len(HANDLE_PREFIX):]

    expected = layout.var / ARCHIVE_DIRNAME / hex_digest[:SHARD_LEN] / f"{hex_digest}.md"
    assert expected.is_file()
    assert archived_path_for_handle(layout, handle) == expected


def test_archive_note_stub_replaces_live_note(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    handle = archive_note("notes/big.md", layout)

    stub_text = note.read_text(encoding="utf-8")
    assert stub_text.startswith("---\n")
    assert f"{SOURCES_KEY}: {handle}\n" in stub_text
    # The stub is much smaller than the original body.
    assert len(stub_text) < len(LONG_BODY)


def test_archive_note_is_reversible(tmp_path: Path) -> None:
    """The core guarantee: the handle in the stub always resolves back to
    the exact original full text, byte for byte."""
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    handle = archive_note("notes/big.md", layout)

    assert read_archived(layout, handle) == LONG_BODY


def test_archive_note_never_deletes_anything(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    handle = archive_note("notes/big.md", layout)

    # The live note still exists (as a stub) and the archived copy exists.
    assert note.is_file()
    assert archived_path_for_handle(layout, handle).is_file()


def test_archive_note_explicit_summary_used_verbatim(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    archive_note("notes/big.md", layout, summary="A hand-written one-liner.")

    stub_text = note.read_text(encoding="utf-8")
    assert "A hand-written one-liner." in stub_text
    assert "composting" not in stub_text.lower()


def test_archive_note_naive_truncation_when_no_summary_given(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    archive_note("notes/big.md", layout)

    stub_text = note.read_text(encoding="utf-8")
    assert "composting" in stub_text.lower()


def test_archive_note_naive_summary_strips_front_matter(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, "---\ntitle: Composting\ntags: [x]\n---\n\n" + LONG_BODY)

    archive_note("notes/big.md", layout)

    stub_text = note.read_text(encoding="utf-8")
    body = stub_text.split("---\n\n", 1)[1]
    assert "title:" not in body
    assert "composting" in body.lower()


def test_archive_note_idempotent_on_same_content(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    handle1 = archive_note("notes/big.md", layout)
    archived_path = archived_path_for_handle(layout, handle1)
    first_write_time = archived_path.stat().st_mtime

    # Re-archive a note holding the SAME original content (simulating a
    # second sweep pass over an already-stubbed... no: archiving the stub
    # itself would address different content. Instead directly call
    # archive_note a second time on fresh identical content to prove the
    # archive file is written once and not re-created differently.
    note2 = layout.notes / "dup.md"
    _write(note2, LONG_BODY)
    handle2 = archive_note("notes/dup.md", layout)

    assert handle1 == handle2
    assert archived_path.stat().st_mtime == first_write_time
    assert read_archived(layout, handle2) == LONG_BODY


def test_archive_note_accepts_absolute_path(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    handle = archive_note(note, layout)

    assert read_archived(layout, handle) == LONG_BODY


def test_archive_note_emits_archive_stub_event_by_default(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    handle = archive_note("notes/big.md", layout)

    log = EventLog(layout.var / "log" / "events.jsonl")
    ok, first_break = log.verify()
    assert (ok, first_break) == (True, None)

    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    import json

    record = json.loads(lines[0])
    assert record["kind"] == EventKind.ARCHIVE_STUB.value
    assert record["payload"]["handle"] == handle
    assert record["payload"]["path"] == "notes/big.md"


def test_archive_note_accepts_injected_event_log(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    note = layout.notes / "big.md"
    _write(note, LONG_BODY)

    isolated_log = EventLog(tmp_path / "isolated.jsonl")
    archive_note("notes/big.md", layout, event_log=isolated_log)

    ok, _ = isolated_log.verify()
    assert ok is True
    default_log_path = layout.var / "log" / "events.jsonl"
    assert not default_log_path.exists()


# ==========================================================================
# stub_oversized_notes
# ==========================================================================


def test_stub_oversized_notes_archives_only_notes_over_threshold(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.notes / "small.md", "Just a short note.")
    _write(layout.notes / "big.md", LONG_BODY)
    knobs = Knobs(archive_stub_threshold_tokens=50)

    handles = stub_oversized_notes(layout, knobs)

    assert len(handles) == 1
    small_text = (layout.notes / "small.md").read_text(encoding="utf-8")
    big_text = (layout.notes / "big.md").read_text(encoding="utf-8")
    assert "Just a short note." in small_text
    assert SOURCES_KEY in big_text


def test_stub_oversized_notes_skips_core_and_orientation_files(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", LONG_BODY)
    _write(tmp_path / "TODO.md", LONG_BODY)
    knobs = Knobs(archive_stub_threshold_tokens=10)

    handles = stub_oversized_notes(layout, knobs)

    assert handles == []
    assert SOURCES_KEY not in (layout.core / "profile.md").read_text(encoding="utf-8")
    assert SOURCES_KEY not in (tmp_path / "TODO.md").read_text(encoding="utf-8")


def test_stub_oversized_notes_is_idempotent(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.notes / "big.md", LONG_BODY)
    knobs = Knobs(archive_stub_threshold_tokens=50)

    first = stub_oversized_notes(layout, knobs)
    second = stub_oversized_notes(layout, knobs)

    assert len(first) == 1
    assert second == []  # already a stub -- nothing left to archive


# ==========================================================================
# sweep
# ==========================================================================


def test_sweep_proposes_notes_past_age_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    old_note = layout.notes / "old.md"
    fresh_note = layout.notes / "fresh.md"
    _write(old_note, "An old note.")
    _write(fresh_note, "A fresh note.")
    _age(old_note, 120)
    _age(fresh_note, 5)
    knobs = Knobs(archive_age_days=90)

    proposals = sweep(layout, knobs)

    assert len(proposals) == 1
    assert proposals[0].path == "notes/old.md"
    assert proposals[0].threshold_days == 90


def test_sweep_never_touches_the_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    old_note = layout.notes / "old.md"
    original_text = "An old note that must survive untouched."
    _write(old_note, original_text)
    _age(old_note, 120)
    knobs = Knobs(archive_age_days=90)

    sweep(layout, knobs)

    assert old_note.read_text(encoding="utf-8") == original_text


def test_sweep_writes_proposals_queue_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    old_note = layout.notes / "old.md"
    _write(old_note, "An old note.")
    _age(old_note, 120)
    knobs = Knobs(archive_age_days=90)

    sweep(layout, knobs)

    queue_path = layout.var / PROPOSALS_BASENAME
    assert queue_path.is_file()
    text = queue_path.read_text(encoding="utf-8")
    assert "ctx:manifest: true" in text
    assert "notes/old.md" in text


def test_sweep_clean_corpus_writes_empty_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    fresh_note = layout.notes / "fresh.md"
    _write(fresh_note, "A fresh note.")
    _age(fresh_note, 5)
    knobs = Knobs(archive_age_days=90)

    proposals = sweep(layout, knobs)

    assert proposals == []
    text = (layout.var / PROPOSALS_BASENAME).read_text(encoding="utf-8")
    assert "Nothing proposed" in text


def test_sweep_skips_core_and_orientation_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "Profile.")
    _write(tmp_path / "TODO.md", "Backlog.")
    _age(layout.core / "profile.md", 500)
    _age(tmp_path / "TODO.md", 500)
    knobs = Knobs(archive_age_days=90)

    proposals = sweep(layout, knobs)

    assert proposals == []


def test_sweep_skips_already_archived_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    note = layout.notes / "old.md"
    _write(note, LONG_BODY)
    archive_note("notes/old.md", layout)
    _age(note, 500)
    knobs = Knobs(archive_age_days=90)

    proposals = sweep(layout, knobs)

    assert proposals == []


def test_sweep_sorts_oldest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    a = layout.notes / "a.md"
    b = layout.notes / "b.md"
    _write(a, "Note A.")
    _write(b, "Note B.")
    _age(a, 100)
    _age(b, 300)
    knobs = Knobs(archive_age_days=90)

    proposals = sweep(layout, knobs)

    assert [p.path for p in proposals] == ["notes/b.md", "notes/a.md"]


def test_sweep_is_idempotent_across_repeated_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    old_note = layout.notes / "old.md"
    _write(old_note, "An old note.")
    _age(old_note, 120)
    knobs = Knobs(archive_age_days=90)

    first = sweep(layout, knobs)
    second = sweep(layout, knobs)

    assert [p.path for p in first] == [p.path for p in second]


def test_archive_proposal_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    old_note = layout.notes / "old.md"
    _write(old_note, "An old note.")
    _age(old_note, 120)
    knobs = Knobs(archive_age_days=90)

    proposals = sweep(layout, knobs)

    assert len(proposals) == 1
    p = proposals[0]
    assert isinstance(p, ArchiveProposal)
    assert p.path == "notes/old.md"
    assert p.tokens > 0
    assert p.age_days >= 120
    assert p.mtime > 0
    assert p.threshold_days == 90
