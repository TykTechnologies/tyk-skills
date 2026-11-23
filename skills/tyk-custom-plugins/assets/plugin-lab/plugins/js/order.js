// One file, four hooks. Each counts its runs in Redis (javascript driver only)
// and leaves a header so the upstream echo shows what each stage saw.
var orderPre = new TykJS.TykMiddleware.NewMiddleware({});
orderPre.NewProcessRequest(function(request, session, config) {
    TykStorageIncr("order:pre", 600);
    request.SetHeaders["X-Stage-Pre"] = "alias=" + (session.alias || "none") +
        ";builtin=" + (request.Headers["X-Builtin"] ? "yes" : "no");
    return orderPre.ReturnData(request, session.meta_data);
});

var orderPostAuth = new TykJS.TykMiddleware.NewMiddleware({});
orderPostAuth.NewProcessRequest(function(request, session, config) {
    TykStorageIncr("order:postauth", 600);
    request.SetHeaders["X-Stage-Post-Auth"] = "alias=" + (session.alias || "none") +
        ";builtin=" + (request.Headers["X-Builtin"] ? "yes" : "no");
    return orderPostAuth.ReturnData(request, session.meta_data);
});

var orderPost = new TykJS.TykMiddleware.NewMiddleware({});
orderPost.NewProcessRequest(function(request, session, config) {
    TykStorageIncr("order:post", 600);
    request.SetHeaders["X-Stage-Post"] = "alias=" + (session.alias || "none") +
        ";builtin=" + (request.Headers["X-Builtin"] ? "yes" : "no");
    return orderPost.ReturnData(request, session.meta_data);
});

var orderResponse = new TykJS.TykMiddleware.NewMiddleware({});
orderResponse.NewProcessResponse(function(response, request, session, config) {
    TykStorageIncr("order:response", 600);
    response.SetHeaders["X-Stage-Response"] = "status=" + response.StatusCode + ";alias=" + (session.alias || "none");
    return orderResponse.ReturnResponseData(response, session.meta_data);
});
