# Map — where things live under `notes/`

> **The law of the map:** every add, move, or delete under `notes/` updates
> this file in the same change. One line per note: path — purpose / when to
> pack it.

## Inventory

- `notes/product/overview.md` — what Ember is and who it's for; pack for
  any product-shaped question.
- `notes/product/roadmap.md` — near-term priorities; pack when a task asks
  "what's next" or touches planning.
- `notes/decisions/architecture-decisions.md` — the standing architecture
  decisions log (tagged `decisions`); pack when a task needs "why is it
  built this way."
- `notes/eng/api-conventions.md` — REST conventions engineers are expected
  to follow; pack for any API-shaped engineering task.
- `notes/eng/deploy-runbook.md` — the full per-environment deploy/rollback
  runbook. **Deliberately oversized** for this corpus's small example
  budget (`pack_budget_tokens = 3000` in `.ctxrc.toml`) — it demonstrates
  `ctx pack`'s oversized-drop behavior rather than being packed whole; read
  it directly, or raise `--budget` for a task that genuinely needs it.
- `notes/support/faq.md` — the top support FAQ. **Pinned**
  (`ctx:pin: true`) — always admitted to a manifest regardless of task
  relevance, the way a corpus pins its highest-traffic reference material.
