# gRPC and Python plugins

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source), gRPC server on `python:3.12-slim` with grpcio 1.76.0, Python Gateway image built from `python:3.11-bookworm`

Docs: https://tyk.io/docs/api-management/plugins/rich-plugins

Both use the same message, `coprocess.Object`: the hook name and type, the request, the session,
`metadata` and `spec` (`APIID`, `OrgID`, `config_data`); a response hook also gets `response`.
You return the object, changed.

## gRPC

The Gateway calls your server's `Dispatcher.Dispatch` once per hook. Gateway configuration:

```json
"coprocess_options": {"enable_coprocess": true, "coprocess_grpc_server": "tcp://grpc-plugin:5555"}
```

(environment: `TYK_GW_COPROCESSOPTIONS_ENABLECOPROCESS=true`,
`TYK_GW_COPROCESSOPTIONS_COPROCESSGRPCSERVER=tcp://grpc-plugin:5555`). One server serves every API
of that Gateway; your code picks the behaviour from `hook_name`.

### The contract ships in the Gateway image

The `.proto` files are in the Gateway image at `/opt/tyk-gateway/coprocess/proto/`. Copy them out
and generate code for your language:

```bash
cid=$(docker create tykio/tyk-gateway:v5.16.0)
docker cp "$cid:/opt/tyk-gateway/coprocess/proto/." grpc/ && docker rm "$cid"
# Python: python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. *.proto
```

`assets/plugin-lab/grpc/` does this in its Dockerfile and implements four hooks in `server.py`:

```python
def grpc_pre(o):
    o.request.set_headers["X-Grpc-Pre"] = "ran"
    if o.request.headers.get("X-Block"):
        o.request.return_overrides.CopyFrom(ReturnOverrides(
            response_code=403, response_body='{"code":"PARTNER_BLOCKED"}',
            headers={"Content-Type": "application/json"}, override_error=True))
    return o
```

```json
"middleware": {"global": {
  "pluginConfig": {"driver": "grpc", "data": {"enabled": true, "value": {"region": "eu"}}},
  "prePlugins":      [{"enabled": true, "functionName": "GrpcPre"}],
  "postPlugins":     [{"enabled": true, "functionName": "GrpcPost", "requireSession": true}],
  "responsePlugins": [{"enabled": true, "functionName": "GrpcResponse", "requireSession": true}]}}
```

No `path`: the name is all the server needs. With the lab server the upstream gets
`X-Grpc-Pre: ran` and `X-Grpc-Post: alias=partner-a;config={"region":"eu"}`, the client gets
`X-Grpc-Response: status=200`, and `X-Block: 1` gets `403 {"code":"PARTNER_BLOCKED"}` exactly. The
hook types the server receives are `Pre`, `Post`, `Response`, and `CustomKeyCheck` for custom
authentication.

`return_overrides` with `response_error` instead of `response_body` (and no `override_error`)
gives `{"error": "<text>"}`, as in JavaScript.

### When the server is down

The first request after the server stops waits about 11 s and gets
`500 {"error": "Middleware error"}`; the next ones get the same 500 at once. The log says
`Dispatch error ... rpc error: code = Unavailable`. APIs without gRPC hooks keep answering 200, and
the gRPC APIs answer 200 again as soon as the server is back, with no reload. Run the server
with more than one replica and health checks; `grpc_round_robin_load_balancing` with a `dns:///`
address spreads calls across replicas (configuration reference:
https://tyk.io/docs/tyk-oss-gateway/configuration).

## Python

Python runs inside the Gateway process. The stock image has no Python: an API with
`driver: "python"` loads, and every request gets 500 with `Driver 'python' isn't loaded` (and at
start-up `Couldn't load Python dispatcher ... python version '' doesn't exist`).

Build an image with Python, as the docs show (`assets/plugin-lab/python-gw/Dockerfile`):

```bash
docker build --build-arg BASE_IMAGE=tykio/tyk-gateway:v5.16.0 -t tyk-gateway-python:v5.16.0 python-gw
```

That image (Python 3.11, `protobuf==4.24.4`) runs Python plugins on 5.16.0. The Dockerfile sets
no user, so the image runs the Gateway as root by default, unlike the stock image (uid 65532);
the lab runs it as your own user (`LAB_USER`), and Python plugins run that way too. Deliver the code in a
bundle (`bundles.md`) with `"driver": "python"` in the manifest:

```python
from tyk.decorators import *
from gateway import TykGateway as tyk

@Hook
def PyPre(request, session, spec):
    request.add_header("X-Py-Pre", "ran")
    if request.get_header("X-Block"):
        request.object.return_overrides.response_code = 403
        request.object.return_overrides.response_error = "blocked by python plugin"
    return request, session

@Hook
def PyPost(request, session, spec):
    request.add_header("X-Py-Post", "alias=%s;config=%s" % (session.alias or "none", spec.get("config_data", "")))
    tyk.log("PyPost ran", "info")
    return request, session
```

The upstream gets `X-Py-Pre: ran` and `X-Py-Post: alias=partner-a;config={"region":"eu"}`;
`X-Block: 1` gets `403 {"error": "blocked by python plugin"}`. A no-op Python post hook adds no
measurable median latency (`errors-and-performance.md`).
