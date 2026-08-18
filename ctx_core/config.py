"""Knobs: every ctx-core budget, threshold, and tunable, with a default.

Per the configurable-variables principle, nothing that governs behavior here
is a bare literal buried in a module elsewhere — every tunable lives on
`Knobs` with a named default. Values resolve in this precedence order
(later wins):

    dataclass default  ->  `.ctxrc.toml` at the corpus root  ->  CLI-flag overrides

`Knobs.load()` performs the first two steps. A caller that also has CLI
flags (the `ctx` entry point, m10) passes them as `overrides`; any key
present there with a non-`None` value wins outright over both the default
and the file.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

# Filename of the per-corpus override file, relative to a Layout's root.
CTXRC_FILENAME = ".ctxrc.toml"


@dataclass
class Knobs:
    """Every tunable ctx-core exposes, with its default.

    See the build spec's "Configuration & tunables" section for the
    provenance of each field. None of this is a secret — see
    `.env.example` (m10) for the one pass-through secret
    (`ANTHROPIC_API_KEY`) that belongs to `ctx-yield`, never to ctx-core.
    """

    # --- pack (m2) ---
    pack_budget_tokens: int = 8000
    """Default packer budget, in tokens, for one `ctx pack` invocation."""

    # --- archive (m4) ---
    archive_stub_threshold_tokens: int = 2000
    """Note size, in tokens, above which `ctx archive` content-addresses
    and stubs it."""

    archive_age_days: int = 90
    """Age, in days since last touch, after which `ctx archive sweep`
    proposes a note as an archiving candidate. Per-domain variance (spec
    Open Question 5) is realized structurally: each domain repo carries its
    own `.ctxrc.toml`, so this single field already varies per domain
    without a separate override field — left for m4/m9 to revisit if a
    shared-default-with-sparse-overrides shape is later wanted."""

    # --- pack retrieval flags (m2): CLI vocabulary defaults ---
    retrieval_summary_default: bool = False
    """Default for the packer's `--summary` retrieval flag."""

    retrieval_last_default: int | None = None
    """Default for the packer's `--last N` retrieval flag."""

    retrieval_decisions_default: bool = False
    """Default for the packer's `--decisions` retrieval flag."""

    # --- eventlog (m5) ---
    eventlog_path: str = "var/log/"
    """Path, relative to the corpus root, where the event log is written.
    Gitignored by default; a domain may opt into committing it."""

    # --- yield-bridge (m6): ctx-yield pass-through ---
    yield_exact: bool = False
    """Pass-through default for ctx-yield's `--exact` flag."""

    yield_model: str | None = None
    """Pass-through default for ctx-yield's `--model` flag."""

    @classmethod
    def load(cls, root: Path, overrides: dict[str, Any] | None = None) -> "Knobs":
        """Build `Knobs` for the corpus at `root`.

        Reads `<root>/.ctxrc.toml` if present (unknown keys are ignored
        rather than raising, so a corpus can carry forward-compatible
        extra keys). `overrides` — intended for CLI-flag values — wins over
        both the file and the defaults for any key present with a
        non-`None` value.
        """
        known_fields = {f.name for f in fields(cls)}
        values: dict[str, Any] = {}

        rc_path = Path(root) / CTXRC_FILENAME
        if rc_path.is_file():
            with rc_path.open("rb") as fh:
                file_data = tomllib.load(fh)
            values.update({k: v for k, v in file_data.items() if k in known_fields})

        if overrides:
            values.update(
                {
                    k: v
                    for k, v in overrides.items()
                    if k in known_fields and v is not None
                }
            )

        return cls(**values)
