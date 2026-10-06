"""Protocol boundaries exercised through real loopback HTTP and fake credentials.

These fixtures validate adapters, not any vendor's live availability or model quality.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

from creativity_lab.providers import CallBudget, ChatProvider, ProviderError


KEY = "fake-protocol-test-key-never-a-real-credential"
PRIVATE_BODY = "private-upstream-body-must-not-reach-error " + KEY


def chat_response(content='{"ideas": []}', finish="stop"):
    return {"choices": [{"finish_reason": finish,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 13, "completion_tokens": 5}}


def responses_response(content='{"ideas": []}', status="completed"):
    return {"status": status, "error": None, "incomplete_details": None,
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": content}]}],
            "usage": {"input_tokens": 17, "output_tokens": 7,
                      "output_tokens_details": {"reasoning_tokens": 3}}}


def anthropic_response(content='{"ideas": []}', stop="end_turn"):
    return {"type": "message", "role": "assistant", "stop_reason": stop,
            "content": [{"type": "text", "text": content}],
            "usage": {"input_tokens": 11, "cache_creation_input_tokens": 7,
                      "cache_read_input_tokens": 5, "output_tokens": 9}}


@contextmanager
def protocol_server(body=None):
    state = {"requests": [], "body": chat_response() if body is None else body,
             "status": 200, "headers": {}}

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append({"method": self.command, "path": self.path,
                                      "headers": {k.lower(): v for k, v in self.headers.items()},
                                      "body": json.loads(raw) if raw else None})
            wait_for = state.get("wait_before_response")
            if wait_for is not None:
                wait_for.wait(timeout=5)
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            for name, value in state["headers"].items():
                self.send_header(name, value)
            self.end_headers()
            response = state["body"]
            if callable(response):
                response = response(state["requests"][-1])
            encoded = response if isinstance(response, bytes) else json.dumps(response).encode("utf-8")
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_POST = handle_request
        do_GET = handle_request

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        wait_for = state.get("wait_before_response")
        if wait_for is not None:
            wait_for.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


class ProtocolRequestsTests(unittest.TestCase):
    def provider(self, endpoint, protocol="openai_chat", **options):
        return ChatProvider(model="fixture-model", base_url=endpoint, api_key=KEY,
                            protocol=protocol, timeout=3, token_param="auto", **options)

    def test_chat_json_request_uses_configured_limit_and_reports_usage(self):
        with protocol_server() as (endpoint, state):
            provider = self.provider(endpoint + "/v1", max_output_tokens=777, json_mode="json_object")
            budget = CallBudget(1)
            payload = {"purpose": "generate", "task": "研究有约束的创新"}
            self.assertEqual(provider.complete(payload, budget), {"ideas": []})
            self.assertEqual(len(state["requests"]), 1)
            request = state["requests"][0]
            self.assertEqual((request["method"], request["path"]), ("POST", "/v1/chat/completions"))
            self.assertEqual(request["headers"].get("authorization"), "Bearer " + KEY)
            self.assertNotIn("x-api-key", request["headers"])
            body = request["body"]
            self.assertEqual(body["model"], "fixture-model")
            self.assertEqual(body["max_completion_tokens"], 777)
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertEqual(json.loads(body["messages"][-1]["content"]), payload)
            self.assertNotIn("temperature", body)
            self.assertNotIn("max_tokens", body)
            self.assertEqual((budget.calls, budget.input_tokens, budget.output_tokens), (1, 13, 5))
            self.assertTrue(budget.usage_complete)
            self.assertEqual(provider.identity["protocol"], "openai_chat")
            self.assertNotIn(KEY, json.dumps(provider.identity))

    def test_responses_skips_reasoning_and_extracts_all_text_blocks(self):
        response = responses_response()
        response["output"] = [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": PRIVATE_BODY}]},
            {"type": "message", "role": "assistant", "status": "completed", "content": [
                {"type": "output_text", "text": '{"ideas":'},
                {"type": "output_text", "text": "[]}"}]}]
        with protocol_server(response) as (endpoint, state):
            provider = self.provider(endpoint + "/v1", "openai_responses", max_output_tokens=777,
                                     json_mode="json_object")
            payload, budget = {"purpose": "test", "task": "独立输入"}, CallBudget(1)
            self.assertEqual(provider.complete(payload, budget, max_output_tokens=321), {"ideas": []})
            request, = state["requests"]
            self.assertEqual(request["path"], "/v1/responses")
            self.assertEqual(request["headers"].get("authorization"), "Bearer " + KEY)
            body = request["body"]
            self.assertEqual(body["max_output_tokens"], 321)
            self.assertEqual(body["text"]["format"], {"type": "json_object"})
            self.assertIsInstance(body["instructions"], str)
            self.assertEqual(json.loads(body["input"]), payload)
            self.assertNotIn("messages", body)
            self.assertNotIn("response_format", body)
            self.assertNotIn("temperature", body)
            self.assertEqual((budget.calls, budget.input_tokens, budget.output_tokens), (1, 17, 7))
            self.assertTrue(budget.usage_complete)  # reasoning_tokens is already part of output_tokens.

    def test_anthropic_system_header_text_and_cache_tokens(self):
        response = anthropic_response()
        response["content"] = [{"type": "thinking", "thinking": PRIVATE_BODY, "signature": "opaque"},
                               {"type": "text", "text": '{"ideas":'},
                               {"type": "text", "text": "[]}"}]
        with protocol_server(response) as (endpoint, state):
            provider = self.provider(endpoint, "anthropic", max_output_tokens=2222, json_mode="prompt")
            budget = CallBudget(1)
            payload = {"purpose": "test", "task": "约束中的创新"}
            self.assertEqual(provider.complete(payload, budget), {"ideas": []})
            request, = state["requests"]
            self.assertEqual(request["path"], "/v1/messages")
            self.assertEqual(request["headers"].get("x-api-key"), KEY)
            self.assertEqual(request["headers"].get("anthropic-version"), "2023-06-01")
            self.assertNotIn("authorization", request["headers"])
            body = request["body"]
            self.assertIsInstance(body["system"], str)
            self.assertEqual(body["messages"][0]["role"], "user")
            self.assertEqual(json.loads(body["messages"][0]["content"]), payload)
            self.assertEqual(body["max_tokens"], 2222)
            self.assertNotIn("max_completion_tokens", body)
            self.assertNotIn("response_format", body)
            self.assertNotIn("text", body)
            self.assertNotIn("thinking", body)
            self.assertNotIn("temperature", body)
            self.assertEqual((budget.calls, budget.input_tokens, budget.output_tokens), (1, 23, 9))
            self.assertTrue(budget.usage_complete)

    def test_prompt_mode_does_not_send_vendor_json_constraints(self):
        cases = [("openai_chat", chat_response()), ("openai_responses", responses_response()),
                 ("anthropic", anthropic_response())]
        for protocol, response in cases:
            with self.subTest(protocol=protocol), protocol_server(response) as (endpoint, state):
                provider = self.provider(endpoint + "/v1", protocol, json_mode="prompt")
                self.assertEqual(provider.complete({"purpose": "test"}, CallBudget(1)), {"ideas": []})
                body = state["requests"][0]["body"]
                self.assertNotIn("response_format", body)
                self.assertNotIn("text", body)
                self.assertNotIn("output_config", body)
                self.assertEqual(len(state["requests"]), 1)

    def test_explicit_auth_header_override_never_sends_both_credentials(self):
        for protocol, response, auth_type, expected in (
                ("openai_chat", chat_response(), "x_api_key", "x-api-key"),
                ("anthropic", anthropic_response(), "bearer", "authorization")):
            with self.subTest(protocol=protocol), protocol_server(response) as (endpoint, state):
                provider = self.provider(endpoint + "/v1", protocol, auth_type=auth_type, json_mode="prompt")
                provider.complete({"purpose": "test"}, CallBudget(1))
                headers = state["requests"][0]["headers"]
                self.assertEqual(headers.get(expected), ("Bearer " if expected == "authorization" else "") + KEY)
                self.assertNotIn("x-api-key" if expected == "authorization" else "authorization", headers)

    def test_output_override_does_not_mutate_later_configured_requests(self):
        with protocol_server() as (endpoint, state):
            provider = self.provider(endpoint, max_output_tokens=2048)
            provider.complete({"purpose": "connection_test"}, CallBudget(1), max_output_tokens=256)
            provider.complete({"purpose": "generate"}, CallBudget(1))
            self.assertEqual([r["body"]["max_completion_tokens"] for r in state["requests"]], [256, 2048])


class ModelListTests(unittest.TestCase):
    def test_model_list_deduplicates_and_drops_untrusted_extra_fields(self):
        rows = [{"id": "model-a", "api_key": KEY, "private": PRIVATE_BODY},
                {"id": "model-a"}, {"id": "model-b"},
                {"id": None}, {"id": False}, {"id": []}, {"id": ""},
                "not-an-object"]
        with protocol_server({"object": "list", "data": rows}) as (endpoint, state):
            provider = ChatProvider(model="", require_model=False, base_url=endpoint + "/v1",
                                    api_key=KEY, protocol="openai_responses", timeout=3)
            models = provider.list_models()
            self.assertEqual([row["id"] for row in models], ["model-a", "model-b"])
            self.assertTrue(all(set(row) == {"id", "name"} and isinstance(row["name"], str)
                                for row in models))
            self.assertNotIn(KEY, json.dumps(models))
            request, = state["requests"]
            self.assertEqual((request["method"], request["path"]), ("GET", "/v1/models"))
            self.assertIsNone(request["body"])
            self.assertEqual(request["headers"].get("authorization"), "Bearer " + KEY)

    def test_anthropic_model_list_uses_version_header_and_preserves_api_prefix(self):
        response = {"data": [{"id": "model-a", "display_name": "Displayed Model A"}], "has_more": False}
        for suffix, expected_path in (("", "/v1/models"), ("/v1/", "/v1/models"),
                                      ("/gateway-prefix", "/gateway-prefix/models")):
            with self.subTest(suffix=suffix), protocol_server(response) as (endpoint, state):
                provider = ChatProvider(model="", require_model=False, base_url=endpoint + suffix,
                                        api_key=KEY, protocol="anthropic", timeout=3)
                self.assertEqual(provider.list_models(), [{"id": "model-a", "name": "Displayed Model A"}])
                request, = state["requests"]
                parsed = urlsplit(request["path"])
                self.assertEqual((request["method"], parsed.path), ("GET", expected_path))
                self.assertEqual(parse_qs(parsed.query).get("limit"), ["100"])
                self.assertEqual(request["headers"].get("x-api-key"), KEY)
                self.assertEqual(request["headers"].get("anthropic-version"), "2023-06-01")

    def test_anthropic_pagination_encodes_cursor_and_deduplicates_page_overlap(self):
        cursor = "model-a/alias?x=1&y=2"

        def page(request):
            if not parse_qs(urlsplit(request["path"]).query).get("after_id"):
                return {"data": [{"id": cursor, "display_name": "Model A"}],
                        "has_more": True, "last_id": cursor}
            return {"data": [{"id": cursor, "display_name": "Repeated A"},
                             {"id": "model-b", "display_name": "Model B"}],
                    "has_more": False, "last_id": "model-b"}

        with protocol_server(page) as (endpoint, state):
            provider = ChatProvider(model="", require_model=False, base_url=endpoint,
                                    api_key=KEY, protocol="anthropic", timeout=3)
            self.assertEqual(provider.list_models(), [{"id": cursor, "name": "Model A"},
                                                      {"id": "model-b", "name": "Model B"}])
            self.assertEqual(len(state["requests"]), 2)
            first, second = state["requests"]
            self.assertEqual(parse_qs(urlsplit(first["path"]).query), {"limit": ["100"]})
            self.assertEqual(parse_qs(urlsplit(second["path"]).query),
                             {"limit": ["100"], "after_id": [cursor]})
            self.assertTrue(all(request["method"] == "GET" and
                                urlsplit(request["path"]).path == "/v1/models"
                                for request in state["requests"]))

    def test_bad_model_list_shapes_raise_sanitized_errors(self):
        for body in ({"data": PRIVATE_BODY}, [], {"unexpected": PRIVATE_BODY}):
            with self.subTest(body=type(body).__name__), protocol_server(body) as (endpoint, _):
                provider = ChatProvider(model="", require_model=False, base_url=endpoint,
                                        api_key=KEY, timeout=3)
                with self.assertRaises(ProviderError) as error:
                    provider.list_models()
                self.assertNotIn(PRIVATE_BODY, str(error.exception))
                self.assertNotIn(KEY, str(error.exception))


class ProtocolFailureTests(unittest.TestCase):
    def provider(self, endpoint, protocol):
        return ChatProvider(model="fixture-model", base_url=endpoint + "/v1", api_key=KEY,
                            protocol=protocol, timeout=3, json_mode="prompt")

    def test_truncation_is_not_success_even_when_text_is_valid_json(self):
        responses = responses_response(status="incomplete")
        responses["incomplete_details"] = {"reason": "max_output_tokens"}
        for protocol, response, expected_usage in (
                ("openai_chat", chat_response(finish="length"), (13, 5)),
                ("openai_responses", responses, (17, 7)),
                ("anthropic", anthropic_response(stop="max_tokens"), (23, 9))):
            with self.subTest(protocol=protocol), protocol_server(response) as (endpoint, state):
                budget = CallBudget(1)
                with self.assertRaises(ProviderError) as error:
                    self.provider(endpoint, protocol).complete({"purpose": "test"}, budget)
                self.assertEqual(error.exception.code, "output_limit")
                self.assertTrue(error.exception.connection_ok)
                self.assertEqual((budget.input_tokens, budget.output_tokens), expected_usage)
                self.assertEqual((budget.calls, len(state["requests"])), (1, 1))

    def test_refusals_are_rejected_without_echoing_refusal_text(self):
        chat = chat_response()
        chat["choices"][0]["message"]["refusal"] = PRIVATE_BODY
        responses = responses_response()
        responses["output"][0]["content"] = [{"type": "refusal", "refusal": PRIVATE_BODY}]
        anthropic = anthropic_response(stop="refusal")
        anthropic["content"][0]["text"] = PRIVATE_BODY
        for protocol, response in (("openai_chat", chat), ("openai_responses", responses),
                                   ("anthropic", anthropic)):
            with self.subTest(protocol=protocol), protocol_server(response) as (endpoint, state):
                budget = CallBudget(1)
                with self.assertRaises(ProviderError) as error:
                    self.provider(endpoint, protocol).complete({"purpose": "test"}, budget)
                self.assertTrue(error.exception.connection_ok)
                self.assertNotIn(PRIVATE_BODY, str(error.exception))
                self.assertNotIn(KEY, str(error.exception))
                self.assertEqual((budget.calls, len(state["requests"])), (1, 1))

    def test_responses_noncompleted_states_never_become_completed_json(self):
        for status in ("queued", "in_progress", "unknown-vendor-state"):
            with self.subTest(status=status), protocol_server(responses_response(status=status)) as (endpoint, state):
                budget = CallBudget(1)
                with self.assertRaises(ProviderError) as error:
                    self.provider(endpoint, "openai_responses").complete({"purpose": "test"}, budget)
                self.assertEqual(error.exception.code, "incomplete_response")
                self.assertTrue(error.exception.connection_ok)
                self.assertEqual((budget.calls, budget.input_tokens, budget.output_tokens), (1, 17, 7))
                self.assertTrue(budget.usage_complete)
                self.assertEqual(len(state["requests"]), 1)

    def test_responses_incomplete_reason_distinguishes_filter_and_output_limit(self):
        for reason, expected_code in (("content_filter", "refused"),
                                      ("max_output_tokens", "output_limit"),
                                      (None, "incomplete_response")):
            response = responses_response(status="incomplete")
            response["incomplete_details"] = {"reason": reason} if reason else None
            with self.subTest(reason=reason), protocol_server(response) as (endpoint, state):
                budget = CallBudget(1)
                with self.assertRaises(ProviderError) as error:
                    self.provider(endpoint, "openai_responses").complete({"purpose": "test"}, budget)
                self.assertEqual(error.exception.code, expected_code)
                self.assertTrue(error.exception.connection_ok)
                self.assertEqual((budget.calls, budget.input_tokens, budget.output_tokens), (1, 17, 7))
                self.assertTrue(budget.usage_complete)
                self.assertEqual(len(state["requests"]), 1)

    def test_invalid_generated_json_is_distinct_from_auth_failure(self):
        for protocol, response in (("openai_chat", chat_response(PRIVATE_BODY)),
                                   ("openai_responses", responses_response(PRIVATE_BODY)),
                                   ("anthropic", anthropic_response(PRIVATE_BODY))):
            with self.subTest(protocol=protocol), protocol_server(response) as (endpoint, state):
                budget = CallBudget(1)
                with self.assertRaises(ProviderError) as error:
                    self.provider(endpoint, protocol).complete({"purpose": "test"}, budget)
                self.assertEqual(error.exception.code, "invalid_json")
                self.assertTrue(error.exception.connection_ok)
                self.assertNotIn(KEY, str(error.exception))
                self.assertEqual((budget.calls, len(state["requests"])), (1, 1))

    def test_http_statuses_are_classified_without_body_leaks_or_retries(self):
        with protocol_server({"error": {"message": PRIVATE_BODY}}) as (endpoint, state):
            provider = self.provider(endpoint, "openai_chat")
            for status, code, retryable in ((401, "auth_failed", False),
                                           (404, "endpoint_not_found", False),
                                           (429, "rate_limited", True),
                                           (500, "provider_unavailable", True)):
                with self.subTest(status=status):
                    state["status"] = status
                    before, budget = len(state["requests"]), CallBudget(1)
                    with self.assertRaises(ProviderError) as error:
                        provider.complete({"purpose": "test"}, budget)
                    failure = error.exception
                    self.assertEqual(failure.code, code)
                    self.assertEqual(failure.http_status, status)
                    self.assertIs(failure.retryable, retryable)
                    self.assertNotIn(PRIVATE_BODY, str(failure))
                    self.assertNotIn(KEY, str(failure))
                    self.assertEqual((budget.calls, len(state["requests"]) - before), (1, 1))
                    self.assertFalse(budget.usage_complete)

    def test_models_auth_failure_is_sanitized_and_never_generates(self):
        with protocol_server({"error": {"message": PRIVATE_BODY}}) as (endpoint, state):
            state["status"] = 401
            provider = ChatProvider(model="", require_model=False, base_url=endpoint,
                                    api_key=KEY, protocol="anthropic", timeout=3)
            with self.assertRaises(ProviderError) as error:
                provider.list_models()
            self.assertEqual(error.exception.code, "auth_failed")
            self.assertEqual(error.exception.http_status, 401)
            self.assertNotIn(KEY, str(error.exception))
            self.assertEqual([r["method"] for r in state["requests"]], ["GET"])

    def test_redirect_never_forwards_get_post_or_credentials(self):
        with protocol_server() as (source, state), protocol_server() as (destination, target):
            state["headers"]["Location"] = destination + "/credential-trap"
            for protocol in ("openai_chat", "openai_responses", "anthropic"):
                provider = self.provider(source, protocol)
                for status in (301, 302, 303, 307, 308):
                    with self.subTest(protocol=protocol, status=status):
                        state["status"] = status
                        budget = CallBudget(1)
                        with self.assertRaises(ProviderError):
                            provider.complete({"purpose": "test"}, budget)
                        with self.assertRaises(ProviderError):
                            provider.list_models()
                        self.assertEqual(budget.calls, 1)
                        self.assertEqual(target["requests"], [])
            self.assertEqual(len(state["requests"]), 30)

    def test_malformed_protocol_usage_does_not_manufacture_token_match(self):
        for protocol, response, bad_field in (("openai_responses", responses_response(), "input_tokens"),
                                             ("anthropic", anthropic_response(), "input_tokens"),
                                             ("anthropic", anthropic_response(), "cache_read_input_tokens")):
            response["usage"][bad_field] = True
            with self.subTest(protocol=protocol, bad_field=bad_field), protocol_server(response) as (endpoint, _):
                budget = CallBudget(1)
                self.assertEqual(self.provider(endpoint, protocol).complete({"purpose": "test"}, budget),
                                 {"ideas": []})
                self.assertFalse(budget.usage_complete)
                self.assertEqual((budget.input_tokens, budget.output_tokens), (0, 0))

    def test_read_timeout_is_one_counted_attempt_with_unknown_usage(self):
        with protocol_server() as (endpoint, state):
            state["wait_before_response"] = threading.Event()
            provider = ChatProvider(model="fixture-model", base_url=endpoint, api_key=KEY,
                                    protocol="openai_chat", timeout=1)
            budget = CallBudget(1)
            with self.assertRaises(ProviderError) as error:
                provider.complete({"purpose": "test"}, budget)
            self.assertIs(error.exception.retryable, True)
            self.assertFalse(error.exception.connection_ok)
            self.assertEqual((budget.calls, len(state["requests"])), (1, 1))
            self.assertFalse(budget.usage_complete)
            self.assertNotIn(KEY, str(error.exception))


if __name__ == "__main__":
    unittest.main()
