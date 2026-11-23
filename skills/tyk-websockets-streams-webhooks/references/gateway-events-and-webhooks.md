# Gateway events and webhooks

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` and `tykio/tyk-gateway-ee:v5.16.0`), Tyk Dashboard 5.16.0

Webhooks and log handlers run on the open source Gateway; global webhooks need a Dashboard. See
https://tyk.io/docs/api-management/gateway-events.

## Event types

The Gateway fires these events (see the docs page above and
https://github.com/TykTechnologies/tyk/blob/v5.16.0/internal/event/event.go):

| Group | Events |
| --- | --- |
| Rate limit | `RatelimitExceeded` (lower-case `l`), `OrgRateLimitExceeded`, `RateLimitSmoothingUp`, `RateLimitSmoothingDown` |
| Quota | `QuotaExceeded`, `OrgQuotaExceeded`; `TriggerExceeded` from the Gateway-level quota monitor |
| Authentication | `AuthFailure`, `KeyExpired`, `UpstreamOAuthError` |
| Versions | `VersionFailure` |
| Circuit breaker | `BreakerTripped`, `BreakerReset`, `BreakerTriggered` |
| Uptime checks | `HostDown`, `HostUp` |
| Keys | `TokenCreated`, `TokenUpdated`, `TokenDeleted` |
| Certificates | `CertificateExpiringSoon`, `CertificateExpired` |
| OAuth 2.0 | `OAuth2ScopeCheckFailed`, `OAuth2ExchangeSucceeded`, `OAuth2ExchangeFailed` (fired, but not accepted as a `trigger` in a Tyk OAS `eventHandlers` entry on 5.16.0) |

A Tyk OAS handler's `trigger` must be one of the 21 names in the Gateway's schema: every event
above except the three OAuth 2.0 ones (public schema at
https://github.com/TykTechnologies/tyk/blob/v5.16.0/apidef/oas/schema/x-tyk-api-gateway.json,
`X-Tyk-EventTrigger`). `TriggerExceeded` is accepted (`200 added`);
`OAuth2ScopeCheckFailed` as the first handler gets `400 ... trigger must be one of the following`.
Names are exact and case-sensitive: `RateLimitExceeded` as the first handler gets the same `400`.

What fires, with what the client gets (auth-token API, default `message` values):

| Client request | Client gets | Event | `{{.Meta.Message}}` |
| --- | --- | --- | --- |
| Over the key's rate limit | `429 {"error": "Rate Limit Exceeded"}` | `RatelimitExceeded` | `Rate Limit Exceeded` |
| Over the key's quota | `403 {"error": "Quota exceeded"}` | `QuotaExceeded` | `Key Quota Limit Exceeded` |
| Unknown key | `403 {"error": "Access to this API has been disallowed"}` | `AuthFailure` | `Auth Failure` |
| Key past its `expires` time | `401 {"error": "Key has expired, please renew"}` | `KeyExpired` | `Attempted access from expired key.` |
| Over the rate limit on a Streams publish path | `429` | `RatelimitExceeded` | `Rate Limit Exceeded` |

Each failing request fires its own event (three `429`s, three webhooks), subject to the cooldown
below.

## A webhook in a Tyk OAS API

Handlers go in `x-tyk-api-gateway.server.eventHandlers` (`assets/streams-lab/definitions/alerts.json`):

```json
"eventHandlers": [
  {"enabled": true, "trigger": "RatelimitExceeded", "type": "webhook", "name": "rate-limit",
   "url": "http://hooks:9000/tyk/ratelimit", "method": "POST", "cooldownPeriod": "1s",
   "bodyTemplate": "custom/event.json", "headers": [{"name": "X-Hook-Source", "value": "tyk"}]},
  {"enabled": true, "trigger": "AuthFailure", "type": "log", "name": "auth-failure-log", "logPrefix": "auth-failure"}
]
```

Rules from the Gateway API (`POST /tyk/apis/oas`) on 5.16.0. **It checks these fields on the
first handler only.** The same mistakes in the second handler (after a valid `log` handler)
are stored with `200 {"status":"ok","action":"added"}`: a wrong-case trigger, an OAuth 2.0
trigger, `"type": "email"`, and a webhook without `bodyTemplate` and `cooldownPeriod`.
A stored handler with a wrong trigger name never matches an event, so it never fires. Run
`scripts/check_events_def.py`, which checks every handler, before loading.

- `bodyTemplate` and `cooldownPeriod` are required for a webhook (`bodyTemplate is required`,
  `cooldownPeriod is required`). A global webhook (`id`) needs `cooldownPeriod` too.
- `cooldownPeriod` must match `^(\d+h)?(\d+m)?(\d+s)?$`: `"500ms"`, `"1.5s"` and `"0"` are
  refused (`Does not match pattern`); `"0s"`, `"0m0s"` and `""` are accepted.
- `type` must be `webhook`, `log` or `custom`. A `log` handler needs `name` and `logPrefix`
  (`name is required`, `logPrefix is required`).
  The docs say log handlers are Classic-only; on 5.16.0 a Tyk OAS `log` handler writes
  `level=warning message="auth-failure:AuthFailure"` to the Gateway log.
- `url` must be absolute. `hooks:9000/x` is accepted, then every delivery fails with
  `Webhook request failed: Post "hooks:9000/x": unsupported protocol scheme "hooks"` in the log.

## What arrives at the receiver

```bash
python3 "$SKILL/scripts/webhook_receiver.py" --port 9000      # or the lab's hooks service
curl -s $HOOKS/_received
```

```json
{"method": "POST", "path": "/tyk/ratelimit",
 "headers": {"User-Agent": "Tyk-Hookshot", "Content-Type": "application/json", "X-Hook-Source": "tyk"},
 "body": "{\"event\": \"RatelimitExceeded\", \"api_path\": \"/alerts/get\", \"origin\": \"10.250.52.1\", \"message\": \"Rate Limit Exceeded\", \"at\": \"2026-11-19 14:57:43.365724814 &#43;0000 UTC\"}\n"}
