# Listen paths and path matching

Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`)

See https://tyk.io/docs/getting-started/key-concepts/url-matching

A request is matched twice. First the Gateway picks the API by listen path (and host). Then,
inside that API, each piece of per-operation middleware decides whether the request is one of
its operations. The second step depends on three Gateway settings, so the same definition can
behave differently on two Gateways. Find out which mode the Gateway is in before you write
paths.

## The three Gateway settings

| `tyk.conf` key under `http_server_options` | Environment variable | Effect |
| --- | --- | --- |
| `enable_strict_routes` | `TYK_GW_HTTPSERVEROPTIONS_ENABLESTRICTROUTES` | A listen path `/app` stops matching `/apple` |
| `enable_path_prefix_matching` | `TYK_GW_HTTPSERVEROPTIONS_ENABLEPATHPREFIXMATCHING` | An operation path must match from the start of the path |
| `enable_path_suffix_matching` | `TYK_GW_HTTPSERVEROPTIONS_ENABLEPATHSUFFIXMATCHING` | An operation path must match to the end of the path |

- In the Gateway's code all three default to `false`.
- The `tyk.conf` inside the Docker images `tykio/tyk-gateway:v5.16.0` and
  `tykio/tyk-gateway-ee:v5.16.0` sets all three to `true`. The Helm chart `tyk-gateway`
  (tyk-charts 5.3.0) sets all three to `true` in its default values.
- A Gateway started with your own `tyk.conf` that leaves the block out, or a package install
  with an older file, runs with all three `false`.

Prefix and suffix together give exact matching. Tyk recommends exact matching and strict routes
(see the page above). Check the running Gateway's `tyk.conf` and environment; when
you cannot, use the probe at the end of this file.

## Picking the API: the listen path

With three APIs, `/app`, `/app/sub/` and `/` (strip on, so the upstream path shows what
is stripped):

| Request | Strict routes off | Strict routes on |
| --- | --- | --- |
| `/app`, `/app/`, `/app/x` | API `/app` | API `/app` |
| `/apple` | API `/app`, upstream receives `/le` | `404 Not Found` |
| `/app/sub/x` | API `/app/sub/` | API `/app/sub/` |
| `/app/sub` | API `/app` (the longer path ends in `/`) | API `/app` |
| `/zzz` | API `/` | API `/` |

- The longest matching listen path wins. An API on `/` catches everything no other API claims.
- APIs with a custom domain are tried before APIs without one, then longer listen paths before
  shorter, and two overlapping listen paths of the same length are picked in no fixed order
  (see "Path Ordering" on the page above).
- No API matches: `404` with the plain text body `Not Found`.
- Every API also answers on `/<api id>/`. That route ignores `strip`: `GET /orders/x` on an API
  whose id is `orders` and whose listen path is `/by-listen/` reaches the upstream as
  `/orders/x`. Treat it as a side door for tests, not as an address to publish.

### Parameters and regular expressions in the listen path

| Listen path | Request | Result |
| --- | --- | --- |
| `/tenants/{tenant}/` | `/tenants/acme/x` | Matched; upstream receives `/x` |
| `/tenants/{tenant}/` | `/tenants/acme`, `/tenants//x` | Not matched |
| `/v2/{app:alpha\|beta}/` | `/v2/alpha/x` | Matched; upstream receives `/x` |
| `/v2/{app:alpha\|beta}/` | `/v2/gamma/x` | Not matched; falls through to the next API |

A regular expression after the colon is honoured in a listen path. Parameter names must start
with a letter and hold only letters, digits and underscores (see the page above).

### Strip

`server.listenPath.strip: true` removes the listen path before the request goes upstream. Any
path in `upstream.url` is put in front: listen path `/orders/`, upstream
`http://orders.example.com:8080/base`, request `/orders/a/b?x=1` reaches the upstream as
`/base/a/b?x=1`. With `strip` false or absent the upstream receives `/orders/a/b`.

### Custom domains

```json
"server": {"listenPath": {"value": "/cd/", "strip": true},
           "customDomain": {"enabled": true, "name": "api.example.com"}}
```

- The Gateway setting `enable_custom_domains` (`TYK_GW_ENABLECUSTOMDOMAINS`) must be `true`.
  With it off the domain is ignored: the API answers on every host.
- With it on, the API answers only for that `Host` (a port in the header is fine). Another API
  on the same listen path without a domain answers every other host.
