# Pitfalls and limitations

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source)

The reference named in brackets has the detail.

## Pitfalls

1. **No driver set.** Plugins listed without `pluginConfig.driver` load, then every request gets
   500 (`Unsupported driver ''`). [`hooks-and-order.md`]
2. **`otto` by habit.** ES6 syntax, inline `code` and `TykStorage*` all fail on `otto` with 500;
   use `driver: "javascript"`. A virtual endpoint on an API with any other driver runs on `otto`
   too, so write it in ES5 there. [`javascript.md`]
3. **`console` or `_` on the `javascript` driver.** `ReferenceError`, 500. Use `log()`. [`javascript.md`]
4. **`ReturnOverrides` without `OverrideError`.** Your JSON body arrives wrapped as
   `{"error": "<your body as a string>"}`. [`javascript.md`]
5. **No `requireSession` on a JavaScript post-auth, post or response hook.** The session arrives
   empty; on a keyless API it can be `null`, so `session.meta_data` throws. [`hooks-and-order.md`]
6. **Counting in a pre hook.** Pre runs for requests the Gateway then refuses (bad key, 429).
   Count in post or response. [`hooks-and-order.md`]
7. **Expecting a response hook on Gateway errors.** It runs when the upstream answers (and on a
   mock response); 401, 403 and 429 from the Gateway skip it. [`hooks-and-order.md`]
8. **Go plugin built with another Gateway version's compiler.** The API loads, every request gets
   500 (`plugin was built with a different version of package ...`). [`go-plugins.md`]
9. **Pinning a versioned `.so` name in the definition.** Name the plain file (`demo.so`); the
   Gateway picks `demo_<its version>_<os>_<arch>.so`, so builds for two versions can sit side by
   side during an upgrade. [`go-plugins.md`]
10. **Overwriting a loaded `.so`.** The Gateway process crashes. Always deploy a new file name.
    [`go-plugins.md`]
11. **New Go build, same module path.** `plugin already loaded`, 500 until a restart. Change the
    module path in `go.mod` for every build you hot-load. [`go-plugins.md`]
12. **An unrecovered panic in Go.** The client's connection closes with no response. Recover in
    the plugin and write a 500 yourself. [`go-plugins.md`]
13. **`ctx.GetDefinition(r)` on a Tyk OAS API.** Returns nil. Use `ctx.GetOASDefinition(r)` or
    `ctx.GetOASConfigData(r)`. [`hooks-and-order.md`]
14. **Custom authentication sessions share one rate limit (Go and JavaScript alike).** Set
    `rate_limit_pattern` in the session's `meta_data`, put the API id in its value when one caller
    uses several APIs, and test with two callers; the pattern keys the quota as well. A gRPC auth
    hook must also set `metadata["token"]`, or the callers it accepts get 403.
    [`custom-authentication.md`]
15. **gRPC server as a single point of failure.** While it is down, every API with gRPC hooks
    answers 500 `Middleware error` (the first after about 11 s). [`grpc-and-python.md`]
16. **Python on the stock image.** 500, `Driver 'python' isn't loaded`; build an image with Python.
    [`grpc-and-python.md`]
17. **Rebuilding a bundle under the same name.** Gateways keep the old copy, even after a restart.
    Every change gets a new bundle name. [`bundles.md`]
18. **Bundle server down when a Gateway first needs a bundle.** That API is left out (404) on that
    Gateway. [`bundles.md`]
19. **`public_key_path` set and the bundle unsigned.** The API is left out (404),
    `Bundle isn't signed`. [`bundles.md`]
20. **Reading `request.Headers["X"]` as a string in JavaScript.** It is a list; use `[0]`.
    [`javascript.md`]

## Limitations

- One driver per API: pre, auth, post-auth, post and response hooks are all the same language.
  Exceptions: virtual endpoints (always JavaScript; on `otto` unless the driver is `javascript`)
  and an operation's own `postPlugins` (Go).
- A running Gateway never reloads Go code it has loaded; new code needs a new module path and
  file name, or a restart.
- JavaScript hooks have a time limit (`jsvm_timeout`, 5 s by default); a long call fails the request.
- Error bodies for unexpected plugin failures are generic (`Internal Server Error`,
  `Middleware error`); the cause is only in the Gateway log.
- The stock Gateway image has no shell and no Python; Python plugins need your own image.
- `TykStorage*` atomicity holds per Redis instance; Gateways with separate Redis instances do not
  share counters (docs: https://tyk.io/docs/api-management/plugins/javascript).
