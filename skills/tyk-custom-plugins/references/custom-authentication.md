# Custom authentication

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source)

Use it only when no built-in method fits (the `tyk-authentication` skill covers those). The
plugin reads the credential, refuses the request or returns a session, and the Gateway then applies
that session's rate limit, quota and access rights. Docs:
https://tyk.io/docs/api-management/authentication/custom-auth

## Definition

```json
"x-tyk-api-gateway": {"server": {"authentication": {"enabled": true,
  "custom": {"enabled": true, "config": {"enabled": true, "functionName": "PartnerAuth",
                                          "path": "/opt/tyk-gateway/plugins/go/demo.so"}}}},
  "middleware": {"global": {"pluginConfig": {"driver": "goplugin"}}}}
```

The same block works with `javascript` (path to the `.js` file) and `grpc` (no `path`; the server
gets hook type `CustomKeyCheck`).

## Give each caller its own limits

On 5.16.0 the sessions that a custom authentication plugin returns all share one rate-limit
counter: with two partners at 2 requests a minute, partner-a gets 200, 200, 429 and partner-b's
first request gets 429 (Go and JavaScript plugins alike). A distinct `KeyID` per caller in Go
does not change it: with `session.KeyID = api.ID + ":" + partner` set before
`ctx.SetSession(r, session, true)` and no `rate_limit_pattern`, partner-a gets 200, 200, 429 and
partner-b 429, 429, 429.

Set `rate_limit_pattern` in the session's `meta_data` to a value that is unique per caller; with
`"$tyk_meta.partner"` partner-a gets 200, 200, 429 and partner-b 200, 200, 429. The Gateway keys
the quota counter on the same value as the rate counter (see
https://github.com/TykTechnologies/tyk/blob/v5.16.0/gateway/mw_rate_limiting.go), so whatever
the pattern shares, it shares for quotas too.

The counter is keyed by the pattern's value alone, across APIs: with three custom-auth APIs using
`$tyk_meta.partner`, partner-a's two requests to the first API use up its allowance on the other
two as well (429 on its first request there). For a counter per caller **and** API, put both in the
value; the lab plugins do this, and each API then gives each partner 200, 200, 429:

```go
MetaData: map[string]interface{}{"partner": partner, "rate_key": api.ID + ":" + partner,
	"rate_limit_pattern": "$tyk_meta.rate_key"},
```

```javascript
"meta_data": {"partner": partner, "rate_key": config.APIID + ":" + partner,
              "rate_limit_pattern": "$tyk_meta.rate_key"},
```

```python
o.session.metadata["rate_key"] = api_id + ":" + partner
o.session.metadata["rate_limit_pattern"] = "$tyk_meta.rate_key"
```

The pattern is the documented custom rate-limit key (https://tyk.io/docs/api-management/rate-limit).
Prove it with two callers, and with two APIs when one partner uses several. Other ways to give a
caller per-API limits belong to the `tyk-rate-limits-keys-policies` skill.

## Go

```go
func PartnerAuth(rw http.ResponseWriter, r *http.Request) {
	partner, ok := partners[r.Header.Get("X-Partner-Token")]
	if !ok {
		rw.Header().Set("Content-Type", "application/json")
		rw.WriteHeader(http.StatusUnauthorized)
		_ = json.NewEncoder(rw).Encode(map[string]string{"error": "unknown or missing X-Partner-Token"})
		return
	}
	api := ctx.GetOASDefinition(r).GetTykExtension().Info // Tyk OAS API: ctx.GetDefinition returns nil
	ctx.SetSession(r, &user.SessionState{
		OrgID: api.OrgID, Alias: partner, Rate: 2, Per: 60, QuotaMax: -1,
		MetaData: map[string]interface{}{"partner": partner, "rate_key": api.ID + ":" + partner,
			"rate_limit_pattern": "$tyk_meta.rate_key"},
		AccessRights: map[string]user.AccessDefinition{
			api.ID: {APIID: api.ID, APIName: api.Name, Versions: []string{"Default"}}},
	}, true)
}
```

Missing or unknown token: `401 {"error":"unknown or missing X-Partner-Token"}`. Known token: 200
with the upstream seeing `X-Go-Alias: partner-a` from a later post hook.

## JavaScript

`assets/plugin-lab/plugins/js/auth.js`: refuse with `ReturnOverrides` and `ReturnData`, accept with
`ReturnAuthData(request, {session fields})`. Missing token: `401 {"error": "unknown or missing X-Partner-Token"}`.

## gRPC

Fill `o.session` (`rate`, `per`, `quota_max`, `alias`, `org_id`, `metadata`, `access_rights`), set
`o.metadata["token"]` to the caller's identity, and return the object; refuse with
`return_overrides`. Without `o.metadata["token"]` every request with a valid token gets 403 (the
docs' Python example sets it too). The lab server's `GrpcAuth` gives 401 for a missing token and
200, 200, 429 per partner with the pattern above.
