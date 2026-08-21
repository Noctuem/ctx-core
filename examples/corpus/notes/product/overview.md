---
title: Ember — product overview
---

# Ember — product overview

Ember (fictional product, Northwind Labs) is a habit-tracking app: a user
defines a small set of habits, checks them off daily, and gets a weekly
review that highlights streaks and the habits quietly slipping.

## Who it's for

Individuals who've tried heavier habit trackers (elaborate stats, social
feeds, gamified streak pressure) and want something closer to a paper
checklist that happens to remember history and nudge gently. Not aimed at
teams or coaches — single-user, private by default.

## Core loop

1. Open the app. See today's habits, unchecked.
2. Check off what's done. No required note, no photo, no friction.
3. Once a week, a short review surfaces: streaks holding, streaks broken,
   and one suggestion (never a lecture) if a habit has slipped three weeks
   running.

## Explicitly out of scope

Social features, leaderboards, and habit "coaching" content are
deliberately not part of Ember — see
`notes/decisions/architecture-decisions.md` for the record of that call
and why it was made early rather than revisited per-feature.
