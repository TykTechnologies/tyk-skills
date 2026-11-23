# Governance in CI: the Dashboard sandbox gate and offline lint

Last verified: 2026-11-19 against Tyk Dashboard 5.16.0, vacuum CLI v0.26.7 and v0.41.0 (`dshanley/vacuum` images), Spectral CLI 6.17.1 (`stoplight/spectral` image)

Public page:
https://tyk.io/docs/tyk-governance/cicd-checks. Open source linters: https://quobix.com/vacuum/
and https://github.com/stoplightio/spectral.

Two gates, used together:

| Gate | Needs | Gives |
| --- | --- | --- |
| Dashboard sandbox (`scripts/governance_check.py check`) | network access to the Dashboard and a token | the Dashboard's own engine and the rulesets it holds now; the authoritative answer |
| Offline lint (`vacuum` with exported rulesets) | Docker or the vacuum binary | fast feedback with no Dashboard access; matches the Dashboard only as far as the table below shows |

## The sandbox gate

`POST /api/evaluations/sandbox/oas` evaluates the file you send and stores nothing
(`dashboard-api.md` has the raw call). It answers HTTP 200 whenever it ran, compliant or not, so
a gate must read the body. `scripts/governance_check.py` does that:

```bash
export TYK_DASHBOARD_URL=https://dashboard.example.com
export TYK_DASHBOARD_TOKEN=<API key of a CI user>   # from the CI secret store; see the token note below
python3 scripts/governance_check.py check apis/orders.json --categories example-payments --api-id orders
```

Real output (exit code 2):

```text
ruleset house-standards 'House standards' action=warn: 2 error(s), 0 warning(s), 0 info
ruleset owasp-baseline 'OWASP baseline' action=warn: 5 error(s), 4 warning(s), 1 info
  error house-standards operation-operationId at paths./orders/{id}.operationId: Operation has no operationId.
        fix: Add a unique operationId.
...
Result: non-compliant (7 error(s) in gating rulesets).
```

| Exit | Meaning | Example |
| --- | --- | --- |
| 0 | passes at the chosen strictness | `assets/orders.oas.json` against `tyk-gateway-baseline,spectral-owasp` |
| 1 | warnings only, with `--strictness strict` | a document with `warn` findings and no `error` |
| 2 | at least one `error` finding | the call above |
| 3 | could not evaluate | a misspelt category (`no ruleset matched ...`), an unknown ruleset ID (`HTTP 404 ... ruleset nope not found`), a 403, a truncated or unparsable answer |

Choices it makes, and why:

- **An empty `rulesets` list is a failure.** A misspelt or unused category returns 200 with
  zero rulesets and zero errors; a gate that only reads `error_count` passes for ever.
- **Every matched ruleset gates by default.** Dashboard compliance ignores `action`; the public
  example script counts only rulesets whose `action` is not `none`, so it can pass a file the
  Dashboard marks non-compliant. `--honor-action` gives the example's behaviour and exits 3 if
  no gating ruleset matched.
- `--strictness standard` (default) fails on errors, `strict` also on warnings, `permissive`
  never fails on findings. All three still exit 3 when the check could not run. Pass
  `--strictness` explicitly in every pipeline, so a gate copied from elsewhere (a demo bundle's
  gate script, for example) gives the same result for the same file.
- `--categories` follows whatever the platform team links to the category today;
  `--ruleset-ids` pins a fixed set and reaches templates and draft rulesets. At most 20 IDs.
- The CI page asks for `apis: write` on the token; on 5.16.0 `apis: read` is enough
  (`concepts.md`, RBAC table). Start with read; if the sandbox answers 403, grant write. Use a dedicated CI user; keep the
  key in the CI secret store.

## Offline lint with vacuum

Export the definitions once (or on a schedule) and lint with the same rules:

```bash
python3 scripts/governance_check.py export --ruleset-ids house-standards,owasp-baseline --out governance/rulesets
# wrote governance/rulesets/house-standards.json
# wrote governance/rulesets/owasp-baseline.json
docker run --rm -v "$PWD:/work:ro" -w /work dshanley/vacuum:v0.26.7 \
  lint -r governance/rulesets/house-standards.json -d -b apis/orders.json
```

vacuum exits 1 when it finds an `error` (`Failed with 2 errors, 0 warnings and 0 informs.`) and
0 when clean.

### Where the linters and the Dashboard agree

How each linter's rule IDs and severities compare with Dashboard 5.16.0, across three documents
(a legacy API, a deliberately insecure one, `assets/orders.oas.json`) and six rulesets (a custom
set with `howToFix`, the OWASP and OpenAPI templates, `tyk-gateway-baseline`, an
`off`-plus-override set, a templated-message set):

| | vacuum v0.26.7 | vacuum v0.41.0 | Spectral CLI 6.17.1 |
| --- | --- | --- | --- |
| Custom rules on the OpenAPI document and on `x-tyk-api-gateway` | same | same | rejects any rule with `howToFix` |
| `extends: [["spectral:owasp","all"]]` | same | 2 extra `owasp-string-limit` errors on the insecure document, and `owasp-protection-global-unsafe-strict` at `error` instead of `info` | cannot resolve `spectral:owasp` |
| `extends: [["spectral:oas","all"]]` | same | same | different rule names (`oas3-server-not-example.com`, `license-url`) and severities (`info-description` and `operation-operationId` at `warn`; the Dashboard says `error`) |
| `[["spectral:oas","off"]]` plus a string override | same (nothing) | reports the overridden rule | reports the overridden rule |
| `"message": "{{description}}"` | same (literal) | fills in the description | fills in the description |
| Unknown function name | reports `Rule 'r4' uses unknown function`, exits 1 | refuses to lint (`uses unknown function`) | refuses the ruleset (`Function is not defined`) |

`path` strings differ between all of them (for example `paths./orders/{id}.operationId` from the
Dashboard against `paths./orders/{id}.get.operationId` from vacuum). Match findings on rule ID
and severity, not on path.

Pin `dshanley/vacuum:v0.26.7` for the offline job, and keep the Dashboard sandbox gate as the
one that decides. Before moving the pin, run both gates over the same files and compare.

One more difference that no linter can reproduce: the Dashboard evaluates its stored copy,
whose `servers` it rewrites (`owasp-and-security-rules.md`). A file that passes in CI can fail
`owasp-security-hosts-https-oas3` after import.

## The workflow asset

`assets/github-actions/api-governance.yml` runs both gates on pull requests that touch `apis/`
or `governance/rulesets/`. Copy `scripts/governance_check.py` to `ci/`, commit the exported
rulesets, set the `TYK_DASHBOARD_URL` and `TYK_DASHBOARD_TOKEN` secrets, and change
`--categories payments` to your category. Against Dashboard 5.16.0 the offline job exits 1 on
the legacy document and 0 on `assets/orders.oas.json`; the sandbox job exits 2 and 0 for the
same pair. The sandbox job makes
two calls per file: one by category (the Dashboard's rulesets as they are) and one with
`--ruleset-ids spectral-owasp`, so the HTTPS rule stays at `error` in CI even when the
Dashboard ruleset has it at `warn` (`assets/orders.oas.json` exits 0 on each call; with an
`http://` server, 0 and 2).
