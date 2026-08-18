"""events: append-only, hash-chained JSONL event log.

Every line is a JSON object carrying `prev_hash` (the sha256 hex digest of
the previous line's canonical bytes) and its own `hash` (the sha256 hex
digest of everything in the line except `hash` itself). Walking the chain
end to end (`verify()`) detects any line that was inserted, reordered,
edited, or dropped by something outside the append path -- a hard failure,
not a mystery.

Single-writer by construction: every `append()` takes an OS-level advisory
lock (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) around a small
sidecar lock file for the whole read-tail / compute / write-line critical
section, so two processes racing against the same log file cannot
interleave writes or corrupt the chain.

Scope: single-machine, single-clone integrity ONLY. Cross-clone or
cross-device sync (e.g. two machines both appending to copies of the same
log and later merging) is explicitly OUT of scope -- this module makes no
attempt to reconcile diverged chains.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

# --- named constants (no bare literals below) ---------------------------

#: Filename convention dependents should use when deriving the actual log
#: file from a corpus-relative directory knob (see `default_eventlog_path`).
EVENTS_FILENAME = "events.jsonl"

#: Suffix of the sidecar lock file used to serialize appends across
#: processes. Lives next to the log file itself, never committed/read as
#: log content.
LOCK_SUFFIX = ".lock"

#: `prev_hash` value stored on the first ever line of a chain (there is no
#: previous line to hash).
GENESIS_HASH = "0" * 64

#: Key name of the self-hash field on every record. Excluded from its own
#: hash computation (a field cannot cover itself).
HASH_FIELD = "hash"

#: How long `append()` will keep retrying a contended lock before giving up
#: (Windows path only -- `msvcrt.locking` can raise instead of blocking
#: indefinitely, so we wrap it in our own bounded retry loop; POSIX's
#: `fcntl.flock` blocks natively and needs none of this).
LOCK_TIMEOUT_SECONDS = 30.0

#: Delay between retries while waiting for a contended lock (Windows only).
LOCK_RETRY_INTERVAL_SECONDS = 0.05

#: Number of bytes `msvcrt.locking` is asked to lock in the sidecar file.
#: The sidecar is never written to beyond this, so one byte is enough to
#: serve as a pure mutex.
LOCK_REGION_BYTES = 1

#: `json.dumps` separators for canonical (whitespace-free) serialization.
_CANONICAL_SEPARATORS = (",", ":")


class EventLogCorruptError(ValueError):
    """Raised by `EventLog.append` when the log's tail line is not a
    well-formed chain record -- undecodable JSON, not a JSON object, or
    missing the `"seq"` field `append` needs to compute the next sequence
    number. `verify()` already detects and reports this class of damage as
    `(False, i)`; this exception is what stops `append` from either
    crashing on a bare `JSONDecodeError`/`KeyError` or, worse, silently
    "repairing" a broken chain by extending it as if the tail were sound.
    """


class EventKind(str, Enum):
    """Well-known event kinds. Any other string is also a valid `kind` --
    this is the minimum vocabulary dependents are guaranteed to find.
    """

    CONTEXT_ASSEMBLED = "context_assembled"
    ARCHIVE_STUB = "archive_stub"
    DOCTOR_RUN = "doctor_run"
    INIT_RUN = "init_run"


def default_eventlog_path(root: Path, eventlog_path: str) -> Path:
    """Resolve the actual log FILE path from a corpus root and a
    `Knobs.eventlog_path` value.

    `Knobs.eventlog_path` (default `"var/log/"`) names a DIRECTORY, relative
    to the corpus root -- not the log file itself. The event log always
    lives at a fixed filename inside that directory:

        <root>/<eventlog_path>/events.jsonl

    e.g. with the default knob, `<root>/var/log/events.jsonl`. Dependents
    (m2, m3, m6, m8) should route through this helper rather than
    hardcoding the join, so the directory-vs-file convention only lives in
    one place.
    """
    return Path(root) / eventlog_path / EVENTS_FILENAME


# --- cross-platform advisory file lock -----------------------------------

if sys.platform == "win32":
    import msvcrt

    def _lock_fd(fd) -> None:
        fd.seek(0)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, LOCK_REGION_BYTES)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not acquire event log lock within "
                        f"{LOCK_TIMEOUT_SECONDS}s"
                    )
                time.sleep(LOCK_RETRY_INTERVAL_SECONDS)

    def _unlock_fd(fd) -> None:
        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, LOCK_REGION_BYTES)

else:
    import fcntl

    def _lock_fd(fd) -> None:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)

    def _unlock_fd(fd) -> None:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)


class _FileLock:
    """Advisory, cross-process, cross-platform exclusive lock backed by a
    sidecar file. Used to guard the whole read-tail / append critical
    section in `EventLog.append`, never held across process lifetimes.
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fd = None

    def __enter__(self) -> "_FileLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self._lock_path, "a+b")
        _lock_fd(self._fd)
        return self

    def __exit__(self, *exc_info: object) -> None:
        assert self._fd is not None
        try:
            _unlock_fd(self._fd)
        finally:
            self._fd.close()
            self._fd = None


