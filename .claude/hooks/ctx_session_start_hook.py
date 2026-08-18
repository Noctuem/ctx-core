#!/usr/bin/env python3
"""ctx-core SessionStart hook: registers this session on the live-session
board (m11), refreshes its heartbeat, and renders the board to stdout so a
session opens already knowing who else is alive and what they claim.

Non-blocking, fail-open: ANY problem here (ctx-core not installed, a
malformed hook payload, a corpus that doesn't look like a ctx-core corpus
yet) is swallowed to a local log file and the hook exits 0 -- same contract
as `ctx_eventlog_hook.py`. A broken hook must never break session start.

Interface contract (spec Module 14): read ctx_core via the installed
Python package/console entry point, never a relative import across the
plugin/engine boundary (no `sys.path` reach-around into a sibling source
tree).

Registration vs. heartbeat: this hook tries `SessionBoard.heartbeat()`
first (the session may already be on the board -- a `resume`/`clear`
SessionStart on an id that's still live shouldn't reset its claims), and
only falls back to `SessionBoard.register()` on `SessionNotFoundError`
(brand-new session, or the prior live file already expired/was released).
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ERROR_LOG_NAME = "ctx-hook-errors.log"


def _log_error(root: Path, message: str) -> None:
    """Best-effort local error log. Never raises -- there is nowhere left
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


def _render_board(entries: list) -> str:
    if not entries:
        return "ctx sessions: no other sessions recorded."
    lines = ["ctx sessions:"]
    for e in entries:
        status = "just-expired" if e.stale else "live"
        claims = ", ".join(e.claims) if e.claims else "(none)"
        lines.append(
            f"  - {e.session_id} [{status}] age={e.age_seconds:.0f}s "
            f"intent={e.intent!r} claims={claims}"
        )
    return "\n".join(lines)


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0  # can't even read stdin -- nothing to do, fail open

    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    cwd_hint = payload.get("cwd") or "."
    root = _find_corpus_root(Path(cwd_hint))
    session_id = payload.get("session_id") or None
    source = payload.get("source") or "startup"

    if not session_id:
        # No session_id in the payload at all -- nothing to register against.
        # Fail open rather than mint an id no other hook call would ever see.
        return 0

    try:
        from ctx_core.config import Knobs
        from ctx_core.layout import Layout
        from ctx_core.sessions import SessionBoard, SessionNotFoundError
    except Exception:
        _log_error(root, "ctx_core not importable -- skipping session registration")
        return 0

    try:
        layout = Layout(root)
        knobs = Knobs.load(root)
        board = SessionBoard(
            layout,
            stale_seconds=knobs.session_stale_seconds,
            heartbeat_seconds=knobs.session_heartbeat_seconds,
        )
        try:
            board.heartbeat(session_id)
        except SessionNotFoundError:
            board.register(session_id, f"Claude Code session (source: {source})")

        entries = board.list()
        print(_render_board(entries))
    except Exception:
        _log_error(root, f"session registration failed: {traceback.format_exc()}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
