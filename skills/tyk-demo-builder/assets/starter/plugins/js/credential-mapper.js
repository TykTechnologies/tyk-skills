// __DISPLAY_NAME__ consumer compatibility: a pre-authentication Tyk JavaScript plugin.
//
// Consumers migrated from a previous gateway often send their credential as a
// `user_key` query parameter or an `app_key` header. This plugin copies that value
// into the Authorization header (the header this API's key scheme reads) before
// authentication runs, and removes both so the credential never reaches the upstream.
//
// Written for the Tyk 5.15 goja engine (pluginConfig.driver "javascript"), which has
// no `console` and no `_` (underscore) helper: only plain ES5 and log() are used, so
// the same file also runs on the older otto engine.
var __JS_PLUGIN_NAME__ = new TykJS.TykMiddleware.NewMiddleware({});

__JS_PLUGIN_NAME__.NewProcessRequest(function (request, session, spec) {
  var credential = "";
  var source = "";

  var params = request.Params || {};
  if (params.user_key && params.user_key.length > 0 && params.user_key[0] !== "") {
    credential = params.user_key[0];
    source = "query:user_key";
  }

  var headers = request.Headers || {};
  var mappedHeaders = [];
  for (var name in headers) {
    var lower = name.toLowerCase();
    if (lower === "app_key" || lower === "app-key") {
      mappedHeaders.push(name);
      if (credential === "" && headers[name].length > 0 && headers[name][0] !== "") {
        credential = headers[name][0];
        source = "header:" + lower;
      }
    }
  }

  if (credential !== "") {
    request.SetHeaders["Authorization"] = credential;
    log("__SLUG__-credential-mapper: mapped credential from " + source + " into Authorization");
  }

  // Never forward the original credential to the upstream.
  request.DeleteParams.push("user_key");
  for (var i = 0; i < mappedHeaders.length; i++) {
    request.DeleteHeaders.push(mappedHeaders[i]);
  }

  return __JS_PLUGIN_NAME__.ReturnData(request, {});
});
