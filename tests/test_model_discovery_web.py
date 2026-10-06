"""End-to-end settings discovery/probes against real local HTTP fixtures only."""
from contextlib import contextmanager
import json
import os
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import urllib.request

from creativity_lab.web import EXAMPLE
from test_protocols import (KEY, PRIVATE_BODY, anthropic_response, chat_response,
                            protocol_server, responses_response)
from test_settings import invoke_real_provider, request, studio_server


@contextmanager
def capture_real_timeouts():
    """Observe the transport option while retaining actual urllib/socket I/O."""
    observed = []
    original = urllib.request.OpenerDirector.open

    def recorded(opener, *args, **kwargs):
        observed.append(kwargs.get("timeout"))
        return original(opener, *args, **kwargs)

    with patch.object(urllib.request.OpenerDirector, "open", recorded):
        yield observed


class ModelDiscoveryWebTests(unittest.TestCase):
    def config(self, server):
        status, body = request(server, "GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertNotIn(KEY.encode(), body)
        return json.loads(body)

    def settings(self, endpoint, protocol="openai_chat", model="saved-model", **options):
        return {"base_url": endpoint + "/v1", "model": model, "api_key": KEY,
                "protocol": protocol, "json_mode": "prompt" if protocol == "anthropic" else "json_object",
                "auth_type": "auto", "token_param": "auto", **options}

    def post(self, server, path, data):
        token = self.config(server)["settings_token"]
        status, raw = request(server, "POST", path, data, token)
        self.assertNotIn(KEY.encode(), raw)
        self.assertNotIn(PRIVATE_BODY.encode(), raw)
        return status, json.loads(raw)

    def save(self, server, settings):
        status, result = self.post(server, "/api/settings", settings)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["live_ready"])
        return result

    def wait_job(self, server, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, body = request(server, "GET", f"/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            self.assertNotIn(KEY.encode(), body)
            job = json.loads(body)
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(.01)
        self.fail("Loopback fixture job did not finish")

    def test_discovery_all_protocols_allows_empty_model_and_never_saves_draft(self):
        for protocol in ("openai_chat", "openai_responses", "anthropic"):
            rows = {"data": [{"id": "fixture-a", "display_name": "Fixture A", "api_key": KEY,
                              "private": PRIVATE_BODY}], "has_more": False}
            with self.subTest(protocol=protocol), protocol_server(rows) as (endpoint, upstream), \
                    patch.dict(os.environ, {}, clear=True), studio_server() as server, capture_real_timeouts() as timeouts:
                before = self.config(server)
                draft = self.settings(endpoint, protocol, model="", timeout=211)
                status, result = self.post(server, "/api/settings/models", draft)
                self.assertEqual(status, 200, result)
                self.assertEqual(result["models"], [{"id": "fixture-a", "name": "Fixture A"}])
                self.assertEqual(result["count"], 1)
                self.assertEqual(result["protocol"], protocol)
                observed, = upstream["requests"]
                self.assertEqual((observed["method"], urlsplit(observed["path"]).path), ("GET", "/v1/models"))
                self.assertIsNone(observed["body"])
                self.assertEqual(timeouts, [30])  # Discovery is bounded independently of slow generation.
                self.assertEqual(self.config(server), before)
                self.assertFalse(self.config(server)["session_configured"])

    def test_saved_options_reach_real_probe_request_for_each_protocol(self):
        for protocol, response, expected_path, token_field, input_tokens, output_tokens in (
                ("openai_chat", chat_response('{"ok":true}'), "/v1/chat/completions", "max_completion_tokens", 13, 5),
                ("openai_responses", responses_response('{"ok":true}'), "/v1/responses", "max_output_tokens", 17, 7),
                ("anthropic", anthropic_response('{"ok":true}'), "/v1/messages", "max_tokens", 23, 9)):
            with self.subTest(protocol=protocol), protocol_server(response) as (endpoint, upstream), \
                    patch.dict(os.environ, {}, clear=True), studio_server() as server:
                saved = self.save(server, self.settings(endpoint, protocol, max_output_tokens=8192,
                                                        timeout=211, temperature=.7))
                self.assertEqual(upstream["requests"], [])
                self.assertEqual((saved["max_output_tokens"], saved["timeout"], saved["temperature"]),
                                 (8192, 211, .7))
                with capture_real_timeouts() as timeouts:
                    status, result = self.post(server, "/api/settings/test", {})
                self.assertEqual(status, 200, result)
                self.assertTrue(result["ok"])
                self.assertEqual(result["protocol"], protocol)
                self.assertEqual(timeouts, [211])
                observed, = upstream["requests"]
                self.assertEqual((observed["method"], observed["path"]), ("POST", expected_path))
                self.assertEqual(observed["body"][token_field], 8192)
                self.assertEqual(observed["body"]["temperature"], .7)
                self.assertEqual(observed["body"]["model"], "saved-model")
                auth = "x-api-key" if protocol == "anthropic" else "authorization"
                self.assertEqual(observed["headers"][auth], KEY if protocol == "anthropic" else "Bearer " + KEY)
                self.assertEqual((result["budget"]["calls"], result["budget"]["input_tokens"],
                                  result["budget"]["output_tokens"]), (1, input_tokens, output_tokens))
                self.assertTrue(result["budget"]["usage_complete"])
                self.assertEqual(self.config(server), saved)

    def test_discovery_auth_failure_has_code_no_private_body_and_no_mutation(self):
        with protocol_server({"error": {"message": PRIVATE_BODY}}) as (endpoint, upstream), \
                patch.dict(os.environ, {}, clear=True), studio_server() as server:
            before = self.config(server)
            upstream["status"] = 401
            status, result = self.post(server, "/api/settings/models", self.settings(endpoint, model=""))
            self.assertEqual(status, 502)
            self.assertEqual((result["code"], result["http_status"], result["retryable"]),
                             ("auth_failed", 401, False))
            self.assertEqual([row["method"] for row in upstream["requests"]], ["GET"])
            self.assertEqual(self.config(server), before)

    def test_discovery_nonce_and_cross_origin_block_network_before_draft_processing(self):
        with protocol_server({"data": []}) as (endpoint, upstream), \
                patch.dict(os.environ, {}, clear=True), studio_server() as server:
            before = self.config(server)
            data = self.settings(endpoint, model="")
            for token, headers in ((None, {}), ("wrong-token", {}),
                                   (before["settings_token"], {"Origin": "https://attacker.example"}),
                                   (before["settings_token"], {"Sec-Fetch-Site": "cross-site"}),
                                   (before["settings_token"], {"Host": "attacker.example"})):
                with self.subTest(headers=headers, valid_token=token == before["settings_token"]):
                    status, body = request(server, "POST", "/api/settings/models", data, token, headers)
                    self.assertEqual(status, 403)
                    self.assertNotIn(KEY.encode(), body)
            self.assertEqual(upstream["requests"], [])
            self.assertEqual(self.config(server), before)

    def test_unsupported_discovery_does_not_prevent_manual_model_save(self):
        with protocol_server({"error": PRIVATE_BODY}) as (endpoint, upstream), \
                patch.dict(os.environ, {}, clear=True), studio_server() as server:
            upstream["status"] = 404
            status, result = self.post(server, "/api/settings/models", self.settings(endpoint, model=""))
            self.assertEqual(status, 502)
            self.assertEqual(result["code"], "endpoint_not_found")
            self.assertEqual(len(upstream["requests"]), 1)
            saved = self.save(server, self.settings(endpoint, model="manually-entered-uncatalogued-model"))
            self.assertEqual(saved["model"], "manually-entered-uncatalogued-model")
            self.assertEqual([row["method"] for row in upstream["requests"]], ["GET"])
            self.assertEqual(self.config(server), saved)

    def test_truncated_probe_reports_reachable_model_and_preserves_known_usage(self):
        responses = responses_response('{"ok":true}', status="incomplete")
        responses["incomplete_details"] = {"reason": "max_output_tokens"}
        for protocol, response, expected_usage in (
                ("openai_chat", chat_response('{"ok":true}', finish="length"), (13, 5)),
                ("openai_responses", responses, (17, 7)),
                ("anthropic", anthropic_response('{"ok":true}', stop="max_tokens"), (23, 9))):
            with self.subTest(protocol=protocol), protocol_server(response) as (endpoint, upstream), \
                    patch.dict(os.environ, {}, clear=True), studio_server() as server:
                saved = self.save(server, self.settings(endpoint, protocol, max_output_tokens=8192))
                status, result = self.post(server, "/api/settings/test", {})
                self.assertEqual(status, 502)
                self.assertEqual(result["code"], "output_limit")
                self.assertTrue(result["connection_ok"])
                self.assertEqual((result["budget"]["calls"], result["budget"]["input_tokens"],
                                  result["budget"]["output_tokens"]), (1, *expected_usage))
                self.assertTrue(result["budget"]["usage_complete"])
                self.assertEqual(len(upstream["requests"]), 1)
                self.assertEqual(self.config(server), saved)

    def test_running_job_retains_entire_accepted_provider_snapshot(self):
        with protocol_server() as (old_endpoint, old_upstream), \
                protocol_server(anthropic_response()) as (new_endpoint, new_upstream), \
                patch.dict(os.environ, {}, clear=True), studio_server() as server:
            self.save(server, self.settings(old_endpoint, max_output_tokens=8192, timeout=177,
                                            temperature=.3, judge_model="accepted-reviewer"))
            gate, entered = threading.Event(), threading.Event()
            original_run_job = server._run_job

            def delayed(*args, **kwargs):
                entered.set()
                if not gate.wait(5):
                    raise RuntimeError("Loopback test gate timed out")
                return original_run_job(*args, **kwargs)

            with patch.object(server, "_run_job", side_effect=delayed), \
                    patch("creativity_lab.web.Engine.run", invoke_real_provider), capture_real_timeouts() as timeouts:
                status, body = request(server, "POST", "/api/run", {**EXAMPLE, "demo": False, "max_calls": 4})
                self.assertEqual(status, 202)
                job_id = json.loads(body)["job_id"]
                self.assertTrue(entered.wait(3))
                try:
                    later_key = "fake-later-endpoint-key"
                    changed = self.save(server, self.settings(new_endpoint, "anthropic", model="later-model",
                                                             api_key=later_key, max_output_tokens=4096,
                                                             timeout=231, temperature=.9, judge_model=""))
                    self.assertNotIn(later_key, json.dumps(changed))
                finally:
                    gate.set()
                job = self.wait_job(server, job_id)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(timeouts, [177, 177])
            self.assertEqual(new_upstream["requests"], [])
            self.assertEqual(len(old_upstream["requests"]), 2)
            for observed in old_upstream["requests"]:
                self.assertEqual(observed["path"], "/v1/chat/completions")
                self.assertEqual(observed["headers"]["authorization"], "Bearer " + KEY)
                self.assertEqual((observed["body"]["max_completion_tokens"], observed["body"]["temperature"]),
                                 (8192, .3))
            self.assertEqual([row["body"]["model"] for row in old_upstream["requests"]],
                             ["saved-model", "accepted-reviewer"])


if __name__ == "__main__":
    unittest.main()
