"""ctx_core.domains: local, opt-in registry of domain-repo corpora.

A single ctx-core *engine* checkout can be used against many *domain*
repos (a personal notes corpus, a team's, a second project's) on one
machine. `DomainRegistry` records which ones a user has pointed `ctx` at —
name, filesystem path, and the engine version active at registration time —
so a future `ctx domains list` can enumerate them and other modules (e.g. a
future multi-domain `ctx pack --domain <name>`) can resolve a name to a
root without the user re-typing a path.

Cross-domain isolation is structural, not a policy this module has to
remember: `DomainRegistry` stores and returns paths and names only. It
never opens a file under a registered domain's root — registration only
stats the path to confirm it exists (`Path.is_dir()`), and every other
operation reads/writes exclusively the registry file itself. Nothing here
gives one domain's `notes/` visibility into another's, or into the
registry-holding machine beyond the bare fact "this name maps to this
path". `tests/test_domains.py` proves this by guarding two fixture
domains' `notes/` trees against being opened during `register`/`list`.

Subcommand semantics this backs (`ctx domains list|register|forget`) are
m10's CLI-wiring job; this module is the library surface underneath it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ctx_core import __version__ as _ENGINE_VERSION

# Default registry location: `~/.ctx-core/domains.json`. Split into a dir
# name + filename (both named constants, per the configurable-variables
# principle) and assembled by `default_registry_path()` rather than frozen
# at import time, so a future platform-specific default (spec: "or
# platform-equivalent config dir") has one seam to change. Tests point
# `DomainRegistry` at a tmp path instead of relying on this default.
DEFAULT_REGISTRY_DIR_NAME = ".ctx-core"
DEFAULT_REGISTRY_FILENAME = "domains.json"


def default_registry_path() -> Path:
    """The default registry file: `~/.ctx-core/domains.json`.

    A function rather than a module-level constant so it always reflects
    the current `Path.home()` (relevant under test monkeypatching) instead
    of whatever it resolved to at import time.
    """
    return Path.home() / DEFAULT_REGISTRY_DIR_NAME / DEFAULT_REGISTRY_FILENAME


@dataclass(frozen=True)
class DomainEntry:
    """One registered domain: name, path, and the engine version that
    registered it. Paths and names only -- never content."""

    name: str
    path: str
    engine_version: str


class DomainAlreadyRegisteredError(ValueError):
    """Raised by `register` when `name` is already in the registry."""


class DomainNotFoundError(KeyError):
    """Raised by `forget` when `name` is not in the registry."""


class DomainPathNotFoundError(FileNotFoundError):
    """Raised by `register` when `path` is not an existing directory."""


class DomainRegistry:
    """Reads/writes the local domain registry at `registry_path`.

    `registry_path` defaults to `default_registry_path()`
    (`~/.ctx-core/domains.json`); pass an explicit path (e.g. a `tmp_path`
    in tests) to point the registry elsewhere. No file is created until the
    first `register()` call -- an absent registry file reads as empty.
    """

    def __init__(self, registry_path: Path | str | None = None) -> None:
        self.registry_path = (
            Path(registry_path) if registry_path is not None else default_registry_path()
        )

    def list(self) -> list[DomainEntry]:
        """All registered domains, in registration order. Empty list if the
        registry file doesn't exist yet."""
        return [DomainEntry(**raw) for raw in self._read_raw()]

    def register(self, name: str, path: Path | str) -> DomainEntry:
        """Record `name` -> `path` in the registry.

        Raises `DomainPathNotFoundError` if `path` is not an existing
        directory (stat only -- nothing under it is opened), and
        `DomainAlreadyRegisteredError` if `name` is already registered.
        The engine version recorded is this package's current
        `ctx_core.__version__` at call time.
        """
        resolved = Path(path).expanduser().resolve(strict=False)
        if not resolved.is_dir():
            raise DomainPathNotFoundError(f"domain path does not exist: {resolved}")

        entries = self._read_raw()
        if any(raw["name"] == name for raw in entries):
            raise DomainAlreadyRegisteredError(f"domain already registered: {name}")

        entry = DomainEntry(name=name, path=str(resolved), engine_version=_ENGINE_VERSION)
        entries.append(asdict(entry))
        self._write_raw(entries)
        return entry

    def forget(self, name: str) -> None:
        """Remove `name` from the registry.

        Raises `DomainNotFoundError` if `name` is not registered (including
        when the registry file doesn't exist at all).
        """
        entries = self._read_raw()
        remaining = [raw for raw in entries if raw["name"] != name]
        if len(remaining) == len(entries):
            raise DomainNotFoundError(f"no such registered domain: {name}")

        self._write_raw(remaining)

    # --- internals: the registry file only, never a registered path's contents ---

    def _read_raw(self) -> list[dict[str, str]]:
        if not self.registry_path.is_file():
            return []
        with self.registry_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return list(data)

    def _write_raw(self, entries: list[dict[str, str]]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(entries, indent=2) + "\n"
        self.registry_path.write_text(text, encoding="utf-8", newline="\n")