- A pattern works as the name: `{sub:[a-z]+}.tenant.example.com` matches
  `acme.tenant.example.com` and not `x1.tenant.example.com`.
- The readback's `servers` list then starts with `http://api.example.com/cd`.

## Picking the operation

Write operation paths **relative to the listen path**: listen path `/orders-api/` and path
`/orders` serve `/orders-api/orders`, whether `strip` is on or off. A path that repeats the
listen path (`/orders-api/orders` in `paths`) does not match for transforms.

Path syntax, the same in every mode:

| In `paths` | Matches |
| --- | --- |
| `/users/{id}` | exactly one segment in place of `{id}`: `/users/42`, not `/users/42/extra` (exact mode) |
| `/users/me` next to `/users/{id}` | `/users/me` runs the literal operation, not the parameter one |
| `/files/*` | the rest of the path, slashes included: `/files/a/b/c`. Not `/files` |
| `/items/{id:[0-9]+}` | **any** value: `/items/abc` matches. The expression is ignored for operations |
| `/users` | case-sensitive: `/USERS` does not match. An operation `GET /Case` with `"allow": {"enabled": true, "ignoreCase": true}` matches `/case` and `/CASE`. The Gateway setting `ignore_endpoint_case` switches case off for every API |

To constrain a parameter, give it a schema (`type`, `pattern`, `enum`) and switch on
`validateRequest` for the operation; a bad value is then refused with 422. Anchor a `pattern`
yourself (`^[0-9]+$`).

### What each mode matches

One API, listen path `/pm/`, operations `GET /users`, `GET /users/{id}`, `GET /users/me`,
`GET /reports/{year}/{month}`, `GET /files/*`. Each operation adds a header naming itself, so
the table shows which operation's middleware runs. "none" means the request is proxied with no
operation middleware.

| Request under `/pm` | All off (wildcard) | Prefix only | Suffix only | Prefix and suffix (exact) |
| --- | --- | --- | --- | --- |
| `/users` | `/users` | `/users` | `/users` | `/users` |
| `/users/` | `/users` | `/users` | none | none |
| `/users/42` | `/users/{id}` | `/users/{id}` | `/users/{id}` | `/users/{id}` |
| `/users/me` | `/users/me` | `/users/me` | `/users/me` | `/users/me` |
| `/users/42/extra` | `/users/{id}` | `/users/{id}` | none | none |
| `/v1/users` | `/users` | none | `/users` | none |
| `/usersX` | `/users` | `/users` | none | none |
| `/USERS` | none | none | none | none |
| `/reports/2026` | none | none | none | none |
| `/files/a/b/c` | `/files/*` | `/files/*` | `/files/*` | `/files/*` |
| `/files` | none | none | none | none |

With an allow list on every operation, "none" becomes
`403 {"error": "Requested endpoint is forbidden"}`. So on a wildcard or prefix Gateway an allow
list for `GET /users` also lets `/usersX` and `/users/42/extra` through; on an exact Gateway it
does not, and a trailing slash (`/users/`) is refused.

### The listen path takes part in allow, block and ignore-authentication matching

Allow lists, block lists and `ignoreAuthentication` are matched against the full request path
as well as the path after the listen path. Other operation middleware (transforms, cache,
rewrite, limits, validation, mocks) sees only the path after the listen path. See
https://tyk.io/docs/getting-started/key-concepts/url-matching

With listen path `/orders2/` and an allow list holding only `GET /orders`:

| Request | Wildcard | Prefix only | Suffix only, or exact |
| --- | --- | --- | --- |
| `/orders2/orders` | 200 | 200 | 200 |
| `/orders2/unknown` | **200** | **200** | 403 |
| `/orders2/orders/1` | 200 | 200 | 403 |

In the first two modes the text `/orders` is found at the start of `/orders2/unknown`, so the
allow list admits every path. Run exact matching, as the documentation recommends, or choose a
listen path that does not begin with the name of one of its own endpoints.

## Probe the mode of a Gateway you cannot inspect

Load a throwaway API with `"paths": {"/probe": {"get": {"operationId": "probe", ...}}}`, listen
path `/zz-probe/`, and `allow: {"enabled": true}` on `probe`. Then:

| Call | 200 means |
| --- | --- |
| `GET /zz-probe/probe` | the API is loaded (always 200) |
| `GET /zz-probe/probeX` | suffix matching is off |
| `GET /zz-probe/x/probe` | prefix matching is off |

Both 403: exact matching. Delete the API afterwards.
