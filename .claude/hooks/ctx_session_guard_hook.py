#!/usr/bin/env python3
"""ctx-core PreToolUse guard hook: blocks a write into a path already
claimed by a DIFFERENT live session on the board (m11), so a claim is
enforceable rather than advisory.

**Fail-open by construction**: this hook only ever DENIES via a deliberate,
narrow path (a real `Conflict` from `SessionBoard.check()`) -- literally
every other outcome, including any internal error, falls through to permit.
That means: ctx-core not importable, a malformed payload, a tool this hook
doesn't recognize, a path outside the corpus, or any unexpected exception
all PERMIT the tool call. A broken guard must never brick a session.

Claude Code's PreToolUse hook protocol (see the Claude Code hooks docs):
exit code 0 permits the tool call; exit code 2 blocks it and feeds stderr
back to the model as the reason. This hook uses exactly those two exit
codes and nothing else.

Interface contract (spec Module 14): read ctx_core via the installed
Python package/console entry point, never a relative import across the
plugin/engine boundary (no `sys.path` reach-around into a sibling source
tree) -- same convention `ctx_eventlog_hook.py` and
`ctx_session_start_hook.py` use.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

EXIT_PERMIT = 0
EXIT_DENY = 2

ERROR_LOG_NAME = "ctx-hook-errors.log"

#: Tool names this guard even looks at -- every other `tool_name` permits
#: immediately, before any ctx_core import is attempted. Bash and other
#: non-path tools are deliberately NOT covered (see done.md): there is no
#: single reliable path field to extract a claim target from.
WRITE_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})

#: `tool_input` keys, checked in order, that name the file this tool call
#: writes to.
_PATH_KEYS = ("file_path", "notebook_path")


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


def _relative_claim_path(target: Path, root: Path) -> str | None:
    """`target`, expressed root-relative with forward slashes, or `None`
    if it doesn't resolve under `root` at all -- a write outside the corpus
    is not a claim this guard has any business blocking."""
    try:
        resolved_target = target if target.is_absolute() else (root / target)
        resolved_target = resolved_target.resolve()
        resolved_root = root.resolve()
        return resolved_target.relative_to(resolved_root).as_posix()
    except (OSError, ValueError):
        return None


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        return EXIT_PERMIT  # can't read stdin -- nothing to check, fail open

    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    tool_name = payload.get("tool_name")
    if tool_name not in WRITE_TOOLS:
        return EXIT_PERMIT  # not a write tool this guard covers -- permit

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return EXIT_PERMIT

    raw_path = None
    for key in _PATH_KEYS:
        if tool_input.get(key):
            raw_path = tool_input[key]
            break
    if not raw_path:
        return EXIT_PERMIT  # no path field this guard recognizes -- permit

    cwd_hint = payload.get("cwd") or "."
    root = _find_corpus_root(Path(cwd_hint))
    session_id = payload.get("session_id") or ""

    try:
        from ctx_core.config import Knobs
        from ctx_core.layout import Layout
        from ctx_core.sessions import SessionBoard
    except Exception:
        _log_error(root, "ctx_core not importable -- guard fails open")
        return EXIT_PERMIT

    try:
        target_rel = _relative_claim_path(Path(raw_path), root)
        if target_rel is None:
            return EXIT_PERMIT  # outside the corpus -- nothing to check

        layout = Layout(root)
        knobs = Knobs.load(root)
        board = SessionBoard(
            layout,
            stale_seconds=knobs.session_stale_seconds,
            heartbeat_seconds=knobs.session_heartbeat_seconds,
        )
        conflict = board.check(target_rel, session_id)
    except Exception:
        _log_error(root, f"guard check failed, permitting: {traceback.format_exc()}")
        return EXIT_PERMIT

    if conflict is None:
        return EXIT_PERMIT

    print(
        f"ctx-core session guard: '{target_rel}' is claimed by session "
        f"'{conflict.session_id}' (intent: {conflict.intent!r}, claim: "
        f"'{conflict.claimed_path}'). Wait for that session to release it, "
        "or coordinate before writing here.",
        file=sys.stderr,
    )
    return EXIT_DENY


if __name__ == "__main__":
    sys.exit(main())
