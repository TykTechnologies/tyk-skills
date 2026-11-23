# Hard block for Kubernetes delivery: Tyk Operator admission webhook

Last verified: 2026-11-19 against Tyk Operator 1.6.0 (chart `tyk-helm/tyk-operator` 1.6.0), Tyk Dashboard 5.16.0, kind (Kubernetes 1.34), cert-manager 1.15.3

The examples assume the Dashboard runs outside the cluster, reached from the pods at
`http://host.docker.internal:<port>`. Public pages:
https://tyk.io/docs/tyk-governance/tyk-operator,
https://tyk.io/docs/product-stack/tyk-operator/crd-reference,
https://tyk.io/docs/developer-support/release-notes/operator.

Installing the Operator, cert-manager and the `tyk-operator-conf` secret, and the rest of the
custom resources, belong to the `tyk-gitops-sync-operator` skill. This page covers only what
governance adds.

## What you get

- `TykRuleset` (short name `tykrs`): a governance ruleset kept in Git. The Operator creates and
  updates it in the Dashboard and deletes it there when the resource is deleted (the Dashboard
  then answers 404 for the ruleset).
- A validating admission webhook on `TykOasApiDefinition` create **and** update. It is part of
  the chart's webhooks (`webhooks.enabled: true` by default) and calls the Dashboard sandbox.
- `status.governancePreflight` and a `PREFLIGHTSTATUS` column on every `TykOasApiDefinition`
  with categories (`passed`, `warned`, `blocked`, `skipped`, `error`), and events
  `GovernancePreflightPassed`, `...Warned`, `...Blocked` and `...Error` (the docs list `Error`
  too; alert on it, and on `blocked`).

The Dashboard user in `tyk-operator-conf` must be allowed to manage rulesets and run the
sandbox (an admin user does; the RBAC table is in `concepts.md`).

