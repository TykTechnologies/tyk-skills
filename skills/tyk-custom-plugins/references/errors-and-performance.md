# What the client sees, and what a plugin costs

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0` linux/arm64, open source)

## Failures, by kind

Rows are for Gateway 5.16.0 on the lab. Bodies are shown on one line; the Gateway indents them.

| Kind | What goes wrong | Client gets | Gateway log (search for) |
| --- | --- | --- | --- |
| Any | `pluginConfig.driver` missing | 500 `{"error": "Internal Server Error"}` | `Unsupported driver ''` |
| JavaScript | Exception thrown | 500 `Internal Server Error` | `Failed to run JS middleware` with the file and line (`javascript` driver) |
| JavaScript | Runs longer than `jsvm_timeout` (default 5 s) | 500 after 5.0 s | `JS middleware timed out after 5s` |
| JavaScript | Function the engine lacks (`console`, `_`, `TykStorage*`) | 500 | `ReferenceError` |
| JavaScript | Modern syntax on `otto` | 500 on every request | at load: `Failed to load JS middleware ... Unexpected reserved word` |
| Go | Built for another Gateway version | 500 on every request | `plugin was built with a different version of package ...` |
| Go | File missing | 500 on every request | `plugin file not found` |
| Go | Same module path as a plugin already loaded | 500 until restart | `plugin already loaded` |
| Go | Panic not recovered by the plugin | Connection closed, no response (curl exit 52) | `Recovered from panic while running Go-plugin middleware func` |
| Go | Loaded `.so` overwritten in place | Gateway process exited | `SIGSEGV: segmentation violation` |
| gRPC | Server down | 500 `{"error": "Middleware error"}`, the first one after 11 s | `Dispatch error ... code = Unavailable` |
| Python | Stock image (no Python) | 500 | `Driver 'python' isn't loaded` |
| Bundle | Server down and bundle not on disk yet | 404 `Not Found` (API not loaded) | `Couldn't fetch bundle` |
| Bundle | Unsigned while `public_key_path` is set | 404 `Not Found` (API not loaded) | `Bundle isn't signed` |

A failing hook never lets the request through; it is not skipped. The 500 bodies say nothing about
the cause, so read the Gateway log (`docker compose logs tyk-gateway`) and search for the strings
above.

## Plugin-chosen errors

| Kind | How | Client gets |
| --- | --- | --- |
| JavaScript | `ReturnOverrides.ResponseCode` + `ResponseError` | `{"error": "<text>"}` |
| JavaScript | `ResponseCode` + `ResponseBody` + `OverrideError: true` | your body exactly, with your headers |
| Go | `rw.WriteHeader(403)` and write a body | your body exactly |
| gRPC | `return_overrides` with `response_body` and `override_error: true` | your body exactly |
| Python | `request.object.return_overrides.response_code` + `response_error` | `{"error": "<text>"}` |

## Performance: measure the hook, not the machine

Compare the same upstream path through two APIs that differ only by the plugin, interleaved, one
request at a time, so noise on the machine hits both alike:

```bash
python3 "$SKILL/scripts/measure_latency.py" --requests 1500 \
  none=$GW/perf-none/status/200 go=$GW/perf-go/status/200 javascript=$GW/perf-js/status/200
```

On Gateway 5.16.0, for a no-op post hook on a keyless API, with the Gateway, the upstream
(`go-httpbin`) and the gRPC server on one host, the median latency each kind adds against the same
API without a plugin is about:

| Kind | Median added |
| --- | --- |
| Go | nothing measurable (0.55 ms against 0.63 ms) |
| JavaScript, `javascript` driver | +0.15 to +0.2 ms |
| JavaScript, `otto` | +1.7 to +1.8 ms |
| gRPC (Python server, same host) | +0.8 ms |
| Python (embedded) | nothing measurable (0.75 ms against 0.76 ms, on the Python image) |

Read these as the fixed price of each mechanism; your plugin's own work comes on top, and a
JavaScript `TykMakeHttpRequest` or a gRPC server on another host adds its network round trip.
Throughput benchmarks (`wrk`) on a shared machine are too noisy to rank the kinds; measure on
hardware like production before promising numbers.
