# Plugin bundles

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 (`tykio/tyk-gateway:v5.16.0`, open source)

A bundle is a zip of plugin files plus `manifest.json`. Gateways download it from a web server
when an API that names it loads, so you do not copy files onto every Gateway. Bundles carry
JavaScript and Python plugins, and Go `.so` files as the Go docs show. Docs: https://tyk.io/docs/api-management/plugins/overview (bundles),
https://tyk.io/docs/tyk-oss-gateway/configuration (settings).

## Gateway settings

```json
"enable_bundle_downloader": true,
"bundle_base_url": "http://bundles:8000/",
"middleware_path": "/opt/tyk-gateway/middleware"
```

Downloads are unpacked under `<middleware_path>/bundles/<hash of the bundle name>/`; the Gateway
user (uid 65532 in the image) must be able to write there. A bundle found there is reused as is
(`Loading existing bundle`), so give each Gateway its own `middleware_path` rather than a shared
folder. Add `public_key_path` to accept only
signed bundles (below).

## Build

```json
{
  "file_list": ["stamp.js"],
  "custom_middleware": {
    "driver": "javascript",
    "pre": [{"name": "bundleStamp", "path": "stamp.js"}]
  }
}
```

The manifest uses the classic names: `pre`, `post`, `post_key_auth`, `auth_check`, `response`,
each with `name`, `path` (file name inside the bundle) and optional `require_session`. Build with
the `bundle build` command of the Gateway binary itself; no other tool is needed:

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work \
  --entrypoint /opt/tyk-gateway/tyk tykio/tyk-gateway:v5.16.0 bundle build -y -o js-v1.zip
# level=warning message="The bundle will be unsigned"
# level=info message="Wrote 'js-v1.zip' (690 bytes)"
```

`-y` skips signing; `-k private.pem` signs (below). The command writes the checksum and signature
into the zip's `manifest.json`. For Go, list the `.so` file in `file_list` and `path`.

## Load

Put the zip on the server, then name it in the API:

```json
"middleware": {"global": {"pluginConfig": {"driver": "javascript",
  "bundle": {"enabled": true, "path": "js-v1.zip"}}}}
```

After a reload the upstream gets `X-Bundle: js-v1`; the log shows `Fetching Bundle: js-v1.zip`.

## Sign

```bash
openssl genrsa -out bundle-signing.pem 2048
openssl rsa -in bundle-signing.pem -pubout -out bundle-signing.pub
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work \
  --entrypoint /opt/tyk-gateway/tyk tykio/tyk-gateway:v5.16.0 bundle build -k bundle-signing.pem -o js-signed.zip
```

On the Gateway, `"public_key_path": "/opt/tyk-gateway/plugins/bundle-signing.pub"` (or
`TYK_GW_PUBLICKEYPATH`). Then a signed bundle loads and an unsigned one does not: the API is left
out (requests get 404 `Not Found`) and the log says
`Couldn't load bundle: Bundle isn't signed`. Keep the private key out of the bundle folder and out
of version control.

With a Dashboard, `public_key_path` is not a bundle-only setting: the Gateway also uses it to
verify Dashboard notifications (see "Gateway configuration" on
https://tyk.io/docs/api-management/plugins/overview). On a
Dashboard-managed Gateway, do not generate a key pair just for bundles; sign bundles with the
private key that matches the key the Gateway already uses, and read
https://tyk.io/docs/planning-for-production#how-the-public-key-affects-plugin-bundles before
setting it.

## Change a bundle: always a new name

| Action | Result |
| --- | --- |
| Rebuild `js-v1.zip` with new code under the same name, reload | Old code kept (`X-Bundle: js-v1`) |
| Same, then restart the Gateway | Old code still kept: the unpacked copy on disk is reused |
| Upload the new code as `js-v2.zip`, point the API at it, reload | New code at once |

Name bundles by version or content hash (`plugins-1.4.0.zip`, `plugins-<sha>.zip`) and change the
API definition to roll forward or back.

The documented alternative is to delete the cached copy and reload (see "Caching plugin bundles"
on the overview page above). After rebuilding `js-v1.zip` under the same name, deleting
`<middleware_path>/bundles/<md5 of "js-v1.zip">` on the Gateway and calling `/tyk/reload/group`
gives the new code (`X-Bundle: js-v1-rebuilt`). It has to be done on every Gateway, so a new name is
the safer habit.

## Several bundles on one API

From 5.15.0 an API can name several bundles as a comma-separated list
(`"path": "auth-bundle.zip,logging-bundle.zip"`). Each is downloaded and unpacked on its own; the
`pre`, `post`, `post_key_auth` and `response` lists are joined in the order given; only one bundle
may set `auth_check`, and all must use the same `driver`, or the API fails to load (see
"Composing Multiple Bundles" on https://tyk.io/docs/api-management/plugins/overview).

## When the bundle server is down

| Situation | Result |
| --- | --- |
| Bundle already on the Gateway's disk, server down, Gateway restarted | Loaded from disk (`Loading existing bundle`), API answers 200 |
| New bundle name, server down, reload | Five download attempts over about 9 s, then `Couldn't fetch bundle`; that API is left out and answers 404 `Not Found`; other APIs keep answering 200 |

So a new Gateway node (empty disk) cannot serve a bundle API until the bundle server answers. Run
the bundle server highly available, or bake bundles into the image's `middleware/bundles` folder.
