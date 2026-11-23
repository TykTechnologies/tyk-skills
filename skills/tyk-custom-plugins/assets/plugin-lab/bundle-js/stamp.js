var bundleStamp = new TykJS.TykMiddleware.NewMiddleware({});
bundleStamp.NewProcessRequest(function(request, session, config) {
    request.SetHeaders["X-Bundle"] = "js-v1";
    return bundleStamp.ReturnData(request, session.meta_data);
});
