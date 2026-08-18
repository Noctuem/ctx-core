"""archive: content-addressed archive + age-based lifecycle sweep.

Two operations, kept structurally separate on purpose:

- `archive_note` (size trigger) -- an oversized note's full body is moved,
  byte-for-byte, to a content-addressed file under `var/archive/`, and the
  live note is replaced with a small stub carrying a `sources:` handle that
  always resolves back to it. Additive-only: nothing is ever deleted, and a
  stub is reversible by construction -- the handle IS the address.
- `sweep` (age trigger) -- never touches a note. It only PROPOSES: notes
  past `knobs.archive_age_days` are written to a `var/archive-proposals.md`
  queue for a human (or a ratified verifier) to act on. Machinery proposes,
  it never applies (the generalized Card 6 spirit this spec carries).

`stub_oversized_notes` closes the loop on the size trigger (build
instruction #1's full policy: scan the corpus, archive everything over
threshold) while `archive_note` itself stays the single-path primitive the
interface contract asks for -- a caller can invoke either the policy or the
primitive directly.

Per-domain `archive_age_days` override (spec Open Question 5): resolved
pragmatically, per the build instructions -- each domain repo's own
`.ctxrc.toml` already gives `Knobs.load` a per-domain value for this field.
No second override mechanism is added here.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Knobs
from .events import EventKind, EventLog, default_eventlog_path
from .indexing import FRONT_MATTER, approx_tokens, build_index
from .layout import Layout

# --- content-addressed archive shape -------------------------------------

#: Directory (under `layout.var`) the content-addressed archive lives in.
ARCHIVE_DIRNAME = "archive"

#: Number of leading hex characters of a hash used as its shard directory --
#: keeps any one directory from holding an unbounded flat file count as a
#: corpus grows.
SHARD_LEN = 2

#: Front-matter key a stub carries; its value is the handle that resolves
#: back to the archived full text. A plain, outsider-readable key -- no
#: private namespace (spec Open Question 3 recommends the generic form for
#: exactly this reason).
SOURCES_KEY = "sources"

#: Handle format: `sha256:<hexdigest>`. The algorithm name is embedded so a
#: handle stays self-describing even if a second algorithm is ever added.
HANDLE_PREFIX = "sha256:"

#: Length, in characters, a naive (author-not-supplied) summary is
#: truncated to. Front matter is stripped first (see `_naive_summary`) so
#: the truncation reads as prose, not YAML-ish key: value lines.
NAIVE_SUMMARY_MAX_CHARS = 240

#: Front-matter key marking a rendered file as generated OUTPUT, not corpus
#: -- mirrors `packer.Manifest`'s own `ctx:manifest: true` convention so
#: `build_index` excludes the proposals queue from being indexed as an
#: ordinary note (indexing a manifest of notes would loop).
PROPOSALS_MANIFEST_KEY = "ctx:manifest"

#: Filename of the lifecycle-sweep proposal queue, under `layout.var`.
PROPOSALS_BASENAME = "archive-proposals.md"


@dataclass
class ArchiveProposal:
    """One note past the age threshold -- a PROPOSAL, never an applied
    change. `sweep` never calls `archive_note` on these; a human (or a
    ratified verifier) decides what happens next.
    """

    path: str            # relative to layout.root, forward-slash form
    tokens: int
    age_days: float
    mtime: float
    threshold_days: int  # the `archive_age_days` value this sweep ran under


# --- path / handle plumbing ------------------------------------------------


def _resolve(path: str | Path, layout: Layout) -> Path:
    """`path` may be given relative to `layout.root` (the normal case,
    matching `Entry.path`'s own convention) or already absolute; either
    works.
    """
    p = Path(path)
    return p if p.is_absolute() else layout.root / p


def _shard_dir(hex_digest: str) -> str:
    return hex_digest[:SHARD_LEN]


def archived_path_for_handle(layout: Layout, handle: str) -> Path:
    """Resolve a `sources:` handle back to the archived file's path. The
    reversibility guarantee lives here: any handle a stub ever carried
    resolves to this same path, deterministically, with no lookup table --
    the hash IS the address.
    """
    hex_digest = handle[len(HANDLE_PREFIX):] if handle.startswith(HANDLE_PREFIX) else handle
    return layout.var / ARCHIVE_DIRNAME / _shard_dir(hex_digest) / f"{hex_digest}.md"


def read_archived(layout: Layout, handle: str) -> str:
    """Read the full text a handle addresses. Exists so a caller (m3's
    doctor stub-resolution check, or a test proving reversibility) never
    has to know the shard-dir scheme itself.
    """
    return archived_path_for_handle(layout, handle).read_text(encoding="utf-8")


def _display_path(p: Path, layout: Layout) -> str:
    try:
        return p.relative_to(layout.root).as_posix()
    except ValueError:
        return str(p)


def _strip_front_matter(text: str) -> str:
    m = FRONT_MATTER.match(text)
    return text[m.end():] if m else text


def _naive_summary(raw: str, limit: int = NAIVE_SUMMARY_MAX_CHARS) -> str:
    """A one-line, front-matter-stripped, whitespace-collapsed truncation --
    used only when the caller doesn't supply its own summary.
    """
    body = _strip_front_matter(raw).strip()
    collapsed = " ".join(body.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "…"


def _has_sources_handle(raw: str) -> bool:
    """Is this file already a stub? (its front matter carries a `sources:`
    key). Used to keep both `sweep` and `stub_oversized_notes` from
    re-proposing/re-archiving something already archived.
    """
    m = FRONT_MATTER.match(raw)
    if not m:
        return False
    for line in m.group(1).splitlines():
        key = line.split(":", 1)[0].strip()
        if key == SOURCES_KEY:
            return True
    return False


def _default_event_log(layout: Layout) -> EventLog:
    # `archive_note`'s contracted signature takes no `knobs` -- so when a
    # caller doesn't inject one, the eventlog path is derived from a fresh
    # `Knobs.load(layout.root)` (a single cheap .ctxrc.toml read) rather
    # than assuming the hardcoded default, so a domain that customized
    # `eventlog_path` still gets its stub events in the right place.
    knobs = Knobs.load(layout.root)
    return EventLog(default_eventlog_path(layout.root, knobs.eventlog_path))


# --- archive_note: the size-triggered primitive -----------------------------


def archive_note(
    path: str | Path,
    layout: Layout,
    *,
    summary: str | None = None,
    event_log: EventLog | None = None,
) -> str:
    """Content-address `path`'s current full text and replace it with a
    stub. Additive-only: the original bytes always survive at the
    content-addressed location this returns a handle to, even though the
    live note at `path` is overwritten with the stub.

    Idempotent on content: archiving the same bytes twice writes the same
    archive file once (content-addressed by construction -- same input,
    same hash, same path) and simply re-derives the same handle rather than
    erroring or duplicating storage.

    `event_log`, if given, is used in place of the default derived from
    `Knobs.load(layout.root)` -- same pattern `packer.pack` uses, so a test
    can point it at an isolated fixture log.
    """
    note_path = _resolve(path, layout)
    raw = note_path.read_text(encoding="utf-8", errors="replace")

    hex_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    handle = f"{HANDLE_PREFIX}{hex_digest}"
    archived_path = archived_path_for_handle(layout, handle)

    archived_path.parent.mkdir(parents=True, exist_ok=True)
    if not archived_path.exists():
        # Content-addressed: if it's already there its bytes are already
        # this exact content (same hash -> same input) -- nothing to redo,
        # and never overwritten once written (additive-only).
        archived_path.write_text(raw, encoding="utf-8", newline="\n")

    final_summary = summary if summary is not None else _naive_summary(raw)
    stub = (
        "---\n"
        f"{SOURCES_KEY}: {handle}\n"
        "---\n\n"
        f"{final_summary}\n"
    )
    note_path.write_text(stub, encoding="utf-8", newline="\n")

    log = event_log if event_log is not None else _default_event_log(layout)
    log.append(
        EventKind.ARCHIVE_STUB,
        {
            "path": _display_path(note_path, layout),
            "handle": handle,
            "tokens": approx_tokens(raw),
            "summary_source": "given" if summary is not None else "truncated",
        },
    )
    return handle


def stub_oversized_notes(layout: Layout, knobs: Knobs) -> list[str]:
    """Content-address every note in `layout.notes` currently over
    `knobs.archive_stub_threshold_tokens`, in one pass -- build instruction
    #1's full size-triggered policy. `archive_note` itself stays the
    single-path primitive the interface contract asks for; this is the
    corpus-wide sweep a caller (the CLI, m10) can run over it.

    Scope: `layout.notes` only, and never a root orientation file
    (`TODO.md`/`Session_Log.md`) -- `layout.core` is force-loaded L1 and
    deliberately kept small/intact rather than stubbed, and the orientation
    files are perpetually-relevant by role, not ordinary corpus bulk.

    Skips anything already a stub (idempotent against a repeat run).
    Returns the handles of everything actually archived, in path order.
    """
    entries, _census = build_index(layout)
    threshold = int(knobs.archive_stub_threshold_tokens)
    handles: list[str] = []
    for entry in sorted(entries, key=lambda e: e.path):
        if entry.tier != "notes" or entry.always_include:
            continue
        if entry.tokens <= threshold:
            continue
        note_path = layout.root / entry.path
        try:
            raw = note_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _has_sources_handle(raw):
            continue
        handles.append(archive_note(entry.path, layout))
    return handles


# --- sweep: the age-triggered proposal queue -------------------------------


def sweep(layout: Layout, knobs: Knobs) -> list[ArchiveProposal]:
    """Age-based lifecycle scan. PROPOSES only -- never calls `archive_note`,
    never edits a note. Rewrites `var/archive-proposals.md` fresh every call
    (mirroring `build_index`'s own "rebuild, never hand-maintain"
    convention: a proposal queue reflects live corpus state at scan time,
    not sweep history -- the event log is where history belongs).

    Scope: `layout.notes` only. `layout.core` (force-loaded L1) and the
    root orientation files are excluded -- perpetually-relevant-by-role
    files, not lifecycle candidates by age.
    """
    entries, _census = build_index(layout)
    threshold = int(knobs.archive_age_days)

    proposals: list[ArchiveProposal] = []
    for entry in entries:
        if entry.tier != "notes" or entry.always_include:
            continue
        if entry.age_days < threshold:
            continue
        note_path = layout.root / entry.path
        try:
            raw = note_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        if _has_sources_handle(raw):
            continue  # already archived -- nothing to propose
        proposals.append(
            ArchiveProposal(
                path=entry.path,
                tokens=entry.tokens,
                age_days=entry.age_days,
                mtime=entry.mtime,
                threshold_days=threshold,
            )
        )

    proposals.sort(key=lambda p: p.age_days, reverse=True)
    _write_proposals_queue(layout, proposals, threshold)
    return proposals


def _render_proposals(proposals: list[ArchiveProposal], threshold: int) -> str:
    lines = [
        "---",
        f"{PROPOSALS_MANIFEST_KEY}: true",
        f"ctx:generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time()))}",
        f"ctx:archive_age_days: {threshold}",
        "---",
        "",
        "# Archive proposals",
        "",
    ]
    if not proposals:
        lines.append(
            f"No notes are past the {threshold}-day age threshold. Nothing proposed."
        )
        return "\n".join(lines) + "\n"

    lines += [
        f"{len(proposals)} note(s) past the {threshold}-day age threshold. "
        "PROPOSALS ONLY -- nothing has been archived or edited. Run "
        "`archive_note(path, layout)` (or the CLI equivalent) by hand to act on one.",
        "",
        "| file | age | tokens |",
        "|---|---|---|",
    ]
    for p in proposals:
        lines.append(f"| `{p.path}` | {p.age_days:.0f}d | {p.tokens:,} |")
    return "\n".join(lines) + "\n"


def _write_proposals_queue(layout: Layout, proposals: list[ArchiveProposal], threshold: int) -> None:
    queue_path = layout.var / PROPOSALS_BASENAME
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(_render_proposals(proposals, threshold), encoding="utf-8", newline="\n")
