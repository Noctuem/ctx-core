"""Tests for Module 8 -- the Claude Code plugin layer (`.claude/`).

Three things are exercised here, per the build spec:

1. **Structure** -- the three surfaces (`skills/ctx-pack`, `commands/`,
   `hooks/`) exist and are well-formed (valid Python, no leaked private
   terms).
2. **The directive-trigger lint** (hard requirement): every
   `.claude/skills/*/SKILL.md` and `.claude/commands/*.md` file's
   frontmatter `description` must read as a directive "Use when..."
   trigger, never a bare category label. The exact rule, spelled out
   once here rather than left implicit:

       description.strip() must start with "Use when" or "Use this when"
       (case-sensitive, checked against the literal prefix).

   Applied to every skill/command file in the repo, including this
   module's own -- no exemption for "it's obviously fine."
3. **Hook-script behavior** -- run `ctx_eventlog_hook.py` with `python`
   directly (never `ctx` itself, per the build note: the CLI doesn't
   exist yet), against a temp corpus, asserting:
   - it appends a verifiable event-log line when `ctx_core` is
     importable in the subprocess's environment, and
   - it fails open (exit 0, no crash, no event written) when `ctx_core`
     is NOT importable there -- the two cases share the same script, and
     only the environment differs, so this is a same-code/
     different-deployment control rather than a predicted mutation.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_DIR = REPO_ROOT / ".claude"
HOOK_SCRIPT = CLAUDE_DIR / "hooks" / "ctx_eventlog_hook.py"

# Terms that must never appear inside the tracked, public plugin surface
# -- see the project CLAUDE.md guard ("never commit the repo owner's
# personal name or absolute local paths").
FORBIDDEN_SUBSTRINGS = ("caleb", "noctuem_vault", "\\users\\caleb", "/users/caleb")

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


def test_claude_readme_exists_and_describes_all_three_surfaces():
    path = CLAUDE_DIR / "README.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "skills/ctx-pack" in text
    assert "commands/ctx-doctor.md" in text
    assert "hooks/ctx_eventlog_hook.py" in text


def test_hook_script_is_valid_python():
    source = HOOK_SCRIPT.read_text(encoding="utf-8")
    ast.parse(source)  # raises SyntaxError if malformed


def test_hook_script_never_reaches_across_the_plugin_engine_boundary():
    """Interface contract: read ctx_core via the installed package /
    console entry point, never a relative `sys.path` reach-around into a
    sibling source tree. A `sys.path.insert` here would be exactly that
    anti-pattern -- this script relies on ordinary package import only."""
    source = HOOK_SCRIPT.read_text(encoding="utf-8")
    assert "from ctx_core" in source or "import ctx_core" in source
    assert "sys.path.insert" not in source
    assert "sys.path.append" not in source


@pytest.mark.parametrize(
    "path",
    [
        CLAUDE_DIR / "skills" / "ctx-pack" / "SKILL.md",
        CLAUDE_DIR / "commands" / "ctx-doctor.md",
        CLAUDE_DIR / "hooks" / "ctx_eventlog_hook.py",
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


def _run_hook(payload: dict, *, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
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
