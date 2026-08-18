"""packer: scoring, admission, budgeting and manifest rendering.

`indexing.py` decides what is DISCOVERABLE; this module decides what gets
PACKED for one task — score every candidate against the task string, admit
pinned/named/flag-driven entries off the top, fit as much of the ranked rest
into the budget as will go, and render the result as an explicit, human-
readable manifest. `packer` imports from `indexing`, never the other way
around.

Every `pack()` call emits exactly one `CONTEXT_ASSEMBLED` event through the
`eventlog` interface (m5) — the append is the thing that makes a later
"is this context earning its tokens" measurement possible at all.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from .config import Knobs
from .events import EventKind, EventLog, default_eventlog_path
from .indexing import Entry, build_index, _terms
from .layout import Layout

# ==========================================================================
# Scoring / admission constants
# ==========================================================================
#
# Every weight/threshold that governs ranking or admission behavior here is
# a named constant rather than a bare literal. This module does not own
# `ctx_core/config.py`, so these are not `Knobs` fields — only
# `pack_budget_tokens`, `retrieval_summary_default`, `retrieval_last_default`
# and `retrieval_decisions_default` come from there; everything below is the
# packer's own internal tuning surface.

#: Weight on term-overlap relevance in the final rank score. Tuned default
#: (carried from the reference implementation's measured tuning, not a
#: guess) — the ratio between this and `W_RECENCY` controls admission order
#: on real inputs, so the two are changed together or not at all.
W_RELEVANCE = 1.0

#: Weight on the recency term in the final rank score. Tuned default — see
#: `W_RELEVANCE`.
W_RECENCY = 0.35

#: A pinned / always-include / named-in-task entry is admitted off the top,
#: but only up to this size — an unbounded pin list is a budget hole. Over
#: the cap, the entry falls back into the ordinary ranked pool instead of
#: being dropped outright, so a relevant one can still win a slot on merit.
#: Tuned default: the smallest round value that admits the two root/
#: named-file classes this rule exists for without also absorbing arbitrary
#: bulk.
ALWAYS_INCLUDE_MAX_TOKENS = 10_000

#: Notes older than this (in days) are dropped from the ranked pool unless
#: their relevance clears `AGE_GATE_RELEVANCE_EXEMPT` — a soft staleness
#: filter, not a hard exclusion. Tuned default.
MAX_ITEM_AGE_DAYS = 40

#: Relevance at or above which the age gate above does not apply — a note
#: that matches the task this well is not "stale bulk" no matter its age.
AGE_GATE_RELEVANCE_EXEMPT = 0.75

#: An entry scoring at or below this FRACTION of the pack's own top match is
#: "floor relevance" for the fresh-bulk discount below. Relative, never an
#: absolute cutoff, because relevance (`overlap / len(query_terms)`) has a
#: scale set by the task string itself: a task naming a file outright tops
#: out much higher than a vaguely worded one. Tuned default: the value that
#: protects a genuinely relevant large document scored against a vaguely
#: worded task (a low top-relevance pack) from being misread as bulk.
FRESH_BULK_MAX_RELEVANCE_FRAC = 0.5

#: Size, in tokens, above which a floor-relevance entry is eligible for the
#: fresh-bulk discount below.
FRESH_BULK_MIN_TOKENS = 4_000

#: How much the fresh-bulk discount shaves off an eligible entry's recency
#: term. Never a size penalty and never touches relevance — a large document
#: that is actually relevant scores above the cutoff and is untouched by
#: this at all.
FRESH_BULK_RECENCY_DISCOUNT = 0.6

#: Two entries whose term sets overlap (Jaccard) at or above this are
#: near-duplicates; the lower-ranked one is dropped rather than doubling up
#: on the same content. Tuned default.
REDUNDANCY_THRESHOLD = 0.86

#: Hard cap on how many ranked candidates are even considered for budgeting,
#: independent of the token budget itself. Tuned default — deliberately
#: small: it is the binding constraint that keeps a pack from ballooning to
#: fill a large budget once the budget itself stops being the limit.
RETRIEVAL_K = 12

#: `--summary` scales the working budget down to this fraction of
#: `knobs.pack_budget_tokens` — "the short version" rather than a separate
#: budget number to keep in sync.
SUMMARY_BUDGET_FRAC = 0.35

# A file named OUTRIGHT in the task string (full relative path, or an
# unambiguous bare filename WITH its extension) is admitted off the top,
# the same way a pinned note is. Matching is exact, never substring, and a
# bare stem (no extension) is deliberately excluded — a stem is
# indistinguishable from ordinary English ("config", "index", "report" are
# all plausible filenames and all plausible task words), so treating it as a
# reference would let incidental wording admit files nobody named.
_TASK_TOKEN = re.compile(r"[\w./\\-]+")


def _task_mentions(task: str) -> set[str]:
    """Filename-shaped tokens in the task string, separator-normalized so a
    task written with either slash style matches entry paths the same way.
    """
    out: set[str] = set()
    for tok in _TASK_TOKEN.findall(task.lower().replace("\\", "/")):
        tok = tok.strip("./-")
        if tok:
            out.add(tok)
    return out


#: Relevance added, in `score`'s upstream `e.relevance`, when the task
#: string names an entry outright — its full path, filename, or bare stem.
#: Tuned default, carried from the reference implementation's config. THE
#: BOUND: matching is exact, never substring (`code` in the task must not
#: boost `decode.md`), and a stem shorter than `PATH_LITERAL_MIN_STEM` is
#: ignored, because short stems are ordinary English rather than a
#: reference. This is the ranking-only counterpart to
#: `_named_for_admission` below — deliberately looser (it accepts a bare
#: stem, admission does not) because the worst case here is one rank
#: position, never a pinned slot.
PATH_LITERAL_WEIGHT = 0.35

#: Minimum bare-stem length `_named_literally` will treat as a reference
#: rather than prose ("code", "index", "report" are all real filenames and
#: all plausible task words below this length).
PATH_LITERAL_MIN_STEM = 4


def _named_literally(mentions: set[str], rel: str) -> bool:
    """Is this entry named OUTRIGHT in the task string — full path, bare
    filename (with extension), or bare stem? Runs on every tier, notes
    included, and complements the stricter `_named_for_admission` below:
    that rule refuses a bare stem because admission skips scoring outright,
    while a stem here only ever adds relevance, so the worst case is one
    rank position rather than an unearned pinned slot.
    """
    p = rel.lower()
    if p in mentions:                       # notes/topic/note.md
        return True
    name = p.rsplit("/", 1)[-1]
    if name in mentions:                    # note.md
        return True
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return len(stem) >= PATH_LITERAL_MIN_STEM and stem in mentions


def _named_for_admission(mentions: set[str], entries: Sequence[Entry]) -> set[str]:
    """Paths the task names as FILES, unambiguously. A basename matching two
    or more entries admits NEITHER — admitting the first would make the
    outcome depend on index order, and admitting all would let one word pin
    a whole class of files. Naming the full path always disambiguates.
    """
    if not mentions:
        return set()
    admitted: set[str] = set()
    basename_owners: dict[str, list[str]] = {}
    for e in entries:
        p = e.path.lower()
        if p in mentions:
            admitted.add(e.path)
        name = p.rsplit("/", 1)[-1]
        if "." in name and name in mentions:
            basename_owners.setdefault(name, []).append(e.path)
    for owners in basename_owners.values():
        if len(owners) == 1:
            admitted.add(owners[0])
    return admitted


def _is_decision_entry(e: Entry) -> bool:
    """Does this entry look like a decision record? Used by the
    `--decisions` retrieval flag. Generic on purpose: a `decisions` tag in
    front matter, or "decision"/"decisions" in the filename stem — no
    project-specific naming convention is assumed.
    """
    if any(t.lower() == "decisions" for t in e.tags):
        return True
    stem = Path(e.path).stem.lower()
    return "decision" in stem


def is_fresh_bulk(e: Entry, rel_cutoff: float) -> bool:
    """Is this entry large, at the relevance floor, and therefore riding on
    recency alone? `rel_cutoff` is an absolute relevance, derived by the
    caller as a fraction of the pack's own top match — see `pack`.
    """
    return e.tokens >= FRESH_BULK_MIN_TOKENS and e.relevance < rel_cutoff


def score(e: Entry, rel_cutoff: float) -> float:
    """Rank key: relevance, plus a recency credit that fresh bulk does not
    get in full. Only the recency TERM is discounted for fresh bulk — never
    relevance itself, so a large document that is actually relevant is
    never held back by this.
    """
    recency = 1.0 / (1.0 + math.log1p(max(0.0, e.age_days)))
    if FRESH_BULK_RECENCY_DISCOUNT and is_fresh_bulk(e, rel_cutoff):
        recency *= 1.0 - FRESH_BULK_RECENCY_DISCOUNT
    return W_RELEVANCE * e.relevance + W_RECENCY * recency


def _term_overlap(a: Entry, b: Entry) -> float:
    ta, tb = set(a.terms), set(b.terms)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _knobs_fingerprint(knobs: Knobs) -> str:
    """A short, stable fingerprint of the knobs a pack ran under, for the
    event payload. `Knobs` (m1) exposes no hash of its own, so this is
    computed here rather than by adding one to a module this build does not
    own.
    """
    canonical = json.dumps(asdict(knobs), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ==========================================================================
# Manifest
# ==========================================================================


@dataclass
class Manifest:
    task: str
    entries: list[Entry]
    dropped: list[tuple[Entry, str]]
    budget_tokens: int
    total_tokens: int
    # A lambda, not `time.time` directly: the latter binds the function
    # object once at class-definition time, which a test's
    # `monkeypatch.setattr(time, "time", ...)` (reassigning the module
    # attribute afterward) would then never see. The lambda re-reads
    # `time.time` fresh on every construction.
    created: float = field(default_factory=lambda: time.time())
    #: What never reached the ranking, and why (see `indexing.build_index`).
    #: Reported, never swallowed — a manifest that quietly dropped files
    #: should not read the same as a corpus that genuinely has few.
    census: dict[str, int] = field(default_factory=dict)

    @property
    def fill(self) -> float:
        return self.total_tokens / self.budget_tokens if self.budget_tokens else 0.0

    def render(self) -> str:
        """The file a session actually opens. Explicit beats judgement."""
        lines = [
            "---",
            "ctx:manifest: true",
            f"ctx:task: {self.task}",
            f"ctx:budget_tokens: {self.budget_tokens}",
            f"ctx:total_tokens: {self.total_tokens}",
            # UTC, not local time: a manifest rendered on one machine and
            # compared/tested on another (or in CI) must not depend on the
            # host's timezone, and `events.py` already established UTC as
            # this codebase's one timestamp convention.
            f"ctx:created: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.created))}",
            "---",
            "",
            f"# Working set — {self.task}",
            "",
            f"{len(self.entries)} files, {self.total_tokens:,} tokens "
            f"({self.fill:.0%} of budget). Load these and only these.",
            "",
            "| file | tokens | age | why |",
            "|---|---|---|---|",
        ]
        for e in self.entries:
            why = "pinned" if e.pinned else f"rel={e.relevance:.2f}"
            lines.append(f"| `{e.path}` | {e.tokens:,} | {e.age_days:.0f}d | {why} |")
        if self.dropped:
            lines += [
                "",
                f"<details><summary>Excluded ({len(self.dropped)}) — "
                "retrievable on request</summary>",
                "",
            ]
            for e, reason in self.dropped[:60]:
                lines.append(f"- `{e.path}` — {reason}")
            if len(self.dropped) > 60:
                lines.append(f"- ...and {len(self.dropped) - 60:,} more (listing capped at 60)")
            lines += ["", "</details>"]
        if self.census:
            lines += [
                "",
                "Never indexed (not candidates): "
                + ", ".join(f"{n:,} {reason}" for reason, n in sorted(self.census.items())),
            ]
        return "\n".join(lines) + "\n"


# ==========================================================================
# pack()
# ==========================================================================


def pack(
    task: str,
    layout: Layout,
    knobs: Knobs,
    *,
    summary: bool = False,
    last: int | None = None,
    decisions: bool = False,
    event_log: EventLog | None = None,
) -> Manifest:
    """Score, dedupe, budget — and log. Rebuilds the index from the
    filesystem on every call (see `build_index`).

    Retrieval flags are packer-level ADMISSION modifiers, layered on top of
    ordinary relevance ranking rather than replacing it:

    - `summary` scales the working budget down to `SUMMARY_BUDGET_FRAC` of
      `knobs.pack_budget_tokens` — a shorter manifest, same selection logic.
    - `last=N` admits the N most recently modified entries off the top
      (bounded by `ALWAYS_INCLUDE_MAX_TOKENS`, same as a pinned note).
    - `decisions` admits every entry that looks like a decision record
      (see `_is_decision_entry`) off the top, on the same terms.

    `event_log`, if given, is used in place of the default
    `EventLog(default_eventlog_path(layout.root, knobs.eventlog_path))` —
    this keeps the event emission path-configurable so a test can point it
    at an isolated fixture log instead of polluting a real one, without
    changing the fact that every pack emits exactly one event.

    `knobs.retrieval_summary_default` / `retrieval_last_default` /
    `retrieval_decisions_default` supply the default when a caller does not
    turn the corresponding flag on: `last=None` (unambiguous — an int and
    `None` are different types) falls back to `knobs.retrieval_last_default`
    exactly when omitted. `summary`/`decisions` are plain booleans per the
    interface contract, so a caller cannot represent "explicitly off" as
    distinct from "not specified" — `summary=False` and an omitted
    `summary` are the same value. The knob can therefore turn a flag ON
    project-wide, but a caller cannot force it OFF against a `True` knob
    default; that is an inherent limit of a two-value flag, not something
    fixable without changing the signature this module was asked to
    realize, and it is stated here rather than silently relied upon.
    """
    if summary is False:
        summary = bool(knobs.retrieval_summary_default)
    if last is None:
        last = knobs.retrieval_last_default
    if decisions is False:
        decisions = bool(knobs.retrieval_decisions_default)

    entries, census = build_index(layout)

    budget = int(knobs.pack_budget_tokens)
    if summary:
        budget = int(budget * SUMMARY_BUDGET_FRAC)

    q = set(_terms(task, 30))
    mentions = _task_mentions(task)

    for e in entries:
        overlap = len(q & set(e.terms))
        rel = min(1.0, overlap / max(3, len(q))) if q else 0.0
        # Named outright in the task — every tier. See _named_literally.
        if mentions and _named_literally(mentions, e.path):
            rel = min(1.0, rel + PATH_LITERAL_WEIGHT)
        e.relevance = rel

    # --- admission off the top: pinned, named, and flag-driven entries ---
    admitted = _named_for_admission(mentions, entries)
    if decisions:
        admitted |= {e.path for e in entries if _is_decision_entry(e)}
    if last:
        by_recency = sorted(entries, key=lambda e: e.mtime, reverse=True)
        admitted |= {e.path for e in by_recency[: max(0, last)]}

    for e in entries:
        if e.path in admitted:
            e.always_include = True
        if e.always_include and e.tokens <= ALWAYS_INCLUDE_MAX_TOKENS:
            e.pinned = True

    pinned = [e for e in entries if e.pinned]
    pool = [e for e in entries if not e.pinned]

    # Soft staleness filter — never applied to a high-relevance match.
    pool = [e for e in pool if e.age_days <= MAX_ITEM_AGE_DAYS or e.relevance >= AGE_GATE_RELEVANCE_EXEMPT]

    # Arithmetic, not judgement: nothing larger than the whole working
    # budget can be packed by any ranking. Reported as its own drop reason
    # rather than folded into the ordinary cutoff message, so a worker
    # reading it does not go looking for a scoring bug.
    oversized = [e for e in pool if e.tokens > budget]
    if oversized:
        pool = [e for e in pool if e.tokens <= budget]

    top_rel = max((e.relevance for e in pool), default=0.0)
    rel_cutoff = FRESH_BULK_MAX_RELEVANCE_FRAC * top_rel

    ranked = sorted(pool, key=lambda e: score(e, rel_cutoff), reverse=True)[:RETRIEVAL_K]

    chosen: list[Entry] = list(pinned)
    dropped: list[tuple[Entry, str]] = [
        (
            e,
            f"larger than the whole working budget ({e.tokens:,} tok vs {budget:,}) "
            "— no ranking could pack it",
        )
        for e in oversized
    ]
    total = sum(e.tokens for e in chosen)
    accounted = {id(e) for e in chosen} | {id(d) for d, _ in dropped}

    for e in ranked:
        if id(e) in accounted:
            continue
        if any(_term_overlap(e, c) >= REDUNDANCY_THRESHOLD for c in chosen):
            dropped.append((e, "near-duplicate of an included file"))
            accounted.add(id(e))
            continue
        if total + e.tokens > budget:
            dropped.append((e, f"over budget ({e.tokens:,} tok, {budget - total:,} remaining)"))
            accounted.add(id(e))
            continue
        chosen.append(e)
        accounted.add(id(e))
        total += e.tokens

    for e in entries:
        if id(e) not in accounted:
            dropped.append((e, "below relevance/recency cutoff"))
            accounted.add(id(e))

    # A dropped always-include entry is the one drop a reader must not have
    # to infer from an ordinary "over budget" line. Two distinct cases share
    # the cap, so each gets its own wording:
    def _drop_note(e: Entry, reason: str) -> str:
        if e.path in admitted:
            return (
                f"{reason} — requested by name/flag but over the "
                f"{ALWAYS_INCLUDE_MAX_TOKENS:,}-token always-include cap; read it directly"
            )
        if e.always_include:
            return (
                f"{reason} — always-include root file over the "
                f"{ALWAYS_INCLUDE_MAX_TOKENS:,}-token always-include cap; read it directly"
            )
        return reason

    dropped = [(e, _drop_note(e, reason)) for e, reason in dropped]

    manifest = Manifest(
        task=task,
        entries=chosen,
        dropped=dropped,
        budget_tokens=budget,
        total_tokens=total,
        census=census,
    )

    log = event_log if event_log is not None else EventLog(default_eventlog_path(layout.root, knobs.eventlog_path))
    log.append(
        EventKind.CONTEXT_ASSEMBLED,
        {
            "task": task,
            "config_hash": _knobs_fingerprint(knobs),
            "budget_tokens": budget,
            "total_tokens": total,
            "n_candidates": len(entries),
            "n_dropped": len(dropped),
            "items": [
                {
                    "item_id": e.item_id,
                    "tier": e.tier,
                    "tokens": e.tokens,
                    "age_days": e.age_days,
                    "relevance": round(e.relevance, 4),
                    "pinned": e.pinned,
                    "handle": e.handle,
                    "path": e.path,
                    "dropped": False,
                }
                for e in chosen
            ]
            + [
                {
                    "item_id": e.item_id,
                    "tier": e.tier,
                    "tokens": e.tokens,
                    "age_days": e.age_days,
                    "relevance": round(e.relevance, 4),
                    "pinned": False,
                    "handle": e.handle,
                    "path": e.path,
                    "dropped": True,
                    "drop_reason": reason,
                    "redundant": reason.startswith("near-duplicate"),
                }
                for e, reason in dropped
            ],
        },
    )

    return manifest
