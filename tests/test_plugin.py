"""Tests for Module 8 -- the Claude Code plugin layer (`.claude/`) --
extended in v0.2 (m14) for the three new session-board hooks and the
`ctx-analyze` skill.

Things exercised here, per the build spec:

1. **Structure** -- every surface (`skills/ctx-pack`, `skills/ctx-analyze`,
   `commands/`, `hooks/`) exists and is well-formed (valid Python, no
   leaked private terms).
2. **The directive-trigger lint** (hard requirement): every
   `.claude/skills/*/SKILL.md` and `.claude/commands/*.md` file's
   frontmatter `description` must read as a directive "Use when..."
   trigger, never a bare category label. The exact rule, spelled out
   once here rather than left implicit:

       description.strip() must start with "Use when" or "Use this when"
       (case-sensitive, checked against the literal prefix).

   Applied to every skill/command file in the repo, including this
   module's own -- no exemption for "it's obviously fine."
3. **Hook-script behavior** -- run each hook script with `python` directly
   (never `ctx` itself), against a temp corpus:
   - `ctx_eventlog_hook.py` -- appends a verifiable event-log line when
     `ctx_core` is importable; fails open (exit 0, no event written) when
     it isn't.
   - `ctx_session_start_hook.py` -- registers/heartbeats this session on
     the board and prints it; fails open the same way.
   - `ctx_session_guard_hook.py` -- denies (exit 2, naming the claiming
     session) a write into a DIFFERENT live session's claim; permits
     (exit 0) a write into the session's OWN claim, a claim that's gone
     stale, and every fail-open case (ctx-core not importable, a
     malformed payload). Foreign-claim vs. own-claim vs. stale-claim vs.
     engine-absent are a same-code/different-deployment control, never a
     predicted mutation.
   - `ctx_session_end_hook.py` -- releases the session's claim and writes
     fresh stats products; fails open the same way.
4. **`ctx-analyze` skill** -- the directive-trigger lint above already
   covers it via the glob-driven parametrization; a dedicated test also
   asserts the explicit "never read the raw event log" prohibition the
   build spec's verification checklist greps for is actually in the body.
"""
from __future__ import annotations

import ast
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_DIR = REPO_ROOT / ".claude"
HOOK_SCRIPT = CLAUDE_DIR / "hooks" / "ctx_eventlog_hook.py"
SESSION_START_HOOK_SCRIPT = CLAUDE_DIR / "hooks" / "ctx_session_start_hook.py"
SESSION_GUARD_HOOK_SCRIPT = CLAUDE_DIR / "hooks" / "ctx_session_guard_hook.py"
SESSION_END_HOOK_SCRIPT = CLAUDE_DIR / "hooks" / "ctx_session_end_hook.py"
ANALYZE_SKILL = CLAUDE_DIR / "skills" / "ctx-analyze" / "SKILL.md"

# Terms that must never appear inside the tracked, public plugin surface
# -- see the project CLAUDE.md guard ("never commit the repo owner's
# personal name or absolute local paths"). Base64-encoded here so the
# guarded literal never appears verbatim in this tracked file itself --
# a leak-guard that spells out the thing it guards would defeat its own
# purpose the moment this file is public. Decoded once at import time;
# the check below still operates on the real substrings.
_ENCODED_FORBIDDEN_SUBSTRINGS = (
    "Y2FsZWI=",
    "bm9jdHVlbV92YXVsdA==",
    "XHVzZXJzXGNhbGVi",
    "L3VzZXJzL2NhbGVi",
)
FORBIDDEN_SUBSTRINGS = tuple(
    base64.b64decode(term).decode("ascii") for term in _ENCODED_FORBIDDEN_SUBSTRINGS
)

DIRECTIVE_PREFIXES = ("Use when", "Use this when")


# --------------------------------------------------------------------------
# Frontmatter parsing (test-only helper -- these files carry simple flat
# `key: value` frontmatter, no nested structures, so a full YAML parser
# would be more machinery than the job needs).
# --------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        body.append(line)
    else:
        return {}  # no closing '---' -- not valid frontmatter

    result: dict[str, str] = {}
    for line in body:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def _is_directive_trigger(description: str) -> bool:
    return description.strip().startswith(DIRECTIVE_PREFIXES)


