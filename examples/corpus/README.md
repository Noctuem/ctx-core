# Example corpus

A small, self-contained ctx-core corpus (fictional domain: Northwind Labs,
a team building a habit-tracking app called Ember) — realistic-shaped, but
entirely made up. No real people, companies, or products are described
here; it exists so this repo has a live corpus of its own to run `ctx`
against, rather than asking a first-time reader to build one from nothing
before they can try anything.

It demonstrates:

- **A customized L1** (`core/profile.md`, `core/card.md`, `core/map.md`) —
  not the shipped template placeholder.
- **A pinned note** (`notes/support/faq.md`, `ctx:pin: true`) — admitted to
  every manifest regardless of task relevance.
- **A decision-tagged note** (`notes/decisions/architecture-decisions.md`,
  `tags: [decisions]`) — admitted off the top with `ctx pack --decisions`.
- **A deliberately oversized note** (`notes/eng/deploy-runbook.md`, ~3.8k
  tokens against this corpus's own 3,000-token pack budget) — shows
  `ctx pack`'s honest "larger than the whole working budget" drop reason
  rather than silently truncating or ignoring it.
- **A small `.ctxrc.toml`** — this corpus deliberately runs a smaller-than-
  default `pack_budget_tokens` (3,000) so the oversized-drop behavior above
  is reachable without a huge note.

## Three commands to try

From the repo root, with the engine installed (`pip install -e .`):

```sh
# 1. Health check — should report OK (this corpus is intentionally clean)
ctx doctor --root examples/corpus

# 2. Pack a manifest — watch the pinned FAQ get admitted and the oversized
#    runbook get dropped with a named reason
ctx pack "help a new teammate ramp up on the product" --root examples/corpus

# 3. Zero-token stats over this corpus's own history
ctx stats --root examples/corpus
```

Run `ctx pack --budget 20000 "deploy runbook" --root examples/corpus` to
see the oversized runbook admitted once the budget is actually big enough
to hold it — the same knob a real corpus operator reaches for.

This corpus is also exercised live in CI (`.github/workflows/ci.yml`) as a
standing fixture, distinct from the doctor-gate proof's scratch corpus
built from `template/`.
