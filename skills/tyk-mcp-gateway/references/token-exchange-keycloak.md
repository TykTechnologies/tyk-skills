# Token exchange: the tool call reaches the MCP server in the user's context

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway-ee:v5.16.0` and `tykio/tyk-gateway:v5.16.0`) and Keycloak 26.8.1

See https://tyk.io/docs/api-management/authentication/token-exchange,
https://tyk.io/docs/ai-management/mcp-gateway/oauth-2-1.

Token exchange (RFC 8693) swaps the caller's token for one issued for the upstream, keeping the
same user. The docs list it as Enterprise Edition, from Gateway 5.14.0. It applies to a proxy in
front of a remote MCP server. It does not carry the user to the backend of a REST-to-MCP proxy
(last section).

## What the MCP server receives

Caller `alice` signs in to the public client `mcp-client` and calls a tool. Claims of the token
the caller sends, and of the token the MCP server receives:

| Claim | Caller's token | Token at the MCP server |
| --- | --- | --- |
| `aud` | `tyk-gateway` | `orders-backend` |
| `azp` | `mcp-client` | `tyk-gateway` |
| `sub` | alice's id | the same id |
| `preferred_username` | `alice` | `alice` |

The two tokens are different strings. The Gateway log line for each exchange:

```text
level=info msg="oauth2 token exchange" api_id=kc-mcp oauth2_provider=keycloak oauth2_audience=orders-backend
  oauth2_exchange_outcome=ok oauth2_exchange_cache_hit=false oauth2_subject_azp=mcp-client oauth2_exchanged_azp=tyk-gateway
```

The trace has a span named `oauth2.exchange` with `oauth2.exchange.provider`,
`oauth2.exchange.outcome` and `oauth2.exchange.cache_status`.

## Keycloak settings it needs

`assets/keycloak-realm.json` is a realm with these settings (replace the three `__...__`
placeholders).

| Where | Setting | Why |
| --- | --- | --- |
| Client `mcp-client` (public, used by the MCP client) | Audience mapper adding `tyk-gateway` to `aud` | The caller's token then has `aud: tyk-gateway`, which the Gateway's `allowedAudiences` accepts |
| Client `tyk-gateway` (confidential, used by the Gateway) | Attribute `standard.token.exchange.enabled: "true"` | Turns on standard token exchange for this client |
| Client `tyk-gateway` | Audience mapper adding `orders-backend` | Without it Keycloak refuses the exchange: `invalid_request`, `Requested audience not available: orders-backend` |
| Client `tyk-gateway` | Realm-role mapper to a flat, multivalued `roles` claim | Per-tool scope checks read this claim from the exchanged token (trap 2) |
| Client `mcp-client` | The same `roles` mapper | Scope checks on the caller's token |
| Client `orders-backend` | Exists | The target audience |
| Keycloak container | `KC_HOSTNAME=http://localhost:<port>` and `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` | Tokens keep one `iss` while the Gateway reaches the token and key endpoints by the container name |

With those two hostname settings, Keycloak's discovery document read from inside the network
shows `issuer` as `http://localhost:<port>/realms/demo` and `token_endpoint` as
`http://keycloak:8080/realms/demo/protocol/openid-connect/token`.

## Gateway definition

The full file is `assets/oauth-mcp-proxy.oas.json`. The exchange part, inside the `oauth2`
scheme:

```json
"tokenExchange": {"enabled": true, "providers": [{
  "name": "keycloak",
  "issuers": ["http://localhost:8081/realms/demo"],
  "tokenEndpoint": "http://keycloak:8080/realms/demo/protocol/openid-connect/token",
  "clientAuth": {"method": "client_secret_basic", "clientId": "tyk-gateway", "clientSecret": "env://EXCHANGE_CLIENT_SECRET"},
  "defaultTarget": {"audience": "orders-backend"},
  "cache": {"enabled": true}
}]}
```

- `issuers` is matched against the token's `iss`. `tokenEndpoint` is called by the Gateway.
- `env://EXCHANGE_CLIENT_SECRET` reads the environment variable `EXCHANGE_CLIENT_SECRET` of the
  Gateway process. Set it on every Gateway replica. Keep the secret out of the definition.
- The JWT scheme on the same proxy needs `stripAuthorizationData: false` and
  `"allowedAudiences": ["tyk-gateway", "orders-backend"]` (trap 1).
- Per tool, `middleware.mcpTools.<tool>.exchange` can ask for another audience or scopes. See
  https://tyk.io/docs/api-management/authentication/token-exchange.

## Two traps

The Gateway handles an MCP call in two passes: the request on `/mcp`, then an internal pass for
the method and the tool (the `/json-rpc-method:` and `/mcp-tool:` spans in the trace). The
exchange runs in the first pass, and the second pass authenticates again with the exchanged
token.

1. **`allowedAudiences` must list the exchange audience as well.** With only the client's
   audience, every call fails:
   `401 {"code":-32001,"message":"Key not authorized: no matching audience found in token: [orders-backend]"}`.
2. **The exchanged token must carry the claims your per-tool scope checks read.** With
   `scopeCheck` on `create_user` reading the `roles` claim, a user who holds the role is refused
   with `403 insufficient_scope` until the `roles` mapper is added to the `tyk-gateway` client,
   because the exchanged token has no `roles` claim.

When the identity provider refuses the exchange, the client gets HTTP 502 with
`{"error":"exchange_failed","idp_error":"invalid_request","idp_error_description":"Requested audience not available: orders-backend"}`.

A caller without the scope is refused before any exchange: a denied `tools/call` produces no
exchange log line, and an allowed call produces one.

## Caching

Without `cache.enabled`, every MCP request does an exchange: `initialize`,
`notifications/initialized` and the tool call each log `oauth2_exchange_cache_hit=false`.
With `"cache": {"enabled": true}` the first request logs `false` and the following ones
`true`. The cache lives in Redis, and its lifetime follows the token lifetimes with a 30 second
safety margin by default. See https://tyk.io/docs/api-management/authentication/token-exchange.

## On the open source image

The same definition loads. The exchange does not run: the MCP server receives the caller's own
token (`aud: tyk-gateway`, `azp: mcp-client`), and the Gateway logs
`OAuth2 token exchange is supported only in Tyk Enterprise Edition; the configured tokenExchange block is being ignored on this build`.

## Not for REST APIs exposed as tools

On a REST-to-MCP proxy with the same authentication, the exchange runs (the log shows it) and the
REST backend still receives no `Authorization` header. In 5.16.0 the user's identity does not
reach the backend of a REST-to-MCP proxy; see `authentication.md` for what does.
