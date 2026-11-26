# Proving the upgrade: snapshot, diff, probes

Last verified: 2026-11-19 against Tyk Dashboard and Gateway 5.15.0, 5.15.1 and 5.16.0, Tyk Pump 1.17.0 and 1.18.0, Tyk Developer Portal 1.19.0, 1.20.0 and 1.20.1

## The state snapshot script

`scripts/tyk_state_snapshot.py` (Python standard library only) reads the stack's state through
its APIs and writes one JSON file:

| Section | Read from | Recorded |
| --- | --- | --- |
| APIs | Dashboard `GET /api/apis?p=-1`, then `GET /api/apis/oas/{id}` per Tyk OAS API | full definition per API ID, volatile fields removed |
| Policies | Dashboard `GET /api/portal/policies?p=-1` | full definition per policy ID |
| Keys | Dashboard `GET /api/keys/detailed/?p=-1` | count and key hashes |
| Gateway | `GET /hello`, `GET /tyk/apis` | version, IDs of the loaded APIs |
| Portal | `GET /portal-api/{products,plans,catalogues,providers,organisations}`, paged | full objects |
| Portal | `GET /portal-api/{users,apps}` | counts and IDs only, no personal data |

Fields ignored by default, at any depth: `created_at`, `updated_at`, `last_updated`,
`date_created`, `CreatedAt`, `UpdatedAt`, `LastSynced`; `--ignore NAME` adds more. Policy
`access_rights_array` is compared without regard to order, because its order can change
between two reads of an unchanged policy.

Settings come from the environment, so nothing secret is typed on the command line:

```bash
export DASH=http://localhost:3000        DASHBOARD_API_KEY=<Dashboard user API key>
export GW=http://localhost:8080          GW_SECRET=<Gateway secret>                  # optional
export PORTAL_URL=http://localhost:3001  PORTAL_API_TOKEN=<Portal admin API token>   # optional
python3 scripts/tyk_state_snapshot.py snapshot --out before.json --label "5.15.1 / 1.19.0"
python3 scripts/tyk_state_snapshot.py preflight before.json
# ... upgrade ...
python3 scripts/tyk_state_snapshot.py snapshot --out after.json --label "5.16.0 / 1.20.0"
python3 scripts/tyk_state_snapshot.py diff before.json after.json --allow-new-fields
```

The script reads these names, the ones the other Tyk skills use. It also accepts
`TYK_DASHBOARD_URL`, `TYK_DASHBOARD_API_KEY`, `TYK_GATEWAY_URL`, `TYK_GATEWAY_SECRET`,
`TYK_PORTAL_URL` and `TYK_PORTAL_TOKEN`, which win when both are set.

`preflight` works from the snapshot only, so it does not see the Gateway configuration file.
Check three things there by hand: `analytics_config.enable_detailed_recording` (detailed
records for every API, so the redaction applies to all of them), `template_path` (a
`template-path` finding is a file to check against it, not a certain failure: a path that
resolves inside `template_path` is accepted), and whether `apply_strip_listen_path_to_url_rewrite`
is set (if it is `true`, the `rewrite-strip` findings keep the old behaviour).

Exit codes: `snapshot` 0, or 2 when a section could not be read (the file is still written,
with the error under `errors`); `diff` 0 when the state matches, 1 when it differs;
`preflight` 0 with no findings, 1 with findings. The output file is created with mode 600.

### Output from a minor upgrade (5.15.1 / 1.19.0 to 5.16.0 / 1.20.0)

```text
$ tyk_state_snapshot.py preflight m0-5.15.1.json
unsafe-api-id: example:partner-a (Example Corp Partner A): after Dashboard 5.16.0 an update through the Dashboard API is refused with 400 unless allow_unsafe_api_ids is true
detailed-logs: example-api-key (Example Corp API Key Services): from Gateway 5.16.0 detailed records store the Authorization and custom auth headers redacted
...
rewrite-strip: example-rewrite (Example Corp Rewrite) operation itemsget: rewrite target starts with the listen path /rewrite/; from Gateway 5.16.0 the listen path is no longer stripped from it unless the Gateway sets apply_strip_listen_path_to_url_rewrite: true
5 finding(s)
not read: the Gateway configuration file; check analytics_config.enable_detailed_recording, template_path and apply_strip_listen_path_to_url_rewrite there

$ tyk_state_snapshot.py diff m0-5.15.1.json m4-final.json
before (... minor start ...): apis=6 policies=3 keys=5 gateway=5.15.1 gateway_loaded=6 portal_products=1 portal_plans=1 portal_catalogues=3 portal_users=1 portal_apps=0
after  (... 5.16.0 ...):      apis=6 policies=3 keys=5 gateway=5.16.0 gateway_loaded=6 portal_products=1 portal_plans=1 portal_catalogues=3 portal_users=1 portal_apps=0
api: changed example-classic (Example Corp Classic): +proxy.dns_discovery
portal product: changed 1 (Example Corp Shared Services): +APIDetails[0].RequiredScopes
2 difference(s)

$ tyk_state_snapshot.py diff m0-5.15.1.json m4-final.json --allow-new-fields
note: api: changed example-classic (Example Corp Classic): +proxy.dns_discovery
note: portal product: changed 1 (Example Corp Shared Services): +APIDetails[0].RequiredScopes
no differences
```

