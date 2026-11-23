# Warn versus block: what happens when a non-compliant API is saved

Last verified: 2026-11-19 against Tyk Dashboard 5.16.0, Tyk Operator 1.6.0

Public pages:
https://tyk.io/docs/tyk-governance/remediate-issues (the Deployment Warning),
https://tyk.io/docs/tyk-governance/tyk-operator (action semantics).

## Summary

| Where the API is saved | `action: none` | `action: warn` | `action: block` |
| --- | --- | --- | --- |
| Dashboard UI (API designer, Save API) | saved; no warning | dialog, then saved if the user picks Save Anyway | not accepted by the Dashboard |
| Dashboard REST (`POST`/`PUT /api/apis/oas`) | saved, plain 200 | saved, plain 200 | not accepted by the Dashboard |
| CI sandbox gate | your exit code decides | your exit code decides | not accepted by the Dashboard |
| Tyk Operator (`kubectl apply`) | admitted | admitted with a `Warning:` line | **denied** when the API has an `error` finding |

Compliance itself does not depend on `action`: an API with an `error` finding is
`non_compliant` whichever row saved it.

## Dashboard UI

With a matching ruleset at `action: warn`, Save API on a non-compliant API opens a dialog:

```text
This API does not fully meet the compliance requirements
Please review the issues for each rule and make the necessary updates before deploying.
Once the issues are resolved, you'll be able to deploy the API as compliant.
Note: If you choose to deploy now, the API will be saved and deployed with a Non-Compliant status.
[Save Anyway] [View Compliance Issues]
```

- **Save Anyway** saves and deploys the API; the toast reads
  `API saved without confirming its compliance status` and the API stays non-compliant.
- **View Compliance Issues** cancels the save and opens the API's Governance tab
  (`/apis/oas/<api id>/governance`).

With every matching ruleset at `action: none`, the same save of the same non-compliant API
shows `This API is in compliance. This API meets the compliance requirements.` and saves it,
while the Governance tab and `GET /api/governance/apis/orders-legacy/compliance` still report
`non_compliant` with 8 errors. The dialog reflects only rulesets with `action: warn`; read
compliance from the Governance tab or the compliance endpoint.

## Dashboard REST

```bash
curl -s -X PUT "$DASH/api/apis/oas/orders-legacy" -H "Authorization: $DASHBOARD_API_KEY" \
  -H 'Content-Type: application/json' --data @orders-legacy.json
# 200 {"Status":"OK","Message":"Api updated","Meta":null}
```

No warning field, no different status, whatever the action. The API is live on the Gateway and
its compliance is evaluated in the background. Tools that write through the Dashboard API
(scripts, Tyk Sync) are not stopped by governance. Put the gate in the pipeline before them.

## Operator

`action: block` exists only on the `TykRuleset` resource. The Operator stores it in the
Dashboard as `warn` (the Dashboard ruleset reads back `"action":"warn"`), so the Dashboard
behaves as in the `warn` column. The hard stop happens in Kubernetes admission:
`operator-admission.md`.
