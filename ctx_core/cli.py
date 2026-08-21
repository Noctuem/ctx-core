"""`ctx`: the command-line entry point wiring every module's subcommand.

Every subcommand below is a thin adapter over an already-built module --
this file owns argument parsing, a corpus root, and exit-code/output
conventions, and nothing else. See the top-level README for what each
subcommand does; see each module's own docstring for how it does it.

Installed as the `ctx` console script (`[project.scripts]` in
pyproject.toml). `python -m ctx_core.cli` is the documented fallback if
`ctx` collides with something already on a user's PATH.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ctx_core import __version__
from ctx_core.archive import PROPOSALS_BASENAME, archive_note
from ctx_core.archive import sweep as run_archive_sweep
from ctx_core.config import Knobs
from ctx_core.doctor import doctor as run_doctor
from ctx_core.domains import (
    DomainAlreadyRegisteredError,
    DomainNotFoundError,
    DomainPathNotFoundError,
    DomainRegistry,
)
from ctx_core.events import EventLog, default_eventlog_path
from ctx_core.init import run_interview
from ctx_core.intake import DEFAULT_SOURCE, VALID_SOURCES
from ctx_core.intake import intake_add as run_intake_add
from ctx_core.intake import intake_list as run_intake_list
from ctx_core.intake import intake_route as run_intake_route
from ctx_core.layout import Layout
from ctx_core.packer import Manifest
from ctx_core.packer import pack as run_pack
from ctx_core.sessions import SessionBoard, SessionNotFoundError
from ctx_core.stats import compute_stats, write_products
from ctx_core.yield_bridge import NOT_INSTALLED_HINT, dead_weight_map, yield_scan

# --- named constants (no bare literals below) ---------------------------

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130

PROG_NAME = "ctx"


# --- shared plumbing ------------------------------------------------------


def _add_root_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Corpus root (default: the current directory).",
    )


def _print_yield_report(layout: Layout, knobs: Knobs, manifest: Manifest) -> None:
    """`ctx pack --report`'s ctx-yield fold-in: dead-weight candidates
    among the entries the manifest actually chose, when `ctx-yield` is
    installed; a plain warning, never a crash, when it isn't or its
    output couldn't be parsed. See `ctx_core.yield_bridge` for the three
    outcomes this mirrors.
    """
    result = yield_scan(layout, knobs)
    if result is None:
        print(f"\nctx-yield: {NOT_INSTALLED_HINT}")
        return
    if result.warning:
        print(f"\nctx-yield warning: {result.warning}")
        return

    dead_weight = dead_weight_map(manifest.entries, result)
    never_recalled = sorted(
        path for path, entry in dead_weight.items() if entry.never_recalled
    )
    if never_recalled:
        print("\nDead-weight candidates (never recalled per ctx-yield):")
        for path in never_recalled:
            print(f"  - {path}")
    else:
        print("\nctx-yield: no dead-weight candidates among the packed entries.")


# --- pack -------------------------------------------------------------


def _cmd_pack(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    knobs = Knobs.load(layout.root)
    manifest = run_pack(
        args.task,
        layout,
        knobs,
        summary=args.summary,
        last=args.last,
        decisions=args.decisions,
        budget=args.budget,
    )
    print(manifest.render(), end="")
    if args.report:
        _print_yield_report(layout, knobs, manifest)
    return EXIT_OK


# --- doctor -------------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    knobs = Knobs.load(layout.root)
    report = run_doctor(layout, knobs)

    if report.hard_failures:
        print("HARD FAILURES:")
        for message in report.hard_failures:
            print(f"  - {message}")
    if report.warnings:
        print("WARNINGS:")
        for message in report.warnings:
            print(f"  - {message}")

    if report.ok:
        suffix = f" ({len(report.warnings)} warning(s))" if report.warnings else ""
        print(f"ctx doctor: OK{suffix}")
    else:
        print(f"ctx doctor: FAILED ({len(report.hard_failures)} hard failure(s))")

    return EXIT_OK if report.ok else EXIT_ERROR


# --- archive -------------------------------------------------------------


def _cmd_archive_sweep(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    knobs = Knobs.load(layout.root)
    proposals = run_archive_sweep(layout, knobs)

    proposals_path = layout.var / PROPOSALS_BASENAME
    print(f"{len(proposals)} archive proposal(s) -- see {proposals_path}")
    for proposal in proposals:
        print(f"  - {proposal.path} ({proposal.age_days:.0f}d old, {proposal.tokens} tokens)")
    print("Proposals only -- nothing has been archived or edited.")
    return EXIT_OK


def _cmd_archive_stub(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    try:
        handle = archive_note(args.path, layout, summary=args.summary)
    except FileNotFoundError:
        print(f"No such note: {args.path}", file=sys.stderr)
        return EXIT_ERROR
    print(f"Archived {args.path} -> {handle}")
    return EXIT_OK


# --- init -------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    knobs = Knobs.load(layout.root)
    event_log = EventLog(default_eventlog_path(layout.root, knobs.eventlog_path))
    if args.answers is not None:
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        run_interview(layout, answers=answers, event_log=event_log)
    else:
        run_interview(layout, answers=None, event_log=event_log)
    return EXIT_OK


# --- domains -------------------------------------------------------------


def _cmd_domains_list(args: argparse.Namespace) -> int:
    registry = DomainRegistry()
    entries = registry.list()
    if not entries:
        print("No domains registered.")
        return EXIT_OK
    for entry in entries:
        print(f"{entry.name}\t{entry.path}\t(engine {entry.engine_version})")
    return EXIT_OK


def _cmd_domains_register(args: argparse.Namespace) -> int:
    registry = DomainRegistry()
    try:
        entry = registry.register(args.name, args.path)
    except DomainAlreadyRegisteredError:
        print(f"'{args.name}' is already registered.", file=sys.stderr)
        return EXIT_ERROR
    except DomainPathNotFoundError as exc:
        print(f"Not an existing directory: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"Registered '{entry.name}' -> {entry.path}")
    return EXIT_OK


def _cmd_domains_forget(args: argparse.Namespace) -> int:
    registry = DomainRegistry()
    try:
        registry.forget(args.name)
    except DomainNotFoundError:
        print(f"No domain named '{args.name}' is registered.", file=sys.stderr)
        return EXIT_ERROR
    print(f"Forgot '{args.name}'.")
    return EXIT_OK


# --- sessions -------------------------------------------------------------


def _session_board(layout: Layout, knobs: Knobs) -> SessionBoard:
    return SessionBoard(
        layout,
        stale_seconds=knobs.session_stale_seconds,
        heartbeat_seconds=knobs.session_heartbeat_seconds,
    )


def _cmd_sessions_list(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    knobs = Knobs.load(layout.root)
    entries = _session_board(layout, knobs).list()
    if not entries:
        print("No sessions recorded.")
        return EXIT_OK
    for e in entries:
        status = "just-expired" if e.stale else "live"
        claims = ", ".join(e.claims) if e.claims else "(none)"
        print(
            f"{e.session_id}\t{status}\tage={e.age_seconds:.0f}s\t"
            f"intent={e.intent!r}\tclaims={claims}"
        )
    return EXIT_OK


def _cmd_sessions_claim(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    knobs = Knobs.load(layout.root)
    board = _session_board(layout, knobs)
    try:
        board.claim(args.session_id, args.paths)
    except SessionNotFoundError:
        # `ctx sessions claim` is the CLI's only way to put a session on the
        # board -- there is no separate `register` subcommand in the v0.2
        # command surface (spec: `ctx sessions list|claim|release`), so an
        # unknown session_id is registered here, using `--intent` if given,
        # rather than making a manual claim a two-step operation.
        #
        # `--intent` empty/omitted: this is very often not a brand-new
        # session but a swept-then-reclaimed one (TODO Medium finding --
        # the sweep+reclaim path used to silently drop the session's
        # recorded intent). Recover it from the newest matching history
        # record before falling back to a blank intent.
        intent = args.intent or board.last_intent(args.session_id) or ""
        board.register(args.session_id, intent)
        board.claim(args.session_id, args.paths)
    print(f"'{args.session_id}' claims: {', '.join(args.paths)}")
    return EXIT_OK


def _cmd_sessions_release(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    knobs = Knobs.load(layout.root)
    board = _session_board(layout, knobs)
    try:
        board.release(args.session_id)
    except SessionNotFoundError:
        print(f"No live session named '{args.session_id}'.", file=sys.stderr)
        return EXIT_ERROR
    print(f"Released '{args.session_id}'.")
    return EXIT_OK


def _cmd_sessions_heartbeat(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    knobs = Knobs.load(layout.root)
    board = _session_board(layout, knobs)
    try:
        board.heartbeat(args.session_id)
    except SessionNotFoundError:
        print(f"No live session named '{args.session_id}'.", file=sys.stderr)
        return EXIT_ERROR
    print(f"Heartbeat refreshed for '{args.session_id}'.")
    return EXIT_OK


# --- intake -------------------------------------------------------------


def _cmd_intake_add(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    path = run_intake_add(layout, args.text, source=args.source, title=args.title)
    print(f"Filed -> {path}")
    return EXIT_OK


def _cmd_intake_route(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    try:
        result = run_intake_route(layout, args.item, args.to)
    except ValueError as exc:
        print(f"Bad destination: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except FileNotFoundError:
        print(f"No such intake item: {args.item}", file=sys.stderr)
        return EXIT_ERROR
    print(f"Routed {args.item} -> {result}")
    return EXIT_OK


def _cmd_intake_list(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    items = run_intake_list(layout)
    if not items:
        print("No unrouted intake items.")
        return EXIT_OK
    for item in items:
        print(
            f"{item.path}\tsource={item.source}\tage={item.age_days:.1f}d\t"
            f"title={item.title!r}"
        )
    return EXIT_OK


# --- stats -------------------------------------------------------------


def _cmd_stats(args: argparse.Namespace) -> int:
    layout = Layout(args.root)
    knobs = Knobs.load(layout.root)
    since_days = args.since if args.since is not None else knobs.stats_window_days
    report = compute_stats(layout, knobs, since_days=since_days)
    paths = write_products(report, layout)
    print(report.render_md(), end="")
    print(f"Products written: {', '.join(str(p) for p in paths)}")
    return EXIT_OK


# --- parser -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG_NAME,
        description=(
            "ctx-core: pack the right context per task under a token budget, "
            "monitor corpus health, and manage context lifecycle."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ctx-core {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    # pack
    pack_parser = subparsers.add_parser(
        "pack", help="Assemble a budgeted context manifest for a task."
    )
    pack_parser.add_argument("task", help="The task the manifest is assembled for.")
    _add_root_option(pack_parser)
    pack_parser.add_argument(
        "--summary", action="store_true", help="Pack a shorter, summary-budget manifest."
    )
    pack_parser.add_argument(
        "--last",
        type=int,
        default=None,
        metavar="N",
        help="Also admit the N most recently modified entries.",
    )
    pack_parser.add_argument(
        "--decisions",
        action="store_true",
        help="Also admit every decision-record entry.",
    )
    pack_parser.add_argument(
        "--budget",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Override the working budget in tokens for this pack only, used "
            "LITERALLY -- not scaled by --summary. Omitted: knob default "
            "(scaled by --summary as usual)."
        ),
    )
    pack_parser.add_argument(
        "--report",
        action="store_true",
        help="Fold in ctx-yield's dead-weight signal, when ctx-yield is installed.",
    )
    pack_parser.set_defaults(handler=_cmd_pack)

    # doctor
    doctor_parser = subparsers.add_parser(
        "doctor", help="Run the corpus health-check gate."
    )
    _add_root_option(doctor_parser)
    doctor_parser.set_defaults(handler=_cmd_doctor)

    # archive
    archive_parser = subparsers.add_parser(
        "archive", help="Content-addressed archiving and lifecycle."
    )
    archive_sub = archive_parser.add_subparsers(dest="archive_action", required=True)

    sweep_parser = archive_sub.add_parser(
        "sweep", help="Propose aging notes for archiving (never applies anything)."
    )
    _add_root_option(sweep_parser)
    sweep_parser.set_defaults(handler=_cmd_archive_sweep)

    stub_parser = archive_sub.add_parser(
        "stub", help="Content-address one note and replace it with a stub."
    )
    stub_parser.add_argument("path", help="Note path, relative to --root or absolute.")
    _add_root_option(stub_parser)
    stub_parser.add_argument(
        "--summary",
        default=None,
        help="Stub summary line (default: a naive truncation of the note).",
    )
    stub_parser.set_defaults(handler=_cmd_archive_stub)

    # init
    init_parser = subparsers.add_parser(
        "init", help="Bootstrap interview -> core/profile.md."
    )
    _add_root_option(init_parser)
    init_parser.add_argument(
        "--answers",
        type=Path,
        default=None,
        metavar="FILE",
        help="Non-interactive: a JSON file of answers (see README).",
    )
    init_parser.set_defaults(handler=_cmd_init)

    # domains
    domains_parser = subparsers.add_parser(
        "domains", help="Local, opt-in registry of known domain repos."
    )
    domains_sub = domains_parser.add_subparsers(dest="domains_action", required=True)

    list_parser = domains_sub.add_parser("list", help="List registered domains.")
    list_parser.set_defaults(handler=_cmd_domains_list)

    register_parser = domains_sub.add_parser(
        "register", help="Register a domain repo by name and path."
    )
    register_parser.add_argument("name")
    register_parser.add_argument("path")
    register_parser.set_defaults(handler=_cmd_domains_register)

    forget_parser = domains_sub.add_parser("forget", help="Forget a registered domain.")
    forget_parser.add_argument("name")
    forget_parser.set_defaults(handler=_cmd_domains_forget)

    # sessions
    sessions_parser = subparsers.add_parser(
        "sessions", help="Live-session board: who's active, what's claimed."
    )
    sessions_sub = sessions_parser.add_subparsers(dest="sessions_action", required=True)

    sessions_list_parser = sessions_sub.add_parser(
        "list", help="List live (and just-expired) sessions."
    )
    _add_root_option(sessions_list_parser)
    sessions_list_parser.set_defaults(handler=_cmd_sessions_list)

    sessions_claim_parser = sessions_sub.add_parser(
        "claim", help="Claim one or more paths for a session (registers it if new)."
    )
    sessions_claim_parser.add_argument("session_id")
    sessions_claim_parser.add_argument("paths", nargs="+", metavar="PATH")
    sessions_claim_parser.add_argument(
        "--intent",
        default="",
        help="Session intent -- only used if this session_id isn't already registered.",
    )
    _add_root_option(sessions_claim_parser)
    sessions_claim_parser.set_defaults(handler=_cmd_sessions_claim)

    sessions_release_parser = sessions_sub.add_parser(
        "release", help="End a session gracefully -- moves its board file to history."
    )
    sessions_release_parser.add_argument("session_id")
    _add_root_option(sessions_release_parser)
    sessions_release_parser.set_defaults(handler=_cmd_sessions_release)

    sessions_heartbeat_parser = sessions_sub.add_parser(
        "heartbeat",
        help="Refresh a live session's heartbeat so it isn't swept as stale.",
    )
    sessions_heartbeat_parser.add_argument("session_id")
    _add_root_option(sessions_heartbeat_parser)
    sessions_heartbeat_parser.set_defaults(handler=_cmd_sessions_heartbeat)

    # intake
    intake_parser = subparsers.add_parser(
        "intake", help="New-layer front door: file, route, and list unrouted context."
    )
    intake_sub = intake_parser.add_subparsers(dest="intake_action", required=True)

    intake_add_parser = intake_sub.add_parser("add", help="File a new-layer note.")
    intake_add_parser.add_argument("text", help="The note's body text.")
    intake_add_parser.add_argument(
        "--source",
        choices=sorted(VALID_SOURCES),
        default=DEFAULT_SOURCE,
        help="Provenance (default: user).",
    )
    intake_add_parser.add_argument(
        "--title", default=None, help="Optional title (also seeds the filename slug)."
    )
    _add_root_option(intake_add_parser)
    intake_add_parser.set_defaults(handler=_cmd_intake_add)

    intake_route_parser = intake_sub.add_parser(
        "route", help="Route an unrouted item into core, notes, or archive."
    )
    intake_route_parser.add_argument(
        "item", help="Intake item path, relative to --root or absolute."
    )
    intake_route_parser.add_argument(
        "--to",
        required=True,
        metavar="DEST",
        help="Destination: core | notes | notes/<subpath> | archive.",
    )
    _add_root_option(intake_route_parser)
    intake_route_parser.set_defaults(handler=_cmd_intake_route)

    intake_list_parser = intake_sub.add_parser(
        "list", help="List the unrouted intake queue, oldest first."
    )
    _add_root_option(intake_list_parser)
    intake_list_parser.set_defaults(handler=_cmd_intake_list)

    # stats
    stats_parser = subparsers.add_parser(
        "stats",
        help=(
            "Zero-token aggregation: packs, drops, budget, doctor pass rate, "
            "intake latency, sessions, ctx-yield dead weight."
        ),
    )
    stats_parser.add_argument(
        "--since",
        type=int,
        default=None,
        metavar="N",
        help="Aggregation window in days (default: knobs.stats_window_days).",
    )
    _add_root_option(stats_parser)
    stats_parser.set_defaults(handler=_cmd_stats)

    return parser


# --- entry point -------------------------------------------------------


def _force_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr where the stream supports it, so `ctx
    stats`'s markdown (em-dashes and friends) doesn't come out as `?`
    mojibake under Windows' default cp1252 console encoding (clean under
    `PYTHONUTF8=1`, but that shouldn't be a precondition for correct
    output). `errors="replace"` rather than crashing on a genuinely
    unencodable byte the target console can't display at all. Best-effort:
    a stream without `.reconfigure` (older Python, a test double, a
    non-TextIOWrapper) is left alone rather than raising.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    """The `ctx` entry point. Returns a process exit code; never raises
    on a plain Ctrl-C (`KeyboardInterrupt` in, `EXIT_INTERRUPTED` out, no
    traceback).
    """
    _force_utf8_stdio()
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        handler = getattr(args, "handler", None)
        if handler is None:
            parser.print_help()
            return EXIT_ERROR
        return handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
