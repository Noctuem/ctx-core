#!/usr/bin/env python3
"""ctx-core PostToolUse hook: appends one event-log line per tool call.

Non-blocking, fail-open: ANY problem here (ctx-core not installed, a
malformed hook payload, a corpus that doesn't look like a ctx-core corpus
yet) is swallowed to a local log file and the hook exits 0. A broken hook
must never break the user's session — see .claude/hooks/README.md.

Interface contract (spec Module 8): read ctx_core via the installed
Python package/console entry point, never a relative import across the
plugin/engine boundary (no `sys.path` reach-around into a sibling source
tree). That way this script works unmodified whether a domain repo
vendors ctx-core on its own PYTHONPATH or installs it from PyPI.

Deliberate simplification (upgrade path noted): today this appends the
event by importing `ctx_core` in-process, because the CLI (m10,
`ctx_core/cli.py`) does not yet expose an event-append verb and `ctx` is
not guaranteed to be on PATH in every environment this hook runs in. Once
m10 adds one, prefer shelling out to `ctx` (falling back to
`python -m ctx_core`, then this in-process import) without changing this
script's stdin/stdout contract.

Automatic heartbeat (v0.2 dogfood finding, `TODO.md` Medium): this hook
fires on every tool call, so it doubles as the session's liveness pulse --
a `SessionStart` hook only fires once (or on resume/clear), which is too
sparse to keep a genuinely long-lived session (dogfooded: ~40 minutes) off
the `session_stale_seconds` sweep. If `session_id` is present in the
payload, this calls `SessionBoard.heartbeat(session_id)` after the event
append; `SessionNotFoundError` (never registered on this board -- e.g. the
`SessionStart` hook isn't wired, or the corpus's board was reset) is
swallowed, same fail-open contract as everything else here -- a session
that was never registered has nothing to heartbeat, and that is not an
error.

Claim-overlap OBSERVER, never a guard (review pass item 5, 2026-08-21):
`ctx_session_guard_hook.py`'s `PreToolUse` guard cannot cover `Bash` --
there is no single reliable field in a Bash `tool_input` naming the
path(s) a shell command touches (a pipeline, redirect, or `mv` can name
any number of paths in arbitrary positions), so guarding it would mean
either false denials on unrelated commands or a parser nobody trusts (see
`.claude/hooks/README.md`'s named limit and the TODO entry this amends).
Instead: after a `Bash` call's event append, this hook mtime-checks every
FILE another live session claims (directory claims are skipped -- an mtime
on a directory says nothing about which file inside it changed) and, for
each claimed file whose mtime is newer than this session's own previous
hook event (or a fixed fallback window, if that can't be determined),
appends one `claim_overlap_observed` event per `(other_session, path)`
hit. This NEVER denies anything -- `ctx_session_guard_hook.py`'s
`PreToolUse` deny path remains the only place this codebase blocks a tool
call -- and it fails open exactly like every other branch in this file:
capped at `CLAIM_OVERLAP_STAT_CAP` stat calls so a session with a large
claim set can't turn every Bash call into a filesystem sweep, and any
error anywhere in the observer is logged to `ctx-hook-errors.log` and
swallowed, never raised. Surfaced downstream in `ctx stats`' Sessions
section as a windowed count (`stats.py`'s `_fold_sessions`).
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HOOK_KIND = "hook_post_tool_use"
ERROR_LOG_NAME = "ctx-hook-errors.log"

#: The one tool name the claim-overlap observer runs for -- see the module
#: docstring's "Claim-overlap OBSERVER" section for why Bash is the gap the
#: path-aware `PreToolUse` guard cannot cover.
BASH_TOOL_NAME = "Bash"

#: `kind` this observer appends -- a plain string, same convention every
#: v0.2 module not owning `events.py` already follows (see `sessions.py`,
#: `stats.py`).
CLAIM_OVERLAP_EVENT_KIND = "claim_overlap_observed"

#: Hard cap on claimed-file stat checks per hook invocation -- bounds the
#: cost of the observer regardless of how many files a live session's claim
#: set names, so a large claim set can't turn every Bash call into a
#: filesystem sweep.
CLAIM_OVERLAP_STAT_CAP = 50

#: Fallback lookback window, in seconds, used when this session's previous
#: hook event `ts` can't be determined (first tool call of a session, or
#: `_previous_hook_ts`'s bounded backward scan doesn't find it). Generous
#: enough to catch a Bash call's own filesystem side effects without
#: reading the whole event log.
CLAIM_OVERLAP_FALLBACK_WINDOW_SECONDS = 120.0

#: How many lines `_previous_hook_ts` scans backward from the tail before
#: giving up and falling back to the fixed window above. This hook fires on
#: every tool call, so a session's own previous event is almost always
#: within a handful of lines of the tail -- an unbounded scan would cost
#: more than the signal is worth.
PREVIOUS_TS_SCAN_LIMIT = 200


def _log_error(root: Path, message: str) -> None:
    """Best-effort local error log. Never raises — there is nowhere left
    to report to if even this fails, so it is caught and dropped."""
    try:
        log_dir = root / "var" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / ERROR_LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except Exception:
        pass


def _find_corpus_root(start: Path) -> Path:
    """Walk upward from `start` for a directory shaped like a ctx-core
    corpus (has both `core/` and `notes/`). Falls back to `start` itself
    so a hook fired outside a real corpus still has *a* root to log
    against instead of raising."""
    try:
        current = start.resolve()
    except Exception:
        return start
    for candidate in (current, *current.parents):
        if (candidate / "core").is_dir() and (candidate / "notes").is_dir():
            return candidate
    return current


def _previous_hook_ts(log_path: Path, session_id: str) -> float | None:
    """Best-effort: the epoch-seconds `ts` of the most recent
    `hook_post_tool_use` event THIS session appended, read straight off the
    raw log file (never through `EventLog`, which has no query API and
    whose `append` would try to chain-verify a read it doesn't need to
    make). Scans backward at most `PREVIOUS_TS_SCAN_LIMIT` lines -- see that
    constant's docstring. Tolerant of a corrupt/malformed tail (skips
    unparsable lines rather than raising); returns `None` on any failure or
    when nothing matches, so the caller falls back to a fixed window
    instead of treating "not found" as "found at time zero".
    """
    try:
        if not log_path.is_file():
            return None
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    checked = 0
    for line in reversed(lines):
        if checked >= PREVIOUS_TS_SCAN_LIMIT:
            break
        checked += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("kind") != HOOK_KIND:
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict) or payload.get("session_id") != session_id:
            continue
        try:
            return datetime.fromisoformat(record["ts"]).timestamp()
        except (KeyError, TypeError, ValueError):
            return None
    return None


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0  # can't even read stdin -- nothing to log, fail open

    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    cwd_hint = payload.get("cwd") or "."
    root = _find_corpus_root(Path(cwd_hint))
    session_id = payload.get("session_id") or None
    tool_name = payload.get("tool_name")

    try:
        from ctx_core.config import Knobs
        from ctx_core.events import EventLog, default_eventlog_path
    except Exception:
        _log_error(root, "ctx_core not importable -- skipping event append")
        return 0

    try:
        knobs = Knobs.load(root)
        # 0.2.4: `eventlog_for` honors `eventlog_per_writer` (this clone's
        # own chain file); the fallback keeps this hook working unmodified
        # against an older installed engine.
        try:
            from ctx_core.events import eventlog_for

            log = eventlog_for(root, knobs)
            log_path = log.path
        except ImportError:
            log_path = default_eventlog_path(root, knobs.eventlog_path)
            log = EventLog(log_path)
        # Captured BEFORE this call's own append below, so it names the
        # PREVIOUS event -- see `_previous_hook_ts`'s docstring.
        previous_ts = (
            _previous_hook_ts(log_path, session_id)
            if tool_name == BASH_TOOL_NAME and session_id
            else None
        )
        log.append(
            HOOK_KIND,
            {
                "tool_name": tool_name,
                "hook_event_name": payload.get("hook_event_name"),
                "session_id": session_id,
                "ts": time.time(),
            },
        )
    except Exception:
        _log_error(root, f"event append failed: {traceback.format_exc()}")
        return 0

    if session_id:
        _heartbeat(root, knobs, session_id)

    if tool_name == BASH_TOOL_NAME and session_id:
        _observe_claim_overlaps(root, knobs, log, session_id, previous_ts)

    return 0


def _heartbeat(root: Path, knobs, session_id: str) -> None:
    """Best-effort liveness refresh -- see the module docstring's
    "Automatic heartbeat" section. Never raises: a missing/uninstalled
    session board must not turn a successful event append into a hook
    failure."""
    try:
        from ctx_core.layout import Layout
        from ctx_core.sessions import SessionBoard, SessionNotFoundError

        layout = Layout(root)
        board = SessionBoard(
            layout,
            stale_seconds=knobs.session_stale_seconds,
            heartbeat_seconds=knobs.session_heartbeat_seconds,
        )
        try:
            board.heartbeat(session_id)
        except SessionNotFoundError:
            pass  # never registered on this board -- nothing to refresh
    except Exception:
        _log_error(root, f"heartbeat failed: {traceback.format_exc()}")


def _observe_claim_overlaps(root: Path, knobs, log, session_id: str, previous_ts: float | None) -> None:
    """Advisory only -- see the module docstring's "Claim-overlap OBSERVER"
    section. Never raises past this function and never denies anything;
    any failure (board unreadable, a claimed path that can't be stat'd,
    the append itself hitting a corrupt log) is logged to
    `ctx-hook-errors.log` and swallowed, same fail-open contract as the
    rest of this file.
    """
    try:
        from ctx_core.layout import Layout
        from ctx_core.sessions import SessionBoard

        layout = Layout(root)
        # Same log/knobs this call already has -- one EventLog instance,
        # one lock-guarded append path, rather than a second one derived
        # independently.
        board = SessionBoard(
            layout,
            stale_seconds=knobs.session_stale_seconds,
            heartbeat_seconds=knobs.session_heartbeat_seconds,
            event_log=log,
        )
        now = time.time()
        cutoff = (
            previous_ts if previous_ts is not None else now - CLAIM_OVERLAP_FALLBACK_WINDOW_SECONDS
        )

        stat_calls = 0
        for entry in board.list():
            if entry.stale or entry.session_id == session_id:
                continue
            for claim in entry.claims:
                if stat_calls >= CLAIM_OVERLAP_STAT_CAP:
                    return
                stat_calls += 1
                try:
                    target = root / claim
                    if not target.is_file():
                        continue  # directory claims (or gone paths) skipped
                    mtime = target.stat().st_mtime
                except OSError:
                    continue
                if mtime > cutoff:
                    log.append(
                        CLAIM_OVERLAP_EVENT_KIND,
                        {
                            "session_id": session_id,
                            "other_session": entry.session_id,
                            "path": claim,
                        },
                    )
    except Exception:
        _log_error(root, f"claim-overlap observer failed: {traceback.format_exc()}")


if __name__ == "__main__":
    sys.exit(main())
