"""Tests for `ctx_core.packer`: scoring, admission, budgeting, rendering.

Two kinds of coverage:

- Small, hand-built `tmp_path` corpora with fully controlled content, mtimes
  and a frozen `time.time()`, one mechanism at a time (admission, budgeting,
  the retrieval flags, the event emission).
- One fixture-based, byte-identical proof against the frozen, generic
  `tests/fixtures/gardening-corpus/` corpus and its frozen expected manifest
  (`tests/fixtures/gardening-corpus-expected-manifest.md`) — see
  `_build/pack/done.md` for what "byte-identical" means for this port (the
  divergences from the private source it was ported from, and why).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from ctx_core.config import Knobs
from ctx_core.events import EventKind, EventLog
from ctx_core.indexing import build_index
from ctx_core.layout import Layout
from ctx_core.packer import (
    ALWAYS_INCLUDE_MAX_TOKENS,
    MAX_ITEM_AGE_DAYS,
    PATH_LITERAL_MIN_STEM,
    PATH_LITERAL_WEIGHT,
    SUMMARY_BUDGET_FRAC,
    Manifest,
    _named_for_admission,
    _named_literally,
    _term_overlap,
    pack,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gardening-corpus"
EXPECTED_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "gardening-corpus-expected-manifest.md"

DAY = 86_400
FIXED_NOW = 1_700_000_000.0  # 2023-11-14T22:13:20Z — arbitrary, frozen


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _age(path: Path, age_days: float, now: float = FIXED_NOW) -> None:
    mtime = now - age_days * DAY
    os.utime(path, (mtime, mtime))


def _freeze_time(monkeypatch: pytest.MonkeyPatch, now: float = FIXED_NOW) -> None:
    monkeypatch.setattr(time, "time", lambda: now)


# ==========================================================================
# Fixture-based byte-identical proof
# ==========================================================================

_FIXTURE_AGES_DAYS = {
    "TODO.md": 5,
    "Session_Log.md": 5,
    "core/profile.md": 5,
    "notes/composting/composting-basics.md": 1,
    "notes/composting/composting-scraps-duplicate.md": 1,
    "notes/watering/watering-schedule.md": 10,
    "notes/pests/aphids.md": 400,
    "notes/decisions/adopt-raised-beds.md": 2,
    "notes/pruning/pruning-guide-long.md": 3,
}

FIXTURE_TASK = "How do I compost kitchen scraps efficiently in my garden?"


def _prepare_fixture_corpus(tmp_path: Path) -> Layout:
    work = tmp_path / "corpus"
    shutil.copytree(FIXTURE, work)
    for rel, age_days in _FIXTURE_AGES_DAYS.items():
        _age(work / rel, age_days)
    return Layout(work)


def test_pack_fixture_matches_frozen_manifest_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_time(monkeypatch)
    layout = _prepare_fixture_corpus(tmp_path)
    knobs = Knobs(pack_budget_tokens=300)
    log = EventLog(tmp_path / "isolated-events.jsonl")

    manifest = pack(FIXTURE_TASK, layout, knobs, event_log=log)

    expected = EXPECTED_MANIFEST.read_text(encoding="utf-8")
    assert manifest.render() == expected


def test_pack_fixture_selection_mechanics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same run as above, asserted mechanism-by-mechanism rather than as one
    opaque string comparison — so a future reader can see WHY each file
    landed where it did without decoding the frozen manifest by hand."""
    _freeze_time(monkeypatch)
    layout = _prepare_fixture_corpus(tmp_path)
    knobs = Knobs(pack_budget_tokens=300)
    log = EventLog(tmp_path / "isolated-events.jsonl")

    manifest = pack(FIXTURE_TASK, layout, knobs, event_log=log)

    chosen_paths = {e.path for e in manifest.entries}
    dropped_reasons = {e.path: reason for e, reason in manifest.dropped}

    # Root orientation files are always admitted (pinned).
    assert "TODO.md" in chosen_paths
    assert "Session_Log.md" in chosen_paths

    # The best term-overlap match wins outright.
    basics = next(e for e in manifest.entries if e.path == "notes/composting/composting-basics.md")
    assert basics.relevance == pytest.approx(1.0)

    # Its near-duplicate sibling is dropped as redundant, not re-admitted.
    assert "near-duplicate" in dropped_reasons["notes/composting/composting-scraps-duplicate.md"]

    # A file larger than the whole budget cannot be packed by any ranking.
    assert "larger than the whole working budget" in dropped_reasons["notes/pruning/pruning-guide-long.md"]

    # A stale, zero-relevance note is dropped rather than admitted on age alone.
    assert "notes/pests/aphids.md" in dropped_reasons

    assert manifest.total_tokens <= manifest.budget_tokens


