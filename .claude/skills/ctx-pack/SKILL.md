---
name: ctx-pack
description: Use when a task needs a context manifest before you start work — assembles a token-budgeted set of corpus files for the task and reads back what it picked.
---

# ctx-pack

Use this skill whenever you are about to start substantive work in a
ctx-core corpus and don't already have a manifest for the task at hand —
starting by browsing the corpus by hand risks pulling in far more (or far
less) context than the task actually needs.

## What to do

1. Run the installed `ctx` console script against the task, in your own
   words:

   ```
   ctx pack "<task description>"
   ```

2. Read the manifest it prints. The manifest is a markdown table of the
   files selected for this task, each with a one-line reason (pinned, or
   a relevance score), a token budget and fill line, and a collapsed list
   of what was considered and dropped.
3. Read the files the manifest lists — that is the working context for
   the task. Don't re-derive a different set by browsing the corpus
   yourself; the packer's admission/ranking logic already made that
   judgment call, under a budget, on purpose.
4. If the manifest looks wrong (missing an obviously relevant note,
   pulling in something stale), that is useful signal for `/ctx-doctor`
   or for revisiting the corpus's front-matter tags/pins — not something
   to silently override by grabbing extra files yourself.

## Retrieval flags

`ctx pack` supports flags for widening or narrowing the pick beyond the
plain relevance ranking:

- `--summary` — bias the pick toward summary-shaped notes.
- `--last N` — always include the N most recently modified entries.
- `--decisions` — always include anything tagged or named as a decision
  record.

Use these when the task explicitly needs recent history or a decision
trail that the relevance ranking alone might rank too low to admit.
