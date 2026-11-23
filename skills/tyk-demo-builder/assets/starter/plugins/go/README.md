# Native Go Post hook

`PostEnrich` adds `X-Demo-Post-Hook: native-go` to the request immediately
before proxying. Use a protected API when proving hook order so a request with
invalid credentials is rejected before this hook can run.

## Build

The Gateway version and target architecture are mandatory. The build produces
both stable `build/post_enrich.so` and versioned
`build/post_enrich_<gateway-version>_linux_<architecture>.so` artifacts, including
the compatibility name without a leading `v`.

```bash
TYK_GATEWAY_VERSION=v5.16.0 TYK_GATEWAY_ARCH=arm64 ./build.sh
```

The script runs `tykio/tyk-plugin-compiler:<version>` under
`--platform=linux/amd64` and invokes its compiler as
`post_enrich.so "" linux <architecture>`. The target architecture may be
`amd64` or `arm64`; the latter is cross-compiled by the amd64 compiler image.

Merge `config/oas-middleware.fragment.json` into
`x-tyk-api-gateway.middleware`. The generator mounts the stable artifact at
`/opt/tyk-gateway/plugins/go/build/post_enrich.so`. Compiled `.so` files stay
under the ignored `build/` directory.

For an artifact-only container build, use the same explicit coupling:

```bash
docker build --platform linux/amd64 \
  --build-arg TYK_GATEWAY_VERSION=v5.16.0 \
  --build-arg TYK_GATEWAY_ARCH=amd64 .
```

Verify through an upstream echo endpoint: a valid authenticated request should
contain `x-demo-post-hook=native-go`, while an invalid request should not reach
the upstream at all.
