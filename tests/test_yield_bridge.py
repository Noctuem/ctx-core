"""Tests for `ctx_core.yield_bridge`: the optional `ctx-yield` composition.

Drives a stub `ctx-yield` binary (`tests/fixtures/fake-ctx-yield/`) with the
fixture directory prepended to `PATH` — never the real network, never a real
ctx-yield install. Covers the four required outcomes (installed+clean,
installed+findings, not installed, malformed JSON) plus the incompatible
JSON-shape and timeout/interrupt paths, the `--exact`/`--model`
knob-pass-through, and the `dead_weight_map` join onto a packer `Entry`.
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
from ctx_core.indexing import Entry
from ctx_core.layout import Layout
from ctx_core.yield_bridge import (
    NOT_INSTALLED_HINT,
    YIELD_SCAN_KIND,
    YieldEntry,
    YieldResult,
    dead_weight_map,
    yield_scan,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "fake-ctx-yield"

# Short enough to keep the timeout test fast; long enough that the "hang"
# stub mode (which sleeps 60s) never accidentally finishes within it.
SHORT_TIMEOUT_SECONDS = 0.5


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path)


@pytest.fixture
def fake_ctx_yield_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prepend the stub binary's directory onto PATH, so `shutil.which`
    resolves the fixture instead of any real `ctx-yield` that might also be
    installed on the machine running this suite.
    """
    monkeypatch.setenv("PATH", str(FIXTURE_DIR) + os.pathsep + os.environ.get("PATH", ""))


