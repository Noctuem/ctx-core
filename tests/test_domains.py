"""Tests for `ctx_core.domains.DomainRegistry`.

Self-contained `tmp_path` fixtures throughout (same pattern as
`tests/test_layout.py` / `tests/test_init.py`) -- no shared fixtures/
directory needed for this module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ctx_core import __version__ as ENGINE_VERSION
from ctx_core.domains import (
    DEFAULT_REGISTRY_DIR_NAME,
    DEFAULT_REGISTRY_FILENAME,
    DomainAlreadyRegisteredError,
    DomainEntry,
    DomainNotFoundError,
    DomainPathNotFoundError,
    DomainRegistry,
    default_registry_path,
)


def _make_domain_repo(root: Path, secret_text: str = "private notes content") -> Path:
    """A fixture 'domain repo': a directory with a notes/ tree holding
    content the registry must never read."""
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "secret.md").write_text(secret_text, encoding="utf-8")
    return root


# --- default_registry_path ---


def test_default_registry_path_is_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    result = default_registry_path()
    assert result == tmp_path / DEFAULT_REGISTRY_DIR_NAME / DEFAULT_REGISTRY_FILENAME


def test_registry_defaults_to_default_registry_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    registry = DomainRegistry()
    assert registry.registry_path == default_registry_path()


# --- list: empty / missing file ---


def test_list_is_empty_when_registry_file_missing(tmp_path: Path) -> None:
    registry = DomainRegistry(tmp_path / "domains.json")
    assert registry.list() == []


# --- register: basic ---


def test_register_returns_and_persists_entry(tmp_path: Path) -> None:
    domain_root = tmp_path / "domain-a"
    _make_domain_repo(domain_root)
    registry_path = tmp_path / "registry" / "domains.json"
    registry = DomainRegistry(registry_path)

    entry = registry.register("alpha", domain_root)

    assert entry == DomainEntry(
        name="alpha", path=str(domain_root.resolve()), engine_version=ENGINE_VERSION
    )
    assert registry.list() == [entry]
    assert registry_path.is_file()


def test_register_persists_across_new_registry_instances(tmp_path: Path) -> None:
    domain_root = tmp_path / "domain-a"
    _make_domain_repo(domain_root)
    registry_path = tmp_path / "domains.json"

    DomainRegistry(registry_path).register("alpha", domain_root)
    reopened = DomainRegistry(registry_path)

    assert [e.name for e in reopened.list()] == ["alpha"]


def test_register_records_current_engine_version(tmp_path: Path) -> None:
    domain_root = tmp_path / "domain-a"
    _make_domain_repo(domain_root)
    registry = DomainRegistry(tmp_path / "domains.json")

    entry = registry.register("alpha", domain_root)

    assert entry.engine_version == ENGINE_VERSION


def test_registry_file_is_plain_json_list(tmp_path: Path) -> None:
    domain_root = tmp_path / "domain-a"
    _make_domain_repo(domain_root)
    registry_path = tmp_path / "domains.json"
    DomainRegistry(registry_path).register("alpha", domain_root)

    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    assert raw == [
        {
            "name": "alpha",
            "path": str(domain_root.resolve()),
            "engine_version": ENGINE_VERSION,
        }
    ]


def test_registry_file_uses_lf_line_endings(tmp_path: Path) -> None:
    domain_root = tmp_path / "domain-a"
    _make_domain_repo(domain_root)
    registry_path = tmp_path / "domains.json"
    DomainRegistry(registry_path).register("alpha", domain_root)

    raw_bytes = registry_path.read_bytes()
    assert b"\r\n" not in raw_bytes


def test_register_creates_missing_registry_dir(tmp_path: Path) -> None:
    domain_root = tmp_path / "domain-a"
    _make_domain_repo(domain_root)
    registry_path = tmp_path / "nested" / "dir" / "domains.json"

    DomainRegistry(registry_path).register("alpha", domain_root)

    assert registry_path.is_file()


# --- register: error behavior ---


def test_register_duplicate_name_raises(tmp_path: Path) -> None:
    domain_a = tmp_path / "domain-a"
    domain_b = tmp_path / "domain-b"
    _make_domain_repo(domain_a)
    _make_domain_repo(domain_b)
    registry = DomainRegistry(tmp_path / "domains.json")
    registry.register("alpha", domain_a)

    with pytest.raises(DomainAlreadyRegisteredError):
        registry.register("alpha", domain_b)

    # Original entry is untouched.
    assert registry.list()[0].path == str(domain_a.resolve())


def test_register_nonexistent_path_raises(tmp_path: Path) -> None:
    registry = DomainRegistry(tmp_path / "domains.json")

    with pytest.raises(DomainPathNotFoundError):
        registry.register("alpha", tmp_path / "does-not-exist")

    assert registry.list() == []


def test_register_file_path_raises(tmp_path: Path) -> None:
    a_file = tmp_path / "not-a-dir.txt"
    a_file.write_text("hi", encoding="utf-8")
    registry = DomainRegistry(tmp_path / "domains.json")

    with pytest.raises(DomainPathNotFoundError):
        registry.register("alpha", a_file)


# --- forget ---


def test_forget_removes_entry(tmp_path: Path) -> None:
    domain_a = tmp_path / "domain-a"
    domain_b = tmp_path / "domain-b"
    _make_domain_repo(domain_a)
    _make_domain_repo(domain_b)
    registry = DomainRegistry(tmp_path / "domains.json")
    registry.register("alpha", domain_a)
    registry.register("beta", domain_b)

    registry.forget("alpha")

    assert [e.name for e in registry.list()] == ["beta"]


def test_forget_unknown_name_raises(tmp_path: Path) -> None:
    registry = DomainRegistry(tmp_path / "domains.json")

    with pytest.raises(DomainNotFoundError):
        registry.forget("nonexistent")


def test_forget_unknown_name_raises_when_registry_file_absent(tmp_path: Path) -> None:
    registry = DomainRegistry(tmp_path / "missing-dir" / "domains.json")

    with pytest.raises(DomainNotFoundError):
        registry.forget("nonexistent")


def test_forget_then_reregister_same_name_succeeds(tmp_path: Path) -> None:
    domain_a = tmp_path / "domain-a"
    domain_b = tmp_path / "domain-b"
    _make_domain_repo(domain_a)
    _make_domain_repo(domain_b)
    registry = DomainRegistry(tmp_path / "domains.json")
    registry.register("alpha", domain_a)
    registry.forget("alpha")

    entry = registry.register("alpha", domain_b)

    assert entry.path == str(domain_b.resolve())


# --- structural isolation: never read into a registered domain's notes/ ---


class _GuardedOpen:
    """Wraps `pathlib.Path.open`/`read_text`/`read_bytes` to fail the test
    the instant anything under a guarded root is opened -- proving
    `DomainRegistry` never reads a registered domain's content, not just
    that it doesn't in this particular run."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, guarded_roots: list[Path]) -> None:
        self._guarded_roots = [r.resolve() for r in guarded_roots]
        self._real_open = Path.open
        self._real_read_text = Path.read_text
        self._real_read_bytes = Path.read_bytes
        monkeypatch.setattr(Path, "open", self._guarded("open", self._real_open))
        monkeypatch.setattr(Path, "read_text", self._guarded("read_text", self._real_read_text))
        monkeypatch.setattr(Path, "read_bytes", self._guarded("read_bytes", self._real_read_bytes))

    def _is_guarded(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return any(
            resolved == root or root in resolved.parents for root in self._guarded_roots
        )

    def _guarded(self, label: str, real):
        def _call(path_self, *args, **kwargs):
            if self._is_guarded(path_self):
                raise AssertionError(
                    f"DomainRegistry.{label} touched a guarded domain path: {path_self}"
                )
            return real(path_self, *args, **kwargs)

        return _call


def test_register_and_list_never_open_a_domains_notes_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    domain_a = tmp_path / "domain-a"
    domain_b = tmp_path / "domain-b"
    _make_domain_repo(domain_a, secret_text="alpha's private notes")
    _make_domain_repo(domain_b, secret_text="beta's private notes")
    registry_path = tmp_path / "registry" / "domains.json"

    # Guard both domains' notes/ trees for the remainder of the test --
    # any open/read_text/read_bytes call under either raises immediately.
    _GuardedOpen(monkeypatch, [domain_a / "notes", domain_b / "notes"])

    registry = DomainRegistry(registry_path)
    registry.register("alpha", domain_a)
    registry.register("beta", domain_b)
    entries = registry.list()

    assert {e.name for e in entries} == {"alpha", "beta"}
    # Sanity: the guard is real -- reading the secret directly still raises.
    with pytest.raises(AssertionError):
        (domain_a / "notes" / "secret.md").read_text(encoding="utf-8")


def test_forget_never_opens_a_domains_notes_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    domain_a = tmp_path / "domain-a"
    _make_domain_repo(domain_a)
    registry_path = tmp_path / "domains.json"
    registry = DomainRegistry(registry_path)
    registry.register("alpha", domain_a)

    _GuardedOpen(monkeypatch, [domain_a / "notes"])

    registry.forget("alpha")

    assert registry.list() == []