def _skill_and_command_files() -> list[Path]:
    files: list[Path] = []
    skills_dir = CLAUDE_DIR / "skills"
    if skills_dir.is_dir():
        files.extend(sorted(skills_dir.glob("*/SKILL.md")))
    commands_dir = CLAUDE_DIR / "commands"
    if commands_dir.is_dir():
        files.extend(sorted(commands_dir.glob("*.md")))
    return files


# --------------------------------------------------------------------------
# Structure tests
# --------------------------------------------------------------------------

def test_ctx_pack_skill_exists():
    path = CLAUDE_DIR / "skills" / "ctx-pack" / "SKILL.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "ctx pack" in text


def test_ctx_doctor_command_exists():
    path = CLAUDE_DIR / "commands" / "ctx-doctor.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "ctx doctor" in text


def test_hook_script_and_readme_exist():
    assert HOOK_SCRIPT.is_file()
    readme = CLAUDE_DIR / "hooks" / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    # The settings.json wiring snippet must actually be documented, with
    # the pieces a domain repo needs to copy verbatim.
    assert "PostToolUse" in text
    assert "matcher" in text
    assert "ctx_eventlog_hook.py" in text


def test_session_hooks_and_readme_exist():
    assert SESSION_START_HOOK_SCRIPT.is_file()
    assert SESSION_GUARD_HOOK_SCRIPT.is_file()
    assert SESSION_END_HOOK_SCRIPT.is_file()
    text = (CLAUDE_DIR / "hooks" / "README.md").read_text(encoding="utf-8")
    assert "SessionStart" in text
    assert "PreToolUse" in text
    assert "SessionEnd" in text
    assert "ctx_session_start_hook.py" in text
    assert "ctx_session_guard_hook.py" in text
    assert "ctx_session_end_hook.py" in text


def test_ctx_analyze_skill_exists_and_reads_stats_products_only():
    assert ANALYZE_SKILL.is_file()
    text = ANALYZE_SKILL.read_text(encoding="utf-8")
    assert "var/stats/summary.md" in text or "var/stats/" in text
    # The build spec's own verification checklist greps this skill's body
    # for the prohibition -- assert it is actually there, not just implied.
    assert "events.jsonl" in text
    normalized = " ".join(text.split())  # collapse markdown line-wrapping
    assert "Never read the raw event log" in normalized


def test_claude_readme_exists_and_describes_every_surface():
    path = CLAUDE_DIR / "README.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "skills/ctx-pack" in text
    assert "skills/ctx-analyze" in text
    assert "commands/ctx-doctor.md" in text
    assert "hooks/ctx_eventlog_hook.py" in text
    assert "hooks/ctx_session_start_hook.py" in text
    assert "hooks/ctx_session_guard_hook.py" in text
    assert "hooks/ctx_session_end_hook.py" in text


@pytest.mark.parametrize(
    "script",
    [HOOK_SCRIPT, SESSION_START_HOOK_SCRIPT, SESSION_GUARD_HOOK_SCRIPT, SESSION_END_HOOK_SCRIPT],
)
def test_hook_script_is_valid_python(script: Path):
    source = script.read_text(encoding="utf-8")
    ast.parse(source)  # raises SyntaxError if malformed


@pytest.mark.parametrize(
    "script",
    [HOOK_SCRIPT, SESSION_START_HOOK_SCRIPT, SESSION_GUARD_HOOK_SCRIPT, SESSION_END_HOOK_SCRIPT],
)
def test_hook_script_never_reaches_across_the_plugin_engine_boundary(script: Path):
    """Interface contract: read ctx_core via the installed package /
    console entry point, never a relative `sys.path` reach-around into a
    sibling source tree. A `sys.path.insert` here would be exactly that
    anti-pattern -- every hook script relies on ordinary package import
    only."""
    source = script.read_text(encoding="utf-8")
    assert "from ctx_core" in source or "import ctx_core" in source
    assert "sys.path.insert" not in source
    assert "sys.path.append" not in source


@pytest.mark.parametrize(
    "path",
    [
        CLAUDE_DIR / "skills" / "ctx-pack" / "SKILL.md",
        CLAUDE_DIR / "skills" / "ctx-analyze" / "SKILL.md",
        CLAUDE_DIR / "commands" / "ctx-doctor.md",
        CLAUDE_DIR / "hooks" / "ctx_eventlog_hook.py",
        CLAUDE_DIR / "hooks" / "ctx_session_start_hook.py",
        CLAUDE_DIR / "hooks" / "ctx_session_guard_hook.py",
        CLAUDE_DIR / "hooks" / "ctx_session_end_hook.py",
        CLAUDE_DIR / "hooks" / "README.md",
        CLAUDE_DIR / "README.md",
    ],
)
def test_no_leaked_private_terms(path: Path):
    text = path.read_text(encoding="utf-8").lower()
    for term in FORBIDDEN_SUBSTRINGS:
        assert term not in text, f"{path} leaks forbidden term {term!r}"


