"""Layout: the physical directory shape of one ctx-core corpus.

A corpus — whether it's the shipped `template/` tree, a fresh clone of it,
or a live domain repo further along — is described by four paths hung off
one root:

- `core/`     — force-loaded L1 (user tailoring): always read every session.
- `notes/`    — on-demand L2: the corpus the packer selects from per task.
- `var/`      — runtime state: the event log, the content-addressed archive.
- `template/` — the seed tree a fresh clone or `ctx init` copies from.

`Layout` only computes paths; it never asserts anything exists on disk.
Callers create what they need (scaffolding, `ctx init`, the packer, …).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# The exact contents `template/core/profile.md` ships with. A corpus is
# still in template state as long as its `core/profile.md` is byte-identical
# to this placeholder — see `Layout.is_template_state`.
PROFILE_PLACEHOLDER = "<!-- run `ctx init` -->\n"

# Filenames under `notes/` that don't count as "a domain-specific note" for
# the template-state check — housekeeping only.
TEMPLATE_STATE_IGNORED_NOTES = frozenset({".gitkeep"})


@dataclass(frozen=True)
class Layout:
    """Paths for one ctx-core corpus, rooted at `root`."""

    root: Path

    def __post_init__(self) -> None:
        # Normalize whatever was passed (str or Path) to a Path once, so
        # every derived property below is a plain Path.
        object.__setattr__(self, "root", Path(self.root))

    @property
    def core(self) -> Path:
        """Force-loaded L1: user tailoring, read every session."""
        return self.root / "core"

    @property
    def notes(self) -> Path:
        """On-demand L2: the corpus the packer selects from per task."""
        return self.root / "notes"

    @property
    def var(self) -> Path:
        """Runtime state: event log, content-addressed archive."""
        return self.root / "var"

    @property
    def template(self) -> Path:
        """The seed tree a fresh clone or `ctx init` copies from."""
        return self.root / "template"

    def is_template_state(self) -> bool:
        """True iff this corpus has not been customized yet.

        Two conditions, both must hold:
        1. `core/profile.md` still equals the shipped placeholder exactly
           (missing entirely counts as "not template state" — a corpus with
           no profile at all isn't the known-good starting point).
        2. `notes/` holds no domain-specific note — nothing there but
           housekeeping files (`.gitkeep`) or nothing at all.

        This is the primitive `doctor` (m3, soft nudge) and `cli-release`'s
        (m10) adoption check both build on.
        """
        profile_path = self.core / "profile.md"
        if not profile_path.is_file():
            return False
        if profile_path.read_text(encoding="utf-8") != PROFILE_PLACEHOLDER:
            return False

        if self.notes.is_dir():
            for entry in self.notes.iterdir():
                if entry.name in TEMPLATE_STATE_IGNORED_NOTES:
                    continue
                return False

        return True
