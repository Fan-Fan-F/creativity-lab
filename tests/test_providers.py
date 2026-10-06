"""HTTP integration tests use only a loopback server and a fake credential."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch

from creativity_lab.providers import (BudgetExhausted, CallBudget, ChatProvider,
                                      ProviderError, parse_object)


@contextmanager
def local_server():
    state = {"requests": [], "status": 200, "redirect": None,
             "body": {"choices": [{"message": {"content": "{\"ideas\": []}"}}],
                      "usage": {"prompt_tokens": 11, "completion_tokens": 7}}}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append({"path": self.path, "headers": dict(self.headers),
                                      "body": json.loads(data)})
            self.send_response(state["status"])
            if state["redirect"]:
                self.send_header("Location", state["redirect"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(state.get("raw_body", json.dumps(state["body"]).encode("utf-8")))

        def do_GET(self):
            state["requests"].append({"path": self.path, "headers": dict(self.headers)})
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


class ParserAndBudgetTests(unittest.TestCase):
    def test_non_object_and_non_json_outputs_are_rejected(self):
        for content in ("[]", "null", "42", "not JSON", "prefix {}", {}, "{" * 2000):
            with self.subTest(content=repr(content)[:60]), self.assertRaises(ProviderError):
                parse_object(content)

    def test_markdown_json_fence_is_tolerated(self):
        self.assertEqual(parse_object('```json\n{"ideas": []}\n```'), {"ideas": []})

    def test_usage_is_all_or_nothing_per_response(self):
        budget = CallBudget(3)
        budget.record({"prompt_tokens": 10, "completion_tokens": 4})
        for bad in ({"prompt_tokens": True, "completion_tokens": 3},
                    {"prompt_tokens": 3, "completion_tokens": -1},
                    {"prompt_tokens": 99}, None):
            budget.record(bad)
        self.assertEqual(budget.input_tokens, 10)
        self.assertEqual(budget.output_tokens, 4)
        self.assertFalse(budget.usage_complete)
        self.assertIn("not a token-matched", budget.as_dict()["token_note"])

    def test_http_requires_literal_loopback_and_rejects_embedded_credentials(self):
        for endpoint in ("http://example.com/v1", "http://127.0.0.1.evil.example/v1",
                         "https://user:password@example.com/v1",
                         "https://example.com/v1?key=secret",
                         "https://example.com/v1#fragment", "file:///tmp/model"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ProviderError):
                ChatProvider(model="test-model", base_url=endpoint, api_key="fake-key")


class HTTPBoundaryTests(unittest.TestCase):
    def test_deeply_nested_upstream_json_is_a_controlled_provider_error(self):
        with local_server() as (endpoint, state):
            state["raw_body"] = b"[" * 10000 + b"0" + b"]" * 10000
            provider = ChatProvider(model="test-model", base_url=endpoint, api_key="fake-key-only", timeout=3)
            budget = CallBudget(1)
            with self.assertRaises(ProviderError) as error:
                provider.complete({"purpose": "test"}, budget)
            self.assertNotIn("fake-key-only", str(error.exception))
            self.assertEqual(budget.calls, 1)
            self.assertFalse(budget.usage_complete)

    def test_real_http_request_json_response_and_reported_usage(self):
        with local_server() as (endpoint, state), patch.dict(os.environ,
                {"CREATIVITY_TOKEN_PARAM": "max_completion_tokens"}):
            provider = ChatProvider(model="test-model", base_url=endpoint + "/v1",
                                    api_key="fake-key-only", timeout=3)
            budget = CallBudget(1)
            result = provider.complete({"purpose": "generate", "task": "研究创新"}, budget)
            self.assertEqual(result, {"ideas": []})
            self.assertEqual((budget.calls, budget.input_tokens, budget.output_tokens), (1, 11, 7))
            request = state["requests"][0]
            self.assertEqual(request["path"], "/v1/chat/completions")
            self.assertEqual(request["headers"]["Authorization"], "Bearer fake-key-only")
            self.assertEqual(request["body"]["model"], "test-model")
            self.assertEqual(request["body"]["max_completion_tokens"], 4096)
            self.assertEqual(json.loads(request["body"]["messages"][1]["content"])["task"],
                             "研究创新")
            self.assertNotIn("fake-key-only", json.dumps(provider.identity))

    def test_http_error_counts_once_and_unknown_token_usage_stays_partial(self):
        with local_server() as (endpoint, state):
            state["status"] = 429
            state["body"] = {"error": "DO NOT ECHO THIS fake-key-only"}
            provider = ChatProvider(model="test-model", base_url=endpoint,
                                    api_key="fake-key-only", timeout=3)
            budget = CallBudget(1)
            with self.assertRaises(ProviderError) as error:
                provider.complete({"purpose": "generate"}, budget)
            self.assertNotIn("fake-key-only", str(error.exception))
            self.assertEqual(budget.calls, 1)
            self.assertFalse(budget.usage_complete)
            with self.assertRaises(BudgetExhausted):
                provider.complete({"purpose": "generate"}, budget)
            self.assertEqual(len(state["requests"]), 1)

    def test_redirect_does_not_send_any_request_or_credential_to_destination(self):
        with local_server() as (source, state), local_server() as (destination, target):
            state["redirect"] = destination + "/credential-trap"
            provider = ChatProvider(model="test-model", base_url=source,
                                    api_key="fake-key-only", timeout=3)
            for redirect_status in (301, 302, 303, 307, 308):
                with self.subTest(status=redirect_status):
                    state["status"] = redirect_status
                    budget = CallBudget(1)
                    with self.assertRaises(ProviderError):
                        provider.complete({"purpose": "generate"}, budget)
                    self.assertEqual(budget.calls, 1)
            self.assertEqual(len(state["requests"]), 5)
            self.assertEqual(target["requests"], [])

    def test_outer_json_arrays_null_and_scalars_are_controlled_errors(self):
        with local_server() as (endpoint, state):
            provider = ChatProvider(model="test-model", base_url=endpoint,
                                    api_key="fake-key-only", timeout=3)
            for outer in ([], None, "response", 42):
                with self.subTest(outer=outer), self.assertRaises(ProviderError):
                    state["body"] = outer
                    provider.complete({"purpose": "generate"}, CallBudget(1))

    def test_missing_or_malformed_usage_does_not_manufacture_token_match(self):
        with local_server() as (endpoint, state):
            provider = ChatProvider(model="test-model", base_url=endpoint,
                                    api_key="fake-key-only", timeout=3)
            for usage in (None, {"prompt_tokens": True, "completion_tokens": 2}):
                budget = CallBudget(1)
                state["body"]["usage"] = usage
                self.assertEqual(provider.complete({"purpose": "generate"}, budget),
                                 {"ideas": []})
                self.assertFalse(budget.usage_complete)
                self.assertEqual((budget.input_tokens, budget.output_tokens), (0, 0))

    def test_malformed_success_response_does_not_leak_body(self):
        with local_server() as (endpoint, state):
            state["body"] = {"choices": [], "private": "fake-key-only"}
            provider = ChatProvider(model="test-model", base_url=endpoint,
                                    api_key="fake-key-only", timeout=3)
            with self.assertRaises(ProviderError) as error:
                provider.complete({"purpose": "generate"}, CallBudget(1))
            self.assertNotIn("fake-key-only", str(error.exception))


if __name__ == "__main__":
    unittest.main()
