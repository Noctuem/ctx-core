"""yield_bridge: optional composition with the sibling `ctx-yield` tool.

`ctx-yield` (https://github.com/Noctuem/ctx-yield) answers "is this AI
context system earning its tokens?" by joining a corpus's force-loaded
files against real session-transcript recall. ctx-core composes with it —
shells out to an already-installed `ctx-yield` binary via `subprocess` —
and never imports it as a library or reimplements any of its logic. The
two tools stay independently useful and independently installable.

Absence of the binary is a normal, expected outcome for anyone using
ctx-core without also installing ctx-yield: `yield_scan` returns `None`
rather than raising, and never silently skips the check without saying so
(see `NOT_INSTALLED_HINT`). A `ctx-yield` that IS installed but produced
something `yield_scan` cannot make sense of (unparseable stdout, JSON
missing the fields this module reads) is a THIRD, equally explicit
outcome — see `YieldResult.warning` — distinct from both "not installed"
and a clean "installed, nothing to report" result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Sequence

from .config import Knobs
from .indexing import Entry
from .layout import Layout

# --- named constants (no bare literals below) ----------------------------

#: The binary `shutil.which` looks for. `ctx-yield`'s own installer
#: (`pyproject.toml` `[project.scripts]`) is what puts this on PATH.
YIELD_BINARY_NAME = "ctx-yield"

#: Ceiling on how long one `ctx-yield scan` subprocess is allowed to run
#: before `yield_scan` gives up and reports a timeout outcome. A scan reads
#: a corpus's files plus recent session transcripts off disk — generous
#: enough for a large corpus, short enough that a hung child doesn't hang
#: whatever's calling `yield_scan` (e.g. `ctx pack --report`, m10).
YIELD_SCAN_TIMEOUT_SECONDS = 30.0

#: Suggested wording for the caller (m10's `ctx pack --report`) to surface
#: when `yield_scan` returns `None`. `yield_scan` itself never prints —
#: it's a pure function of its inputs so its result is equally usable by a
#: human-readable report or a `--json` one — but a caller that swallows a
#: `None` without telling the user is exactly the silent no-op the build
#: spec forbids. This constant exists so every caller says the same thing.
NOT_INSTALLED_HINT = (
    f"'{YIELD_BINARY_NAME}' not found on PATH — recall-signal check "
    "skipped. Install it from https://github.com/Noctuem/ctx-yield to "
    "see which force-loaded files are never actually recalled."
)


@dataclass(frozen=True)
class YieldEntry:
    """One file from a `ctx-yield scan --json` report, reduced to the
    fields `yield_bridge` folds onto the packer's index.

    `rel_path` mirrors ctx-yield's own `rel_path` field (POSIX-relative to
    the scanned root) so it can be matched directly against
    `ctx_core.indexing.Entry.path`, which uses the same convention.
    """

    rel_path: str
    tokens: int
    recall_count: int
    never_recalled: bool
    growth_flagged: bool


@dataclass(frozen=True)
class YieldResult:
    """The outcome of one `yield_scan()` call where the binary WAS found
    (a call where it wasn't returns `None` instead — see `yield_scan`).

    - Clean or findings result: `entries` holds one `YieldEntry` per file
      ctx-yield's scan inventoried (possibly empty — a corpus with nothing
      flagged is a legitimate, successful outcome, not a special case).
      `warning` is `None` and `raw` holds the full parsed report dict.
    - Degraded result: ctx-yield ran but its stdout could not be turned
      into `entries` (not valid JSON, or valid JSON missing the shape this
      module reads). `entries` is `[]`, `warning` is a human-readable
      explanation of what went wrong, and `raw` is the parsed JSON when
      parsing got that far (so a caller wanting to inspect the unexpected
      shape doesn't have to re-run anything), else `None`.

    A caller should treat "empty `entries`, `warning` set" as "the check
    did not really run" and "empty `entries`, `warning` is `None`" as "the
    check ran and found nothing to flag" — never conflate the two.
    """

    entries: list[YieldEntry] = field(default_factory=list)
    warning: str | None = None
    raw: dict[str, Any] | None = None


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill `proc` and any children IT spawned (e.g. a platform shim that
    launches a nested interpreter). `Popen.kill()` alone only terminates
    the immediate child — on a multi-hop launch that leaves a grandchild
    running past the timeout `yield_scan` was meant to enforce, which is
    exactly the "spawned processes must not outlive what started them"
    failure mode. `start_new_session` (POSIX) / `CREATE_NEW_PROCESS_GROUP`
    (Windows), set at spawn time in `_run_ctx_yield`, are what make a
    whole-tree kill possible here.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone
    proc.kill()  # belt-and-suspenders for the immediate child either way


def _run_ctx_yield(cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Run `cmd`, killing the whole child process tree on a timeout or
    Ctrl-C rather than leaving anything orphaned (graceful interrupt — see
    me-code conventions). `subprocess.run`'s own `timeout=` kwarg only
    kills the immediate child on `TimeoutExpired`; the explicit `Popen`
    here additionally covers `KeyboardInterrupt` and multi-hop launches.
    """
    popen_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **popen_kwargs
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        _kill_process_tree(proc)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _parse_report(stdout: str) -> YieldResult:
    """Turn one `ctx-yield scan --json` stdout capture into a `YieldResult`.

    Unparseable JSON and JSON that parses but lacks the fields this module
    reads are BOTH the "degraded" outcome (`warning` set, `entries` empty)
    — this is the pragmatic resolution of the build spec's open question
    about not pinning a minimum `ctx-yield` version: rather than guess at
    which shape changes are compatible, any shape this module doesn't
    recognize is reported as its own explicit, non-fatal outcome instead
    of either crashing or being silently swallowed.
    """
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return YieldResult(
            warning=f"ctx-yield produced output that is not valid JSON: {exc}"
        )

    if not isinstance(report, dict):
        return YieldResult(
            warning="ctx-yield's JSON report was not an object at the top level "
            f"(got {type(report).__name__})"
        )

    records = report.get("records")
    if not isinstance(records, list):
        return YieldResult(
            warning="ctx-yield's JSON report has no 'records' list "
            "(unexpected/incompatible ctx-yield version?)",
            raw=report,
        )

    entries: list[YieldEntry] = []
    try:
        for record in records:
            entries.append(
                YieldEntry(
                    rel_path=record["rel_path"],
                    tokens=int(record["tokens"]),
                    recall_count=int(record["recall_count"]),
                    never_recalled=bool(record["never_recalled"]),
                    growth_flagged=bool(record.get("growth_flagged", False)),
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        return YieldResult(
            warning="ctx-yield's JSON report records are missing an expected "
            f"field (unexpected/incompatible ctx-yield version?): {exc}",
            raw=report,
        )

    return YieldResult(entries=entries, raw=report)


def yield_scan(
    layout: Layout, knobs: Knobs | None = None, *, timeout: float | None = None
) -> YieldResult | None:
    """Run `ctx-yield scan --json` against `layout.root` and return its
    result, composed as a subprocess call — never an import of `ctx-yield`
    as a library.

    Returns `None` when `ctx-yield` is not on PATH — distinguished from
    `YieldResult(entries=[])`, which means ctx-yield WAS run and cleanly
    found nothing to flag. See `NOT_INSTALLED_HINT` for what a caller
    should tell the user in the `None` case.

    `knobs.yield_exact` / `knobs.yield_model` (default `False` / `None`)
    pass through to ctx-yield's own `--exact` / `--model` flags — ctx-core
    never reads `ANTHROPIC_API_KEY` itself (that credential belongs to
    ctx-yield's optional exact-tokenizer path); the child process simply
    inherits the parent's environment unchanged, so the variable reaches
    ctx-yield if the user's shell already has it set.

    `timeout` overrides `YIELD_SCAN_TIMEOUT_SECONDS` for one call (mainly
    so tests can exercise the timeout path without a 30-second test).
    """
    binary = shutil.which(YIELD_BINARY_NAME)
    if binary is None:
        return None

    knobs = knobs if knobs is not None else Knobs()
    effective_timeout = (
        timeout if timeout is not None else YIELD_SCAN_TIMEOUT_SECONDS
    )

    cmd = [binary, "scan", "--json", str(layout.root)]
    if knobs.yield_exact:
        cmd.append("--exact")
    if knobs.yield_model:
        cmd.extend(["--model", knobs.yield_model])

    try:
        proc = _run_ctx_yield(cmd, timeout=effective_timeout)
    except subprocess.TimeoutExpired:
        return YieldResult(
            warning=f"ctx-yield scan timed out after {effective_timeout:g}s"
        )
    except OSError as exc:
        # Defensive: shutil.which already confirmed the binary resolves, but
        # it can still fail to actually launch (permissions, a broken shim,
        # a race where it was removed between the which() and the spawn).
        return YieldResult(warning=f"ctx-yield could not be run: {exc}")

    # ctx-yield's own exit code encodes ITS `--budget` gate, which ctx-core
    # never passes — a non-zero exit with a well-formed report is not a
    # failure from this module's point of view, so the exit code is not
    # consulted here. `_parse_report` is what decides success vs. degraded,
    # purely from whether stdout is the shape this module expects.
    return _parse_report(proc.stdout)


def dead_weight_map(
    entries: Sequence[Entry], result: YieldResult
) -> dict[str, YieldEntry]:
    """Fold `result`'s recall signal onto a packer index by path.

    Returns `{entry.path: YieldEntry}` for every `Entry` in `entries` that
    ctx-yield's scan also inventoried (matched by `Entry.path` ==
    `YieldEntry.rel_path` — both are POSIX-relative to the same corpus
    root). An `Entry` ctx-yield never saw — it scans a different, generally
    narrower set of "force-loaded context files" than the packer's whole
    `core/`+`notes/` index — is simply absent from the returned map, never
    an error and never a fabricated zero entry.

    This is intentionally a plain read-side join rather than a mutation of
    `Entry` itself: `Entry` (owned by the `pack` module) has a fixed,
    published field set, and bolting extra attributes onto it from here
    would make `Entry`'s shape depend on whether `yield_bridge` happened to
    run. `ctx pack --report` (m10) is the intended reader — for any `Entry`
    present in this map with `.never_recalled` true, that force-loaded
    file is a dead-weight candidate per ctx-yield's own recall signal.
    """
    by_path = {ye.rel_path: ye for ye in result.entries}
    return {e.path: by_path[e.path] for e in entries if e.path in by_path}
