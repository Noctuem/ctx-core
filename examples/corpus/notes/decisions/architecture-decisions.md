---
title: Architecture decisions log
tags: [decisions]
---

# Architecture decisions log

Standing decisions for the (fictional) Ember codebase — append-only,
newest on top. `ctx pack --decisions` admits this note off the top
regardless of task relevance, the same way a pinned note is.

## No social features (2026-03-02)

Ember stays single-user and private. Sharing, leaderboards, and any
"coach" persona are explicitly rejected — the product's whole pitch is
low-pressure self-tracking, and a social layer inverts that. Re-proposals
should link here rather than re-litigating from scratch.

## Offline-first, sync reconciled by last-write-wins (2026-01-14)

Check-ins are written locally first and synced opportunistically. Conflict
resolution is last-write-wins on the check-in timestamp, not a merge — a
habit check-in has no meaningful partial state to merge, so the added
complexity of a real CRDT wasn't worth it for this data shape.

## SQLite on-device, no local server (2025-11-20)

The client talks to a single SQLite file directly rather than running a
local sync daemon — fewer moving parts, and habit data volume never
approaches a scale that needs anything heavier.