@pytest.fixture
def no_ctx_yield_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Replace PATH with an empty directory, so `ctx-yield` genuinely cannot
    be found -- deterministic even if the host machine has a real one
    installed.
    """
    empty_dir = tmp_path / "empty-path"
    empty_dir.mkdir()
    monkeypatch.setenv("PATH", str(empty_dir))


def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("FAKE_CTX_YIELD_MODE", mode)


# --- not installed ---------------------------------------------------------


def test_not_installed_returns_none(no_ctx_yield_on_path: None, layout: Layout) -> None:
    assert yield_scan(layout) is None


# --- installed + clean ------------------------------------------------------


def test_installed_clean_returns_empty_entries_no_warning(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "clean")

    result = yield_scan(layout)

    assert result is not None
    assert isinstance(result, YieldResult)
    assert result.entries == []
    assert result.warning is None
    assert result.raw is not None
    assert result.raw["records"] == []


# --- installed + findings ---------------------------------------------------


def test_installed_findings_returns_entries(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "findings")

    result = yield_scan(layout)

    assert result is not None
    assert result.warning is None
    assert len(result.entries) == 2

    by_path = {e.rel_path: e for e in result.entries}
    assert by_path["core/card.md"] == YieldEntry(
        rel_path="core/card.md",
        tokens=42,
        recall_count=0,
        never_recalled=True,
        growth_flagged=False,
    )
    assert by_path["core/map.md"].never_recalled is False
    assert by_path["core/map.md"].recall_count == 5


# --- malformed JSON ----------------------------------------------------------


def test_malformed_json_is_a_distinct_warning_outcome(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "malformed")

    result = yield_scan(layout)

    assert result is not None
    assert result.entries == []
    assert result.warning is not None
    assert "not valid JSON" in result.warning
    assert result.raw is None


def test_unexpected_json_shape_is_also_a_distinct_warning_outcome(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    """Valid JSON that lacks the fields this module reads (simulating an
    incompatible ctx-yield version, per the build spec's open question on
    not pinning a minimum version) is reported distinctly from both a clean
    result and a JSON-parse failure.
    """
    _set_mode(monkeypatch, "bad-shape")

    result = yield_scan(layout)

    assert result is not None
    assert result.entries == []
    assert result.warning is not None
    assert "records" in result.warning
    assert result.raw == {"meta": {}, "totals": {}}


# --- knob pass-through (--exact / --model) ----------------------------------


def test_exact_and_model_knobs_pass_through_to_the_child(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "findings")
    knobs = Knobs(yield_exact=True, yield_model="claude-test-model")

    result = yield_scan(layout, knobs)

    assert result is not None
    assert result.warning is None
    # The stub echoes whether --exact was passed into meta.exact.
    assert result.raw["meta"]["exact"] is True


def test_default_knobs_do_not_pass_exact_or_model(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "findings")

    result = yield_scan(layout)

    assert result is not None
    assert result.raw["meta"]["exact"] is False


# --- timeout / graceful interrupt -------------------------------------------


def test_scan_that_hangs_times_out_and_is_reported(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    """The stub sleeps 60s; `yield_scan` must give up at `timeout` and
    report it (never hang the caller, never crash). Asserting the wall
    time stayed well under the stub's sleep duration is what proves the
    child was actually killed rather than merely outlasted.
    """
    _set_mode(monkeypatch, "hang")

    started = time.monotonic()
    result = yield_scan(layout, timeout=SHORT_TIMEOUT_SECONDS)
    elapsed = time.monotonic() - started

    assert result is not None
    assert result.entries == []
    assert result.warning is not None
    assert "timed out" in result.warning
    assert elapsed < 10.0  # far under the stub's 60s sleep


# --- dead_weight_map join ----------------------------------------------------


def _entry(path: str) -> Entry:
    return Entry(
        path=path,
        tier="core",
        handle="deadbeef",
        title=path,
        tokens=10,
        mtime=0.0,
    )


def test_dead_weight_map_joins_by_path(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "findings")
    result = yield_scan(layout)
    assert result is not None

    pack_entries = [_entry("core/card.md"), _entry("core/map.md"), _entry("notes/unrelated.md")]

    joined = dead_weight_map(pack_entries, result)

    assert set(joined) == {"core/card.md", "core/map.md"}
    assert joined["core/card.md"].never_recalled is True
    assert joined["core/map.md"].never_recalled is False
    assert "notes/unrelated.md" not in joined


def test_dead_weight_map_empty_result_yields_empty_map() -> None:
    pack_entries = [_entry("core/card.md")]
    assert dead_weight_map(pack_entries, YieldResult()) == {}


# --- event_log emission (TODO Medium: yield-bridge eventlog line) ----------


def _log_kinds(log: EventLog) -> list[dict]:
    if not log.path.exists():
        return []
    return [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]


def test_no_event_log_emits_nothing(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "clean")
    log = EventLog(layout.root / "var" / "log" / "events.jsonl")

    yield_scan(layout)  # no event_log= at all

    assert not log.path.exists()


def test_clean_run_emits_exactly_one_event(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "findings")
    log = EventLog(layout.root / "var" / "log" / "events.jsonl")

    result = yield_scan(layout, event_log=log)

    records = _log_kinds(log)
    assert len(records) == 1
    assert records[0]["kind"] == YIELD_SCAN_KIND
    assert records[0]["payload"] == {
        "ran": True,
        "degraded": False,
        "warning": None,
        "n_entries": len(result.entries),
        "timeout_s": pytest.approx(30.0),
    }


def test_not_installed_emits_ran_false(
    no_ctx_yield_on_path: None, layout: Layout
) -> None:
    log = EventLog(layout.root / "var" / "log" / "events.jsonl")

    result = yield_scan(layout, event_log=log)

    assert result is None
    records = _log_kinds(log)
    assert len(records) == 1
    payload = records[0]["payload"]
    assert payload["ran"] is False
    assert payload["degraded"] is False
    assert payload["warning"] == NOT_INSTALLED_HINT
    assert payload["n_entries"] == 0


def test_degraded_run_emits_ran_true_degraded_true(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "malformed")
    log = EventLog(layout.root / "var" / "log" / "events.jsonl")

    yield_scan(layout, event_log=log)

    payload = _log_kinds(log)[0]["payload"]
    assert payload["ran"] is True
    assert payload["degraded"] is True
    assert "not valid JSON" in payload["warning"]
    assert payload["n_entries"] == 0


def test_timeout_emits_ran_true_degraded_true(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "hang")
    log = EventLog(layout.root / "var" / "log" / "events.jsonl")

    yield_scan(layout, timeout=SHORT_TIMEOUT_SECONDS, event_log=log)

    payload = _log_kinds(log)[0]["payload"]
    assert payload["ran"] is True
    assert payload["degraded"] is True
    assert "timed out" in payload["warning"]
    assert payload["timeout_s"] == pytest.approx(SHORT_TIMEOUT_SECONDS)


def test_two_scans_emit_two_events_not_one(
    fake_ctx_yield_on_path: None, monkeypatch: pytest.MonkeyPatch, layout: Layout
) -> None:
    _set_mode(monkeypatch, "clean")
    log = EventLog(layout.root / "var" / "log" / "events.jsonl")

    yield_scan(layout, event_log=log)
    yield_scan(layout, event_log=log)

    assert len(_log_kinds(log)) == 2


# --- fixture shims resolve on this platform ---------------------------------


def test_fixture_shim_is_directly_invocable() -> None:
    """Sanity check on the fixture itself, independent of `yield_scan` /
    `shutil.which`: the platform-appropriate shim in `fake-ctx-yield/` runs
    and produces the expected clean report when invoked directly. Catches a
    broken shim (wrong shebang, missing `+x`, bad `%~dp0` quoting) with a
    clearer failure than a confusing `yield_scan` result would.
    """
    if sys.platform == "win32":
        cmd = [str(FIXTURE_DIR / "ctx-yield.cmd"), "scan", "--json", "."]
    else:
        cmd = [str(FIXTURE_DIR / "ctx-yield"), "scan", "--json", "."]

    env = dict(os.environ, FAKE_CTX_YIELD_MODE="clean")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)

    assert proc.returncode == 0
    assert '"records": []' in proc.stdout
