---
title: Support FAQ (top questions)
ctx:pin: true
---

# Support FAQ (top questions)

Pinned (`ctx:pin: true`) — this note is admitted to every `ctx pack`
manifest regardless of task relevance, the way a corpus pins its
highest-traffic reference material (a real support team's top-5 FAQ
belongs in every context, not just tasks that happen to mention it).

## "My streak reset but I checked in every day"

Almost always a timezone edge case: a check-in made just after local
midnight can land on the wrong day if the device's clock and the server's
UTC day boundary disagree by a few minutes. Ask for the device timezone
and the exact local time of the check-in.

## "The weekly review didn't mention my slipping habit"

Known gap, tracked in `notes/product/roadmap.md` — the review currently
buries the slipping-habit callout below the streak list rather than
surfacing it first. Not a bug, a prioritization gap.

## "Can I share my streaks with a friend"

No — by design, not by omission. See
`notes/decisions/architecture-decisions.md` ("No social features") for
why. Point the user there rather than filing it as a feature request each
time.

## "Data lost after reinstalling on a new phone"

Ember is local-first with opportunistic sync (see architecture decisions);
if sync never ran on the old device before reinstall, that data was never
uploaded and is not recoverable. This is a real (fictional) product gap,
not a support-side misunderstanding — flag it rather than reassure past it.
