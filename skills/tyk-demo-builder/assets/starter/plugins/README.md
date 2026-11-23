# Tyk plugins

`compose.yaml` mounts this directory read-only into every gateway at
`/opt/tyk-gateway/plugins`, so it exists in every bundle even when no plugin
module is selected. The subdirectories below appear only with their module:

- `go/` (`go-plugin`): a native Go `Post` hook that enriches the upstream
  request.
- `grpc/` (`grpc-plugin`): a gRPC coprocess `Post` hook that enriches requests
  and can terminate a request before the upstream is called.
- `js/` (`js-plugin`): a pre-authentication JavaScript plugin that maps a
  credential sent as `?user_key=` or an `app_key` header into `Authorization`,
  loaded by file path from the API definition.

Native Go plugins share a Go ABI with Tyk Gateway. Build them for the exact
Gateway release and Linux architecture that will load the `.so`. The gRPC
example uses Tyk's protobuf contract and does not load code into the Gateway
process, but its dependency is still pinned to the matching Gateway source.

JavaScript plugins need no build. On Gateway 5.15.0 and later the API selects
the goja engine with `pluginConfig.driver: "javascript"`; older gateways use
`"otto"`. goja has no `console` and no `_` helper, so the plugin uses `log()`
only and runs on either engine.

The Go and gRPC examples are not activated merely by copying these files. Merge
the relevant fragment under each `config/` directory into a protected API
definition, build the asset, and reload the API.