# ==========================================================================
# Admission: named-in-task, pinned, decisions, last, summary
# ==========================================================================


def test_named_file_full_path_is_admitted_off_the_top(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "irrelevant.md", "# Irrelevant\n\nNothing about the task here.\n")
    _age(layout.notes / "irrelevant.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack(
        "please read notes/irrelevant.md carefully",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    entry = next(e for e in manifest.entries if e.path == "notes/irrelevant.md")
    assert entry.pinned is True


def test_named_bare_filename_is_admitted_when_unambiguous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "sub" / "widget.md", "# Widget\n\nUnrelated content.\n")
    _age(layout.notes / "sub" / "widget.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack(
        "go look at widget.md please",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    entry = next(e for e in manifest.entries if e.path == "notes/sub/widget.md")
    assert entry.pinned is True


def test_ambiguous_basename_admits_neither(tmp_path: Path) -> None:
    entries = [
        _fake_entry("a/widget.md"),
        _fake_entry("b/widget.md"),
    ]
    admitted = _named_for_admission({"widget.md"}, entries)
    assert admitted == set()


def test_bare_stem_without_extension_is_never_admitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stem alone ('widget', no '.md') is ordinary English and must not
    admit a file — only the full path or basename-with-extension count."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "widget.md", "# Widget\n\nUnrelated content.\n")
    _age(layout.notes / "widget.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack(
        "the widget approach needs work",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    entry = next(e for e in manifest.entries if e.path == "notes/widget.md")
    assert entry.pinned is False


# ==========================================================================
# Ranking-only stem boost (_named_literally / PATH_LITERAL_WEIGHT)
# ==========================================================================


def test_named_literally_matches_full_path_basename_and_bare_stem() -> None:
    assert _named_literally({"notes/topic/note.md"}, "notes/topic/note.md") is True
    assert _named_literally({"note.md"}, "notes/topic/note.md") is True
    assert _named_literally({"note"}, "notes/topic/note.md") is True
    assert _named_literally({"other"}, "notes/topic/note.md") is False


def test_named_literally_short_stem_is_not_matched() -> None:
    # "abc" is 3 chars, below PATH_LITERAL_MIN_STEM (4) -- ordinary English,
    # not treated as a reference.
    assert len("abc") < PATH_LITERAL_MIN_STEM
    assert _named_literally({"abc"}, "notes/abc.md") is False


def test_stem_named_in_task_boosts_relevance_but_is_not_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task mentioning a note by its bare filename STEM (no extension)
    boosts that note's relevance -- the complement to `_named_for_admission`,
    which refuses the bare stem for admission. The boost applies on every
    tier, including notes, and never promotes the entry to `pinned`."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(
        layout.notes / "zucchini-guide.md",
        "# Filler\n\nSomething entirely different: apples oranges grapefruit melons kiwis.\n",
    )
    _age(layout.notes / "zucchini-guide.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack(
        "tell me about the zucchini-guide approach please",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    entry = next(e for e in manifest.entries if e.path == "notes/zucchini-guide.md")
    # Zero term overlap with the task, so relevance is exactly the stem boost.
    assert entry.relevance == pytest.approx(PATH_LITERAL_WEIGHT)
    assert entry.pinned is False


def test_decisions_flag_admits_tagged_note_at_zero_relevance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(
        layout.notes / "decision.md",
        "---\ntags: [decisions]\n---\n# Chose the blue paint\n\nCompletely unrelated to the task.\n",
    )
    _age(layout.notes / "decision.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)

    without_flag = pack("what colour is the shed", layout, knobs, event_log=EventLog(tmp_path / "e1.jsonl"))
    with_flag = pack(
        "what colour is the shed",
        layout,
        knobs,
        decisions=True,
        event_log=EventLog(tmp_path / "e2.jsonl"),
    )

    before = next(e for e in without_flag.entries if e.path == "notes/decision.md")
    after = next(e for e in with_flag.entries if e.path == "notes/decision.md")
    assert before.pinned is False
    assert after.pinned is True


def test_last_flag_admits_most_recently_touched_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "stale.md", "# Stale\n\nUnrelated old note.\n")
    _age(layout.notes / "stale.md", 30)
    _write(layout.notes / "fresh.md", "# Fresh\n\nAlso unrelated but brand new.\n")
    _age(layout.notes / "fresh.md", 0)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack(
        "totally unrelated query string",
        layout,
        knobs,
        last=1,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    fresh = next(e for e in manifest.entries if e.path == "notes/fresh.md")
    assert fresh.pinned is True
    stale = [e for e in manifest.entries if e.path == "notes/stale.md"]
    assert stale == [] or stale[0].pinned is False


def test_summary_flag_scales_down_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    knobs = Knobs(pack_budget_tokens=1000)

    full = pack("anything", layout, knobs, event_log=EventLog(tmp_path / "e1.jsonl"))
    short = pack("anything", layout, knobs, summary=True, event_log=EventLog(tmp_path / "e2.jsonl"))

    assert short.budget_tokens < full.budget_tokens


def test_retrieval_summary_default_knob_applies_when_flag_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    knobs = Knobs(pack_budget_tokens=1000, retrieval_summary_default=True)

    manifest = pack("anything", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl"))

    assert manifest.budget_tokens < 1000


def test_retrieval_last_default_knob_applies_when_flag_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "stale.md", "# Stale\n\nUnrelated old note.\n")
    _age(layout.notes / "stale.md", 30)
    _write(layout.notes / "fresh.md", "# Fresh\n\nAlso unrelated but brand new.\n")
    _age(layout.notes / "fresh.md", 0)
    knobs = Knobs(pack_budget_tokens=8000, retrieval_last_default=1)

    manifest = pack(
        "totally unrelated query string", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl")
    )

    fresh = next(e for e in manifest.entries if e.path == "notes/fresh.md")
    assert fresh.pinned is True


def test_retrieval_decisions_default_knob_applies_when_flag_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(
        layout.notes / "decision.md",
        "---\ntags: [decisions]\n---\n# Chose the blue paint\n\nCompletely unrelated to the task.\n",
    )
    _age(layout.notes / "decision.md", 1)
    knobs = Knobs(pack_budget_tokens=8000, retrieval_decisions_default=True)

    manifest = pack(
        "what colour is the shed", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl")
    )

    entry = next(e for e in manifest.entries if e.path == "notes/decision.md")
    assert entry.pinned is True


def test_explicit_false_cannot_override_a_true_default_knob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented limitation, not a silent footgun: `summary` is a plain
    `bool` per the interface contract, so `summary=False` and an omitted
    `summary` are the same value — a caller cannot force the flag off
    against a `True` knob default. See the docstring on `pack`."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    knobs = Knobs(pack_budget_tokens=1000, retrieval_summary_default=True)

    manifest = pack(
        "anything", layout, knobs, summary=False, event_log=EventLog(tmp_path / "events.jsonl")
    )

    assert manifest.budget_tokens < 1000  # the knob still won


def test_budget_override_is_used_literally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    knobs = Knobs(pack_budget_tokens=1000)

    manifest = pack(
        "anything", layout, knobs, budget=250, event_log=EventLog(tmp_path / "events.jsonl")
    )

    assert manifest.budget_tokens == 250


def test_budget_override_with_summary_is_literal_not_scaled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit `--budget N` means exactly N, even under `--summary` --
    `SUMMARY_BUDGET_FRAC` only ever scales the knob-derived default."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    knobs = Knobs(pack_budget_tokens=1000)

    manifest = pack(
        "anything",
        layout,
        knobs,
        summary=True,
        budget=250,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    assert manifest.budget_tokens == 250


def test_budget_omitted_falls_back_to_the_knob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    knobs = Knobs(pack_budget_tokens=1000)

    full = pack("anything", layout, knobs, event_log=EventLog(tmp_path / "e1.jsonl"))
    assert full.budget_tokens == 1000

    short = pack(
        "anything", layout, knobs, summary=True, event_log=EventLog(tmp_path / "e2.jsonl")
    )
    assert short.budget_tokens == int(1000 * SUMMARY_BUDGET_FRAC)  # unchanged behavior


# ==========================================================================
# Budgeting / dedup mechanics in isolation
# ==========================================================================


def test_oversized_entry_dropped_with_named_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "huge.md", "word " * 2000)  # far bigger than the budget below
    _age(layout.notes / "huge.md", 1)
    knobs = Knobs(pack_budget_tokens=50)

    manifest = pack("word", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl"))

    reason = dict((e.path, r) for e, r in manifest.dropped)["notes/huge.md"]
    assert "larger than the whole working budget" in reason
    assert not any(e.path == "notes/huge.md" for e in manifest.entries)


def test_top_oversized_note_present_when_the_best_match_is_dropped_for_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dogfooding finding: a corpus can pack to 100% fill on
    low-relevance filler while the single best-matching note -- itself
    oversized -- silently never made it in. `top_oversized` (and its
    rendered note) is how the manifest says so."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "huge-best-match.md", "compost kitchen scraps " * 2000)
    _age(layout.notes / "huge-best-match.md", 1)
    _write(layout.notes / "filler.md", "gardening notes about something else entirely")
    _age(layout.notes / "filler.md", 1)
    knobs = Knobs(pack_budget_tokens=200)

    manifest = pack(
        "compost kitchen scraps",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    assert manifest.top_oversized is not None
    assert manifest.top_oversized.path == "notes/huge-best-match.md"
    rendered = manifest.render()
    assert "did not fit" in rendered
    assert "notes/huge-best-match.md" in rendered.split("| file |")[0]  # the note, not the table


def test_top_oversized_note_absent_when_the_best_match_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "small.md", "compost kitchen scraps efficiently")
    _age(layout.notes / "small.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack(
        "compost kitchen scraps",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    assert manifest.top_oversized is None
    assert "did not fit" not in manifest.render()


def test_top_oversized_note_absent_when_nothing_is_oversized_but_something_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary over-budget drop (fits alone, just loses out to a
    tighter pack) must not be mistaken for the oversized-top-hit case."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "a.md", "compost kitchen scraps " * 50)
    _age(layout.notes / "a.md", 1)
    _write(layout.notes / "b.md", "compost kitchen scraps " * 50)
    _age(layout.notes / "b.md", 1)
    knobs = Knobs(pack_budget_tokens=350)  # room for one of the two, neither is oversized alone

    manifest = pack(
        "compost kitchen scraps",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    assert manifest.top_oversized is None
    assert "did not fit" not in manifest.render()


def test_always_include_root_file_over_cap_gets_generic_drop_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root orientation file (TODO.md/Session_Log.md) dropped for size gets
    its own wording, distinct from the named-in-task case above -- a reader
    must not mistake it for an ordinary budget miss."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.root / "TODO.md", "word " * 50_000)  # far over the always-include cap and the budget
    _age(layout.root / "TODO.md", 1)
    knobs = Knobs(pack_budget_tokens=5000)

    manifest = pack(
        "totally unrelated task string", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl")
    )

    reason = dict((e.path, r) for e, r in manifest.dropped)["TODO.md"]
    assert "always-include root file" in reason
    assert "requested by name/flag" not in reason
    assert not any(e.path == "TODO.md" for e in manifest.entries)


def test_near_duplicate_entries_one_survives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    body = "# Note\n\nApples oranges bananas grapes melons kiwis pears plums.\n"
    _write(layout.notes / "one.md", body)
    _write(layout.notes / "two.md", body)
    _age(layout.notes / "one.md", 1)
    _age(layout.notes / "two.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack("apples oranges bananas", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl"))

    chosen_paths = {e.path for e in manifest.entries}
    dropped_paths = {e.path for e, _ in manifest.dropped}
    assert len(chosen_paths & {"notes/one.md", "notes/two.md"}) == 1
    assert len(dropped_paths & {"notes/one.md", "notes/two.md"}) == 1


def test_term_overlap_jaccard() -> None:
    a = _fake_entry("a.md", terms=["x", "y", "z"])
    b = _fake_entry("b.md", terms=["x", "y", "w"])
    assert _term_overlap(a, b) == pytest.approx(2 / 4)


def test_age_gate_drops_stale_zero_relevance_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "ancient.md", "# Ancient\n\nCompletely unrelated content.\n")
    _age(layout.notes / "ancient.md", MAX_ITEM_AGE_DAYS + 10)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack("some other query", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl"))

    assert not any(e.path == "notes/ancient.md" for e in manifest.entries)


def test_age_gate_exempts_high_relevance_stale_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(
        layout.notes / "ancient-relevant.md",
        "# Ancient relevant\n\nsprocket widget gizmo contraption, all four together.\n",
    )
    _age(layout.notes / "ancient-relevant.md", MAX_ITEM_AGE_DAYS + 10)
    knobs = Knobs(pack_budget_tokens=8000)

    # A 4-term query with full overlap reaches relevance 1.0 -- well clear
    # of AGE_GATE_RELEVANCE_EXEMPT -- whereas a 1-term query could never
    # exceed 1/3 (the `max(3, len(q))` floor in the relevance formula) and
    # would not actually exercise the exemption.
    manifest = pack(
        "sprocket widget gizmo contraption",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    assert any(e.path == "notes/ancient-relevant.md" for e in manifest.entries)


def test_age_gate_drop_has_distinct_reason_not_the_generic_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An age-gated drop must be distinguishable, in `manifest.dropped`,
    from an ordinary low-relevance drop -- review finding 2026-08-21: both
    used to fall through to the same "below relevance/recency cutoff"
    wording."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "ancient.md", "# Ancient\n\nCompletely unrelated content.\n")
    _age(layout.notes / "ancient.md", MAX_ITEM_AGE_DAYS + 10)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack("some other query", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl"))

    reason = dict((e.path, r) for e, r in manifest.dropped)["notes/ancient.md"]
    assert reason.startswith(f"older than {MAX_ITEM_AGE_DAYS}d")
    assert "below relevance/recency cutoff" not in reason


def test_age_gate_exemption_reachable_by_a_two_term_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the coverage fix, a 2-term query's PADDED relevance could
    never clear `AGE_GATE_RELEVANCE_EXEMPT` (ceiling 2/3 < 0.75) no matter
    how good the match -- only naming, pinning, or `--last` could rescue an
    old note. The gate must instead read UNPADDED coverage: full 2/2
    overlap on a 2-term task clears 0.75 and survives the pool."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(
        layout.notes / "ancient-relevant.md",
        "# Ancient relevant\n\nsprocket widget, sprocket widget, all about it.\n",
    )
    _age(layout.notes / "ancient-relevant.md", MAX_ITEM_AGE_DAYS + 10)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack("sprocket widget", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl"))

    assert any(e.path == "notes/ancient-relevant.md" for e in manifest.entries)


def test_age_gate_still_drops_a_two_term_task_with_zero_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The companion negative case: the same 2-term task against an old
    note with NO term overlap is still gated and dropped with the item-1
    reason -- the fix widens what CAN clear the exemption, it does not
    loosen the gate itself."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(
        layout.notes / "ancient-unrelated.md",
        "# Ancient unrelated\n\nCompletely different subject matter entirely.\n",
    )
    _age(layout.notes / "ancient-unrelated.md", MAX_ITEM_AGE_DAYS + 10)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack("sprocket widget", layout, knobs, event_log=EventLog(tmp_path / "events.jsonl"))

    assert not any(e.path == "notes/ancient-unrelated.md" for e in manifest.entries)
    reason = dict((e.path, r) for e, r in manifest.dropped)["notes/ancient-unrelated.md"]
    assert reason.startswith(f"older than {MAX_ITEM_AGE_DAYS}d")


def test_intake_item_under_cap_is_admitted_regardless_of_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrouted intake note is surfaced in every pack when it fits under
    `knobs.intake_always_include_max_tokens` -- admitted the same way an
    always-include root file is, even though the task text has nothing to
    do with its content."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(
        layout.notes / "intake" / "2023-11-01-a-fresh-idea.md",
        "---\nctx:layer: new\nctx:source: user\nctx:received: 2023-11-01T00:00:00+00:00\n---\n"
        "# A fresh idea\n\nCompletely unrelated to gardening or any task string.\n",
    )
    _age(layout.notes / "intake" / "2023-11-01-a-fresh-idea.md", 1)
    knobs = Knobs(pack_budget_tokens=8000, intake_always_include_max_tokens=2000)

    manifest = pack(
        "totally unrelated task about something else entirely",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    entry = next(
        e for e in manifest.entries if e.path == "notes/intake/2023-11-01-a-fresh-idea.md"
    )
    assert entry.pinned is True
    assert entry.always_include is True


def test_intake_item_over_cap_is_dropped_with_distinct_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrouted intake note bigger than the intake cap is dropped
    outright (never falls back into the ranked pool), with a drop reason
    that names the intake cap specifically -- distinct from the general
    always-include-over-cap wording."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(
        layout.notes / "intake" / "2023-11-01-big-idea.md",
        "---\nctx:layer: new\nctx:source: user\nctx:received: 2023-11-01T00:00:00+00:00\n---\n"
        "# Big idea\n\n" + ("word " * 100) + "\n",
    )
    _age(layout.notes / "intake" / "2023-11-01-big-idea.md", 1)
    knobs = Knobs(pack_budget_tokens=8000, intake_always_include_max_tokens=10)

    manifest = pack(
        "totally unrelated task about something else entirely",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    assert not any(e.path == "notes/intake/2023-11-01-big-idea.md" for e in manifest.entries)
    reason = dict((e.path, r) for e, r in manifest.dropped)["notes/intake/2023-11-01-big-idea.md"]
    assert "intake-always-include cap" in reason
    assert "10" in reason


def test_no_intake_dir_is_identical_to_no_modifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent `notes/intake/` changes nothing about an ordinary pack --
    same shape of run as any corpus with no intake queue at all."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "ordinary.md", "# Ordinary\n\nNothing intake-related here.\n")
    _age(layout.notes / "ordinary.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)

    assert not (layout.notes / "intake").exists()

    manifest = pack(
        "totally unrelated task string",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    entry = next(e for e in manifest.entries if e.path == "notes/ordinary.md")
    assert entry.pinned is False


def test_pinned_entry_over_cap_falls_back_to_ordinary_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "big-named.md", "word " * (ALWAYS_INCLUDE_MAX_TOKENS * 2))
    _age(layout.notes / "big-named.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)

    manifest = pack(
        "please see notes/big-named.md",
        layout,
        knobs,
        event_log=EventLog(tmp_path / "events.jsonl"),
    )

    entry = next(
        (e for e in manifest.entries if e.path == "notes/big-named.md"),
        None,
    )
    if entry is not None:
        assert entry.pinned is False  # too big for the always-include cap


# ==========================================================================
# Manifest
# ==========================================================================


def test_manifest_fill_property() -> None:
    m = Manifest(task="t", entries=[], dropped=[], budget_tokens=100, total_tokens=25)
    assert m.fill == pytest.approx(0.25)


def test_manifest_fill_zero_budget_is_zero() -> None:
    m = Manifest(task="t", entries=[], dropped=[], budget_tokens=0, total_tokens=0)
    assert m.fill == 0.0


def test_manifest_render_lists_excluded_pairs_and_census(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "history" / "old.md", "# Old\n\nExcluded by prefix.\n")
    _write(layout.notes / "kept.md", "# Kept\n\nSurvives.\n")
    _age(layout.notes / "history" / "old.md", 1)
    _age(layout.notes / "kept.md", 1)
    # build_index directly, to exercise exclude_prefixes, then render through
    # a hand-built Manifest to check the census line's format.
    entries, census = build_index(layout, exclude_prefixes=("notes/history/",))
    manifest = Manifest(task="t", entries=entries, dropped=[], budget_tokens=100, total_tokens=10, census=census)
    rendered = manifest.render()
    assert "Never indexed (not candidates): 1 excluded by caller-supplied prefix" in rendered


# ==========================================================================
# CONTEXT_ASSEMBLED event emission
# ==========================================================================


def test_pack_emits_exactly_one_context_assembled_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    _write(layout.notes / "a.md", "# A\n\nSome content.\n")
    _age(layout.notes / "a.md", 1)
    knobs = Knobs(pack_budget_tokens=8000)
    log_path = tmp_path / "isolated" / "events.jsonl"
    log = EventLog(log_path)

    pack("some task", layout, knobs, event_log=log)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == EventKind.CONTEXT_ASSEMBLED.value
    assert record["payload"]["task"] == "some task"
    assert record["payload"]["n_candidates"] == 1
    ok, first_break = log.verify()
    assert (ok, first_break) == (True, None)


def test_pack_event_log_is_injectable_and_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller can point the event emission at an isolated log so tests
    (or a dry-run) never touch the corpus's real `var/log/events.jsonl`."""
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    knobs = Knobs(pack_budget_tokens=8000)
    isolated_log_path = tmp_path / "elsewhere" / "isolated.jsonl"

    pack("task", layout, knobs, event_log=EventLog(isolated_log_path))

    assert isolated_log_path.exists()
    assert not (layout.var / "log" / "events.jsonl").exists()


def test_pack_uses_default_eventlog_path_when_not_injected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    knobs = Knobs(pack_budget_tokens=8000)

    pack("task", layout, knobs)

    assert (layout.root / "var" / "log" / "events.jsonl").exists()


def test_two_packs_append_two_chained_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_time(monkeypatch)
    layout = Layout(tmp_path)
    knobs = Knobs(pack_budget_tokens=8000)
    log = EventLog(tmp_path / "events.jsonl")

    pack("first task", layout, knobs, event_log=log)
    pack("second task", layout, knobs, event_log=log)

    ok, first_break = log.verify()
    assert (ok, first_break) == (True, None)
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# --- helpers ---


def _fake_entry(path: str, terms: list[str] | None = None):
    from ctx_core.indexing import Entry

    return Entry(
        path=path,
        tier="notes",
        handle="h",
        title=Path(path).stem,
        tokens=10,
        mtime=0.0,
        terms=terms or [],
    )
