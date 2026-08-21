---
title: API conventions
---

# API conventions

Conventions the (fictional) Ember backend follows. New endpoints should
match these rather than introducing a one-off style.

## Shape

- Resource-oriented, plural nouns: `/habits`, `/habits/{id}/check-ins`.
- JSON in, JSON out. No XML, no form-encoded bodies for anything new.
- Timestamps are always UTC ISO-8601 with an explicit offset
  (`2026-08-21T14:03:00+00:00`), never a bare epoch integer.

## Errors

A non-2xx response body is always `{"error": {"code": str, "message":
str}}` — `code` is a stable machine-readable string (`habit_not_found`),
`message` is for a human. Never leak a stack trace or raw exception text
into the response body.

## Pagination

Cursor-based (`?cursor=...&limit=...`), never offset-based — check-in
history is append-heavy enough that an offset cursor drifts under
concurrent writes.

## Versioning

No `/v1/` prefix; additive changes (new optional field, new endpoint) ship
without a version bump. A breaking change gets a new endpoint name rather
than a version bump, since the client fleet can't be forced to upgrade in
lockstep.
