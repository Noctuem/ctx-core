---
description: Use when you need to verify this corpus is healthy before committing, shipping, or trusting a pack — runs `ctx doctor` and reports the pass/fail verdict.
---

Run the installed `ctx doctor` command against this corpus and report its
verdict back to the user.

Steps:

1. Run `ctx doctor`.
2. Read its exit code and output. A clean run exits `0`; any hard failure
   (corpus integrity, a broken event-log chain, force-loaded context over
   budget) exits non-zero.
3. Report the verdict plainly:
   - If it passed: say so, and list any *warnings* separately from hard
     failures — a warning (this corpus is still in template state, or the
     packer's view looks stale against the working tree) doesn't fail the
     check, but is worth surfacing.
   - If it failed: list every hard failure `ctx doctor` reported, in the
     order it reported them. Don't soften a hard failure into a
     suggestion — it's a gate.
4. Don't try to fix what `ctx doctor` reports as part of running this
   command — it's a health check, not a repair tool.
