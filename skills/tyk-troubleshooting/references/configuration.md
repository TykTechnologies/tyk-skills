Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`)

# Which configuration value is in force

## Precedence

A Tyk component reads its configuration file (`tyk.conf` for the Gateway), then environment
variables override single fields. On Gateway 5.16.0:

| `tyk.conf` | Environment | In force |
| --- | --- | --- |
| `"proxy_default_timeout": 5` | `TYK_GW_PROXYDEFAULTTIMEOUT=2` | 2: a 3-second upstream call gets `504` after about 2 s |
| same | also `TYK_GW_PROXY_DEFAULT_TIMEOUT=1` (misspelt) | still 2: the misspelt variable is ignored without a warning |
| `"log_level": "info"` | `TYK_GW_LOGLEVEL=debug` and `TYK_LOGLEVEL=warn` | `warn`: the global variable wins (`health-and-logs.md`) |

The variable name is `TYK_GW_` plus the JSON path with underscores between nested sections
and no underscores inside a key: `proxy_default_timeout` is `TYK_GW_PROXYDEFAULTTIMEOUT`,
`http_server_options.enable_strict_routes` is `TYK_GW_HTTPSERVEROPTIONS_ENABLESTRICTROUTES`.
The Dashboard uses `TYK_DB_`, Pump `TYK_PMP_`, the Portal `PORTAL_`. A name that does not map
to a field is ignored silently, so check the name, not just the value.

Docs: https://tyk.io/docs/tyk-oss-gateway/configuration (every Gateway field with its
variable), https://tyk.io/docs/tyk-dashboard/configuration

## Ask the Gateway what it is using

Switch on config inspection (`enable_config_inspection: true` or
`TYK_GW_ENABLECONFIGINSPECTION=true`, restart), then ask the control API with the Gateway
secret:

```bash
curl -s "$GW/config?field=proxy_default_timeout" -H "x-tyk-authorization: $SECRET"
# {"config_field":"proxy_default_timeout","env":"TYK_GW_PROXYDEFAULTTIMEOUT","value":"2","obfuscated":false}
curl -s "$GW/env?env=TYK_GW_PROXYDEFAULTTIMEOUT" -H "x-tyk-authorization: $SECRET"
# same shape, looked up by variable name
curl -s "$GW/env?env=TYK_GW_PROXY_DEFAULT_TIMEOUT" -H "x-tyk-authorization: $SECRET"
# 404 {"error":"environment variable not found"}   <- the name is wrong
curl -s "$GW/config?field=secret" -H "x-tyk-authorization: $SECRET"
# {"config_field":"secret","env":"TYK_GW_SECRET","value":"*REDACTED*","obfuscated":true}
```

- Without the secret header the endpoints answer 403. An unknown field answers
  `404 {"error":"field not found"}`.
- `GET /env` with no parameter lists every valid variable with its current value.
- Secret fields (the Gateway secret, node secret, storage passwords and others) come back as
  `*REDACTED*`.
- The value shown is the Gateway's own setting. `TYK_LOGLEVEL` overrode the level without
  changing what `/config?field=log_level` reported.
- Switch inspection off again when you are done; it is a view of the whole configuration.

Docs: https://tyk.io/docs/planning-for-production/ensure-high-availability/config-inspection

## Common precedence traps

- A Compose `env_file` and an `environment:` block both set the same variable: Compose uses
  the `environment:` value. Read the container's real environment with
  `docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' <container>` and
  filter out secrets before sharing it.
- A setting changed in `tyk.conf` but overridden by a variable in the Helm values, Compose
  file or systemd unit does nothing. Config inspection shows which variable holds the field.