# --------------------------------------------------------------------------
# The directive-trigger lint (hard requirement)
# --------------------------------------------------------------------------

def test_every_skill_and_command_has_a_directive_use_when_description():
    files = _skill_and_command_files()
    # Guard against a vacuously-true lint: this must actually be
    # exercising files, not passing because the glob found nothing.
    assert len(files) >= 2, "expected at least the ctx-pack skill and the ctx-doctor command"

    failures: list[str] = []
    for path in files:
        frontmatter = _parse_frontmatter(path.read_text(encoding="utf-8"))
        description = frontmatter.get("description")
        if not description:
            failures.append(f"{path}: no frontmatter `description` field")
            continue
        if not _is_directive_trigger(description):
            failures.append(
                f"{path}: description does not start with "
                f"{DIRECTIVE_PREFIXES!r}: {description!r}"
            )
    assert not failures, "\n".join(failures)


def test_directive_lint_actually_discriminates(tmp_path: Path):
    """A lint that only ever passes is not known to test anything (per
    me-code.md). Prove the same parsing + check helpers used above reject
    a bare category-label description, on a fixture file the real
    corpus never sees."""
    bad = tmp_path / "SKILL.md"
    bad.write_text(
        "---\n"
        "name: some-skill\n"
        "description: Packs context for a task.\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    frontmatter = _parse_frontmatter(bad.read_text(encoding="utf-8"))
    assert not _is_directive_trigger(frontmatter["description"])

    good = tmp_path / "GOOD.md"
    good.write_text(
        "---\n"
        "description: Use when a task needs packing.\n"
        "---\n",
        encoding="utf-8",
    )
    frontmatter_good = _parse_frontmatter(good.read_text(encoding="utf-8"))
    assert _is_directive_trigger(frontmatter_good["description"])


# --------------------------------------------------------------------------
# Hook-script behavior (run with `python` directly, never `ctx`)
# --------------------------------------------------------------------------

def _make_corpus(root: Path) -> None:
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "notes").mkdir(parents=True, exist_ok=True)


