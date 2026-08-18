#!/usr/bin/env python3
"""ctx-core SessionEnd (or Stop) hook: releases this session's board claim
(moves its live file to `var/sessions/history/`, m11) and refreshes the
stats products (m13) so `var/stats/summary.md|json` reflect this session's
activity without anyone remembering to run `ctx stats` by hand.

Non-blocking, fail-open, zero tokens: this is a pure `ctx_core` package
import -- same pattern `ctx_eventlog_hook.py` and `ctx_session_start_hook.py`
use, deliberately never shelling out to a `ctx` CLI verb. ANY problem here
(ctx-core not installed, a malformed payload, a corpus that doesn't look
like a ctx-core corpus, this session never having registered) is swallowed
to a local log file and the hook exits 0 -- a broken hook must never break
session end.

Wire this under either `SessionEnd` (current Claude Code hook event name)
or `Stop` (older/alternate name a given Claude Code version may expose) --
see `.claude/hooks/README.md`. The script itself doesn't care which fired
it; only `hook_event_name` in the payload differs.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ERROR_LOG_NAME = "ctx-hook-errors.log"


def _log_error(root: Path, message: str) -> None:
    try:
        log_dir = root / "var" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / ERROR_LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except Exception:
        pass


def _find_corpus_root(start: Path) -> Path:
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
        return 0

    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    cwd_hint = payload.get("cwd") or "."
    root = _find_corpus_root(Path(cwd_hint))
    session_id = payload.get("session_id") or None

    try:
        from ctx_core.config import Knobs
        from ctx_core.layout import Layout
        from ctx_core.sessions import SessionBoard, SessionNotFoundError
        from ctx_core.stats import compute_stats, write_products
    except Exception:
        _log_error(root, "ctx_core not importable -- skipping session end refresh")
        return 0

    try:
        layout = Layout(root)
        knobs = Knobs.load(root)

        if session_id:
            board = SessionBoard(
                layout,
                stale_seconds=knobs.session_stale_seconds,
                heartbeat_seconds=knobs.session_heartbeat_seconds,
            )
            try:
                board.release(session_id)
            except SessionNotFoundError:
                pass  # never registered, already released, or already swept -- fine

        report = compute_stats(layout, knobs, since_days=knobs.stats_window_days)
        write_products(report, layout)
    except Exception:
        _log_error(root, f"session end refresh failed: {traceback.format_exc()}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