A changed value prints old and new, for example
`portal provider: changed 1 (Example Corp Dashboard): Status: "Up" -> "Down"`.

### Two things the snapshot needs

- **Key listing (optional).** With hashed keys (the default), the Dashboard key list fails until the
  Gateway allows hashed key listing. Before 5.16.3 the Gateway answers
  `{"status":"error","message":"Hashed key listing is disabled in config (enable_hashed_keys_listing)"}`;
  from 5.16.3 the message reads `Hashed key listing is disabled in the Gateway config: set
  enable_hashed_keys_listing to true in tyk.conf (or TYK_GW_ENABLEHASHEDKEYSLISTING)`.
  The Dashboard answers `404 {"Status":"Error","Message":"Could not retrieve keys","Meta":null}`.
  Setting `enable_hashed_keys_listing: true` (`TYK_GW_ENABLEHASHEDKEYSLISTING=true`) on the
  Gateway makes `GET /api/keys/detailed/?p=-1` return the key hashes. Without it the snapshot
  records the error and the other sections. The setting only serves the snapshot: skip the key
  section if you do not want it, or set it back after the last snapshot. `GET /api/keys/`
  itself is not a JSON route: it returns the Dashboard's HTML login page with status 200.
- **A valid Portal token** after a Portal 1.20.1 upgrade (`breaking-changes-5-15-to-5-16.md`).

## Traffic probes

The snapshot proves configuration survived; it does not prove requests behave the same. Keep
a short list of real calls with expected status and, where an upstream echoes it, the
upstream path, and run it before and after every step. Include at least one keyless API, one
keyed API with and without a key, and every API that `preflight` flagged.

```bash
curl -s -o /dev/null -w '%{http_code}\n' "$GW/<listen path>/<path>"                       # 200
curl -s -o /dev/null -w '%{http_code}\n' "$GW/<keyed listen path>/<path>"                 # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: $KEY" "$GW/<keyed listen path>/<path>"  # 200
```

A probe key must not sit on a quota you can exhaust: a key on a policy with a quota starts
answering 429 and then 403 under a 0.2 s probe loop. Use a key without a quota for the
loop.

## Health endpoints

| Component | Call | Answer when healthy |
| --- | --- | --- |
| Gateway | `GET /hello` | `{"status":"pass","version":"5.16.0","description":"Tyk GW",...}`; a liveness check |
| Gateway | `GET /ready` | `{"status":"pass","version":"5.16.0","description":"Tyk GW Ready",...}`; 503 until APIs load |
| Dashboard | `GET /hello` | `{"status": "ok"}` (no version) |
| Portal | `GET /live`, `GET /ready` | `{"code":"OK","message":"Success"}` |
| Pump | log line | `## Tyk Pump, 1.18.0 ##`, then `Purged N records...` |

The Dashboard and Portal versions are not in these answers: read them from the image tag
(`docker compose images`) or the start-up log (`Version: 1.20.1` for the Portal).

## Portal checks after the upgrade

From the Portal upgrade guide, https://tyk.io/docs/portal/install/upgrade-guide
("Post-Upgrade Verification"): Admin Portal reachable, provider sync healthy, consumer login
works, catalogue visible, theme renders, no `ERROR` or `WARN` in the logs. Through the API:

```bash
curl -s -H "Authorization: $PORTAL_API_TOKEN" "$PORTAL_URL/portal-api/providers" | jq -c '.[] | {Status, LastSynced}'
# {"Status":"Up","LastSynced":"Just now"}
```

A provider showing `"Down"` after a restart or restore comes back `"Up"` after
`PUT /portal-api/providers/{id}/synchronize`. The guide also says a policy that references a
deleted API makes the whole sync fail. The stored default theme is not upgraded in place,
custom theme or not (`backups-and-rollback.md`); the `tyk-developer-portal-branding` skill gets
the new default and re-bases on it.
