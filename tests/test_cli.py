"""Tests for `ctx_core.cli` -- the `ctx` entry point.

Two tiers, per the build spec's "verify the real entry point" rule (a bare
in-process call is not sufficient -- see `me-code.md`'s `python -m
iris.server` lesson):

- **Fast tier** (always runs): calls `ctx_core.cli.main()` in-process,
  capturing stdout/stderr via `capsys`. Covers CLI-owned logic (argument
  wiring, output formatting, exit codes) for every subcommand.
- **Subprocess tier** (`test_subprocess_*`): drives the real, installed
  entry point out-of-process -- `python -m ctx_core.cli` for the bulk of
  subcommands, plus one dedicated test invoking the literal `ctx` console
  script `pyproject.toml` installs, proving the `[project.scripts]` wiring
  itself. Requires `pip install -e .` to have been run in this checkout's
  venv first (see README). Skipped when `CTX_CORE_FAST=1` is set in the
  environment -- the fast tier alone is enough for the edit loop; the full
  suite (this file included) is the merge gate.

Every corpus a test touches is a fresh `tmp_path` copy of `template/` (or a
minimal hand-built tree) -- no test ever mutates this repo's own files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ctx_core.cli import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK, main
from ctx_core.layout import Layout

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "template"
FAKE_CTX_YIELD_DIR = REPO_ROOT / "tests" / "fixtures" / "fake-ctx-yield"

SAMPLE_ANSWERS = {
    "name": "Ada",
    "working_style": "iterative, likes short feedback loops",
    "communication_preferences": "direct, low ceremony",
    "domain_purpose": "a personal research notebook",
}

# The fast-tier/subprocess-tier split the build spec asks for: set
# CTX_CORE_FAST=1 to skip every subprocess-driven test in this file (the
# in-process tier alone stays fast enough for the edit loop). Full suite
# (this file with the env var unset) is the merge gate -- see README's
# "Running the tests" section.
FAST_MODE = os.environ.get("CTX_CORE_FAST") == "1"
requires_subprocess = pytest.mark.skipif(
    FAST_MODE, reason="CTX_CORE_FAST=1 skips the subprocess CLI tier"
)


# --- shared fixtures / helpers --------------------------------------------


def _fresh_corpus(tmp_path: Path, name: str = "corpus") -> Path:
    """A fresh, isolated corpus: a copy of `template/`, never the repo's
    own tree."""
    root = tmp_path / name
    shutil.copytree(TEMPLATE_DIR, root)
    return root


def _add_note(root: Path, rel_path: str, body: str) -> Path:
    note_path = root / rel_path
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(body, encoding="utf-8", newline="\n")
    return note_path


def _write_answers_file(tmp_path: Path, answers: dict[str, str]) -> Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    return path


def _isolated_home_env(home_dir: Path) -> dict[str, str]:
    """A subprocess environment whose `Path.home()` resolves to `home_dir`
    -- so a subprocess-driven `ctx domains` test never touches the real
    developer machine's `~/.ctx-core/domains.json`."""
    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    return env


