var partnerAuth = new TykJS.TykMiddleware.NewMiddleware({});
partnerAuth.NewProcessRequest(function(request, session, config) {
    var partners = {"tok-a-123": "partner-a", "tok-b-456": "partner-b"};
    var token = request.Headers["X-Partner-Token"];
    var partner = token ? partners[token[0]] : undefined;
    if (partner === undefined) {
        request.ReturnOverrides.ResponseCode = 401;
        request.ReturnOverrides.ResponseError = "unknown or missing X-Partner-Token";
        return partnerAuth.ReturnData(request, {});
    }
    return partnerAuth.ReturnAuthData(request, {
        "org_id": "default", "alias": partner, "rate": 2, "per": 60, "quota_max": -1,
        "meta_data": {"partner": partner, "rate_key": config.APIID + ":" + partner,
                      "rate_limit_pattern": "$tyk_meta.rate_key"},
        "access_rights": {"js-auth": {"api_id": "js-auth", "api_name": "js-auth", "versions": ["Default"]}}});
});
