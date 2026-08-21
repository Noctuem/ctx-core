"""sessions: the live-session board -- one JSON file per session.

`var/sessions/live/<session-id>.json` is written ONLY by the session that
owns it: `{session_id, started_at, heartbeat_at, intent, claims, pid}`. No
shared table, no lock, no merge conflict -- single-writer per file by
construction (the m5 `events.py` lesson, generalized: put the invariant in
the shape of the data, not in caller discipline).

Liveness is heartbeat-age based: a session whose `heartbeat_at` is older
than `stale_seconds` is stale. A stale live file is swept to
`var/sessions/history/` (never deleted -- `ended_reason: "expired"`) the
next time any `SessionBoard` method runs; a graceful `release()` does the
same move with `ended_reason: "released"`. History is append-only by
construction: a destination collision (same session id ending twice) gets a
timestamp-disambiguated filename rather than overwriting anything.

Claims are corpus-root-relative paths. Overlap is structural (path-segment
prefix), never a string prefix: `notes/foo` and `notes/foobar` do NOT
overlap, but `notes/foo` and `notes/foo/bar.md` do (ancestor/descendant),
same as `notes/foo` and `notes/foo` (identical). `check()` is the read-only
primitive a guard hook (m14) wires to answer "does a DIFFERENT live session
claim this path" -- it does not itself block anything.

`stale_seconds` / `heartbeat_seconds`: `Knobs` (m1) carries
`session_stale_seconds` / `session_heartbeat_seconds` (defaults mirror the
module-level ones below), and every call site already threads them through
-- the CLI's `_session_board` and all four plugin hooks pass
`knobs.session_stale_seconds` / `knobs.session_heartbeat_seconds`
explicitly. `SessionBoard.__init__` also accepts an optional
`knobs: Knobs | None`, read whenever the plain `stale_seconds` /
`heartbeat_seconds` keyword params are left at their default (`None`) --
so a caller with a `Knobs` in hand can pass it directly, and a caller
without one (a test, a one-off script) can still set either value as a
bare keyword.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Sequence

from .config import Knobs
from .events import EventLog, default_eventlog_path
from .layout import Layout

# ==========================================================================
# Named constants
# ==========================================================================

#: Heartbeat age, in seconds, past which a session's claims stop blocking
#: and its live file becomes sweep-eligible. Generous default: a crashed
#: session must not wedge the fleet, but this should comfortably outlast an
#: ordinary build/tool pause. Mirrors `Knobs.session_stale_seconds`'s own
#: default -- kept here as a documented, importable constant for a caller
#: that wants the number without constructing a `Knobs`.
DEFAULT_STALE_SECONDS = 1800.0  # 30 minutes

#: How often a live session is expected to refresh its heartbeat file.
#: Advisory only -- `SessionBoard` does not itself run a timer; a caller
#: (the SessionStart/hook loop) polls at this cadence and calls
#: `.heartbeat()`. Mirrors `Knobs.session_heartbeat_seconds`'s own default.
DEFAULT_HEARTBEAT_SECONDS = 120.0  # 2 minutes

#: `var/sessions/live/` and `var/sessions/history/`, relative to `layout.var`.
LIVE_DIRNAME = "sessions/live"
HISTORY_DIRNAME = "sessions/history"

#: Event kinds this module appends. `EventKind` (m5) does not yet carry
#: these -- m14 may fold them into that enum later; until then any
#: non-empty string is a valid `kind` per `EventLog.append`'s own contract,
#: so plain module-level string constants are used here.
SESSION_START = "session_start"
SESSION_CLAIM = "session_claim"
SESSION_RELEASE = "session_release"
SESSION_EXPIRE = "session_expire"

#: Session ids are used verbatim as filenames (`<session_id>.json`) -- keep
#: them to a safe, portable charset so a caller cannot (accidentally or
#: otherwise) escape `live/`/`history/` via `/`, `\`, or `..`.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_RESERVED_SESSION_IDS = {".", ".."}

#: Matches the `-{stamp}` suffix `_move_to_history` appends on a same-id
#: collision (see its docstring): `<session_id>-YYYY-MM-DDTHHMMSS.ffffffZ`.
#: `_utc_now_iso()` always carries a literal `+00:00` UTC offset, so
#: `_move_to_history` substitutes that for `Z` BEFORE stripping colons out
#: of the time portion -- doing it in the other order (colons stripped
#: first) turns `+00:00` into `+0000`, which the second `.replace` then
#: never finds, and the stamp silently ends in `+0000` instead of the `Z`
#: this pattern (and the stamp's own docstring) name (TODO Low, state-
#: report survey item 4, 2026-08-21 -- previously a comment-vs-code
#: mismatch, fixed by reordering the two replaces rather than the regex).
#: `last_intent()` uses this to tell a repeat-ending's history file
#: (`<id>-<stamp>.json`) apart from a DIFFERENT session id that merely
#: starts with `<id>-` (e.g. `last_intent("a")` must not match `a-b.json`).
_HISTORY_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}\.\d+Z$")


class SessionNotFoundError(LookupError):
    """Raised when a method addressing an existing live session
    (`heartbeat`, `claim`, `release`) is called with a `session_id` that
    has no live file -- never registered, already released, or already
    swept as stale.
    """


# ==========================================================================
# Data shapes
# ==========================================================================


@dataclass(frozen=True)
class SessionEntry:
    """One session as reported by `SessionBoard.list()` -- live or
    just-swept-stale (see `stale`).
    """

    session_id: str
    started_at: str
    heartbeat_at: str
    intent: str
    claims: tuple[str, ...]
    pid: int
    stale: bool
    age_seconds: float


@dataclass(frozen=True)
class Conflict:
    """What `SessionBoard.check()` returns when a DIFFERENT live session
    already claims an overlapping path -- everything a guard hook (m14)
    needs to name the claiming session and its intent in a denial message.
    """

    session_id: str
    intent: str
    claimed_path: str


# ==========================================================================
# Path / time helpers
# ==========================================================================


def generate_session_id() -> str:
    """A convenience id generator (32 hex chars, `uuid4`) for callers that
    don't already have a natural session identity. `register()` takes
    `session_id` as a parameter rather than minting its own, so using this
    is optional.
    """
    return uuid.uuid4().hex


def _utc_now_iso() -> str:
    """Timezone-aware UTC timestamp, ISO-8601 -- same convention as
    `events.py`.
    """
    return datetime.now(timezone.utc).isoformat()


def _validate_session_id(session_id: str) -> None:
    if (
        not session_id
        or session_id in _RESERVED_SESSION_IDS
        or not _SESSION_ID_RE.fullmatch(session_id)
    ):
        raise ValueError(f"invalid session_id: {session_id!r}")


def _normalize_claim_path(path: str) -> str:
    """Corpus-root-relative, forward-slash, no leading/trailing slashes --
    the canonical form every claim is stored and compared in.
    """
    p = PurePosixPath(str(path).replace("\\", "/").strip("/"))
    parts = [part for part in p.parts if part not in ("", ".", "/")]
    return "/".join(parts)


def _paths_overlap(a: str, b: str) -> bool:
    """Structural overlap: identical, or one is an ancestor of the other.
    Never a string-prefix test -- `notes/foo` must not "overlap"
    `notes/foobar`.
    """
    pa = PurePosixPath(_normalize_claim_path(a)).parts
    pb = PurePosixPath(_normalize_claim_path(b)).parts
    n = min(len(pa), len(pb))
    if n == 0:
        return pa == pb  # both empty -> equal (a bare root claim)
    return pa[:n] == pb[:n]


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write `data` as JSON to `path` without a reader ever observing a
    torn/partial write: serialize to a sibling temp file, then
    `os.replace` -- atomic on both POSIX and Windows (same directory, same
    filesystem). This is what makes it safe for `list()`/`check()` to read
    every live file while another process may be mid-write to its own.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    os.replace(tmp, path)


def _default_event_log(layout: Layout) -> EventLog:
    # Same pattern as `archive.py`'s `_default_event_log`: a fresh
    # `Knobs.load` (one cheap `.ctxrc.toml` read) so a domain that
    # customized `eventlog_path` still gets SESSION_* events in the right
    # place, without this module owning a `knobs` parameter of its own.
    knobs = Knobs.load(layout.root)
    return EventLog(default_eventlog_path(layout.root, knobs.eventlog_path))


# ==========================================================================
# SessionBoard
# ==========================================================================


class SessionBoard:
    """One corpus's live-session board, rooted at `layout.var/sessions/`.

    `stale_seconds` / `heartbeat_seconds` are plain keyword params, `None`
    by default; when either is left `None`, its value comes from `knobs`
    (an explicit `Knobs`, or a fresh `Knobs()` default when `knobs` is also
    omitted) -- so a caller holding a `Knobs` can pass it once via `knobs=`,
    and a caller without one can still pin either value directly. A
    non-`None` keyword always wins over `knobs` for that one field.
    `event_log`, if given, replaces the default `EventLog` derived from
    `Knobs.load(layout.root)` -- same injectable-for-test-isolation pattern
    `packer.pack` / `archive.archive_note` use.
    """

    def __init__(
        self,
        layout: Layout,
        *,
        stale_seconds: float | None = None,
        heartbeat_seconds: float | None = None,
        knobs: Knobs | None = None,
        event_log: EventLog | None = None,
    ) -> None:
        self.layout = layout
        resolved_knobs = knobs if knobs is not None else Knobs()
        self.stale_seconds = float(
            stale_seconds if stale_seconds is not None else resolved_knobs.session_stale_seconds
        )
        self.heartbeat_seconds = float(
            heartbeat_seconds
            if heartbeat_seconds is not None
            else resolved_knobs.session_heartbeat_seconds
        )
        self._event_log = event_log if event_log is not None else _default_event_log(layout)

    # --- directories -----------------------------------------------------

    @property
    def live_dir(self) -> Path:
        return self.layout.var / LIVE_DIRNAME

    @property
    def history_dir(self) -> Path:
        return self.layout.var / HISTORY_DIRNAME

    def _live_path(self, session_id: str) -> Path:
        _validate_session_id(session_id)
        return self.live_dir / f"{session_id}.json"

    # --- file IO -----------------------------------------------------------

    def _iter_live_files(self) -> list[Path]:
        if not self.live_dir.is_dir():
            return []
        return sorted(self.live_dir.glob("*.json"))

    @staticmethod
    def _read_session_file(path: Path) -> dict | None:
        """`None` on any read/parse failure -- a file mid-write (extremely
        unlikely given `_atomic_write_json`) or otherwise unreadable is
        skipped by callers rather than crashing a `list()`/`check()` over
        one bad entry.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _is_stale(self, data: dict, now: float) -> bool:
        age = self._age_seconds(data, now)
        return age > self.stale_seconds

    @staticmethod
    def _age_seconds(data: dict, now: float) -> float:
        hb = data.get("heartbeat_at")
        try:
            return now - datetime.fromisoformat(hb).timestamp()
        except (TypeError, ValueError):
            # Missing/malformed heartbeat -- treat as maximally stale
            # rather than raising, so one damaged file can't wedge a sweep.
            return float("inf")

    def _move_to_history(self, live_path: Path, data: dict, *, reason: str) -> dict:
        record = dict(data)
        record["ended_at"] = _utc_now_iso()
        record["ended_reason"] = reason
        dest = self.history_dir / live_path.name
        if dest.exists():
            # Append-only: never overwrite a prior history entry. A same-id
            # collision (a session id reused after already ending once) is
            # disambiguated with a timestamp suffix instead: `+00:00` -> `Z`
            # FIRST, then colons stripped -- reversing this order leaves
            # `+0000` behind instead (see `_HISTORY_STAMP_RE`'s docstring).
            stamp = record["ended_at"].replace("+00:00", "Z").replace(":", "")
            dest = self.history_dir / f"{live_path.stem}-{stamp}{live_path.suffix}"
        _atomic_write_json(dest, record)
        live_path.unlink(missing_ok=True)
        return record

    # --- sweep -------------------------------------------------------------

    def _sweep_stale(self, *, now: float | None = None, exclude: str | None = None) -> list[dict]:
        """Move every live file whose heartbeat has aged past
        `stale_seconds` to history, appending one `SESSION_EXPIRE` event
        per file. `exclude` skips a session id -- the caller's own, mid
        register/heartbeat/claim/release. Returns the swept records
        (history shape: carries `ended_at`/`ended_reason`).
        """
        now = now if now is not None else time.time()
        swept: list[dict] = []
        for path in self._iter_live_files():
            session_id = path.stem
            if session_id == exclude:
                continue
            data = self._read_session_file(path)
            if data is None:
                continue
            if not self._is_stale(data, now):
                continue
            record = self._move_to_history(path, data, reason="expired")
            self._event_log.append(
                SESSION_EXPIRE,
                {
                    "session_id": session_id,
                    "intent": data.get("intent", ""),
                    "claims": data.get("claims", []),
                    "heartbeat_at": data.get("heartbeat_at"),
                },
            )
            swept.append(record)
        return swept

    # --- public interface ----------------------------------------------

    def register(self, session_id: str, intent: str) -> None:
        """Create `session_id`'s live file (overwriting any prior file of
        the same id -- registering is idempotent-by-intent, not append).
        Sweeps other stale sessions first, then appends `SESSION_START`.
        """
        _validate_session_id(session_id)
        self._sweep_stale(exclude=session_id)
        now_iso = _utc_now_iso()
        data = {
            "session_id": session_id,
            "started_at": now_iso,
            "heartbeat_at": now_iso,
            "intent": intent,
            "claims": [],
            "pid": os.getpid(),
        }
        _atomic_write_json(self._live_path(session_id), data)
        self._event_log.append(
            SESSION_START, {"session_id": session_id, "intent": intent, "pid": data["pid"]}
        )

    def heartbeat(self, session_id: str) -> None:
        """Refresh `session_id`'s `heartbeat_at`. No event is appended --
        this runs on a cadence (`heartbeat_seconds`) and would flood the
        log; liveness is read straight off the file's own timestamp.
        """
        self._sweep_stale(exclude=session_id)
        path = self._live_path(session_id)
        data = self._read_session_file(path)
        if data is None:
            raise SessionNotFoundError(session_id)
        data["heartbeat_at"] = _utc_now_iso()
        _atomic_write_json(path, data)

    def claim(self, session_id: str, paths: Sequence[str]) -> None:
        """Add `paths` (corpus-root-relative) to `session_id`'s claim set.
        Merges with existing claims (order-preserving de-dup) rather than
        replacing them. Does not itself check for conflicts with other
        sessions -- that's `check()`'s job, wired by the guard hook.
        """
        self._sweep_stale(exclude=session_id)
        path = self._live_path(session_id)
        data = self._read_session_file(path)
        if data is None:
            raise SessionNotFoundError(session_id)
        normalized = [_normalize_claim_path(p) for p in paths]
        merged = list(dict.fromkeys([*data.get("claims", []), *normalized]))
        data["claims"] = merged
        data["heartbeat_at"] = _utc_now_iso()  # claiming counts as activity
        _atomic_write_json(path, data)
        self._event_log.append(SESSION_CLAIM, {"session_id": session_id, "paths": normalized})

    def release(self, session_id: str) -> None:
        """End `session_id` gracefully: move its live file to history
        (`ended_reason: "released"`) and append `SESSION_RELEASE`.
        """
        self._sweep_stale(exclude=session_id)
        path = self._live_path(session_id)
        data = self._read_session_file(path)
        if data is None:
            raise SessionNotFoundError(session_id)
        self._move_to_history(path, data, reason="released")
        self._event_log.append(
            SESSION_RELEASE,
            {
                "session_id": session_id,
                "intent": data.get("intent", ""),
                "claims": data.get("claims", []),
            },
        )

    def last_intent(self, session_id: str) -> str | None:
        """The `intent` recorded in the NEWEST history record for
        `session_id` (by `ended_at`), or `None` if `session_id` has never
        ended (no history record at all -- brand new, or still live).

        History holds one file per ending: `<session_id>.json` for the
        first, `<session_id>-<stamp>.json` for every repeat (see
        `_move_to_history`). Both are considered; the stamp suffix is
        matched structurally (`_HISTORY_STAMP_RE`), not by string prefix,
        so a different session id that happens to start with
        `<session_id>-` is never mistaken for a repeat ending of this one.

        Exists so a caller (`ctx sessions claim`'s re-register-on-expiry
        path) can recover a swept session's intent without the CLI reaching
        into `history_dir` layout itself.
        """
        _validate_session_id(session_id)
        if not self.history_dir.is_dir():
            return None

        candidates: list[Path] = []
        exact = self.history_dir / f"{session_id}.json"
        if exact.is_file():
            candidates.append(exact)
        prefix = f"{session_id}-"
        for path in self.history_dir.glob(f"{session_id}-*.json"):
            suffix = path.stem[len(prefix):]
            if _HISTORY_STAMP_RE.fullmatch(suffix):
                candidates.append(path)
        if not candidates:
            return None

        newest_data: dict | None = None
        newest_ended_at = ""
        for path in candidates:
            data = self._read_session_file(path)
            if data is None:
                continue
            ended_at = data.get("ended_at", "")
            if newest_data is None or ended_at >= newest_ended_at:
                newest_ended_at = ended_at
                newest_data = data
        return newest_data.get("intent") if newest_data is not None else None

    def list(self) -> list[SessionEntry]:
        """Every session that was live at the start of this call: entries
        still in `live/` after the sweep (never stale, by definition), plus
        every entry this exact call just swept to history (`stale=True`) --
        realizing "rendered as stale and swept to history by the next
        `sessions` invocation" in one step. Sorted by `session_id` for a
        deterministic read.
        """
        now = time.time()
        swept = self._sweep_stale(now=now)
        entries = [self._entry_from_data(record, now, stale=True) for record in swept]
        for path in self._iter_live_files():
            data = self._read_session_file(path)
            if data is None:
                continue
            entries.append(self._entry_from_data(data, now, stale=False))
        entries.sort(key=lambda e: e.session_id)
        return entries

    def check(self, path: str, session_id: str) -> Conflict | None:
        """Does a DIFFERENT live session already claim a path overlapping
        `path`? Read-only -- the primitive a guard hook wires; it does not
        block anything itself. Sweeps stale sessions first, so an expired
        claimant never produces a false conflict.
        """
        self._sweep_stale(exclude=session_id)
        target = _normalize_claim_path(path)
        for live_path in self._iter_live_files():
            other_id = live_path.stem
            if other_id == session_id:
                continue
            data = self._read_session_file(live_path)
            if data is None:
                continue
            for claimed in data.get("claims", []):
                if _paths_overlap(target, claimed):
                    return Conflict(
                        session_id=other_id,
                        intent=data.get("intent", ""),
                        claimed_path=claimed,
                    )
        return None

    @staticmethod
    def _entry_from_data(data: dict, now: float, *, stale: bool) -> SessionEntry:
        return SessionEntry(
            session_id=data.get("session_id", ""),
            started_at=data.get("started_at", ""),
            heartbeat_at=data.get("heartbeat_at", ""),
            intent=data.get("intent", ""),
            claims=tuple(data.get("claims", [])),
            pid=data.get("pid", -1),
            stale=stale,
            age_seconds=SessionBoard._age_seconds(data, now),
        )