def run_module_cli(
    args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Drive the real entry point out-of-process via `python -m
    ctx_core.cli` -- the documented PATH-collision fallback, and just as
    much "the shipped CLI" as the `ctx` console script (same `main()`)."""
    return subprocess.run(
        [sys.executable, "-m", "ctx_core.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- --version -------------------------------------------------------------


def test_version_prints_version_and_exits_zero(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == EXIT_OK
    assert "ctx-core" in capsys.readouterr().out


def test_no_command_prints_help_and_returns_error(capsys: pytest.CaptureFixture) -> None:
    result = main([])
    assert result == EXIT_ERROR
    assert "usage" in capsys.readouterr().out.lower()


# --- pack -------------------------------------------------------------


def test_pack_prints_a_manifest(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = _fresh_corpus(tmp_path)
    _add_note(root, "notes/gardening.md", "Composting kitchen scraps efficiently.\n")

    result = main(["pack", "compost kitchen scraps", "--root", str(root)])

    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert "ctx:manifest: true" in out
    assert "gardening.md" in out


def test_pack_budget_flag_overrides_the_knob(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)

    result = main(["pack", "a task", "--root", str(root), "--budget", "111"])

    assert result == EXIT_OK
    assert "ctx:budget_tokens: 111" in capsys.readouterr().out


def test_pack_note_when_top_ranked_candidate_is_oversized(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    _add_note(root, "notes/huge.md", "compost kitchen scraps " * 200)

    result = main(
        ["pack", "compost kitchen scraps", "--root", str(root), "--budget", "50"]
    )

    assert result == EXIT_OK
    assert "did not fit" in capsys.readouterr().out


def test_pack_report_warns_when_ctx_yield_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    empty_path_dir = tmp_path / "empty-path"
    empty_path_dir.mkdir()
    monkeypatch.setenv("PATH", str(empty_path_dir))

    result = main(["pack", "a task", "--root", str(root), "--report"])

    assert result == EXIT_OK
    assert "ctx-yield" in capsys.readouterr().out


def test_pack_report_lists_dead_weight_from_fake_ctx_yield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    monkeypatch.setenv(
        "PATH", str(FAKE_CTX_YIELD_DIR) + os.pathsep + os.environ.get("PATH", "")
    )
    monkeypatch.setenv("FAKE_CTX_YIELD_MODE", "findings")

    result = main(["pack", "a task", "--root", str(root), "--report"])

    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert "ctx-yield" in out


# --- doctor -------------------------------------------------------------


def test_doctor_exits_zero_on_a_healthy_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)

    result = main(["doctor", "--root", str(root)])

    assert result == EXIT_OK
    assert "OK" in capsys.readouterr().out


def test_doctor_exits_nonzero_and_names_hard_failures_on_a_dangling_stub(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    _add_note(
        root,
        "notes/broken.md",
        "---\nsources: sha256:" + "0" * 64 + "\n---\n\nstub with no archived original\n",
    )

    result = main(["doctor", "--root", str(root)])

    assert result == EXIT_ERROR
    out = capsys.readouterr().out
    assert "HARD FAILURES" in out


# --- archive -------------------------------------------------------------


def test_archive_sweep_never_mutates_notes(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    note_path = _add_note(root, "notes/old.md", "An old note.\n")
    original_bytes = note_path.read_bytes()

    result = main(["archive", "sweep", "--root", str(root)])

    assert result == EXIT_OK
    assert note_path.read_bytes() == original_bytes
    assert "Proposals only" in capsys.readouterr().out
    assert (Layout(root).var / "archive-proposals.md").is_file()


def test_archive_stub_replaces_the_note_with_a_content_addressed_stub(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    note_path = _add_note(root, "notes/big.md", "Original full text goes here.\n")

    result = main(["archive", "stub", "notes/big.md", "--root", str(root)])

    assert result == EXIT_OK
    assert "Archived" in capsys.readouterr().out
    assert note_path.read_text(encoding="utf-8").startswith("---\nsources: sha256:")


def test_archive_stub_on_missing_note_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)

    result = main(["archive", "stub", "notes/does-not-exist.md", "--root", str(root)])

    assert result == EXIT_ERROR
    assert "No such note" in capsys.readouterr().err


# --- init -------------------------------------------------------------


def test_init_noninteractive_writes_profile_from_answers_file(
    tmp_path: Path,
) -> None:
    root = _fresh_corpus(tmp_path)
    answers_path = _write_answers_file(tmp_path, SAMPLE_ANSWERS)

    result = main(["init", "--root", str(root), "--answers", str(answers_path)])

    assert result == EXIT_OK
    profile = (root / "core" / "profile.md").read_text(encoding="utf-8")
    assert "Ada" in profile
    assert "a personal research notebook" in profile


# --- domains -------------------------------------------------------------


def test_domains_register_list_forget_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    domain_root = tmp_path / "a-domain"
    domain_root.mkdir()

    assert main(["domains", "register", "alpha", str(domain_root)]) == EXIT_OK
    capsys.readouterr()

    assert main(["domains", "list"]) == EXIT_OK
    assert "alpha" in capsys.readouterr().out

    assert main(["domains", "forget", "alpha"]) == EXIT_OK
    capsys.readouterr()

    assert main(["domains", "list"]) == EXIT_OK
    assert "No domains registered" in capsys.readouterr().out


def test_domains_forget_unknown_name_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    result = main(["domains", "forget", "does-not-exist"])

    assert result == EXIT_ERROR
    assert "No domain named" in capsys.readouterr().err


# --- sessions (v0.2, m14) -------------------------------------------------


def test_sessions_claim_registers_a_new_session_and_lists_it(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)

    claim_result = main(
        [
            "sessions",
            "claim",
            "sess-1",
            "notes/composting",
            "--intent",
            "build the sessions module",
            "--root",
            str(root),
        ]
    )
    assert claim_result == EXIT_OK
    assert "notes/composting" in capsys.readouterr().out

    list_result = main(["sessions", "list", "--root", str(root)])
    assert list_result == EXIT_OK
    out = capsys.readouterr().out
    assert "sess-1" in out
    assert "build the sessions module" in out


def test_sessions_release_ends_a_claimed_session(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    main(["sessions", "claim", "sess-2", "notes/x", "--root", str(root)])
    capsys.readouterr()

    result = main(["sessions", "release", "sess-2", "--root", str(root)])

    assert result == EXIT_OK
    assert "Released" in capsys.readouterr().out
    assert (Layout(root).var / "sessions" / "history" / "sess-2.json").is_file()


def test_sessions_release_unknown_session_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)

    result = main(["sessions", "release", "does-not-exist", "--root", str(root)])

    assert result == EXIT_ERROR
    assert "No live session" in capsys.readouterr().err


def test_sessions_heartbeat_refreshes_a_claimed_session(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    main(["sessions", "claim", "sess-3", "notes/x", "--root", str(root)])
    capsys.readouterr()
    before = json.loads(
        (Layout(root).var / "sessions" / "live" / "sess-3.json").read_text(encoding="utf-8")
    )["heartbeat_at"]

    result = main(["sessions", "heartbeat", "sess-3", "--root", str(root)])

    assert result == EXIT_OK
    assert "Heartbeat refreshed" in capsys.readouterr().out
    after = json.loads(
        (Layout(root).var / "sessions" / "live" / "sess-3.json").read_text(encoding="utf-8")
    )["heartbeat_at"]
    assert after >= before


def test_sessions_claim_after_expiry_recovers_recorded_intent(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The sweep+reclaim finding: a session claims with an intent, its
    heartbeat goes stale and gets swept, and a later `claim` on the same
    session_id with no `--intent` must recover the intent it had rather
    than silently registering it blank."""
    root = _fresh_corpus(tmp_path)
    knobs_path = root / ".ctxrc.toml"
    knobs_path.write_text("session_stale_seconds = 0.01\n", encoding="utf-8")

    main(
        [
            "sessions",
            "claim",
            "sess-4",
            "notes/x",
            "--intent",
            "a real intent worth keeping",
            "--root",
            str(root),
        ]
    )
    capsys.readouterr()

    import time

    time.sleep(0.05)  # older than session_stale_seconds -- goes stale

    # something else drives the sweep first (claim's own sweep excludes
    # its own session_id, so a bare re-claim wouldn't trigger it) -- a
    # `list` call, another session's activity, or a scheduled sweep all
    # do this in practice; `list` is the simplest to drive here.
    main(["sessions", "list", "--root", str(root)])
    capsys.readouterr()
    assert not (Layout(root).var / "sessions" / "live" / "sess-4.json").exists()

    # re-claim without --intent -- the session_id is gone from live/ by now
    result = main(["sessions", "claim", "sess-4", "notes/x", "--root", str(root)])
    assert result == EXIT_OK
    capsys.readouterr()

    list_result = main(["sessions", "list", "--root", str(root)])
    assert list_result == EXIT_OK
    out = capsys.readouterr().out
    assert "a real intent worth keeping" in out


def test_sessions_claim_explicit_intent_still_wins_over_recovered_one(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    knobs_path = root / ".ctxrc.toml"
    knobs_path.write_text("session_stale_seconds = 0.01\n", encoding="utf-8")

    main(
        [
            "sessions",
            "claim",
            "sess-5",
            "notes/x",
            "--intent",
            "the old intent",
            "--root",
            str(root),
        ]
    )
    capsys.readouterr()

    import time

    time.sleep(0.05)

    main(["sessions", "list", "--root", str(root)])  # drives the sweep
    capsys.readouterr()

    result = main(
        [
            "sessions",
            "claim",
            "sess-5",
            "notes/x",
            "--intent",
            "a brand new intent",
            "--root",
            str(root),
        ]
    )
    assert result == EXIT_OK
    capsys.readouterr()

    list_result = main(["sessions", "list", "--root", str(root)])
    out = capsys.readouterr().out
    assert "a brand new intent" in out
    assert "the old intent" not in out


def test_sessions_heartbeat_unknown_session_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)

    result = main(["sessions", "heartbeat", "does-not-exist", "--root", str(root)])

    assert result == EXIT_ERROR
    assert "No live session" in capsys.readouterr().err


# --- intake (v0.2, m14) ---------------------------------------------------


def test_intake_add_files_a_new_layer_note(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)

    result = main(
        ["intake", "add", "A fresh idea worth filing.", "--source", "project", "--root", str(root)]
    )

    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert "Filed" in out
    assert (root / "notes" / "intake").is_dir()


def test_intake_list_then_route_clears_the_queue(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    main(["intake", "add", "Something new.", "--root", str(root)])
    capsys.readouterr()

    list_result = main(["intake", "list", "--root", str(root)])
    assert list_result == EXIT_OK
    out = capsys.readouterr().out
    assert "something-new" in out or "source=user" in out

    intake_dir = root / "notes" / "intake"
    item_path = next(intake_dir.glob("*.md"))
    rel_item = item_path.relative_to(root).as_posix()

    route_result = main(["intake", "route", rel_item, "--to", "notes", "--root", str(root)])
    assert route_result == EXIT_OK
    assert "Routed" in capsys.readouterr().out

    cleared_list = main(["intake", "list", "--root", str(root)])
    assert cleared_list == EXIT_OK
    assert "No unrouted intake items" in capsys.readouterr().out


def test_intake_route_bad_destination_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    main(["intake", "add", "Something new.", "--root", str(root)])
    capsys.readouterr()
    intake_dir = root / "notes" / "intake"
    item_path = next(intake_dir.glob("*.md"))
    rel_item = item_path.relative_to(root).as_posix()

    result = main(["intake", "route", rel_item, "--to", "nowhere", "--root", str(root)])

    assert result == EXIT_ERROR
    assert "Bad destination" in capsys.readouterr().err


# --- stats (v0.2, m14) -----------------------------------------------------


def test_stats_writes_products_and_prints_a_report(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)
    _add_note(root, "notes/gardening.md", "Composting kitchen scraps efficiently.\n")
    main(["pack", "compost", "--root", str(root)])
    capsys.readouterr()

    result = main(["stats", "--root", str(root)])

    assert result == EXIT_OK
    out = capsys.readouterr().out
    assert "ctx-core stats" in out
    assert "Products written" in out
    assert (Layout(root).var / "stats" / "summary.md").is_file()
    assert (Layout(root).var / "stats" / "summary.json").is_file()


def test_stats_since_flag_overrides_the_knob_default(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _fresh_corpus(tmp_path)

    result = main(["stats", "--since", "3", "--root", str(root)])

    assert result == EXIT_OK
    summary = json.loads((Layout(root).var / "stats" / "summary.json").read_text(encoding="utf-8"))
    assert summary["window"]["since_days"] == 3


# --- UTF-8 stdio (TODO Medium: Windows cp1252 mojibake) --------------------


class _StreamWithoutReconfigure:
    """A minimal writable stream with no `.reconfigure` -- stands in for
    an older Python, or any file-like object that doesn't offer it.
    `main()` must tolerate this rather than crash on `AttributeError`."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, s: str) -> int:
        self.written.append(s)
        return len(s)

    def flush(self) -> None:
        pass


def test_main_does_not_crash_when_stdio_lacks_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", _StreamWithoutReconfigure())
    monkeypatch.setattr(sys, "stderr", _StreamWithoutReconfigure())

    result = main([])  # no subcommand -- prints help, returns EXIT_ERROR

    assert result == EXIT_ERROR


def test_main_reconfigures_stdio_to_utf8_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    fake_out = MagicMock()
    fake_err = MagicMock()
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", fake_err)

    main([])

    fake_out.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
    fake_err.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


# --- graceful interrupt -------------------------------------------------


def test_keyboard_interrupt_exits_130_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    import ctx_core.cli as cli_module

    def _raise_interrupt(_args: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "_cmd_doctor", _raise_interrupt)

    result = main(["doctor", "--root", str(tmp_path)])

    assert result == EXIT_INTERRUPTED
    assert "Interrupted" in capsys.readouterr().err


# --- subprocess tier: the real, shipped entry point -----------------------


@requires_subprocess
def test_subprocess_version() -> None:
    result = run_module_cli(["--version"])
    assert result.returncode == EXIT_OK
    assert "ctx-core" in result.stdout


@requires_subprocess
def test_subprocess_pack(tmp_path: Path) -> None:
    root = _fresh_corpus(tmp_path)
    _add_note(root, "notes/gardening.md", "Composting kitchen scraps efficiently.\n")

    result = run_module_cli(["pack", "compost kitchen scraps", "--root", str(root)])

    assert result.returncode == EXIT_OK
    assert "ctx:manifest: true" in result.stdout


@requires_subprocess
def test_subprocess_doctor(tmp_path: Path) -> None:
    root = _fresh_corpus(tmp_path)
    result = run_module_cli(["doctor", "--root", str(root)])
    assert result.returncode == EXIT_OK
    assert "OK" in result.stdout


@requires_subprocess
def test_subprocess_archive_sweep_and_stub(tmp_path: Path) -> None:
    root = _fresh_corpus(tmp_path)
    note_path = _add_note(root, "notes/big.md", "Original full text goes here.\n")

    sweep_result = run_module_cli(["archive", "sweep", "--root", str(root)])
    assert sweep_result.returncode == EXIT_OK

    stub_result = run_module_cli(["archive", "stub", "notes/big.md", "--root", str(root)])
    assert stub_result.returncode == EXIT_OK
    assert note_path.read_text(encoding="utf-8").startswith("---\nsources: sha256:")


@requires_subprocess
def test_subprocess_init_noninteractive(tmp_path: Path) -> None:
    root = _fresh_corpus(tmp_path)
    answers_path = _write_answers_file(tmp_path, SAMPLE_ANSWERS)

    result = run_module_cli(
        ["init", "--root", str(root), "--answers", str(answers_path)]
    )

    assert result.returncode == EXIT_OK
    profile = (root / "core" / "profile.md").read_text(encoding="utf-8")
    assert "Ada" in profile


@requires_subprocess
def test_subprocess_domains_roundtrip(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    domain_root = tmp_path / "a-domain"
    domain_root.mkdir()
    env = _isolated_home_env(home_dir)

    register_result = run_module_cli(
        ["domains", "register", "alpha", str(domain_root)], env=env
    )
    assert register_result.returncode == EXIT_OK

    list_result = run_module_cli(["domains", "list"], env=env)
    assert list_result.returncode == EXIT_OK
    assert "alpha" in list_result.stdout

    forget_result = run_module_cli(["domains", "forget", "alpha"], env=env)
    assert forget_result.returncode == EXIT_OK


@requires_subprocess
def test_subprocess_sessions_claim_list_release(tmp_path: Path) -> None:
    root = _fresh_corpus(tmp_path)

    claim_result = run_module_cli(
        ["sessions", "claim", "sub-sess-1", "notes/x", "--root", str(root)]
    )
    assert claim_result.returncode == EXIT_OK

    list_result = run_module_cli(["sessions", "list", "--root", str(root)])
    assert list_result.returncode == EXIT_OK
    assert "sub-sess-1" in list_result.stdout

    heartbeat_result = run_module_cli(
        ["sessions", "heartbeat", "sub-sess-1", "--root", str(root)]
    )
    assert heartbeat_result.returncode == EXIT_OK
    assert "Heartbeat refreshed" in heartbeat_result.stdout

    release_result = run_module_cli(["sessions", "release", "sub-sess-1", "--root", str(root)])
    assert release_result.returncode == EXIT_OK
    assert (Layout(root).var / "sessions" / "history" / "sub-sess-1.json").is_file()


@requires_subprocess
def test_subprocess_intake_add_list_route(tmp_path: Path) -> None:
    root = _fresh_corpus(tmp_path)

    add_result = run_module_cli(["intake", "add", "A subprocess-filed note.", "--root", str(root)])
    assert add_result.returncode == EXIT_OK

    list_result = run_module_cli(["intake", "list", "--root", str(root)])
    assert list_result.returncode == EXIT_OK
    assert "source=user" in list_result.stdout

    item_path = next((root / "notes" / "intake").glob("*.md"))
    rel_item = item_path.relative_to(root).as_posix()
    route_result = run_module_cli(["intake", "route", rel_item, "--to", "notes", "--root", str(root)])
    assert route_result.returncode == EXIT_OK


@requires_subprocess
def test_subprocess_stats_writes_products(tmp_path: Path) -> None:
    root = _fresh_corpus(tmp_path)

    result = run_module_cli(["stats", "--root", str(root)])

    assert result.returncode == EXIT_OK
    assert "ctx-core stats" in result.stdout
    assert (Layout(root).var / "stats" / "summary.json").is_file()


@requires_subprocess
def test_subprocess_console_script_is_wired_correctly() -> None:
    """Proves the `[project.scripts] ctx = ctx_core.cli:main` wiring
    itself, not just `python -m ctx_core.cli` -- requires `pip install -e
    .` to have been run first (see README)."""
    ctx_script = shutil.which("ctx")
    if ctx_script is None:
        pytest.skip("`ctx` console script not on PATH -- run `pip install -e .` first")

    result = subprocess.run(
        [ctx_script, "--version"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == EXIT_OK
    assert "ctx-core" in result.stdout
