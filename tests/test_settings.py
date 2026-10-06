"""Settings lifecycle uses real local HTTP upstreams and fake credentials only."""
from contextlib import contextmanager
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from creativity_lab.providers import CallBudget, ChatProvider
from creativity_lab.web import EXAMPLE, StudioServer, serve_main, _existing_installation


@contextmanager
def upstream_stub():
    state = {"requests": [], "status": 200, "content": {"ok": True},
             "usage": {"prompt_tokens": 17, "completion_tokens": 5}, "error": "upstream private body"}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append({"path": self.path, "headers": dict(self.headers), "body": json.loads(raw)})
            body = ({"choices": [{"message": {"content": json.dumps(state["content"])}}], "usage": state["usage"]}
                    if state["status"] == 200 else {"error": state["error"]})
            encoded = json.dumps(body).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@contextmanager
def studio_server():
    server = StudioServer(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(server, method, path, data=None, token=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    request_headers = {"Content-Type": "application/json"}
    if token is not None:
        request_headers["X-Settings-Token"] = token
    request_headers.update(headers or {})
    connection.request(method, path, body=json.dumps(data).encode() if data is not None else None, headers=request_headers)
    response = connection.getresponse()
    status, body = response.status, response.read()
    connection.close()
    return status, body


def environment(endpoint):
    return {"CREATIVITY_MODEL": "environment-model", "CREATIVITY_BASE_URL": endpoint + "/v1",
            "CREATIVITY_API_KEY": "fake-environment-secret", "CREATIVITY_JUDGE_MODEL": "",
            "CREATIVITY_TOKEN_PARAM": "max_completion_tokens"}


def invoke_real_provider(engine, config):
    """Skip idea schema overhead; exercise the shipped adapter and live headers."""
    budget = CallBudget(config.max_calls)
    engine.provider.complete({"purpose": "test", "task": config.task}, budget)
    if engine.judge.identity != engine.provider.identity:
        engine.judge.complete({"purpose": "test", "task": config.task}, budget)
    return {"schema_version": 1, "status": "complete", "task": config.task, "demo": False,
            "config": asdict(config), "budget": budget.as_dict(), "ideas": [], "archive": [], "trace": [],
            "provenance": {"generator": engine.provider.identity, "judge": engine.judge.identity}}


class SettingsIntegrationTests(unittest.TestCase):
    def config(self, server):
        status, body = request(server, "GET", "/api/config")
        self.assertEqual(status, 200)
        config = json.loads(body)
        self.assertIsInstance(config["settings_token"], str)
        self.assertGreaterEqual(len(config["settings_token"]), 20)
        return config

    def save(self, server, settings):
        token = self.config(server)["settings_token"]
        status, body = request(server, "POST", "/api/settings", settings, token)
        self.assertEqual(status, 200, body.decode())
        return json.loads(body)

    def wait_for_job(self, server, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, body = request(server, "GET", f"/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            job = json.loads(body)
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(.01)
        self.fail("settings test job timed out")

    def start_live(self, server):
        status, body = request(server, "POST", "/api/run", {**EXAMPLE, "demo": False, "rounds": 1, "max_calls": 4})
        self.assertEqual(status, 202, body.decode())
        return json.loads(body)["job_id"]

    def test_save_has_no_upstream_call_then_live_uses_saved_headers_and_parameter(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            public = self.save(server, {"base_url": endpoint + "/v1", "model": "saved-model", "api_key": "fake-saved-secret", "judge_model": "", "token_param": "max_tokens"})
            self.assertEqual(public["model"], "saved-model")
            self.assertEqual(public["base_url"], endpoint + "/v1")
            self.assertEqual(public["token_param"], "max_tokens")
            self.assertTrue(public["key_present"])
            self.assertTrue(public["live_ready"])
            self.assertEqual(upstream["requests"], [])
            self.assertNotIn("fake-saved-secret", json.dumps(public))
            with patch("creativity_lab.web.Engine.run", invoke_real_provider):
                job_id = self.start_live(server)
                job = self.wait_for_job(server, job_id)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["budget"]["calls"], 1)
            self.assertEqual(len(upstream["requests"]), 1)
            observed = upstream["requests"][0]
            self.assertEqual(observed["path"], "/v1/chat/completions")
            self.assertEqual(observed["headers"]["Authorization"], "Bearer fake-saved-secret")
            self.assertEqual(observed["body"]["model"], "saved-model")
            self.assertEqual(observed["body"]["max_tokens"], 4096)
            self.assertNotIn("max_completion_tokens", observed["body"])
            for path in ("/api/config", f"/api/jobs/{job_id}", f"/api/jobs/{job_id}/export.json", f"/api/jobs/{job_id}/export.md"):
                status, body = request(server, "GET", path)
                self.assertEqual(status, 200)
                self.assertNotIn(b"fake-saved-secret", body)
                self.assertNotIn(b"fake-environment-secret", body)

    def test_settings_mutations_require_correct_server_token(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            initial = self.config(server)
            for path, data in (("/api/settings", {"model": "new-model"}), ("/api/settings/test", {}), ("/api/settings/reset", {}), ("/api/settings/models", {})):
                for token in (None, "wrong-token"):
                    status, body = request(server, "POST", path, data, token)
                    self.assertEqual(status, 403, body.decode())
            self.assertEqual(self.config(server), initial)
            self.assertEqual(upstream["requests"], [])

    def test_probe_uses_configured_limit_one_real_request_and_does_not_save(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            saved = self.save(server, {"model": "saved-model", "api_key": "fake-saved-secret", "token_param": "max_tokens"})
            token = saved["settings_token"]
            status, body = request(server, "POST", "/api/settings/test", {"model": "probe-model", "api_key": "fake-probe-secret", "token_param": "max_completion_tokens", "max_output_tokens": 8192, "timeout": 180}, token)
            self.assertEqual(status, 200, body.decode())
            result = json.loads(body)
            self.assertEqual(result["budget"]["calls"], 1)
            self.assertEqual(result["budget"]["input_tokens"], 17)
            self.assertEqual(result["budget"]["output_tokens"], 5)
            self.assertTrue(result["budget"]["usage_complete"])
            self.assertEqual(len(upstream["requests"]), 1)
            observed = upstream["requests"][0]
            self.assertEqual(observed["body"]["model"], "probe-model")
            self.assertEqual(observed["headers"]["Authorization"], "Bearer fake-probe-secret")
            self.assertEqual(observed["body"]["max_completion_tokens"], 8192)
            self.assertIsInstance(result["elapsed_ms"], int)
            self.assertNotIn("max_tokens", observed["body"])
            self.assertNotIn("fake-probe-secret", body.decode())
            self.assertEqual(self.config(server), saved)
            status, body = request(server, "POST", "/api/settings/test", {}, token)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["budget"]["calls"], 1)
            self.assertEqual(len(upstream["requests"]), 2)
            self.assertEqual(upstream["requests"][-1]["body"]["model"], "saved-model")
            self.assertEqual(upstream["requests"][-1]["headers"]["Authorization"], "Bearer fake-saved-secret")

    def test_failed_probe_counts_once_sanitizes_body_and_preserves_saved_settings(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            saved = self.save(server, {"model": "saved-model", "api_key": "fake-saved-secret"})
            upstream["status"] = 401
            upstream["error"] = "fake-upstream-body-secret Authorization fake-probe-secret"
            status, body = request(server, "POST", "/api/settings/test", {"model": "probe-model", "api_key": "fake-probe-secret"}, saved["settings_token"])
            self.assertGreaterEqual(status, 400)
            response = json.loads(body)
            self.assertEqual(response["budget"]["calls"], 1)
            self.assertFalse(response["budget"]["usage_complete"])
            self.assertEqual(len(upstream["requests"]), 1)
            for secret in (b"fake-upstream-body-secret", b"fake-probe-secret", b"fake-saved-secret"):
                self.assertNotIn(secret, body)
            self.assertEqual(self.config(server), saved)

    def test_empty_key_reuses_same_endpoint_saved_key(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            self.save(server, {"base_url": endpoint + "/v1", "model": "first-model", "api_key": "fake-retained-secret"})
            updated = self.save(server, {"base_url": endpoint + "/v1/", "model": "second-model", "api_key": ""})
            self.assertTrue(updated["key_present"])
            with patch("creativity_lab.web.Engine.run", invoke_real_provider):
                job = self.wait_for_job(server, self.start_live(server))
            self.assertEqual(job["status"], "completed")
            self.assertEqual(upstream["requests"][0]["headers"]["Authorization"], "Bearer fake-retained-secret")
            self.assertEqual(upstream["requests"][0]["body"]["model"], "second-model")

    def test_changed_remote_address_requires_explicit_key_and_loopback_does_not_inherit(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            saved = self.save(server, {"base_url": "https://first.example/v1", "model": "remote-model", "api_key": "fake-first-secret"})
            status, body = request(server, "POST", "/api/settings", {"base_url": "https://second.example/v1", "api_key": ""}, saved["settings_token"])
            self.assertEqual(status, 400, body.decode())
            self.assertEqual(self.config(server), saved)
            public = self.save(server, {"base_url": endpoint + "/different", "model": "keyless-local", "api_key": ""})
            self.assertFalse(public["key_present"])
            self.assertTrue(public["live_ready"])
            with patch("creativity_lab.web.Engine.run", invoke_real_provider):
                self.assertEqual(self.wait_for_job(server, self.start_live(server))["status"], "completed")
            self.assertEqual(len(upstream["requests"]), 1)
            self.assertNotIn("Authorization", upstream["requests"][0]["headers"])

    def test_invalid_fields_addresses_and_types_do_not_mutate_or_call_upstream(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            before = self.config(server)
            invalid = [{"base_url": "http://remote.example/v1"}, {"base_url": "http://127.0.0.1.evil.example/v1"},
                       {"base_url": "https://user:fake-secret@example.com/v1"}, {"base_url": "https://@example.com/v1"},
                       {"base_url": "https://example.com/v1?api_key=fake-secret"}, {"base_url": "https://example.com/v1#fake-secret"},
                       {"base_url": 3}, {"model": []}, {"model": ""}, {"api_key": True}, {"judge_model": None},
                       {"token_param": "unknown"}, {"unknown_field": "fake-secret"}, [],
                       {"protocol": "unknown"}, {"json_mode": "schema"}, {"auth_type": "unknown"},
                       {"timeout": True}, {"timeout": 301}, {"timeout": 0}, {"max_output_tokens": 255},
                       {"max_output_tokens": 32769}, {"temperature": True}, {"temperature": float("nan")},
                       {"temperature": 2.1}, {"temperature": 10 ** 400}, {"protocol": "anthropic", "json_mode": "json_object"}]
            for settings in invalid:
                for path in ("/api/settings", "/api/settings/test"):
                    with self.subTest(settings=settings, path=path):
                        status, body = request(server, "POST", path, settings, before["settings_token"])
                        self.assertEqual(status, 400, body.decode())
                        self.assertNotIn(b"fake-secret", body)
            self.assertEqual(self.config(server), before)
            self.assertEqual(upstream["requests"], [])

    def test_reset_removes_override_restores_environment_and_requires_empty_body(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            initial = self.config(server)
            saved = self.save(server, {"model": "saved-model", "api_key": "fake-saved-secret", "token_param": "max_tokens"})
            self.assertEqual(request(server, "POST", "/api/settings/reset", {"model": "no"}, saved["settings_token"])[0], 400)
            self.assertEqual(self.config(server), saved)
            status, body = request(server, "POST", "/api/settings/reset", {}, saved["settings_token"])
            self.assertEqual(status, 200)
            self.assertEqual(self.config(server), initial)
            self.assertEqual(upstream["requests"], [])
            with patch("creativity_lab.web.Engine.run", invoke_real_provider):
                self.assertEqual(self.wait_for_job(server, self.start_live(server))["status"], "completed")
            observed = upstream["requests"][0]
            self.assertEqual(observed["body"]["model"], "environment-model")
            self.assertEqual(observed["headers"]["Authorization"], "Bearer fake-environment-secret")
            self.assertIn("max_completion_tokens", observed["body"])

    def test_settings_isolated_per_server_and_tokens_not_interchangeable(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as first, studio_server() as second:
            initial_second = self.config(second)
            saved = self.save(first, {"model": "first-only", "api_key": "fake-first-only-secret"})
            self.assertEqual(self.config(second), initial_second)
            self.assertNotEqual(saved["settings_token"], initial_second["settings_token"])
            self.assertEqual(request(second, "POST", "/api/settings", {"model": "bad"}, saved["settings_token"])[0], 403)
            with patch("creativity_lab.web.Engine.run", invoke_real_provider):
                self.assertEqual(self.wait_for_job(first, self.start_live(first))["status"], "completed")
                self.assertEqual(self.wait_for_job(second, self.start_live(second))["status"], "completed")
            self.assertEqual([row["body"]["model"] for row in upstream["requests"]], ["first-only", "environment-model"])
            self.assertEqual([row["headers"]["Authorization"] for row in upstream["requests"]], ["Bearer fake-first-only-secret", "Bearer fake-environment-secret"])

    def test_job_snapshots_settings_before_background_execution(self):
        with upstream_stub() as (old_endpoint, old_upstream), upstream_stub() as (new_endpoint, new_upstream), patch.dict(os.environ, environment(old_endpoint), clear=True), studio_server() as server:
            self.save(server, {"model": "accepted-generator", "judge_model": "accepted-judge", "api_key": "fake-accepted-secret", "token_param": "max_tokens"})
            gate, entered = threading.Event(), threading.Event()
            original_run_job = server._run_job
            def delayed_job(*args, **kwargs):
                entered.set()
                if not gate.wait(5):
                    raise RuntimeError("test gate timed out")
                return original_run_job(*args, **kwargs)
            with patch.object(server, "_run_job", side_effect=delayed_job), patch("creativity_lab.web.Engine.run", invoke_real_provider):
                job_id = self.start_live(server)
                self.assertTrue(entered.wait(3))
                try:
                    self.save(server, {"base_url": new_endpoint + "/v1", "model": "later-generator", "judge_model": "later-judge", "api_key": "fake-later-secret", "token_param": "max_completion_tokens"})
                finally:
                    gate.set()
                job = self.wait_for_job(server, job_id)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(new_upstream["requests"], [])
            self.assertEqual([row["body"]["model"] for row in old_upstream["requests"]], ["accepted-generator", "accepted-judge"])
            self.assertTrue(all(row["headers"]["Authorization"] == "Bearer fake-accepted-secret" for row in old_upstream["requests"]))
            self.assertTrue(all("max_tokens" in row["body"] and "max_completion_tokens" not in row["body"] for row in old_upstream["requests"]))

    def test_same_origin_boundary_applies_to_settings_even_with_token(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            before = self.config(server)
            for headers in ({"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Host": "attacker.example"}):
                status, _ = request(server, "POST", "/api/settings", {"model": "changed"}, before["settings_token"], headers)
                self.assertEqual(status, 403)
            self.assertEqual(self.config(server), before)
            self.assertEqual(upstream["requests"], [])

    def test_deep_raw_json_is_controlled_error_without_settings_change(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True), studio_server() as server:
            before = self.config(server)
            raw = b"[" * 10000 + b'{"api_key":"fake-nested-secret"}' + b"]" * 10000
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            connection.request("POST", "/api/settings", body=raw,
                               headers={"Content-Type": "application/json", "X-Settings-Token": before["settings_token"]})
            response = connection.getresponse()
            status, body = response.status, response.read()
            connection.close()
            self.assertEqual(status, 400)
            self.assertNotIn(b"fake-nested-secret", body)
            self.assertNotIn(b"fake-environment-secret", body)
            self.assertEqual(self.config(server), before)
            self.assertEqual(upstream["requests"], [])


class ProviderSettingsSnapshotTests(unittest.TestCase):
    def test_token_parameter_is_constructor_snapshot_and_explicit_keyless_stays_keyless(self):
        with upstream_stub() as (endpoint, upstream), patch.dict(os.environ, environment(endpoint), clear=True):
            os.environ["CREATIVITY_TOKEN_PARAM"] = "max_tokens"
            provider = ChatProvider(model="snapshot-model", base_url=endpoint + "/v1", api_key="")
            os.environ["CREATIVITY_TOKEN_PARAM"] = "max_completion_tokens"
            provider.complete({"purpose": "test"}, CallBudget(1))
            observed = upstream["requests"][0]
            self.assertIn("max_tokens", observed["body"])
            self.assertNotIn("max_completion_tokens", observed["body"])
            self.assertNotIn("Authorization", observed["headers"])


class PortFallbackTests(unittest.TestCase):
    def test_double_click_reuses_same_installation_and_preserves_settings_and_jobs(self):
        with studio_server() as server:
            server.jobs["existing-job"] = {"status": "completed", "result": "preserved"}
            with patch("creativity_lab.web.webbrowser.open") as browser, patch("builtins.print"):
                serve_main(port=server.server_address[1], open_browser=True)
            browser.assert_called_once_with(f"http://127.0.0.1:{server.server_address[1]}")
            self.assertEqual(server.jobs["existing-job"]["result"], "preserved")
            self.assertGreater(server.socket.fileno(), -1)

    def test_other_installation_or_version_is_never_reused(self):
        with studio_server() as server:
            original = server.public_config()
            for override in ({"installation_id": "another-installation"}, {"version": "older-version"}, {"app": "unrelated-app"}):
                with patch.object(server, "public_config", return_value={**original, **override}):
                    self.assertFalse(_existing_installation(server.server_address[1]))

    def test_occupied_port_falls_back_opens_new_url_and_preserves_old_app(self):
        occupied = StudioServer(0)
        old_port = occupied.server_address[1]
        old_jobs = {"existing-job": {"status": "completed", "result": "preserved"}}
        occupied.jobs.update(old_jobs)
        attempted = []
        new_server = SimpleNamespace(server_address=("127.0.0.1", old_port + 1),
                                     serve_forever=Mock(), server_close=Mock())
        real_constructor = StudioServer
        def constructor(port):
            attempted.append(port)
            if port == old_port:
                # Verify exclusive binding using the real occupied socket.
                # Fail fast rather than entering serve_forever if reuse regresses.
                unexpected = real_constructor(port)
                unexpected.server_close()
                raise AssertionError("StudioServer unexpectedly rebound the old app's port")
            new_server.server_address = ("127.0.0.1", port)
            return new_server
        class ImmediateTimer:
            def __init__(self, delay, callback):
                self.callback = callback
            def start(self):
                self.callback()
        try:
            with patch("creativity_lab.web.StudioServer", side_effect=constructor), \
                 patch("creativity_lab.web.threading.Timer", ImmediateTimer), \
                 patch("creativity_lab.web.webbrowser.open") as browser, patch("builtins.print"):
                serve_main(port=old_port, open_browser=True)
            self.assertEqual(attempted[:2], [old_port, old_port + 1])
            new_server.serve_forever.assert_called_once_with()
            new_server.server_close.assert_called_once_with()
            browser.assert_called_once_with(f"http://127.0.0.1:{old_port+1}")
            self.assertNotEqual(occupied.socket.fileno(), -1)
            self.assertEqual(occupied.socket.getsockname()[1], old_port)
            self.assertEqual(occupied.jobs, old_jobs)
        finally:
            occupied.server_close()

    def test_port_zero_is_forwarded_to_os_once(self):
        attempted, created = [], []
        real_constructor = StudioServer
        def constructor(port):
            attempted.append(port)
            server = real_constructor(port)
            server.serve_forever = Mock()
            created.append(server)
            return server
        with patch("creativity_lab.web.StudioServer", side_effect=constructor), patch("builtins.print"):
            serve_main(port=0, open_browser=False)
        self.assertEqual(attempted, [0])
        self.assertGreater(created[0].server_address[1], 0)
        created[0].serve_forever.assert_called_once_with()
        self.assertEqual(created[0].socket.fileno(), -1)


if __name__ == "__main__":
    unittest.main()
