---
name: tyk-custom-plugins
description: Extend Tyk Gateway 5.16 with custom code the right way: decide whether configuration is enough, then write, build, deploy and prove Go, JavaScript, gRPC or Python plugins, virtual endpoints, custom authentication and plugin bundles. Use when asked to write a Tyk plugin or middleware, build a Go plugin, fix "plugin was built with a different version", add a pre or post hook, a virtual endpoint, or sign and serve bundles. Not for built-in authentication methods, which belong to the tyk-authentication skill.
---

# Tyk Custom Plugins

Agents asked for "a Tyk plugin" tend to write one before checking that a header transform would do,
copy a JavaScript example for the older engine, build a Go plugin with the wrong compiler, or
overwrite the file a running Gateway has loaded. Tyk Gateway 5.16.0 loads most of those mistakes
without complaint and then answers 500; an overwritten Go plugin file crashes the Gateway. This
skill gives the choice, the definitions, the build and deploy commands and the exact failures for
each kind on the open source Gateway 5.16.0, plus a checker and a latency tool.

## What you need

- Every plugin kind here runs on the **open source Gateway** with Redis, no licence. Go plugins
  need the plugin compiler image with the Gateway's exact tag; Python needs a Gateway image with
  Python added; gRPC needs your server. Details: `references/choose-the-kind.md`. Editions and
  licences: the `tyk-facts` skill.
- A stack: `references/lab.md` (copy `assets/plugin-lab/`), the `tyk-demo-builder` skill for a
  Dashboard demo, or the `tyk-deployment-planner` skill for a real installation.

## Clarify first

Ask only what the request leaves open. Defaults in brackets.

1. **What must the code do, exactly?** [Check `references/choose-the-kind.md`: if a built-in
   feature does it, use that and stop.]
2. **Which Gateway version and image (open source or Enterprise Edition), which architecture?**
   [Read it from the running Gateway: `curl -s $GW/hello`; never assume.]
3. **Which language can the team maintain?** [JavaScript with `driver: "javascript"` for small
   logic; Go for heavy traffic or Go libraries; gRPC for other languages.]
4. **Where does it run: before authentication, as authentication, after it, or on the response?**
   [Post, with `requireSession: true`, when it needs the caller.]
5. **How will plugin files reach every Gateway?** [Files mounted on each Gateway for a lab;
   versioned, signed bundles for more than one Gateway.]
6. **What must the client see when the plugin refuses a request?** [Your own JSON body and status.]

## Workflow

Set these once. `$SKILL` is this skill's folder.

```bash
SKILL=<this skill's folder>
GW=http://localhost:8080            # Gateway
SECRET=<gateway secret>             # "secret" in tyk.conf, or TYK_GW_SECRET
DASH=http://localhost:3000          # Dashboard, when there is one
DASHBOARD_API_KEY=<Dashboard user API key>
```

1. **Choose.** Walk the table in `references/choose-the-kind.md`. Tell the user why
   configuration is not enough before writing code.

2. **Find the Gateway's version and switches.** `curl -s "$GW/hello"` gives the version. In its
   configuration check `enable_jsvm` (JavaScript, virtual endpoints), `coprocess_options` (gRPC,
   Python) and `enable_bundle_downloader`, `bundle_base_url`, `public_key_path` (bundles).

3. **Write the plugin and the hook block.** Start from `assets/plugin-lab/` (one working plugin per
   kind). Hooks go under `x-tyk-api-gateway.middleware.global`; one `pluginConfig.driver` for the
   whole API:

   ```json
   "middleware": {"global": {
     "pluginConfig": {"driver": "javascript", "data": {"enabled": true, "value": {"region": "eu"}}},
     "prePlugins":  [{"enabled": true, "functionName": "jsDeny", "path": "/opt/tyk-gateway/plugins/js/deny.js"}],
     "postPlugins": [{"enabled": true, "functionName": "orderPost", "path": "/opt/tyk-gateway/plugins/js/order.js",
                      "requireSession": true}]}}
   ```

   Every field and the order hooks run in: `references/hooks-and-order.md`.

4. **Build (Go only).** With the compiler tag equal to the Gateway version:

   ```bash
   docker run --rm -v "$PWD:/plugin-source" tykio/tyk-plugin-compiler:v5.16.0-ng demo.so
   # == validation OK: demo_v5.16.0_linux_arm64.so ==
   ```

   Reference the plain name (`/opt/tyk-gateway/plugins/go/demo.so`) in the definition; the
   Gateway loads `demo_<its version>_<os>_<arch>.so`. Enterprise Edition Gateway:
   `tykio/tyk-plugin-compiler-ee`. All of it: `references/go-plugins.md`.

5. **Check the definition before loading.**

   ```bash
   python3 "$SKILL/scripts/check_plugins.py" api.json --map /opt/tyk-gateway/plugins=./plugins
   python3 "<the tyk-api-definitions-routing skill's folder>/scripts/check_tyk_oas.py" api.json
   ```

   Fix every `ERROR`; tell the user about every `WARN` and `NOTE`.

6. **Deploy and load.** Put the files on every Gateway (or build, sign and upload a bundle:
   `references/bundles.md`), then create the API and reload:

   ```bash
   curl -s -X POST "$GW/tyk/apis/oas" -H "x-tyk-authorization: $SECRET" \
     -H 'Content-Type: application/json' --data @api.json
   curl -s "$GW/tyk/reload/group" -H "x-tyk-authorization: $SECRET"
   ```

   With a Dashboard, create it through `$DASH/api/apis/oas` (the `tyk-api-definitions-routing` skill); the
   plugin files must still be on each Gateway or in a bundle. Then read the Gateway log for this
   API: no `Could not load Go-plugin`, `Failed to load JS middleware`, `Couldn't load bundle` or
   `Unsupported driver`.

