"""Tests for `ctx_core.layout.Layout` and `ctx_core.config.Knobs`.

Uses `tests/fixtures/`-free, self-contained temp trees (fixtures/ belongs to
a later module) — a copy of `template/` for the template-state tests, and
plain `tmp_path` directories for the path/Knobs tests.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ctx_core.config import Knobs
from ctx_core.layout import PROFILE_PLACEHOLDER, Layout

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "template"


def _copy_template(dest: Path) -> None:
    shutil.copytree(TEMPLATE_DIR, dest)


# --- Layout paths ---


def test_layout_paths_hang_off_root(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    assert layout.root == tmp_path
    assert layout.core == tmp_path / "core"
    assert layout.notes == tmp_path / "notes"
    assert layout.var == tmp_path / "var"
    assert layout.template == tmp_path / "template"


def test_layout_accepts_str_root(tmp_path: Path) -> None:
    layout = Layout(str(tmp_path))
    assert layout.root == tmp_path
    assert isinstance(layout.root, Path)


# --- is_template_state ---


def test_shipped_template_is_in_template_state() -> None:
    layout = Layout(TEMPLATE_DIR)
    assert layout.is_template_state() is True


def test_fresh_clone_is_in_template_state(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    layout = Layout(clone)
    assert layout.is_template_state() is True


def test_missing_profile_is_not_template_state(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    (clone / "core" / "profile.md").unlink()
    layout = Layout(clone)
    assert layout.is_template_state() is False


def test_customized_profile_is_not_template_state(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    (clone / "core" / "profile.md").write_text(
        "# Profile\n\nName: Example\n", encoding="utf-8"
    )
    layout = Layout(clone)
    assert layout.is_template_state() is False


def test_domain_note_breaks_template_state(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    (clone / "notes" / "example.md").write_text("# A note\n", encoding="utf-8")
    layout = Layout(clone)
    assert layout.is_template_state() is False


def test_gitkeep_alone_does_not_break_template_state(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    # The shipped .gitkeep is already there; is_template_state must still
    # read True with only housekeeping present.
    assert (clone / "notes" / ".gitkeep").is_file()
    layout = Layout(clone)
    assert layout.is_template_state() is True


def test_empty_notes_dir_is_template_state(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    (clone / "notes" / ".gitkeep").unlink()
    layout = Layout(clone)
    assert layout.is_template_state() is True


def test_missing_notes_dir_is_template_state(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _copy_template(clone)
    shutil.rmtree(clone / "notes")
    layout = Layout(clone)
    assert layout.is_template_state() is True


def test_placeholder_constant_matches_shipped_file() -> None:
    text = (TEMPLATE_DIR / "core" / "profile.md").read_text(encoding="utf-8")
    assert text == PROFILE_PLACEHOLDER


# --- Knobs defaults ---


def test_knobs_defaults_present_without_any_config(tmp_path: Path) -> None:
    knobs = Knobs.load(tmp_path)
    assert knobs.pack_budget_tokens == 8000
    assert knobs.archive_stub_threshold_tokens == 2000
    assert knobs.archive_age_days == 90
    assert knobs.retrieval_summary_default is False
    assert knobs.retrieval_last_default is None
    assert knobs.retrieval_decisions_default is False
    assert knobs.eventlog_path == "var/log/"
    assert knobs.yield_exact is False
    assert knobs.yield_model is None


def test_knobs_direct_construction_uses_defaults() -> None:
    knobs = Knobs()
    assert knobs.pack_budget_tokens == 8000


# --- Knobs.load: .ctxrc.toml ---


def test_knobs_load_reads_ctxrc_toml(tmp_path: Path) -> None:
    (tmp_path / ".ctxrc.toml").write_text(
        "pack_budget_tokens = 4000\narchive_age_days = 30\n",
        encoding="utf-8",
    )
    knobs = Knobs.load(tmp_path)
    assert knobs.pack_budget_tokens == 4000
    assert knobs.archive_age_days == 30
    # Untouched fields keep their defaults.
    assert knobs.archive_stub_threshold_tokens == 2000


def test_knobs_load_ignores_unknown_toml_keys(tmp_path: Path) -> None:
    (tmp_path / ".ctxrc.toml").write_text(
        'pack_budget_tokens = 5000\nsome_future_key = "value"\n',
        encoding="utf-8",
    )
    knobs = Knobs.load(tmp_path)
    assert knobs.pack_budget_tokens == 5000
    assert not hasattr(knobs, "some_future_key")


def test_knobs_load_with_no_ctxrc_file_uses_defaults(tmp_path: Path) -> None:
    knobs = Knobs.load(tmp_path)
    assert knobs.pack_budget_tokens == 8000


# --- Knobs.load: override precedence (CLI flags win over file and default) ---


def test_knobs_overrides_win_over_ctxrc_file(tmp_path: Path) -> None:
    (tmp_path / ".ctxrc.toml").write_text(
        "pack_budget_tokens = 4000\n", encoding="utf-8"
    )
    knobs = Knobs.load(tmp_path, overrides={"pack_budget_tokens": 1234})
    assert knobs.pack_budget_tokens == 1234


def test_knobs_overrides_win_over_default(tmp_path: Path) -> None:
    knobs = Knobs.load(tmp_path, overrides={"archive_age_days": 7})
    assert knobs.archive_age_days == 7


def test_knobs_none_override_does_not_clobber_file_value(tmp_path: Path) -> None:
    (tmp_path / ".ctxrc.toml").write_text(
        "pack_budget_tokens = 4000\n", encoding="utf-8"
    )
    knobs = Knobs.load(tmp_path, overrides={"pack_budget_tokens": None})
    assert knobs.pack_budget_tokens == 4000


def test_knobs_unknown_override_key_is_ignored(tmp_path: Path) -> None:
    knobs = Knobs.load(tmp_path, overrides={"not_a_real_knob": 99})
    assert not hasattr(knobs, "not_a_real_knob")
