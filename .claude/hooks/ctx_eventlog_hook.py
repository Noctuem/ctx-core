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
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

HOOK_KIND = "hook_post_tool_use"
ERROR_LOG_NAME = "ctx-hook-errors.log"


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

    try:
        from ctx_core.config import Knobs
        from ctx_core.events import EventLog, default_eventlog_path
    except Exception:
        _log_error(root, "ctx_core not importable -- skipping event append")
        return 0

    try:
        knobs = Knobs.load(root)
        log = EventLog(default_eventlog_path(root, knobs.eventlog_path))
        log.append(
            HOOK_KIND,
            {
                "tool_name": payload.get("tool_name"),
                "hook_event_name": payload.get("hook_event_name"),
                "session_id": payload.get("session_id"),
                "ts": time.time(),
            },
        )
    except Exception:
        _log_error(root, f"event append failed: {traceback.format_exc()}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
