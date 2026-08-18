"""Tests for `ctx_core.intake`: New-layer front door + routing.

Self-contained `tmp_path` corpora throughout (same pattern as
`tests/test_archive.py`). Covers:

- `intake_add`: front matter shape, source default/validation, slug from
  title/text-head, filesystem-safe slugs, collision suffixing, event
  emission (default + injected log).
- `intake_route`: routing to all three destinations (`core`, `notes/<sub>`,
  `archive`), layer rewrite per destination, archive-route content
  survival via the content-addressed handle, preserved unrelated
  front-matter keys, bad-destination rejection, event payload (source,
  age-at-routing, destination).
- `intake_list`: unrouted-queue membership, oldest-first ordering, item
  fields, title fallback.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ctx_core.archive import read_archived
from ctx_core.config import Knobs
from ctx_core.events import EventLog, default_eventlog_path
from ctx_core.intake import (
    INTAKE_ADD,
    INTAKE_DIRNAME,
    INTAKE_ROUTE,
    LAYER_KEY,
    LAYER_NECESSARY,
    LAYER_NEW,
    LAYER_RELEVANT,
    RECEIVED_KEY,
    SOURCE_KEY,
    SOURCE_PROJECT,
    SOURCE_RESEARCH,
    SOURCE_USER,
    IntakeItem,
    intake_add,
    intake_list,
    intake_route,
)
from ctx_core.layout import Layout

T0 = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _events(layout: Layout) -> list[dict]:
    knobs = Knobs.load(layout.root)
    log = EventLog(default_eventlog_path(layout.root, knobs.eventlog_path))
    if not log.path.exists():
        return []
    return [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]


# ==========================================================================
# intake_add
# ==========================================================================


def test_intake_add_writes_new_layer_front_matter(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    path = intake_add(layout, "Some raw text just captured.", now=T0)

    assert path.is_file()
    assert path.parent == layout.notes / INTAKE_DIRNAME
    text = path.read_text(encoding="utf-8")
    assert f"{LAYER_KEY}: {LAYER_NEW}" in text
    assert f"{SOURCE_KEY}: {SOURCE_USER}" in text  # default source
    assert f"{RECEIVED_KEY}: {T0.isoformat()}" in text
    assert "Some raw text just captured." in text


def test_intake_add_filename_uses_date_and_slug(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    path = intake_add(layout, "body", title="My Great Title!", now=T0)

    assert path.name == "2026-08-18-my-great-title.md"


def test_intake_add_source_project_and_research_accepted(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    p1 = intake_add(layout, "a", source=SOURCE_PROJECT, now=T0)
    p2 = intake_add(layout, "b", source=SOURCE_RESEARCH, now=T0)

    assert f"{SOURCE_KEY}: {SOURCE_PROJECT}" in p1.read_text(encoding="utf-8")
    assert f"{SOURCE_KEY}: {SOURCE_RESEARCH}" in p2.read_text(encoding="utf-8")


def test_intake_add_bad_source_rejected(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    with pytest.raises(ValueError):
        intake_add(layout, "text", source="carrier-pigeon", now=T0)

    # nothing written on rejection
    intake_dir = layout.notes / INTAKE_DIRNAME
    assert not intake_dir.exists() or not any(intake_dir.iterdir())


def test_intake_add_slug_falls_back_to_text_head_when_no_title(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    path = intake_add(layout, "Remember to water the tomatoes daily", now=T0)

    assert "remember-to-water-the-tomatoes" in path.name


def test_intake_add_slug_is_filesystem_safe(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    path = intake_add(layout, "body", title="Weird/Chars: *?<>| & Spaces!!", now=T0)

    # only lowercase alnum + hyphens survive between the date and ".md"
    stem = path.stem
    assert stem.startswith("2026-08-18-")
    slug = stem[len("2026-08-18-"):]
    assert slug != ""
    assert all(c.isalnum() or c == "-" for c in slug)


def test_intake_add_empty_slug_source_falls_back_to_default(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    path = intake_add(layout, "!!!???", now=T0)

    assert path.name == "2026-08-18-note.md"


def test_intake_add_collision_suffixed(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    p1 = intake_add(layout, "first", title="Same Title", now=T0)
    p2 = intake_add(layout, "second", title="Same Title", now=T0)
    p3 = intake_add(layout, "third", title="Same Title", now=T0)

    assert p1.name == "2026-08-18-same-title.md"
    assert p2.name == "2026-08-18-same-title-2.md"
    assert p3.name == "2026-08-18-same-title-3.md"
    assert "first" in p1.read_text(encoding="utf-8")
    assert "second" in p2.read_text(encoding="utf-8")
    assert "third" in p3.read_text(encoding="utf-8")


def test_intake_add_emits_intake_add_event_by_default(tmp_path: Path) -> None:
    layout = Layout(tmp_path)

    path = intake_add(layout, "text", source=SOURCE_PROJECT, title="T", now=T0)

    events = _events(layout)
    assert len(events) == 1
    assert events[0]["kind"] == INTAKE_ADD
    assert events[0]["payload"]["path"] == f"notes/{INTAKE_DIRNAME}/{path.name}"
    assert events[0]["payload"]["source"] == SOURCE_PROJECT
    assert events[0]["payload"]["title"] == "T"
    assert events[0]["payload"]["received"] == T0.isoformat()


def test_intake_add_accepts_injected_event_log(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    isolated_log = EventLog(tmp_path / "isolated.jsonl")

    intake_add(layout, "text", event_log=isolated_log, now=T0)

    ok, _ = isolated_log.verify()
    assert ok is True
    default_log_path = layout.var / "log" / "events.jsonl"
    assert not default_log_path.exists()


# ==========================================================================
# intake_route
# ==========================================================================


def test_intake_route_to_core_rewrites_layer_and_moves_file(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    added = intake_add(layout, "Body text.", title="Core Bound", now=T0)

    result = intake_route(layout, "notes/intake/" + added.name, "core", now=T0)

    assert not added.exists()
    assert result == layout.core / added.name
    text = result.read_text(encoding="utf-8")
    assert f"{LAYER_KEY}: {LAYER_NECESSARY}" in text
    assert "Body text." in text
    assert "title: Core Bound" in text  # unrelated front-matter key preserved


def test_intake_route_to_notes_subpath_rewrites_layer_and_moves_file(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    added = intake_add(layout, "Gardening notes.", now=T0)

    result = intake_route(layout, added, "notes/gardening", now=T0)

    assert not added.exists()
    assert result == layout.notes / "gardening" / added.name
    text = result.read_text(encoding="utf-8")
    assert f"{LAYER_KEY}: {LAYER_RELEVANT}" in text
    assert "Gardening notes." in text


def test_intake_route_to_bare_notes_rewrites_layer_and_moves_file(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    added = intake_add(layout, "Top-level note.", now=T0)

    result = intake_route(layout, added, "notes", now=T0)

    assert result == layout.notes / added.name
    assert f"{LAYER_KEY}: {LAYER_RELEVANT}" in result.read_text(encoding="utf-8")


def test_intake_route_to_archive_content_survives_content_addressed(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    body_text = "This is the exact body that must be recoverable after archiving."
    added = intake_add(layout, body_text, now=T0)

    result = intake_route(layout, added, "archive", now=T0)

    # the item's own path still exists, now as a stub (never deleted)
    assert result == added
    assert added.is_file()
    stub_text = added.read_text(encoding="utf-8")
    assert "sources:" in stub_text

    # the ORIGINAL full note body (front matter included) survives,
    # recoverable via the archive's content-addressed handle
    handle_line = next(
        line for line in stub_text.splitlines() if line.startswith("sources:")
    )
    handle = handle_line.split(":", 1)[1].strip()
    archived_text = read_archived(layout, handle)
    assert body_text in archived_text
    assert f"{LAYER_KEY}: {LAYER_NEW}" in archived_text  # pre-archive content, verbatim


def test_intake_route_archive_never_deletes(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    added = intake_add(layout, "Do not lose me.", now=T0)

    intake_route(layout, added, "archive", now=T0)

    assert added.is_file()  # stub, not gone


def test_intake_route_bad_destination_raises(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    added = intake_add(layout, "text", now=T0)

    with pytest.raises(ValueError):
        intake_route(layout, added, "moon-base", now=T0)

    # nothing moved on rejection
    assert added.is_file()


def test_intake_route_emits_event_with_source_age_and_destination(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    added = intake_add(layout, "text", source=SOURCE_RESEARCH, now=T0)

    routed_at = T0 + timedelta(days=3, hours=6)
    intake_route(layout, added, "core", now=routed_at)

    events = _events(layout)
    route_events = [e for e in events if e["kind"] == INTAKE_ROUTE]
    assert len(route_events) == 1
    payload = route_events[0]["payload"]
    assert payload["source"] == SOURCE_RESEARCH
    assert payload["destination"] == "core"
    assert payload["layer"] == LAYER_NECESSARY
    assert payload["age_days"] == pytest.approx(3.25, abs=1e-6)


def test_intake_route_to_archive_also_emits_archive_stub_event(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    added = intake_add(layout, "text", now=T0)

    intake_route(layout, added, "archive", now=T0)

    kinds = [e["kind"] for e in _events(layout)]
    assert "archive_stub" in kinds
    assert INTAKE_ROUTE in kinds


def test_intake_route_accepts_injected_event_log(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    added = intake_add(layout, "text", now=T0, event_log=EventLog(tmp_path / "isolated-add.jsonl"))
    isolated_log = EventLog(tmp_path / "isolated-route.jsonl")

    intake_route(layout, added, "core", event_log=isolated_log, now=T0)

    ok, _ = isolated_log.verify()
    assert ok is True
    default_log_path = layout.var / "log" / "events.jsonl"
    assert not default_log_path.exists()


# ==========================================================================
# intake_list
# ==========================================================================


def test_intake_list_empty_when_no_intake_dir(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    assert intake_list(layout) == []


def test_intake_list_returns_item_fields(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    intake_add(layout, "text", source=SOURCE_PROJECT, title="A Title", now=T0)

    items = intake_list(layout, now=T0 + timedelta(days=2))

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, IntakeItem)
    assert item.path.startswith(f"notes/{INTAKE_DIRNAME}/")
    assert item.source == SOURCE_PROJECT
    assert item.received == T0
    assert item.age_days == pytest.approx(2.0, abs=1e-6)
    assert item.title == "A Title"


def test_intake_list_title_falls_back_to_text_head(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    intake_add(layout, "First line of the body becomes the title.", now=T0)

    items = intake_list(layout, now=T0)

    assert items[0].title == "First line of the body becomes the title."


def test_intake_list_sorted_oldest_first(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    intake_add(layout, "newest", title="Newest", now=T0 + timedelta(days=5))
    intake_add(layout, "oldest", title="Oldest", now=T0)
    intake_add(layout, "middle", title="Middle", now=T0 + timedelta(days=2))

    items = intake_list(layout, now=T0 + timedelta(days=10))

    assert [it.title for it in items] == ["Oldest", "Middle", "Newest"]


def test_intake_list_excludes_routed_items(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    intake_add(layout, "stays", title="Stays", now=T0)
    routed = intake_add(layout, "goes", title="Goes", now=T0)

    intake_route(layout, routed, "core", now=T0)

    items = intake_list(layout, now=T0)
    assert [it.title for it in items] == ["Stays"]
    assert all(it.path != f"notes/{INTAKE_DIRNAME}/{routed.name}" for it in items)


def test_intake_list_excludes_archived_items(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    intake_add(layout, "stays", title="Stays", now=T0)
    archived = intake_add(layout, "goes to archive", title="Archived", now=T0)

    intake_route(layout, archived, "archive", now=T0)

    items = intake_list(layout, now=T0)
    assert [it.title for it in items] == ["Stays"]