# --- canonical serialization / hashing ------------------------------------


def _canonical_json(obj: Any) -> str:
    """The one canonical-bytes definition every hash in this module is
    computed from: `json.dumps` with sorted keys, non-ASCII characters left
    as-is, and no incidental whitespace. Deterministic across processes and
    Python versions for any JSON-safe `obj`.
    """
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=_CANONICAL_SEPARATORS
    )


def _hash_line(line: str) -> str:
    """sha256 hex digest of one on-disk line's canonical bytes (UTF-8
    encoded, no trailing newline). Since every line is written already in
    canonical form, a raw line read back off disk IS its own canonical
    bytes -- no re-serialization needed to (re)compute this.
    """
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _hash_record(record: Mapping[str, Any]) -> str:
    """sha256 hex digest covering every field of `record` EXCEPT
    `HASH_FIELD` itself (a field cannot cover itself). This is the value
    stored in a line's own `hash` field.
    """
    coverable = {k: v for k, v in record.items() if k != HASH_FIELD}
    return hashlib.sha256(_canonical_json(coverable).encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    """Timezone-aware UTC timestamp, ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def _read_raw_lines(path: Path) -> list[str]:
    """The log file's lines as literal strings, split on `\\n` only (never
    `str.splitlines()`, which also breaks on unicode separators like
    U+2028 that could legitimately appear inside a JSON string value and
    would otherwise silently corrupt line boundaries). No trailing empty
    element from the final newline. Missing file -> `[]`.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


# --- the log ---------------------------------------------------------------


@dataclass(frozen=True)
class EventLog:
    """One hash-chained JSONL event log at `path` (the log FILE path, not
    a directory -- see `default_eventlog_path` for deriving it from
    `Knobs.eventlog_path`).
    """

    path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))

    @property
    def _lock_path(self) -> Path:
        return self.path.with_name(self.path.name + LOCK_SUFFIX)

    def append(self, kind: str, payload: dict) -> None:
        """Append one event, lock-guarded. `kind` should be an
        `EventKind` member or any other non-empty string; `payload` must be
        JSON-serializable.
        """
        if not kind:
            raise ValueError("kind must be a non-empty string")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _FileLock(self._lock_path):
            lines = _read_raw_lines(self.path)
            if lines:
                prev_hash = _hash_line(lines[-1])
                try:
                    tail_record = json.loads(lines[-1])
                except json.JSONDecodeError as exc:
                    raise EventLogCorruptError(
                        f"cannot append to {self.path}: tail line is not valid JSON ({exc})"
                    ) from exc
                if not isinstance(tail_record, dict) or "seq" not in tail_record:
                    raise EventLogCorruptError(
                        f"cannot append to {self.path}: tail line is missing the "
                        f"\"seq\" field required to chain from it"
                    )
                seq = tail_record["seq"] + 1
            else:
                prev_hash = GENESIS_HASH
                seq = 0

            record: dict[str, Any] = {
                "seq": seq,
                "ts": _utc_now_iso(),
                "kind": kind,
                "payload": payload,
                "prev_hash": prev_hash,
            }
            record[HASH_FIELD] = _hash_record(record)
            line = _canonical_json(record)

            with open(self.path, "a", encoding="utf-8", newline="") as f:
                f.write(line + "\n")

    def verify(self) -> tuple[bool, int | None]:
        """Walk the full chain from the start. Returns `(True, None)` if
        every line's own hash matches its content and every line's
        `prev_hash` matches the actual hash of the line before it (or
        `GENESIS_HASH` for the first line). Returns `(False, i)` for the
        0-based index of the first line that fails either check -- an
        edited, reordered, inserted, or truncated-from-the-middle line.
        An empty or missing log verifies as `(True, None)` trivially.
        """
        lines = _read_raw_lines(self.path)
        expected_prev = GENESIS_HASH
        for i, line in enumerate(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return False, i

            if not isinstance(record, dict) or HASH_FIELD not in record:
                return False, i

            if _hash_record(record) != record[HASH_FIELD]:
                return False, i

            if record.get("prev_hash") != expected_prev:
                return False, i

            expected_prev = _hash_line(line)

        return True, None
