"""Stub `ctx-yield` binary for `tests/test_yield_bridge.py`.

Never the real network, never a real ctx-yield install — this fixture IS
the install, for test purposes. It mimics just enough of `ctx-yield scan
--json`'s real output shape (see the sibling repo's `ctx_yield/cli.py`
`build_report()`) for `yield_bridge._parse_report` to exercise its real
parsing path.

One script covers every scenario `test_yield_bridge.py` drives — clean,
findings, and malformed — selected by the `FAKE_CTX_YIELD_MODE`
environment variable, so tests don't need a family of near-identical stub
scripts (and the two platform shims next to this file both just delegate
here).
"""

from __future__ import annotations

import json
import os
import sys
import time


def _report(records: list[dict], exact: bool) -> dict:
    total_tokens = sum(r["tokens"] for r in records)
    never_recalled_count = sum(1 for r in records if r["never_recalled"])
    return {
        "meta": {
            "root": ".",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "sessions": 30,
            "model": "test-model",
            "exact": exact,
        },
        "records": records,
        "totals": {
            "total_tokens": total_tokens,
            "file_count": len(records),
            "never_recalled_count": never_recalled_count,
            "budget": None,
            "budget_exceeded": False,
        },
        "growth": {"is_git_repo": False},
    }


def main() -> int:
    mode = os.environ.get("FAKE_CTX_YIELD_MODE", "clean")
    args = sys.argv[1:]
    exact = "--exact" in args

    if mode == "hang":
        # Sleeps well past any short test timeout so the caller's
        # timeout-and-kill path actually gets exercised.
        time.sleep(60)
        return 0

    if mode == "malformed":
        sys.stdout.write("{not valid json")
        return 0

    if mode == "bad-shape":
        # Valid JSON, but not the shape `_parse_report` reads (no
        # "records" key) -- simulates an incompatible ctx-yield version.
        sys.stdout.write(json.dumps({"meta": {}, "totals": {}}))
        return 0

    if mode == "findings":
        records = [
            {
                "path": "core/card.md",
                "rel_path": "core/card.md",
                "kind": "core",
                "source": "test",
                "bytes": 100,
                "tokens": 42,
                "token_method": "heuristic",
                "error_pct": 0.0,
                "recall_count": 0,
                "last_recalled": None,
                "never_recalled": True,
                "growth_flagged": False,
                "growth_ratio": None,
            },
            {
                "path": "core/map.md",
                "rel_path": "core/map.md",
                "kind": "core",
                "source": "test",
                "bytes": 200,
                "tokens": 88,
                "token_method": "heuristic",
                "error_pct": 0.0,
                "recall_count": 5,
                "last_recalled": "2026-01-01T00:00:00+00:00",
                "never_recalled": False,
                "growth_flagged": False,
                "growth_ratio": None,
            },
        ]
        sys.stdout.write(json.dumps(_report(records, exact)))
        return 0

    # mode == "clean" (default)
    sys.stdout.write(json.dumps(_report([], exact)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
