"""indexing: candidate discovery for the packer.

This module decides what is DISCOVERABLE: it walks a corpus's `core/` and
`notes/` trees into `Entry` rows and owns every index-time classification —
prose vs. data, always-include root files, general path exclusions. Ranking,
admission, budgeting and manifest rendering — the CHOOSING — live in
`packer.py`, which imports from here and never the other way around.

Cheap by design: `build_index` re-walks the filesystem on every call rather
than maintaining a persisted, hand-updated index. A hand-maintained index
drifts from the corpus silently; a rebuilt one cannot.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .layout import Layout

SECONDS_PER_DAY = 86_400

# Front matter is a leading `---\n ... \n---\n` block of `key: value` lines.
FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)

# Keys may contain colons or dots (namespaced front-matter keys such as
# `ctx:pin`), so a naive `split(":", 1)` would silently drop any of them.
_FRONT_MATTER_KV = re.compile(r"^\s*([\w:.-]+)\s*:\s*(.*)$")


def approx_tokens(text: str) -> int:
    """Cheap token estimate: ~4 characters per token, floor of 1.

    A rough estimator on purpose — exact tokenization needs a real tokenizer
    (the `ctx-yield --exact` path, composed separately, m6) and would be far
    too slow to run on every file on every pack.
    """
    return max(1, len(text) // 4)


@dataclass
class Entry:
    """One indexed file.

    The published fields are `path, tier, handle, title, tokens, mtime,
    pinned, tags, terms` (the interface contract every dependent should
    treat as stable). The remaining fields are pack-time working state,
    filled in by `packer.pack` and not meaningful before a pack runs.
    """

    path: str                 # relative to layout.root, forward-slash form
    tier: str                 # "core" | "notes"
    handle: str                # sha256 content hash
    title: str
    tokens: int
    mtime: float
    pinned: bool = False
    tags: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)   # cheap retrieval keys

    # --- pack-time working state (see packer.py) ---
    age_days: float = 0.0
    relevance: float = 0.0
    is_data: bool = False       # structured data blob vs. prose; see classify_is_data
    always_include: bool = False

    @property
    def item_id(self) -> str:
        return hashlib.sha256(self.path.encode()).hexdigest()[:16]


def _parse_front_matter(text: str) -> tuple[dict, str]:
    m = FRONT_MATTER.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        mm = _FRONT_MATTER_KV.match(line)
        if not mm:
            continue
        k, v = mm.group(1).strip(), mm.group(2).strip()
        if v.startswith("[") and v.endswith("]"):
            meta[k] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        elif v.lower() in ("true", "false"):
            meta[k] = v.lower() == "true"
        else:
            meta[k] = v
    return meta, text[m.end():]


_STOP = set(
    "the a an and or but if then of to in on for with as is are was were "
    "be been this that these those it its we you i our your they them".split()
)

# How many of a document's most frequent non-stopword terms to keep as its
# cheap retrieval key. A small, fixed cap: the packer only ever needs enough
# terms to estimate task/document overlap, not a full-text index.
TERMS_PER_ENTRY = 60


def _terms(text: str, k: int = TERMS_PER_ENTRY) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9_-]{3,}", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in _STOP:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:k]]


# Repo-root orientation files a task benefits from regardless of subject
# matter: the backlog (what is already meant to happen) and the append-only
# session record (what already happened). Neither is task-specific, so
# relevance scoring has nothing true to say about them — they are admitted
# off the top, the same way a pinned note is, bounded by
# `packer.ALWAYS_INCLUDE_MAX_TOKENS`.
#
# ABSENT IS NORMAL, NEVER A WARNING. A corpus that keeps its history purely
# in git (or an event log) legitimately has neither file. A rule that
# complained here would fire on every such pack.
#
# They live at the repo ROOT, outside `core/`/`notes/`, which `build_index`'s
# tree walk does not otherwise reach.
ALWAYS_INCLUDE_NAMES = ("TODO.md", "Session_Log.md")


def _root_orientation_entries(layout: Layout, now: float, seen: set[str]) -> list[Entry]:
    """Index the always-include root files. A bounded list, never a tree
    walk: the list is two literal names, so nothing else at the root becomes
    a pack candidate as a side effect.
    """
    out: list[Entry] = []
    for name in ALWAYS_INCLUDE_NAMES:
        if name in seen:
            continue
        p = layout.root / name
        try:
            if not p.is_file():
                continue                      # absent is normal — see above
            raw = p.read_text(encoding="utf-8", errors="replace")
            st = p.stat()
        except OSError:
            continue
        meta, body = _parse_front_matter(raw)
        title = str(meta.get("title") or "")
        if not title:
            for line in body.splitlines():
                if line.strip():
                    title = line.lstrip("#").strip()[:100]
                    break
        out.append(
            Entry(
                path=name,
                tier="notes",
                handle=hashlib.sha256(raw.encode()).hexdigest(),
                title=title or Path(name).stem,
                tokens=approx_tokens(raw),
                mtime=st.st_mtime,
                age_days=(now - st.st_mtime) / SECONDS_PER_DAY,
                terms=_terms(body),
                always_include=True,
            )
        )
    return out


# Prose suffixes: documentation formats a person reads and writes by hand.
# A short, stable list — used as the POSITIVE side of the prose/data split so
# a new, unlisted extension defaults to being treated as ordinary content
# rather than silently misclassified as a data store.
PROSE_SUFFIXES = {
    ".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc",
    ".asciidoc", ".org", ".textile", ".rdoc", ".pod",
}

# Structured DATA — a machine-written store, not prose a person reads whole.
# Narrow on purpose: `.yaml`/`.yml`/`.toml`/`.ini` are NOT here, because those
# are configuration a person writes and edits by hand, which is closer to
# prose behaviour than store behaviour for the purpose of this split.
DATA_SUFFIXES = {".json", ".jsonl", ".ndjson", ".csv", ".tsv"}

# Size, in approx tokens, above which a DATA_SUFFIXES file is classified a
# data STORE rather than ordinary small config content. Below this a
# `notes/foo.json` is source-like config that costs a pack nothing; above it,
# a large machine-generated blob (an exported log, a big fixture dump) is
# neither prose nor something a working set can hold, and `packer.py`'s
# fresh-bulk discount and age gate should not exempt it the way it exempts
# small, load-bearing config.
DATA_BLOB_MIN_TOKENS = 2_000


def classify_is_data(rel: str, tokens: int, data_blob_min_tokens: int = DATA_BLOB_MIN_TOKENS) -> bool:
    """Is this file a structured DATA store, for the purpose of the
    fresh-bulk discount and age-gate exemptions in `packer.py`?

    Module level and taking plain values (never a `Knobs`), so a test can
    drive the real decision instead of reimplementing its arithmetic and
    then agreeing with itself.
    """
    suffix = Path(rel).suffix.lower()
    if suffix in PROSE_SUFFIXES:
        return False
    return bool(data_blob_min_tokens and suffix in DATA_SUFFIXES and tokens >= data_blob_min_tokens)


def _title_from_body(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
        if line.strip():
            return line.strip()[:100]
    return fallback


def build_index(
    layout: Layout,
    *,
    exclude_prefixes: Sequence[str] = (),
    data_blob_min_tokens: int = DATA_BLOB_MIN_TOKENS,
) -> tuple[list[Entry], dict[str, int]]:
    """Rebuild the index from the filesystem: `core/` and `notes/` walked as
    markdown-family corpus, plus the always-include root files.

    `exclude_prefixes` is a GENERAL exclusion knob — path prefixes (relative
    to `layout.root`, forward-slash form) that should never become pack
    candidates, e.g. a project's own history/archive tree that would
    otherwise crowd every pack with material about itself rather than about
    the task. It is a caller-supplied list, never a name hardcoded to any
    specific project's directory convention.

    Returns the entries plus a census of what was excluded and why — a
    manifest that quietly dropped files should read differently from a
    corpus that genuinely has few of them.
    """
    now = time.time()
    entries: list[Entry] = []
    census: dict[str, int] = {}

    scopes = [("core", layout.core), ("notes", layout.notes)]
    for tier, base in scopes:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.md")):
            rel = p.relative_to(layout.root).as_posix()
            if any(rel.startswith(prefix) for prefix in exclude_prefixes):
                census["excluded by caller-supplied prefix"] = (
                    census.get("excluded by caller-supplied prefix", 0) + 1
                )
                continue
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
                st = p.stat()
            except OSError:
                census["unreadable"] = census.get("unreadable", 0) + 1
                continue
            meta, body = _parse_front_matter(raw)
            if meta.get("ctx:manifest"):
                continue  # a manifest is output, not corpus; indexing it is a loop
            title = str(meta.get("title") or "") or _title_from_body(body, p.stem)
            tags = list(meta.get("tags") or [])
            tokens = approx_tokens(raw)
            entries.append(
                Entry(
                    path=rel,
                    tier=tier,
                    handle=hashlib.sha256(raw.encode()).hexdigest(),
                    title=title,
                    tokens=tokens,
                    mtime=st.st_mtime,
                    age_days=(now - st.st_mtime) / SECONDS_PER_DAY,
                    pinned=bool(meta.get("ctx:pin") or meta.get("pin")),
                    tags=tags,
                    terms=_terms(body),
                    is_data=classify_is_data(rel, tokens, data_blob_min_tokens),
                )
            )

    entries.extend(_root_orientation_entries(layout, now, {e.path for e in entries}))
    return entries, census
