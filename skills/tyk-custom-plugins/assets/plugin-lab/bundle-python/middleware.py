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
