from http.server import BaseHTTPRequestHandler, HTTPServer
from graphql import build_schema, graphql_sync
import json


schema = build_schema("type Query { hello: String!, shipment(id: ID!): Shipment! } type Shipment { id: ID!, status: String! }")
schema.get_type("Query").fields["hello"].resolve = lambda *_: "hello through Tyk"
schema.get_type("Query").fields["shipment"].resolve = lambda _, info, id: {"id": id, "status": "READY"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        result = graphql_sync(schema, body.get("query", ""), variable_values=body.get("variables"))
        payload = {"data": result.data}
        if result.errors:
            payload["errors"] = [{"message": str(error)} for error in result.errors]
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
