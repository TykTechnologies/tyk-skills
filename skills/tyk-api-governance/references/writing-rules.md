# Writing rules that the Dashboard runs as you meant

Last verified: 2026-11-19 against Tyk Dashboard 5.16.0, vacuum CLI v0.26.7 and v0.41.0, Spectral CLI 6.17.1

Each behaviour below is what `POST /api/evaluations/sandbox/oas?ruleset_ids=<id>` reports on
Dashboard 5.16.0. Public pages:
https://tyk.io/docs/tyk-governance/rulesets (format and examples),
https://tyk.io/docs/tyk-governance/ruleset-templates,
https://github.com/stoplightio/spectral/blob/develop/docs/getting-started/3-rulesets.md (the Spectral format), https://quobix.com/vacuum/rulesets/understanding/ (vacuum).

## Shape of a rule

```json
{
  "rules": {
    "tyk-authentication-enabled": {
      "description": "The Gateway must authenticate every request to this API.",
      "message": "x-tyk-api-gateway.server.authentication.enabled is not true.",
      "severity": "error",
      "given": "$['x-tyk-api-gateway'].server",
      "then": {"field": "authentication.enabled", "function": "truthy"},
      "howToFix": "Set x-tyk-api-gateway.server.authentication.enabled to true."
    }
  }
}
```

- `given` is a JSONPath into the document; `then.field` is a key (or dotted key path) under each
  match; `then.function` is a built-in function (`truthy`, `pattern`, `enumeration`, ...).
- `howToFix` is shown as `how_to_fix` in every result and in the Governance tab. It is not part
  of the Spectral format: the Spectral CLI rejects a ruleset that has it
  (`must NOT have additional properties`); vacuum accepts it.
- `severity`: `error`, `warn`, `info`, `hint`, or `off`. Omitted means `warn`.
- The definition may be JSON or YAML; both are accepted and evaluated alike.
- Rules can target the OpenAPI document and the `x-tyk-api-gateway` extension in the same
  ruleset. Use the bracket form `$['x-tyk-api-gateway']` because of the hyphens.
- `assets/rulesets/tyk-gateway-baseline.json` is a complete example with four rules on
  the extension and the operations.

## Starting from a template

The two shipped templates are `{"extends":[["spectral:owasp","all"]]}` (OWASP API Security) and
`{"extends":[["spectral:oas","all"]]}` (OpenAPI Best Practices). These forms of `extends`
work: `[["spectral:oas","all"]]`, `[["spectral:oas","recommended"]]`, `["spectral:oas"]`
and the YAML `extends: spectral:oas`.

Change a template rule's severity, or switch it off, with **a string value**:

```json
{"extends": [["spectral:owasp", "all"]],
 "rules": {"owasp-security-hosts-https-oas3": "warn", "owasp-rate-limit": "off"}}
```

That ruleset reports `owasp-security-hosts-https-oas3` as `warn` and no `owasp-rate-limit`.

Three shapes that do not do what they say:

1. **An object override is rejected.** The form shown in the public Operator page,

   ```yaml
   extends: ["spectral:oas"]
   rules:
     operation-operationId:
       severity: error
   ```

   gives `400 definition is not a valid Spectral ruleset: at '/rules/operation-operationId':
   missing properties 'given', 'then'; at '/rules/operation-operationId': value must be one of
   'error', 'warn', 'info', 'hint', 'off'; ...` from the Dashboard, and a `TykRuleset` in that
   form stays `SYNCSTATUS Failed` with the same message. Write `operation-operationId: error`.
2. **`off` plus an override evaluates nothing.** `{"extends":[["spectral:oas","off"]],
   "rules":{"operation-operationId":"error"}}` is accepted and reports 0 issues on a document
   whose operations have no `operationId`. Spectral CLI 6.17.1 and vacuum v0.41.0 both report
   the error for the same pair; vacuum v0.26.7 reports nothing, like the Dashboard. To run one
   rule, write it out in full (as above) instead of switching a template off.
3. **Message templates are printed as written.** `"message": "{{description}}"` appears as
   `{{description}}` in sandbox results and in the saved findings of
   `/api/governance/evaluation-details`. vacuum v0.41.0 fills it in; v0.26.7 does not. Write
   the message out.

## Paths that silently pass

A rule only runs where `given` matches. If the node does not exist, nothing is reported:

| Rule (all `function: truthy`, severity `error`) | No `authentication` block | `enabled: false` | `enabled: true` |
| --- | --- | --- | --- |
| `given: $['x-tyk-api-gateway'].server.authentication`, `field: enabled` | **passes** | fails | passes |
| `given: $['x-tyk-api-gateway'].server`, `field: authentication.enabled` | fails | fails | passes |
| `given: $`, `field: x-tyk-api-gateway.server.authentication.enabled` | fails | fails | passes |
| `given: $['x-tyk-api-gateway'].server`, `field: $.authentication.enabled` | fails | fails | **fails** |

Point `given` at a node that always exists and put the rest of the path in `field` as dotted
keys. Do not start `field` with `$`. Test every new rule against a document that lacks the
block entirely, not only against one where the value is wrong.

## Mistakes the Dashboard accepts

- **Unknown function.** `"then":{"function":"doesNotExist"}` is accepted with 201. Every
  evaluation then reports an `error` issue `Unknown function 'doesNotExist' in rule 'r4'` on
  each API in the ruleset's categories, so they all go non-compliant. vacuum catches it
  (v0.26.7: `Rule 'r4' uses unknown function 'doesNotExist'`, exit 1), which makes a local
  vacuum run a useful check before you upload a ruleset.
- **A ruleset created without `"active": true`** is inactive. **A `PUT` without `active`**
  deactivates it. Read back after every write.

## Try a rule before it governs anything

Create the ruleset with `"categories": []` (or inactive) and evaluate it by ID:

```bash
curl -s -X POST "$DASH/api/evaluations/sandbox/oas?ruleset_ids=house-standards" \
  -H "Authorization: $DASHBOARD_API_KEY" -H 'Content-Type: application/json' --data-binary @orders.json
```

`ruleset_ids` evaluated both a category-less ruleset and an inactive one. Link the categories
(GET, edit, PUT the whole record) once the results are what you expect.