```

This body comes from the lab template `templates/event.json`, mounted at
`<template_path>/custom/event.json`:

```text
{"event": "{{.Type}}", "api_path": "{{.Meta.Path}}", "origin": "{{.Meta.Origin}}", "message": "{{.Meta.Message}}", "at": "{{.TimeStamp}}"}
```

### Templates

- **The path is resolved under the Gateway's `template_path`.** `custom/event.json` and the
  absolute `/opt/tyk-gateway/templates/custom/event.json` both work. `templates/custom/event.json`
  (the form the docs examples use) is looked for at
  `/opt/tyk-gateway/templates/templates/custom/event.json`, fails with
  `Custom template load failure, using default.` in the log, and the default template is sent
  instead. A file outside `template_path` needs `allow_unsafe_webhook_template_paths` (5.16.0
  release notes: https://tyk.io/docs/developer-support/release-notes/gateway).
- **Values are HTML-escaped.** Templates are rendered with Go's HTML template engine: `+` becomes
  `&#43;`, `"` becomes `&#34;` and `<` becomes `&lt;` (a request to `/alerts/a"b<c>` gives
  `"api_path": "/alerts/a&#34;b&lt;c&gt;"`). Receivers must decode entities or avoid such fields.
- `Content-Type: application/json` is set for templates whose name ends in `.json`.
- The default template includes the `key` field (`"key": "{{.Meta.Key}}"`, docs "Webhook
  payload"); for `AuthFailure` it holds the value the client tried. Use your own template
  without `{{.Meta.Key}}` unless the receiver needs it.
- Field names per event are on the docs page ("Event Metadata"); `{{.Type}}` is the event name and
  `{{.TimeStamp}}` a string such as `2026-11-19 14:29:33.628007234 +0000 UTC`.

### Cooldown, retries and timing

- **Cooldown de-duplicates identical requests, not events.** The Gateway hashes the method, URL and
  body; a second identical webhook inside `cooldownPeriod` is dropped. A body that differs (another
  path, or a timestamp in the template) is always sent: with `{{.TimeStamp}}` in the template,
  three `429`s within 2 s give three webhooks despite a `1s` cooldown.
- **`"cooldownPeriod": "0s"` sends an identical payload once, ever.** The de-duplication record is
  stored in Redis with no expiry (`ttl -1`); further `429`s on the same path from the same key send
  nothing, while a `429` on another path does. Use at least `1s`.
- **No retries.** A receiver that answers `500` gets exactly one request; the Gateway logs
  `Request to webhook failed` with `responseCode=500`. An unreachable receiver logs
  `Webhook request failed: ...`. Make the receiver idempotent and always up, or queue behind it.
- **The client is not delayed.** A `403` that fires a webhook to an unreachable host returns in
  about 0.03 s.

## Global webhooks (Dashboard)

Defined once in the Dashboard, referenced by id from any API:

```bash
curl -s -X POST "$DASH/api/hooks" -H "Authorization: $DASHBOARD_API_KEY" -d '{"name": "Example Corp alerts",
  "method": "POST", "target_path": "http://hooks:9000/global", "template_path": "",
  "header_map": {"X-Source": "tyk-global"}, "event_timeout": 1}'
# {"Status":"OK","Message":"Webhook created","Meta":null}
curl -s "$DASH/api/hooks" -H "Authorization: $DASHBOARD_API_KEY"     # {"hooks": [{"id": "...", ...}], "pages": ...}
```

In the API: `{"enabled": true, "trigger": "AuthFailure", "type": "webhook", "id": "<hook id>",
"cooldownPeriod": "1s"}`. The Dashboard copies `name`, `url`, `method` and `headers` into the stored
definition (the docs list `url`, `method`, `bodyTemplate` and `headers`; the global webhook above has
an empty template path), and a bad key then produces a webhook with `X-Source: tyk-global` and the
default template body. Docs: https://tyk.io/docs/api-management/gateway-events#using-webhooks-with-tyk-dashboard.

## Elsewhere

- Key lifecycle events: on 5.16.0, creating a key through the Gateway API does not fire the API's
  `TokenCreated` webhook. Prove the exact path your keys take (Dashboard, Portal, Gateway API)
  before you rely on these events.
- Custom JavaScript event handlers (`type: custom`) and the Gateway-level quota monitor
  (`monitor` in `tyk.conf`, which fires `TriggerExceeded`): the docs page above. JavaScript
  plugins in general: the `tyk-custom-plugins` skill.
