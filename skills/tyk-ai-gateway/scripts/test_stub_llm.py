"""Unit tests for the stand-in LLM backend shipped in assets/eval-stack/stub_llm.py."""
import http.client
import importlib.util
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

STUB = Path(__file__).resolve().parent.parent / "assets" / "eval-stack" / "stub_llm.py"
spec = importlib.util.spec_from_file_location("stub_llm", STUB)
stub_llm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub_llm)


class StubLlmTest(unittest.TestCase):
    def setUp(self):
        stub_llm.STATE.update(mode="ok", last=None, count=0)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), stub_llm.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body) if body is not None else None
        connection.request(method, path, payload, headers or {"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read().decode()
        connection.close()
        return response.status, dict(response.getheaders()), data

    def chat(self, **extra):
        body = {"model": "stub-small", "messages": [{"role": "user", "content": "hello there"}]}
        body.update(extra)
        return self.call("POST", "/v1/chat/completions", body)

    def test_chat_completion_names_the_backend_and_counts_tokens(self):
        status, headers, data = self.chat()
        answer = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-stub-name"], stub_llm.NAME)
        self.assertEqual(answer["object"], "chat.completion")
        content = answer["choices"][0]["message"]["content"]
        self.assertIn("SERVED-BY=%s" % stub_llm.NAME, content)
        self.assertIn("echo: hello there", content)
        self.assertEqual(answer["usage"]["prompt_tokens"], 2)
        self.assertEqual(answer["usage"]["total_tokens"],
                         answer["usage"]["prompt_tokens"] + answer["usage"]["completion_tokens"])

    def test_streaming_sends_chunks_usage_and_done(self):
        status, headers, data = self.chat(stream=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        events = [line[6:] for line in data.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1], "[DONE]")
        chunks = [json.loads(event) for event in events[:-1]]
        text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"])
        self.assertIn("echo: hello there", text)
        self.assertEqual(chunks[-1]["choices"], [])
        self.assertGreater(chunks[-1]["usage"]["total_tokens"], 0)

    def test_chunked_request_body_is_read(self):
        body = json.dumps({"model": "stub-small", "messages": [{"role": "user", "content": "chunked"}]}).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest("POST", "/v1/chat/completions")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.endheaders()
        half = len(body) // 2
        for part in (body[:half], body[half:]):
            connection.send(b"%x\r\n%s\r\n" % (len(part), part))
        connection.send(b"0\r\n\r\n")
        response = connection.getresponse()
        answer = json.loads(response.read())
        connection.close()
        self.assertIn("echo: chunked", answer["choices"][0]["message"]["content"])

    def test_last_request_is_recorded(self):
        self.chat(temperature=0.5)
        status, _, data = self.call("GET", "/_last")
        record = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(record["count"], 1)
        self.assertEqual(record["last"]["path"], "/v1/chat/completions")
        self.assertEqual(record["last"]["body"]["temperature"], 0.5)
        self.assertIn("content-type", record["last"]["headers"])

    def test_failure_modes(self):
        self.call("POST", "/_mode", {"mode": "error"})
        self.assertEqual(self.chat()[0], 500)
        self.call("POST", "/_mode", {"mode": "ratelimit"})
        status, headers, _ = self.chat()
        self.assertEqual(status, 429)
        self.assertEqual(headers["Retry-After"], "1")
        self.call("POST", "/_mode", {"mode": "ok"})
        self.assertEqual(self.chat()[0], 200)

    def test_models_and_embeddings(self):
        status, _, data = self.call("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual([m["id"] for m in json.loads(data)["data"]], stub_llm.MODELS)
        status, _, data = self.call("POST", "/v1/embeddings", {"model": "stub-small", "input": ["a b", "c"]})
        answer = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(len(answer["data"]), 2)
        self.assertEqual(answer["usage"]["total_tokens"], 3)

    def test_iban_and_tool_call_triggers(self):
        body = {"model": "stub-small", "messages": [{"role": "user", "content": "please say-iban"}]}
        answer = json.loads(self.call("POST", "/v1/chat/completions", body)[2])
        self.assertIn(stub_llm.IBAN, answer["choices"][0]["message"]["content"])
        body = {"model": "stub-small", "messages": [{"role": "user", "content": "call-tool"}],
                "tools": [{"type": "function", "function": {"name": "lookup"}}]}
        answer = json.loads(self.call("POST", "/v1/chat/completions", body)[2])
        choice = answer["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["tool_calls"][0]["function"]["name"], "lookup")

    def test_unknown_path_is_404(self):
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        self.assertEqual(self.call("POST", "/v1/unknown", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
