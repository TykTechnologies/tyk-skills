function veHello(request, session, config) {
    var who = request.Params["name"] ? request.Params["name"][0] : "world";
    return TykJsResponse({
        Body: JSON.stringify({hello: who, method: request.Method, api: config.APIID, settings: config.config_data}),
        Code: 200,
        Headers: {"Content-Type": "application/json"}
    }, session.meta_data);
}

function veAggregate(request, session, config) {
    var raw = TykMakeHttpRequest(JSON.stringify({
        Method: "GET", Domain: "http://upstream:8080", Resource: "/uuid", Headers: {}, Body: ""
    }));
    var upstream = JSON.parse(raw);
    return TykJsResponse({
        Body: JSON.stringify({upstreamStatus: upstream.Code, upstreamBody: JSON.parse(upstream.Body)}),
        Code: 200,
        Headers: {"Content-Type": "application/json"}
    }, session.meta_data);
}