7. **Prove it.** For each hook: the request it should change (read the upstream echo), the
   request it should stop (status and body exactly as the user asked), and one request the Gateway
   itself refuses (bad key or 429), to see which hooks ran (`references/hooks-and-order.md`). For
   custom authentication, two callers with separate limits (`references/custom-authentication.md`).
   Then break it on purpose once (stop the gRPC server, throw in JavaScript) and record what the
   client gets (`references/errors-and-performance.md`).

8. **Measure the cost** when the user cares about latency:

   ```bash
   python3 "$SKILL/scripts/measure_latency.py" --requests 1500 \
     none=$GW/perf-none/status/200 plugin=$GW/perf-plugin/status/200
   ```

9. **Tell the user** which kind and why, where the files live, how a new version is deployed (new
   `.so` file name and module path, or new bundle name), what each failure looks like to the client,
   and what was not checked on their stack.

## References

| File | Read it for |
| --- | --- |
| `references/choose-the-kind.md` | Configuration first; which kind; what each needs; one driver per API |
| `references/hooks-and-order.md` | Every OAS field; order against built-in middleware; session and context per kind |
| `references/go-plugins.md` | Compiler images and the version rule, build, load, naming, updating, editions, failures |
| `references/javascript.md` | `javascript` against `otto`, the plugin API, `ReturnOverrides`, response hooks, storage, virtual endpoints |
| `references/grpc-and-python.md` | gRPC server from the image's `.proto` files, server down, Python image and hooks |
| `references/bundles.md` | Build, sign, serve, load; caching by name; bundle server down |
| `references/custom-authentication.md` | Auth plugins in Go, JavaScript and gRPC; per-caller limits |
| `references/errors-and-performance.md` | What the client sees per failure; latency cost per kind |
| `references/lab.md` | The lab: every kind in one Compose stack |
| `references/pitfalls-and-limitations.md` | Twenty pitfalls and the limits of the product |

Scripts (Python standard library, with tests): `scripts/check_plugins.py`,
`scripts/measure_latency.py`. Assets: `assets/plugin-lab/`.

## Pitfalls

The costliest; all twenty are in `references/pitfalls-and-limitations.md`.

- No `pluginConfig.driver`: every request gets 500.
- `otto` cannot load modern syntax, inline code or `TykStorage*`; `javascript` has no `console`.
- `ReturnOverrides` without `OverrideError: true` wraps your body in `{"error": ...}`.
- A Go plugin from another Gateway version's compiler: the API loads, every request gets 500.
- Overwriting a loaded `.so` crashes the Gateway; a new build with the same module path is refused
  until a restart.
- An unrecovered Go panic closes the client's connection with no response.
- Custom authentication sessions share one rate limit unless `meta_data.rate_limit_pattern` is set;
  the pattern's value is one counter across APIs, so include the API id for per-API limits.
- A bundle rebuilt under the same name is never picked up, even after a restart.
- While a gRPC server is down, its APIs answer 500; while a bundle server is down, a Gateway that
  lacks the bundle leaves the API out (404).

## Limitations

- One language per API, except virtual endpoints (JavaScript) and operation-level Go `postPlugins`.
- Go code cannot be hot-reloaded in place; Go plugins must be rebuilt for every Gateway release
  (plan the upgrade with the `tyk-upgrades` skill).
- gRPC plugins are marked unavailable on Tyk Cloud; check with the `tyk-facts` skill first.
- Unexpected plugin failures give the client a generic 500; the cause is only in the Gateway log.
- Python needs a custom Gateway image; the stock image has no Python and no shell.
- JavaScript hooks are cut off at `jsvm_timeout` (5 s by default).

## Verification checklist

Before reporting success:

- [ ] Configuration alone was ruled out, and the reason was given to the user.
- [ ] `check_plugins.py` and `check_tyk_oas.py` report no errors; every warning was explained.
- [ ] The Gateway log shows the plugin loaded and none of the load errors in step 6.
- [ ] Go: the compiler tag equals the Gateway version, and the definition names the plain `.so`.
- [ ] Each hook's effect was seen in a real response or upstream echo, and a refused request
      returned exactly the status and body the user asked for.
- [ ] A request refused by the Gateway (bad key or 429) showed which hooks ran.
- [ ] Custom authentication: two callers kept separate limits.
- [ ] One deliberate failure was run and the client's view recorded.
- [ ] The update path was tried once: a new `.so` name and module path, or a new bundle name.
- [ ] No private key, token or licence is left in a file you hand over.

## Hand-offs

- The rest of the API definition: the `tyk-api-definitions-routing` skill.
- Built-in authentication methods: the `tyk-authentication` skill.
- Keys, policies, rate limits and quotas: the `tyk-rate-limits-keys-policies` skill.
- Logs, traces and metrics for plugins: the `tyk-observability` skill. Errors you cannot explain
  from the tables: the `tyk-troubleshooting` skill.
- Delivering definitions and bundles through Git: the `tyk-gitops-sync-operator` skill.
- Planning a Gateway upgrade (order, rollback, when to ship the rebuilt `.so` or bundle): the
  `tyk-upgrades` skill.
- Licensing and editions: the `tyk-facts` skill. A demo: the `tyk-demo-builder` skill. A real
  installation: the `tyk-deployment-planner` skill.
