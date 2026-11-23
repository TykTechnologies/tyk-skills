// Pre hook: refuse requests that carry X-Block with an exact JSON body; tag the rest.
var jsDeny = new TykJS.TykMiddleware.NewMiddleware({});
jsDeny.NewProcessRequest(function(request, session, config) {
    if (request.Headers["X-Block"] !== undefined) {
        request.ReturnOverrides.ResponseCode = 403;
        request.ReturnOverrides.ResponseBody = '{"code":"PARTNER_BLOCKED"}';
        request.ReturnOverrides.ResponseHeaders = {"Content-Type": "application/json"};
        request.ReturnOverrides.OverrideError = true;
    }
    request.SetHeaders["X-Region"] = config.config_data.region || "";
    return jsDeny.ReturnData(request, session ? session.meta_data : {});
});
