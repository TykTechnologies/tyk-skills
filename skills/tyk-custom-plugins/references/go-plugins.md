# Go plugins

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` linux/arm64, `tykio/tyk-gateway-ee:v5.16.0`), plugin compilers `tykio/tyk-plugin-compiler:v5.16.0-ng`, `tykio/tyk-plugin-compiler-ee:v5.16.0-ng`, `tykio/tyk-plugin-compiler:v5.15.1-ng`

Docs: https://tyk.io/docs/api-management/plugins/golang,
https://tyk.io/docs/api-management/plugins/golang/plugin-compiler

## The rule

A Go plugin is a shared object loaded into the Gateway process. It must be built with the plugin
compiler image whose tag is the Gateway's exact version, for the Gateway's architecture. Resolve
the Gateway version from the running Gateway, never from memory:

```bash
curl -s "$GW/hello"            # {"status":"pass","version":"5.16.0",...}
docker image inspect tykio/tyk-gateway:v5.16.0 --format '{{.Architecture}}'   # arm64 or amd64
```

## Compiler images

| Image | Runs on | Builds for | Notes |
| --- | --- | --- | --- |
| `tykio/tyk-plugin-compiler:v5.16.0` | amd64 only (Docker Hub lists one architecture) | amd64, arm64 | The long-standing image |
| `tykio/tyk-plugin-compiler:v5.16.0-ng` | amd64 and arm64 | amd64, arm64, s390x | Same arguments; checks the result after the build |
| `tykio/tyk-plugin-compiler-ee:v5.16.0-ng` | amd64 and arm64 | amd64, arm64 | For the Enterprise Edition Gateway |
| `tykio/tyk-plugin-compiler-fips:v5.16.0-ng` | amd64 and arm64 | amd64, arm64 | For a FIPS Gateway, which rejects a plugin built without FIPS crypto (docs page above) |

The compilers are published per Gateway release (Docker Hub lists all four `v5.16.0` tags above),
and the NG images exist from 5.15.0 and 5.13.2 (docs page above). On an arm64 machine the `-ng`
image builds natively.

## Build

The source folder holds `plugin.go` (package `main`, an empty `main()`, one exported function per
hook) and `go.mod`. Give `go.mod` a module path that is unique per build (see "Updating").

```bash
cd my-plugin        # plugin.go, go.mod: "module example.com/tyk-plugins/demo-v1" and "go 1.26"
docker run --rm -v "$PWD:/plugin-source" tykio/tyk-plugin-compiler:v5.16.0-ng demo.so
```

The NG image ended with (about 45 seconds the first time):

```text
  [ok] architecture: AArch64
  [ok] go toolchain: go1.26.10 (matches gateway)
  [ok] GLIBC ceiling: 2.17 <= 2.17
== validation OK: demo_v5.16.0_linux_arm64.so ==
```

The output is `demo_v5.16.0_linux_arm64.so` in the source folder: `<name>_<gateway version>_<os>_<arch>.so`.
Cross-build with two more arguments: `demo.so "" linux amd64`. The empty second argument is
`plugin_id`: the long-standing image rewrites the `go.mod` module path from it, while the NG image
never rewrites an existing `go.mod` (docs page above), so with NG set the module path yourself.

A function in the plugin that uses a Gateway API newer than the compiler fails to compile (a
`ctx.GetOASConfigData` call with the 5.15.1 compiler: `undefined: ctx.GetOASConfigData`).

## Load

Copy the `.so` to every Gateway (or ship it in a bundle, `bundles.md`), reference the **plain**
name in the definition, and reload:

```json
"middleware": {"global": {
  "pluginConfig": {"driver": "goplugin", "data": {"enabled": true, "value": {"region": "eu"}}},
  "prePlugins":      [{"enabled": true, "functionName": "Deny",          "path": "/opt/tyk-gateway/plugins/go/demo.so"}],
  "postPlugins":     [{"enabled": true, "functionName": "AddContext",    "path": "/opt/tyk-gateway/plugins/go/demo.so"}],
  "responsePlugins": [{"enabled": true, "functionName": "StampResponse", "path": "/opt/tyk-gateway/plugins/go/demo.so"}]}}