def _run_hook(
    payload: dict, *, cwd: Path, env: dict, script: Path = HOOK_SCRIPT
) -> subprocess.CompletedProcess:
    # `-S` skips the interpreter's automatic `site` import -- which is what
    # processes site-packages' `.pth` files, including the one an editable
    # `pip install -e .` registers for ctx_core. Without it, an installed
    # ctx-core is importable via site-packages regardless of PYTHONPATH, so
    # the "engine not importable" test below could no longer simulate
    # absence by stripping PYTHONPATH alone. `-S` leaves PYTHONPATH itself
    # untouched (unlike `-I`, which also blanks it), so the "engine
    # importable" test's `PYTHONPATH=REPO_ROOT` still works, and ctx-core
    # has zero third-party dependencies (see pyproject.toml), so nothing
    # the hook needs lives in site-packages to begin with.
    return subprocess.run(
        [sys.executable, "-S", str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=30,
    )


def test_hook_appends_event_when_engine_importable(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)

    # Make ctx_core importable in the subprocess by putting the repo root
    # on PYTHONPATH (ctx-core has no console entry point yet -- m10 --
    # so this is the realistic "engine present" deployment for now).
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)

    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "session_id": "test-session-1",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr

    events_path = corpus / "var" / "log" / "events.jsonl"
    assert events_path.is_file(), "expected an event-log line to be appended"

    lines = [ln for ln in events_path.read_text(encoding="utf-8").split("\n") if ln.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "hook_post_tool_use"
    assert record["payload"]["tool_name"] == "Bash"
    assert record["payload"]["session_id"] == "test-session-1"

    # The chain itself must actually validate -- not just "a file exists".
    from ctx_core.events import EventLog

    ok, first_break = EventLog(events_path).verify()
    assert ok, f"chain broke at line {first_break}"


def test_hook_heartbeats_an_already_registered_session(tmp_path: Path):
    """Automatic heartbeat (TODO Medium finding): a `PostToolUse` firing
    for a session that's already on the board must refresh its
    `heartbeat_at` -- this is what keeps a long-lived session off the
    stale sweep without any user action."""
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    from ctx_core.layout import Layout
    from ctx_core.sessions import SessionBoard

    board = SessionBoard(Layout(corpus))
    board.register("hb-session-1", "a long-lived session")
    before = json.loads(
        (corpus / "var" / "sessions" / "live" / "hb-session-1.json").read_text(encoding="utf-8")
    )["heartbeat_at"]

    import time as _time

    _time.sleep(0.01)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "session_id": "hb-session-1",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    after = json.loads(
        (corpus / "var" / "sessions" / "live" / "hb-session-1.json").read_text(encoding="utf-8")
    )["heartbeat_at"]
    assert after != before


def test_hook_heartbeat_tolerates_an_unregistered_session(tmp_path: Path):
    """A `session_id` the board has never seen (SessionStart hook not
    wired, or a fresh board) must not turn the event append into a
    failure -- `SessionNotFoundError` is swallowed."""
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "session_id": "never-registered",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert not (corpus / "var" / "sessions" / "live" / "never-registered.json").exists()
    # the event itself still landed -- heartbeat failure never blocks it
    events_path = corpus / "var" / "log" / "events.jsonl"
    assert events_path.is_file()


def test_hook_fails_open_when_engine_not_importable(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)

    # Deliberately deny ctx_core: no repo root on PYTHONPATH, and run from
    # an isolated cwd that has nothing importable in it either.
    isolated_cwd = tmp_path / "isolated"
    isolated_cwd.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path / "definitely-empty")

    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "session_id": "test-session-2",
    }
    result = _run_hook(payload, cwd=isolated_cwd, env=env)

    assert result.returncode == 0, result.stderr

    events_path = corpus / "var" / "log" / "events.jsonl"
    assert not events_path.exists(), "should not have written an event with no engine"

    error_log = corpus / "var" / "log" / "ctx-hook-errors.log"
    assert error_log.is_file()
    assert "not importable" in error_log.read_text(encoding="utf-8")


def test_hook_fails_open_on_malformed_stdin(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input="not valid json {{{",
        capture_output=True,
        text=True,
        cwd=str(corpus),
        env=env,
        timeout=30,
    )
    # Malformed input has no `cwd` hint, so the hook falls back to its own
    # process cwd (`corpus`, which does look like a corpus) -- it should
    # still degrade to "no usable payload fields" without crashing.
    assert result.returncode == 0, result.stderr