**Without the `governance` scope nothing is enforced.** The Operator page says the Operator
treats the Dashboard's 403 as "not licensed": a `TykRuleset` stays in a `Failed` transaction
with no ruleset ID, so no ruleset matches, and an evaluation that gets 403 is admitted with a
warning and `status: skipped` (see "Licensing and Skip Conditions" on
https://tyk.io/docs/tyk-governance/tyk-operator). A
`block` ruleset then protects nothing. Check the licence first (SKILL.md step 1), and treat
`PREFLIGHTSTATUS skipped` or a `Failed` `TykRuleset` as "not governed".

## Walk-through with the assets

```bash
kubectl create namespace tyk
kubectl apply -f assets/operator/api-compliant.yaml          # carries category "payments"
kubectl -n tyk wait --for=jsonpath='{.status.latestTransaction.status}'=Successful tykoas/catalogue --timeout=120s
kubectl apply -f assets/operator/tykruleset-block.yaml       # action: block, categories [payments]
kubectl -n tyk wait --for=jsonpath='{.status.latestTransaction.status}'=Successful tykrs/payments-standards --timeout=120s
kubectl -n tyk get tykrs payments-standards
# NAME                 NAME                 RULESETID                  ACTIVE   ACTION   SYNCSTATUS
# payments-standards   Payments standards   6aff0d2cc2a36b000163c2e9   true     block    Successful
kubectl apply -f assets/operator/api-noncompliant.yaml       # no operationId
# configmap/legacy-catalogue-oas created
# Error from server (Forbidden): error when creating "assets/operator/api-noncompliant.yaml": admission webhook
#   "vtykoasapidefinition.kb.io" denied the request: governance preflight blocked this API (1 error(s)):
#   - tyk-operation-id-required (paths./items.operationId): Operation has no operationId.
# Fix the violations or relax the ruleset action; the full violation list is available on the Dashboard.
kubectl -n tyk get tykoas
# NAME        ...   SYNCSTATUS   ...   CATEGORIES     PREFLIGHTSTATUS
# catalogue   ...   Successful   ...   ["payments"]   passed
```

The denied object is not stored in Kubernetes and not sent to the Dashboard. The ConfigMap in
the same file is created: the webhook guards only the `TykOasApiDefinition`.

**Order matters on a new organisation.** Apply an API that carries the category before the
`TykRuleset` that names it. Otherwise the ruleset stays `SYNCSTATUS Failed` with
`categories [fresh-category] are not valid org API categories`.

## Outcomes

| Situation | `kubectl apply` | Status |
| --- | --- | --- |
| `block` ruleset, `error` finding | denied (above); also denied on update: a `kubectl patch` of an existing API that has become non-compliant is refused with the same message | object unchanged |
| `warn` ruleset, `error` finding | `Warning: preflight warned: 1 error(s), 0 warning(s) — operation-operationid-required (paths./items.operationId): Operation has no operationId.` then `created` | `warned` |
| No matching `block` finding | created | `passed` |
| Ruleset exists only in the Dashboard (made there, same category) | created, no warning | `passed`, while the Dashboard reports the API `non_compliant` |

The webhook and the reconciler consider only `TykRuleset` resources in the API's namespace
(https://tyk.io/docs/tyk-governance/tyk-operator; the Dashboard-made ruleset row above shows the
effect). Manage each
category's rulesets from one place: all as `TykRuleset`, or none.

## Gaps to close

- **Editing the ConfigMap bypasses admission.** Removing the `operationId` from the referenced
  ConfigMap (the `TykOasApiDefinition` untouched) is accepted by Kubernetes; the reconciler
  pushes the non-compliant document to the Dashboard and sets `PREFLIGHTSTATUS blocked` and a
  `GovernancePreflightBlocked` event. Review ConfigMap changes like API changes, run the CI
  gate on the document in the same pull request, and alert on `PREFLIGHTSTATUS blocked`.
- **Fail-open by default.** `webhooks.governanceFailurePolicy` is `Ignore` in chart 1.6.0. With
  the Operator scaled to 0, a non-compliant API is admitted silently; once the Operator comes
  back it syncs the API to the Dashboard and marks it `blocked`. A restart of the Operator
  pod (for example after a lost leader election on a loaded node) opens the same window.
- **`Fail` closes it, for every API.** With
  `helm upgrade ... --reuse-values --set webhooks.governanceFailurePolicy=Fail` the webhook's
  `failurePolicy` becomes `Fail` and the pod gets `GOVERNANCE_WEBHOOK_FAILURE_POLICY=Fail`. Then:
  - Operator down: every `TykOasApiDefinition` create fails, governed or not
    (`failed calling webhook "vtykoasapidefinition.kb.io": ... connection refused`).
  - Dashboard down, Operator up: a governed API is denied with `governance preflight could not
    run and the failure policy is Fail: governance: sandbox request: Post ...`; an API without
    categories is created.
- **The webhook judges against the Dashboard's copy of the ruleset.** Its call names ruleset
  IDs (visible in the `Fail` message above), so a `TykRuleset` change applies only once the
  Operator has pushed it. A rule added to a `TykRuleset` may already block an API applied
  straight afterwards, but that depends on timing; to be certain, wait for the `TykRuleset`'s
  `status.latestTransaction.status` to read `Successful` with a new time before applying APIs,
  or apply rulesets in an earlier sync wave than APIs.
- **Use string overrides in `ruleset.raw`.** The object form in the public example
  (`operation-operationId: {severity: error}`) leaves the `TykRuleset` `Failed` with
  `HTTP 400: definition is not a valid Spectral ruleset ...`; write `operation-operationId: error`
  (`writing-rules.md`).
- `kubectl get tykrs` shows `SYNCSTATUS Successful` only when the Dashboard accepted the
  ruleset; check it after every change, together with the Dashboard's copy
  (`GET /api/rulesets/<RULESETID>` reads `"action":"warn"` for a `block` resource).
