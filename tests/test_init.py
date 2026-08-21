"""Tests for `ctx_core.init.run_interview`.

Uses `tests/fixtures/`-free, self-contained temp trees -- a copy of
`template/` per test, same pattern as `tests/test_layout.py`. Interactive
mode is exercised by monkeypatching `builtins.input` rather than driving a
real stdin, so the suite stays hermetic.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ctx_core.config import Knobs
from ctx_core.events import EventKind, EventLog, default_eventlog_path
from ctx_core.init import (
    ANSWER_FIELDS,
    CANCELLED_MESSAGE,
    UNSPECIFIED_ANSWER,
    run_interview,
)
from ctx_core.layout import PROFILE_PLACEHOLDER, Layout

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "template"

SAMPLE_ANSWERS = {
    "name": "Ada",
    "working_style": "iterative, likes short feedback loops",
    "communication_preferences": "direct, low ceremony",
    "domain_purpose": "a personal research notebook",
}


def _copy_template(dest: Path) -> None:
    shutil.copytree(TEMPLATE_DIR, dest)


def _profile_text(root: Path) -> str:
    return (root / "core" / "profile.md").read_text(encoding="utf-8")


# --- non-interactive: basic write ---


def test_writes_profile_from_answers(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)

    run_interview(layout, answers=SAMPLE_ANSWERS)

    text = _profile_text(clone)
    for key, heading, _prompt in ANSWER_FIELDS:
        assert f"## {heading}" in text
        assert SAMPLE_ANSWERS[key] in text


def test_replaces_placeholder_exactly_once(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    assert _profile_text(clone) == PROFILE_PLACEHOLDER

    run_interview(layout, answers=SAMPLE_ANSWERS)

    text = _profile_text(clone)
    assert text != PROFILE_PLACEHOLDER
    assert "run `ctx init`" not in text


def test_creates_core_dir_if_missing(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    layout = Layout(root)
    assert not layout.core.exists()

    run_interview(layout, answers=SAMPLE_ANSWERS)

    assert (layout.core / "profile.md").is_file()


def test_missing_keys_render_as_unspecified(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)

    run_interview(layout, answers={"name": "Ada"})

    text = _profile_text(clone)
    assert "Ada" in text
    assert UNSPECIFIED_ANSWER in text


def test_blank_values_render_as_unspecified(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)

    run_interview(layout, answers={**SAMPLE_ANSWERS, "domain_purpose": "   "})

    text = _profile_text(clone)
    assert UNSPECIFIED_ANSWER in text


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)

    run_interview(layout, answers={**SAMPLE_ANSWERS, "favorite_color": "teal"})

    text = _profile_text(clone)
    assert "teal" not in text


# --- non-interactive: idempotency / revision ---


def test_rerunning_with_same_answers_is_byte_identical(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)

    run_interview(layout, answers=SAMPLE_ANSWERS)
    first = _profile_text(clone)
    run_interview(layout, answers=SAMPLE_ANSWERS)
    second = _profile_text(clone)

    assert first == second


def test_rerunning_with_new_answers_overwrites_not_duplicates(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)

    run_interview(layout, answers=SAMPLE_ANSWERS)
    revised = {**SAMPLE_ANSWERS, "name": "Grace"}
    run_interview(layout, answers=revised)

    text = _profile_text(clone)
    assert "Grace" in text
    assert "Ada" not in text
    # Still exactly one profile.md -- no `.md.1` / backup artifacts.
    assert list((layout.core).glob("profile*")) == [layout.core / "profile.md"]


def test_non_interactive_overwrites_hand_customized_profile(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    (layout.core / "profile.md").write_text(
        "# Profile\n\nName: Hand Written\n", encoding="utf-8"
    )

    run_interview(layout, answers=SAMPLE_ANSWERS)

    text = _profile_text(clone)
    assert "Ada" in text
    assert "Hand Written" not in text


# --- INIT_RUN event emission (TODO Low, state-report survey item 2) ---


def _kinds(log: EventLog) -> list[dict]:
    if not log.path.exists():
        return []
    return [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]


def test_writes_profile_emits_init_run_event(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))

    run_interview(layout, answers=SAMPLE_ANSWERS, event_log=log)

    records = _kinds(log)
    assert len(records) == 1
    assert records[0]["kind"] == EventKind.INIT_RUN.value
    assert records[0]["payload"]["customized"] is True
    assert set(records[0]["payload"]["fields"]) == set(SAMPLE_ANSWERS)


def test_all_blank_answers_emit_customized_false(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))

    run_interview(layout, answers={}, event_log=log)

    records = _kinds(log)
    assert records[0]["payload"] == {"customized": False, "fields": []}


def test_rerunning_emits_one_event_per_write(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))

    run_interview(layout, answers=SAMPLE_ANSWERS, event_log=log)
    run_interview(layout, answers=SAMPLE_ANSWERS, event_log=log)

    assert len(_kinds(log)) == 2


def test_default_event_log_used_when_none_given(tmp_path: Path) -> None:
    """No `event_log=` -- falls back to the corpus's own default log path
    (same injectable-for-test-isolation pattern as `sessions.py`/`archive.py`).
    """
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)

    run_interview(layout, answers=SAMPLE_ANSWERS)

    log_path = default_eventlog_path(layout.root, Knobs().eventlog_path)
    assert log_path.is_file()
    records = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert records["kind"] == EventKind.INIT_RUN.value


def test_keyboard_interrupt_emits_no_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    log = EventLog(default_eventlog_path(layout.root, Knobs().eventlog_path))

    def _raise_interrupt(_prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _raise_interrupt)

    run_interview(layout, event_log=log)

    assert not log.path.exists()


# --- interactive mode (stdin via monkeypatched input) ---


def _feed(monkeypatch: pytest.MonkeyPatch, values: list[str]) -> None:
    queue = list(values)

    def _fake_input(_prompt: str = "") -> str:
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", _fake_input)


def test_interactive_prompts_for_each_field_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)

    answer_values = [SAMPLE_ANSWERS[key] for key, _h, _p in ANSWER_FIELDS]
    _feed(monkeypatch, answer_values)

    run_interview(layout)

    text = _profile_text(clone)
    for key in SAMPLE_ANSWERS:
        assert SAMPLE_ANSWERS[key] in text


def test_interactive_keyboard_interrupt_leaves_profile_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)

    def _raise_interrupt(_prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _raise_interrupt)

    run_interview(layout)  # must not raise / traceback

    assert _profile_text(clone) == PROFILE_PLACEHOLDER
    assert CANCELLED_MESSAGE.strip() in capsys.readouterr().out


def test_interactive_revise_declined_leaves_profile_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    run_interview(layout, answers=SAMPLE_ANSWERS)
    before = _profile_text(clone)

    _feed(monkeypatch, ["n"])
    run_interview(layout)

    assert _profile_text(clone) == before


def test_interactive_revise_accepted_rewrites_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    run_interview(layout, answers=SAMPLE_ANSWERS)

    revised_values = ["Grace", "deep focus blocks", "blunt", "a lab notebook"]
    _feed(monkeypatch, ["y", *revised_values])
    run_interview(layout)

    text = _profile_text(clone)
    assert "Grace" in text
    assert "Ada" not in text


def test_interactive_skips_revise_prompt_on_fresh_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    assert _profile_text(clone) == PROFILE_PLACEHOLDER

    # No "y"/"n" fed first -- if the revise prompt fired on a placeholder
    # profile this would pop the wrong value into the first question.
    answer_values = [SAMPLE_ANSWERS[key] for key, _h, _p in ANSWER_FIELDS]
    _feed(monkeypatch, answer_values)

    run_interview(layout)

    text = _profile_text(clone)
    assert SAMPLE_ANSWERS["name"] in text