def test_hook_fails_open_on_empty_stdin(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input="",
        capture_output=True,
        text=True,
        cwd=str(corpus),
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_hook_falls_back_to_process_cwd_when_no_corpus_found(tmp_path: Path):
    """A hook fired somewhere that isn't a ctx-core corpus at all (no
    `core/`/`notes/` anywhere up the tree) must still degrade gracefully
    -- not crash -- even though there is nowhere sensible to log to."""
    bare_dir = tmp_path / "not-a-corpus"
    bare_dir.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)

    payload = {"cwd": str(bare_dir), "hook_event_name": "PostToolUse", "tool_name": "Read"}
    result = _run_hook(payload, cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# ctx_session_start_hook.py (SessionStart)
# --------------------------------------------------------------------------


def test_session_start_hook_registers_and_prints_the_board(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)

    payload = {
        "cwd": str(corpus),
        "hook_event_name": "SessionStart",
        "session_id": "start-session-1",
        "source": "startup",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env, script=SESSION_START_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert "start-session-1" in result.stdout

    live_file = corpus / "var" / "sessions" / "live" / "start-session-1.json"
    assert live_file.is_file()
    record = json.loads(live_file.read_text(encoding="utf-8"))
    assert record["session_id"] == "start-session-1"


def test_session_start_hook_fails_open_when_engine_not_importable(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    isolated_cwd = tmp_path / "isolated"
    isolated_cwd.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path / "definitely-empty")

    payload = {
        "cwd": str(corpus),
        "hook_event_name": "SessionStart",
        "session_id": "start-session-2",
        "source": "startup",
    }
    result = _run_hook(payload, cwd=isolated_cwd, env=env, script=SESSION_START_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert not (corpus / "var" / "sessions" / "live" / "start-session-2.json").exists()
    error_log = corpus / "var" / "log" / "ctx-hook-errors.log"
    assert error_log.is_file()
    assert "not importable" in error_log.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# ctx_session_guard_hook.py (PreToolUse) -- deny/permit/fail-open
# --------------------------------------------------------------------------


def _register_and_claim(corpus: Path, session_id: str, intent: str, paths: list[str], *, stale_seconds: float = 1800.0):
    from ctx_core.layout import Layout
    from ctx_core.sessions import SessionBoard

    board = SessionBoard(Layout(corpus), stale_seconds=stale_seconds)
    board.register(session_id, intent)
    board.claim(session_id, paths)
    return board


def test_guard_denies_write_into_a_different_sessions_claim(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    _register_and_claim(corpus, "session-a", "building the sessions module", ["notes/composting"])

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(corpus / "notes" / "composting" / "file.md")},
        "session_id": "session-b",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env, script=SESSION_GUARD_HOOK_SCRIPT)

    assert result.returncode == 2
    assert "session-a" in result.stderr
    assert "building the sessions module" in result.stderr


def test_guard_permits_write_into_the_sessions_own_claim(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    _register_and_claim(corpus, "session-a", "building the sessions module", ["notes/composting"])

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(corpus / "notes" / "composting" / "file.md")},
        "session_id": "session-a",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env, script=SESSION_GUARD_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr


def test_guard_permits_write_into_a_now_stale_claim(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    (corpus / ".ctxrc.toml").write_text("session_stale_seconds = 0.05\n", encoding="utf-8")
    _register_and_claim(
        corpus, "session-a", "a crashed build", ["notes/composting"], stale_seconds=0.05
    )
    import time as _time

    _time.sleep(0.2)  # let session-a's claim go stale

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(corpus / "notes" / "composting" / "file.md")},
        "session_id": "session-b",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env, script=SESSION_GUARD_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr


def test_guard_fails_open_when_engine_not_importable(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    isolated_cwd = tmp_path / "isolated"
    isolated_cwd.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path / "definitely-empty")

    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(corpus / "notes" / "anything.md")},
        "session_id": "some-session",
    }
    result = _run_hook(payload, cwd=isolated_cwd, env=env, script=SESSION_GUARD_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr


def test_guard_permits_non_write_tools_without_checking_the_board(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
        "session_id": "some-session",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env, script=SESSION_GUARD_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr


def test_guard_permits_write_outside_the_corpus(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    outside = tmp_path / "elsewhere.md"
    payload = {
        "cwd": str(corpus),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(outside)},
        "session_id": "some-session",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env, script=SESSION_GUARD_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# ctx_session_end_hook.py (SessionEnd / Stop)
# --------------------------------------------------------------------------


def test_session_end_hook_releases_claim_and_writes_stats_products(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    _register_and_claim(corpus, "end-session-1", "wrapping up", ["notes/x"])

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    payload = {
        "cwd": str(corpus),
        "hook_event_name": "SessionEnd",
        "session_id": "end-session-1",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env, script=SESSION_END_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert not (corpus / "var" / "sessions" / "live" / "end-session-1.json").exists()
    assert (corpus / "var" / "sessions" / "history" / "end-session-1.json").is_file()
    assert (corpus / "var" / "stats" / "summary.json").is_file()
    assert (corpus / "var" / "stats" / "summary.md").is_file()


def test_session_end_hook_tolerates_an_unregistered_session_id(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    payload = {
        "cwd": str(corpus),
        "hook_event_name": "SessionEnd",
        "session_id": "never-registered",
    }
    result = _run_hook(payload, cwd=tmp_path, env=env, script=SESSION_END_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert (corpus / "var" / "stats" / "summary.json").is_file()


def test_session_end_hook_fails_open_when_engine_not_importable(tmp_path: Path):
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    isolated_cwd = tmp_path / "isolated"
    isolated_cwd.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path / "definitely-empty")

    payload = {
        "cwd": str(corpus),
        "hook_event_name": "SessionEnd",
        "session_id": "some-session",
    }
    result = _run_hook(payload, cwd=isolated_cwd, env=env, script=SESSION_END_HOOK_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert not (corpus / "var" / "stats" / "summary.json").exists()
    error_log = corpus / "var" / "log" / "ctx-hook-errors.log"
    assert error_log.is_file()
    assert "not importable" in error_log.read_text(encoding="utf-8")
