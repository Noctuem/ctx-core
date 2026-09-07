"""intake: New-layer front door + routing.

`Layout` (m1) already realizes three lifecycle layers structurally --
`core/` is Necessary, `notes/` is Relevant, `var/archive/` is Archive
(content-addressed, via m4's `archive_note`). This module adds the missing
fourth stage: **New** -- context that just arrived, filed under
`notes/intake/` with provenance and a received timestamp, until a session or
a human routes it into one of the other three.

Three operations, kept structurally separate on purpose:

- `intake_add` -- writes a new-layer note. Pure filesystem write; never
  routes, never guesses a destination.
- `intake_route` -- moves a note (plain filesystem rename, never `git mv`)
  into `core/`, a `notes/<subpath>`, or the content-addressed archive, and
  rewrites its layer to match. Routing to `archive` goes through the
  shipped `archive_note` (m4) -- additive-only, never deletes.
- `intake_list` -- the unrouted queue, oldest first. No auto-routing
  anywhere: filing is machinery, deciding where something belongs is a
  session or a human call (the v0.2 spec's semi-automatic stance).

Layer and source vocabulary are exported as module-level constants so the
packer and stats (m14, m13) read the same names -- one seam, not a string
re-typed in three places.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .archive import archive_note
from .config import Knobs
from .events import EventLog, eventlog_for
from .indexing import FRONT_MATTER, SECONDS_PER_DAY
from .layout import Layout

# --- lifecycle-layer vocabulary (the one seam packer/stats/doctor read) ---

LAYER_NEW = "new"
LAYER_NECESSARY = "necessary"
LAYER_RELEVANT = "relevant"
LAYER_ARCHIVE = "archive"

# --- source vocabulary ------------------------------------------------------

SOURCE_USER = "user"
SOURCE_PROJECT = "project"
SOURCE_RESEARCH = "research"
VALID_SOURCES = frozenset({SOURCE_USER, SOURCE_PROJECT, SOURCE_RESEARCH})
DEFAULT_SOURCE = SOURCE_USER

# --- EventKind additions ----------------------------------------------------
#
# `events.EventKind` (m5) is an open vocabulary -- any non-empty string is a
# valid `kind`. These two are THIS module's addition, defined here (not in
# `events.py`, which intake does not own) per the build instructions; m14
# (wire-02) should treat these as the canonical `INTAKE_ADD`/`INTAKE_ROUTE`
# kind strings rather than re-typing the literals.
INTAKE_ADD = "intake_add"
INTAKE_ROUTE = "intake_route"

# --- front-matter keys this module owns -------------------------------------

LAYER_KEY = "ctx:layer"
SOURCE_KEY = "ctx:source"
RECEIVED_KEY = "ctx:received"
TITLE_KEY = "title"

# --- filesystem / dest conventions ------------------------------------------

#: `notes/intake/` -- where every New-layer item is filed, relative to
#: `layout.notes`.
INTAKE_DIRNAME = "intake"

#: Literal `--to` destination values understood by `intake_route`, beyond
#: the `"notes"` / `"notes/<subpath>"` family.
DEST_CORE = "core"
DEST_ARCHIVE = "archive"
NOTES_DEST_PREFIX = "notes"

SLUG_MAX_LEN = 60
DEFAULT_SLUG = "note"

# Same namespaced-key convention `ctx_core.indexing._FRONT_MATTER_KV` uses
# (`ctx:layer` must parse as ONE key, not split on its embedded colon) --
# duplicated here rather than imported, since it is a private helper with no
# interface-contract stability guarantee; verified against `indexing.py`
# byte-for-byte at build time (see done.md).
_FRONT_MATTER_KV = re.compile(r"^\s*([\w:.-]+)\s*:\s*(.*)$")
_SLUG_DISALLOWED = re.compile(r"[^a-z0-9]+")


@dataclass
class IntakeItem:
    """One unrouted New-layer note, as `intake_list` reports it."""

    path: str            # relative to layout.root, forward-slash form
    source: str
    received: datetime   # timezone-aware UTC
    age_days: float
    title: str


# --- path / front-matter plumbing -------------------------------------------


def _resolve(path: str | Path, layout: Layout) -> Path:
    """`path` may be given relative to `layout.root` or already absolute --
    same convention `archive._resolve` uses.
    """
    p = Path(path)
    return p if p.is_absolute() else layout.root / p


def _display_path(p: Path, layout: Layout) -> str:
    try:
        return p.relative_to(layout.root).as_posix()
    except ValueError:
        return str(p)


def _parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    """Front matter as an order-preserving `{key: raw_value}` dict, plus
    everything after the closing `---`. Unlike `indexing._parse_front_matter`
    this keeps every value as a plain string (list/bool coercion isn't
    needed for the three keys this module manages) and preserves key order
    so `intake_route` can rewrite `ctx:layer` in place without disturbing an
    unrelated `title:` key a caller may have set.
    """
    m = FRONT_MATTER.match(raw)
    if not m:
        return {}, raw
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        mm = _FRONT_MATTER_KV.match(line)
        if not mm:
            continue
        meta[mm.group(1).strip()] = mm.group(2).strip()
    return meta, raw[m.end():]


def _render_front_matter(meta: dict[str, str], body: str) -> str:
    lines = ["---"] + [f"{k}: {v}" for k, v in meta.items()] + ["---"]
    return "\n".join(lines) + "\n" + body


def _title_from_body(body: str, fallback: str) -> str:
    """Same fallback convention as `indexing._title_from_body`, duplicated
    (private helper, no import) -- first heading, else first non-blank line,
    else the filename stem.
    """
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
        if line.strip():
            return line.strip()[:100]
    return fallback


def _default_event_log(layout: Layout) -> EventLog:
    # Same pattern `archive_note` uses: derive the default log path from a
    # fresh `Knobs.load(layout.root)` rather than assuming the hardcoded
    # default, so a domain that customized `eventlog_path` still gets intake
    # events routed correctly.
    return eventlog_for(layout.root, Knobs.load(layout.root))


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _slugify(text: str) -> str:
    slug = _SLUG_DISALLOWED.sub("-", text.strip().lower()).strip("-")
    slug = slug[:SLUG_MAX_LEN].strip("-")
    return slug or DEFAULT_SLUG


def _slug_source(text: str, title: str | None) -> str:
    if title:
        return title
    stripped = text.strip()
    head = stripped.splitlines()[0] if stripped else ""
    return head[:SLUG_MAX_LEN]


def _unique_filename(dir_path: Path, stem: str, suffix: str = ".md") -> str:
    """Filesystem-safe collision handling: `<stem>.md`, then `<stem>-2.md`,
    `<stem>-3.md`, ... -- never overwrites an existing intake item.
    """
    candidate = f"{stem}{suffix}"
    if not (dir_path / candidate).exists():
        return candidate
    n = 2
    while True:
        candidate = f"{stem}-{n}{suffix}"
        if not (dir_path / candidate).exists():
            return candidate
        n += 1


def _resolve_dest(layout: Layout, dest: str) -> tuple[Path, str]:
    """`dest` -> `(target_dir, realized_layer)` for the `core` / `notes[/…]`
    families. `archive` is handled separately by `intake_route` (it goes
    through `archive_note`, which has no "target directory" of its own).
    """
    if dest == DEST_CORE:
        return layout.core, LAYER_NECESSARY
    if dest == NOTES_DEST_PREFIX or dest.startswith(NOTES_DEST_PREFIX + "/"):
        subpath = dest[len(NOTES_DEST_PREFIX):].lstrip("/")
        target = (layout.notes / subpath) if subpath else layout.notes
        return target, LAYER_RELEVANT
    raise ValueError(
        f"bad destination {dest!r} -- must be 'core', 'archive', 'notes', "
        "or 'notes/<subpath>'"
    )


# --- intake_add --------------------------------------------------------------


def intake_add(
    layout: Layout,
    text: str,
    *,
    source: str = DEFAULT_SOURCE,
    title: str | None = None,
    event_log: EventLog | None = None,
    now: datetime | None = None,
) -> Path:
    """Write a New-layer front door note to
    `notes/intake/<date>-<slug>.md`.

    `now`, if given, fixes the received timestamp (and the date in the
    filename) -- an addition beyond the interface contract's
    `(layout, text, *, source, title)` shape, kept optional so byte-exact
    tests don't need to monkeypatch `datetime.now`. `event_log`, if given,
    is used in place of the default derived from `Knobs.load(layout.root)`
    -- same injection pattern `archive_note`/`pack` use.
    """
    if source not in VALID_SOURCES:
        raise ValueError(
            f"bad source {source!r} -- must be one of {sorted(VALID_SOURCES)}"
        )

    received = now if now is not None else datetime.now(timezone.utc)
    date_str = received.date().isoformat()
    slug = _slugify(_slug_source(text, title))

    intake_dir = layout.notes / INTAKE_DIRNAME
    intake_dir.mkdir(parents=True, exist_ok=True)
    filename = _unique_filename(intake_dir, f"{date_str}-{slug}")
    dest_path = intake_dir / filename

    meta = {
        LAYER_KEY: LAYER_NEW,
        SOURCE_KEY: source,
        RECEIVED_KEY: received.isoformat(),
    }
    if title:
        meta[TITLE_KEY] = title
    body = "\n\n" + text.rstrip("\n") + "\n"
    content = _render_front_matter(meta, body)
    dest_path.write_text(content, encoding="utf-8", newline="\n")

    log = event_log if event_log is not None else _default_event_log(layout)
    log.append(
        INTAKE_ADD,
        {
            "path": _display_path(dest_path, layout),
            "source": source,
            "title": title,
            "received": received.isoformat(),
        },
    )
    return dest_path


# --- intake_route --------------------------------------------------------------


def intake_route(
    layout: Layout,
    item: str | Path,
    dest: str,
    *,
    event_log: EventLog | None = None,
    now: datetime | None = None,
) -> Path:
    """Route an intake item to `core`, `notes[/<subpath>]`, or `archive`.

    `core`/`notes[/<subpath>]`: a plain filesystem move (no `git mv`, no
    shelling out) -- the file's basename is preserved, its `ctx:layer` is
    rewritten to the destination's realized layer (`necessary`/`relevant`),
    every other front-matter key (e.g. a caller-set `title:`) survives
    untouched.

    `archive`: goes through the shipped `archive_note` (m4) -- the item's
    live path is overwritten with a content-addressed stub, never deleted.
    There is no separate `ctx:layer: archive` line to write on the stub
    (its fixed two-line `sources:` front matter has no room for one, and a
    stub's very existence already IS the archive-layer representation --
    its full body lives at the content-addressed handle `archive_note`
    returns). See done.md for this documented as a deliberate reading, not
    a silent one.

    `now`, if given, fixes the "routed at" instant used to compute
    age-at-routing (addition beyond the interface contract, same rationale
    as `intake_add`'s `now`).
    """
    if dest == DEST_ARCHIVE:
        target_dir, realized_layer = None, LAYER_ARCHIVE
    else:
        target_dir, realized_layer = _resolve_dest(layout, dest)

    source_path = _resolve(item, layout)
    raw = source_path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_front_matter(raw)

    source = meta.get(SOURCE_KEY, DEFAULT_SOURCE)
    received_str = meta.get(RECEIVED_KEY)
    received = _parse_ts(received_str) if received_str else None
    routed_at = now if now is not None else datetime.now(timezone.utc)
    age_days = (
        (routed_at - received).total_seconds() / SECONDS_PER_DAY
        if received is not None
        else 0.0
    )

    log = event_log if event_log is not None else _default_event_log(layout)

    if dest == DEST_ARCHIVE:
        archive_note(_display_path(source_path, layout), layout, event_log=log)
        result_path = source_path
    else:
        assert target_dir is not None
        target_dir.mkdir(parents=True, exist_ok=True)
        meta[LAYER_KEY] = realized_layer
        new_raw = _render_front_matter(meta, body)
        target_path = target_dir / source_path.name
        target_path.write_text(new_raw, encoding="utf-8", newline="\n")
        source_path.unlink()
        result_path = target_path

    log.append(
        INTAKE_ROUTE,
        {
            "path": _display_path(result_path, layout),
            "source": source,
            "age_days": round(age_days, 6),
            "destination": dest,
            "layer": realized_layer,
        },
    )
    return result_path


# --- intake_list --------------------------------------------------------------


def intake_list(layout: Layout, *, now: datetime | None = None) -> list[IntakeItem]:
    """The unrouted New-layer queue, oldest (earliest `ctx:received`)
    first. Only files whose `ctx:layer` is still `new` are counted -- once
    `intake_route` rewrites that key (or turns the file into an archive
    stub with no `ctx:layer` at all), it drops out of the queue on its own,
    with no separate "already routed" bookkeeping needed.
    """
    intake_dir = layout.notes / INTAKE_DIRNAME
    if not intake_dir.is_dir():
        return []

    ref_now = now if now is not None else datetime.now(timezone.utc)
    items: list[IntakeItem] = []
    for p in sorted(intake_dir.glob("*.md")):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = _parse_front_matter(raw)
        if meta.get(LAYER_KEY) != LAYER_NEW:
            continue

        source = meta.get(SOURCE_KEY, DEFAULT_SOURCE)
        received_str = meta.get(RECEIVED_KEY)
        received = (
            _parse_ts(received_str)
            if received_str
            else datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        )
        age_days = (ref_now - received).total_seconds() / SECONDS_PER_DAY
        title = meta.get(TITLE_KEY) or _title_from_body(body, p.stem)

        items.append(
            IntakeItem(
                path=_display_path(p, layout),
                source=source,
                received=received,
                age_days=age_days,
                title=title,
            )
        )

    items.sort(key=lambda it: it.received)
    return items
