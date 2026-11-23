"""A Tyk gRPC plugin server: one Dispatch call per hook, picked by hook_name."""
import logging
from concurrent import futures

import grpc
import coprocess_object_pb2 as obj_pb
import coprocess_object_pb2_grpc as obj_grpc
from coprocess_common_pb2 import HookType
from coprocess_return_overrides_pb2 import ReturnOverrides

PARTNERS = {"tok-a-123": "partner-a", "tok-b-456": "partner-b"}


def grpc_pre(o):
    o.request.set_headers["X-Grpc-Pre"] = "ran"
    if o.request.headers.get("X-Block"):
        o.request.return_overrides.CopyFrom(ReturnOverrides(
            response_code=403, response_body='{"code":"PARTNER_BLOCKED"}',
            headers={"Content-Type": "application/json"}, override_error=True))
    return o


def grpc_auth(o):
    partner = PARTNERS.get(o.request.headers.get("X-Partner-Token", ""))
    if partner is None:
        o.request.return_overrides.CopyFrom(ReturnOverrides(
            response_code=401, response_error="unknown or missing X-Partner-Token"))
        return o
    api_id = o.spec["APIID"]
    o.session.rate, o.session.per, o.session.quota_max = 2, 60, -1
    o.session.alias = partner
    o.session.org_id = o.spec["OrgID"]
    o.session.metadata["partner"] = partner
    o.session.metadata["rate_key"] = api_id + ":" + partner   # one counter per API and partner
    o.session.metadata["rate_limit_pattern"] = "$tyk_meta.rate_key"
    o.metadata["token"] = "partner-" + partner   # required: without it every request got 403
    o.session.access_rights[api_id].api_id = api_id
    o.session.access_rights[api_id].versions.append("Default")
    return o


def grpc_post(o):
    o.request.set_headers["X-Grpc-Post"] = "alias=%s;config=%s" % (o.session.alias or "none", o.spec.get("config_data", ""))
    return o


def grpc_response(o):
    o.response.headers["X-Grpc-Response"] = "status=%d" % o.response.status_code
    return o


HOOKS = {"GrpcPre": grpc_pre, "GrpcAuth": grpc_auth, "GrpcPost": grpc_post, "GrpcResponse": grpc_response}


class Dispatcher(obj_grpc.DispatcherServicer):
    def Dispatch(self, o, context):
        hook = HOOKS.get(o.hook_name)
        logging.info("hook=%s type=%s", o.hook_name, HookType.Name(o.hook_type))
        return hook(o) if hook else o

    def DispatchEvent(self, event, context):
        return obj_pb.EventReply()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    obj_grpc.add_DispatcherServicer_to_server(Dispatcher(), server)
    server.add_insecure_port("[::]:5555")
    server.start()
    logging.info("listening on :5555")
    server.wait_for_termination()
