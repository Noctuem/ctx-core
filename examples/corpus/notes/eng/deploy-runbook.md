---
title: Deploy runbook (all environments)
---

# Deploy runbook (all environments)

**This note is deliberately oversized for this example corpus's small
demo budget** (`pack_budget_tokens = 3000` in `.ctxrc.toml`). It exists
to give `ctx pack` a real, realistic-shaped file to drop as "larger
than the whole working budget" -- the same failure mode a real corpus
hits when someone dumps a full ops runbook into one note instead of
splitting it per-environment or letting `ctx archive` stub it. Read it
directly, or run `ctx pack --budget 20000 "deploy runbook"` to see it
admitted instead.

Twelve environments, each with its own deploy trigger, bake/soak
window, rollback owner, and escalation path. Fictional (Northwind
Labs / Ember) but shaped like a real multi-region rollout.

## Standing rollback conditions (apply to every environment below)

- error rate exceeds 2% over a 5-minute rolling window
- p99 check-in latency exceeds 800ms for 3 consecutive minutes
- the sync reconciliation queue depth exceeds 10,000 for more than 10 minutes
- any database migration step fails or times out
- a canary health check reports more than 3 consecutive failures

Any one of the above triggers an automatic rollback to the previous known-good build in that environment; a human on-call is paged regardless of whether the automatic rollback succeeds.

## Environment: dev

**Description:** shared development sandbox.

**Deploy trigger:** auto-deploy on every merge to main; no approval gate.

**Escalation:** Slack #ember-dev-deploys.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `dev` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `dev` in the promotion chain.
3. Confirm no active incident is open against `dev` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `dev`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: qa

**Description:** manual QA sign-off environment.

**Deploy trigger:** deployed on demand by QA before a release candidate is cut.

**Escalation:** Slack #ember-qa.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `qa` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `qa` in the promotion chain.
3. Confirm no active incident is open against `qa` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `qa`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: staging

**Description:** production-shaped pre-release environment.

**Deploy trigger:** deployed automatically once a release branch is cut.

**Escalation:** Slack #ember-releases.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `staging` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `staging` in the promotion chain.
3. Confirm no active incident is open against `staging` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `staging`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: canary

**Description:** 5% of production traffic, US region only.

**Deploy trigger:** promoted from staging after a 2-hour bake with no new error-rate alerts.

**Escalation:** PagerDuty escalation policy 'Ember Canary'.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `canary` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `canary` in the promotion chain.
3. Confirm no active incident is open against `canary` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `canary`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: prod-us-east

**Description:** primary US production region.

**Deploy trigger:** promoted from canary after a 24-hour soak with no rollback triggers.

**Escalation:** PagerDuty escalation policy 'Ember Prod'.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `prod-us-east` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `prod-us-east` in the promotion chain.
3. Confirm no active incident is open against `prod-us-east` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `prod-us-east`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: prod-us-west

**Description:** secondary US production region, DR partner to us-east.

**Deploy trigger:** promoted from prod-us-east 2 hours after a clean soak there.

**Escalation:** PagerDuty escalation policy 'Ember Prod'.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `prod-us-west` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `prod-us-west` in the promotion chain.
3. Confirm no active incident is open against `prod-us-west` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `prod-us-west`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: prod-eu

**Description:** EU production region (data residency: EU-only).

**Deploy trigger:** promoted from prod-us-east after a clean 24-hour soak; never receives a build that hasn't run in a US region first.

**Escalation:** PagerDuty escalation policy 'Ember Prod EU'.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `prod-eu` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `prod-eu` in the promotion chain.
3. Confirm no active incident is open against `prod-eu` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `prod-eu`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: prod-apac

**Description:** APAC production region.

**Deploy trigger:** promoted from prod-eu after a clean 12-hour soak.

**Escalation:** PagerDuty escalation policy 'Ember Prod APAC'.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `prod-apac` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `prod-apac` in the promotion chain.
3. Confirm no active incident is open against `prod-apac` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `prod-apac`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: prod-latam

**Description:** LATAM production region, smallest traffic share.

**Deploy trigger:** promoted last, after every other production region is stable on the build.

**Escalation:** PagerDuty escalation policy 'Ember Prod'.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `prod-latam` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `prod-latam` in the promotion chain.
3. Confirm no active incident is open against `prod-latam` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `prod-latam`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: dr-standby

**Description:** cold standby, not traffic-serving.

**Deploy trigger:** kept within one release of prod-us-east; promoted to active only during a declared DR event.

**Escalation:** PagerDuty escalation policy 'Ember DR'.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `dr-standby` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `dr-standby` in the promotion chain.
3. Confirm no active incident is open against `dr-standby` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `dr-standby`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: perf-lab

**Description:** isolated load-testing environment, not customer-facing.

**Deploy trigger:** deployed manually before a scheduled load test; never auto-deployed.

**Escalation:** Slack #ember-perf.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `perf-lab` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `perf-lab` in the promotion chain.
3. Confirm no active incident is open against `perf-lab` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `perf-lab`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

## Environment: sandbox-partners

**Description:** read-only environment for third-party integration partners.

**Deploy trigger:** deployed on the same cadence as staging, one release behind production.

**Escalation:** Slack #ember-partners.

**Pre-deploy checklist:**

1. Confirm the previous deploy to `sandbox-partners` has no open rollback ticket.
2. Confirm the build's migration set (if any) has already run cleanly
   in every environment upstream of `sandbox-partners` in the promotion chain.
3. Confirm no active incident is open against `sandbox-partners` or any
   environment it depends on for sync/reconciliation traffic.
4. Post the deploy plan to the environment's escalation channel above
   before starting, including the build hash and the promotion chain
   it traveled.

**Rollback owner:** the on-call engineer for `sandbox-partners`'s escalation
policy above, until explicitly handed off in the incident channel.

**Post-deploy verification:** confirm check-in write latency, sync
queue depth, and error rate are all within the standing rollback
conditions for at least the bake/soak window named in this
environment's deploy trigger before considering the deploy complete.