```

For `/opt/tyk-gateway/plugins/go/demo.so` the Gateway looked for, in this order (its log):
`demo_v5.16.0_linux_arm64.so`, `demo_5.16.0_linux_arm64.so`, then `demo.so`. So keep builds for
the current and the next Gateway version side by side and an upgrade needs no definition change.
Planning the upgrade itself (order, rollback, when to ship the rebuilt `.so` or bundle) is the
`tyk-upgrades` skill.
The Gateway log confirms a response hook with `Loaded Go response plugin: StampResponse`.

Signatures (docs page above):

```go
func AddContext(rw http.ResponseWriter, r *http.Request)                          // pre, auth, post-auth, post
func StampResponse(rw http.ResponseWriter, res *http.Response, req *http.Request) // response
```

With the lab plugin (`assets/plugin-lab/go/plugin.go`) the upstream receives
`X-Go-Alias: partner-a`, `X-Go-Region: eu`, `X-Go-API: go-hooks`; the client gets
`X-Go-Response: stamped`; with `X-Block: 1` the pre hook answers
`403 {"code":"PARTNER_BLOCKED"}` itself. Writing a status of 400 or more ends the request with
your body and logs `plugin function sent error response code: 403`. Writing nothing passes the
request on.

## What goes wrong

| Situation | What the client gets | Gateway log |
| --- | --- | --- |
| Plugin built with the 5.15.1 compiler, loaded by 5.16.0 | 500 `{"error": "Internal Server Error"}` on every request; the API itself loads | `Could not load Go-plugin ... plugin was built with a different version of package internal/runtime/syscall/linux` |
| No file at the path or its versioned names | 500 on every request | `plugin file ... doesn't exist` three times, then `plugin file not found` |
| New build, same `go.mod` module path, new file name, reload | 500 on every request until a restart | `plugin.Open(...): plugin already loaded` |
| `.so` overwritten in place while the Gateway runs, then reload | The Gateway process crashes (`SIGSEGV: segmentation violation`) and exits | |
| The plugin panics | No HTTP response: the connection closes (curl: `Empty reply from server`, exit 52); the Gateway keeps serving other requests | `Recovered from panic while running Go-plugin middleware func` and a stack trace |

Recover inside your own functions and write an error response, so a bug in one request does not
drop the connection. The lab's `Guarded` function does this; with `X-Fail: 1` the client gets
`500 {"error":"plugin failure"}` instead of a closed connection:

```go
defer func() {
    if v := recover(); v != nil {
        rw.Header().Set("Content-Type", "application/json")
        rw.WriteHeader(http.StatusInternalServerError)
        _, _ = rw.Write([]byte(`{"error":"plugin failure"}`))
    }
}()
```

The NG compiler reports a mismatched toolchain at build time (`Go toolchain mismatch`); the Gateway
reports it only when an API loads the plugin. `go version -m <file>.so` shows the toolchain a
plugin was built with.

## Updating a plugin

A running Gateway never reloads Go code that it has already loaded. To deploy a new build without
a restart:

1. Change the module path in `go.mod` (for example `demo-v1` to `demo-v2`) and build to a new name
   (`demo2.so`).
2. Copy the new file next to the old one. Never overwrite a loaded file.
3. Point the definition at the new path and reload. The new code answers at once (for example
   `X-Go-Build: v4` from a `demo4.so` with module `demo-v4`).

Restarting the Gateway also loads whatever file the path names, and is the only option when the
module path is unchanged.

## Editions

Build plugins for the Enterprise Edition Gateway with `tykio/tyk-plugin-compiler-ee`, whose NG
build reports `[ok] edition: 'ee' build tag present`. The docs state that an `ee` plugin will not
load into a community Gateway and that a FIPS Gateway rejects a plugin built without FIPS crypto.
Build with the compiler that matches the Gateway image you deploy, test on that image, and do not
rely on a plugin from another edition's compiler loading.
