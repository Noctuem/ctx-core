"""Tests for `ctx_core.indexing`: candidate discovery for the packer.

Covers front-matter parsing, prose/data classification, the always-include
root files, general path exclusion, and the tree walk over `core/`/`notes/`.
Self-contained `tmp_path` trees throughout — the shared frozen fixture
corpus (`tests/fixtures/gardening-corpus/`) is reserved for the
byte-identical manifest proof in `test_packer.py`.
"""

from __future__ import annotations

import time
from pathlib import Path

from ctx_core.indexing import (
    DATA_BLOB_MIN_TOKENS,
    Entry,
    _parse_front_matter,
    _terms,
    approx_tokens,
    build_index,
    classify_is_data,
)
from ctx_core.layout import Layout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# --- approx_tokens ---


def test_approx_tokens_floors_to_one() -> None:
    assert approx_tokens("") == 1
    assert approx_tokens("abc") == 1
    assert approx_tokens("a" * 400) == 100


# --- front matter ---


def test_parse_front_matter_extracts_scalars_lists_and_bools() -> None:
    text = (
        "---\n"
        "title: A Note\n"
        "ctx:pin: true\n"
        "tags: [a, b, c]\n"
        "---\n"
        "Body text.\n"
    )
    meta, body = _parse_front_matter(text)
    assert meta == {"title": "A Note", "ctx:pin": True, "tags": ["a", "b", "c"]}
    assert body == "Body text.\n"


def test_parse_front_matter_absent_returns_empty_meta_and_full_body() -> None:
    text = "# Just a note\n\nNo front matter here.\n"
    meta, body = _parse_front_matter(text)
    assert meta == {}
    assert body == text


def test_parse_front_matter_namespaced_key_is_not_dropped() -> None:
    # A naive `split(":", 1)` would silently drop `ctx:pin`'s value.
    meta, _ = _parse_front_matter("---\nctx:pin: true\n---\nbody\n")
    assert meta["ctx:pin"] is True


# --- _terms ---


def test_terms_drops_stopwords_and_short_words() -> None:
    terms = _terms("the cat sat on a mat and looked at the big dog")
    assert "the" not in terms
    assert "and" not in terms
    assert "at" not in terms  # too short (< 4 chars)
    assert "looked" in terms
    assert "big" not in terms  # 3 chars, below the length floor


def test_terms_ranks_by_frequency() -> None:
    terms = _terms("apple apple apple banana banana cherry")
    assert terms[0] == "apple"
    assert terms[1] == "banana"
    assert terms[2] == "cherry"


# --- classify_is_data ---


def test_classify_is_data_prose_suffix_never_data() -> None:
    assert classify_is_data("notes/big.md", DATA_BLOB_MIN_TOKENS * 10) is False


def test_classify_is_data_small_data_suffix_stays_ordinary() -> None:
    assert classify_is_data("var/config.json", DATA_BLOB_MIN_TOKENS - 1) is False


def test_classify_is_data_large_data_suffix_is_data() -> None:
    assert classify_is_data("var/log/events.jsonl", DATA_BLOB_MIN_TOKENS) is True


def test_classify_is_data_unlisted_suffix_never_data() -> None:
    assert classify_is_data("notes/config.yaml", DATA_BLOB_MIN_TOKENS * 10) is False


# --- build_index ---


def test_build_index_walks_core_and_notes(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.core / "profile.md", "# Profile\n\nSome user tailoring text.\n")
    _write(layout.notes / "topic" / "note.md", "# A Note\n\nAbout gardening topics.\n")

    entries, census = build_index(layout)

    by_path = {e.path: e for e in entries}
    assert "core/profile.md" in by_path
    assert by_path["core/profile.md"].tier == "core"
    assert "notes/topic/note.md" in by_path
    assert by_path["notes/topic/note.md"].tier == "notes"
    assert census == {}


def test_build_index_missing_trees_are_not_an_error(tmp_path: Path) -> None:
    layout = Layout(tmp_path)  # neither core/ nor notes/ exists
    entries, census = build_index(layout)
    assert entries == []
    assert census == {}


def test_build_index_skips_its_own_manifest_output(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(
        layout.notes / "views" / "MANIFEST.md",
        "---\nctx:manifest: true\n---\n\nrendered output\n",
    )
    entries, _ = build_index(layout)
    assert entries == []


def test_build_index_reads_title_pin_and_tags_from_front_matter(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(
        layout.notes / "pinned.md",
        "---\ntitle: Pinned Note\nctx:pin: true\ntags: [important, decisions]\n---\n"
        "Body content here.\n",
    )
    entries, _ = build_index(layout)
    assert len(entries) == 1
    e = entries[0]
    assert e.title == "Pinned Note"
    assert e.pinned is True
    assert e.tags == ["important", "decisions"]


def test_build_index_title_falls_back_to_first_heading(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.notes / "note.md", "# The Real Title\n\nBody.\n")
    entries, _ = build_index(layout)
    assert entries[0].title == "The Real Title"


def test_build_index_general_exclusion_prefix_is_not_repo_specific(tmp_path: Path) -> None:
    """A project's own history/archive tree is excluded via a caller-
    supplied, general path prefix — never a name hardcoded to any specific
    project's directory convention."""
    layout = Layout(tmp_path)
    _write(layout.notes / "history" / "old-run.md", "# Old run\n\nStuff.\n")
    _write(layout.notes / "kept.md", "# Kept\n\nStuff.\n")

    entries, census = build_index(layout, exclude_prefixes=("notes/history/",))

    paths = {e.path for e in entries}
    assert "notes/history/old-run.md" not in paths
    assert "notes/kept.md" in paths
    assert census == {"excluded by caller-supplied prefix": 1}


def test_build_index_always_include_root_files_present(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout.root / "TODO.md", "# TODO\n\n- do a thing\n")
    entries, _ = build_index(layout)
    todo = [e for e in entries if e.path == "TODO.md"][0]
    assert todo.tier == "notes"
    assert todo.always_include is True


def test_build_index_always_include_root_files_absent_is_normal(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    entries, census = build_index(layout)
    assert entries == []
    assert census == {}  # absence never counted as an exclusion or a warning


def test_entry_item_id_is_stable_hash_of_path() -> None:
    e = Entry(path="notes/a.md", tier="notes", handle="h", title="A", tokens=1, mtime=0.0)
    assert e.item_id == e.item_id
    assert len(e.item_id) == 16
    other = Entry(path="notes/b.md", tier="notes", handle="h", title="B", tokens=1, mtime=0.0)
    assert e.item_id != other.item_id
