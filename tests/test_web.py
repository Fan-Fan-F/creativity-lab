"""HTTP integration tests cover loopback boundaries, budgets and sanitized errors."""
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from creativity_lab.web import EXAMPLE, StudioServer, validate_request
from creativity_lab.providers import ProviderError


class WebBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = StudioServer(0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        body = json.dumps(data).encode() if data is not None else None
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        status, response_headers, body = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, response_headers, body

    def wait_for_job(self, job_id):
        for _ in range(100):
            status, _, body = self.request("GET", f"/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            job = json.loads(body)
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.02)
        self.fail("job did not complete")

    def test_static_has_security_headers_and_no_remote_assets(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn(b'lang="zh-CN"', body)
        self.assertNotIn(b'<script src="http', body)

    def test_host_and_cross_site_requests_rejected(self):
        self.assertEqual(self.request("GET", "/api/config", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/run", EXAMPLE, {"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/run", EXAMPLE, {"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_config_returns_editable_endpoint_but_never_secret(self):
        sentinel = "sk-test-secret-do-not-leak"
        with patch.dict(os.environ, {"CREATIVITY_API_KEY": sentinel, "CREATIVITY_MODEL": "test-model", "CREATIVITY_BASE_URL": "https://private.example/v1"}):
            status, _, body = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["key_present"])
        self.assertEqual(data["model"], "test-model")
        self.assertNotIn(sentinel.encode(), body)
        self.assertEqual(data["base_url"], "https://private.example/v1")
        self.assertNotIn("api_key", data)

    def test_request_limits_and_narrow_schema(self):
        for data in ({**EXAMPLE, "max_calls": 201}, {**EXAMPLE, "rounds": True}, {**EXAMPLE, "base_url": "http://attacker"}, {**EXAMPLE, "references": [""]}, {**EXAMPLE, "mode": "unknown"}):
            self.assertEqual(self.request("POST", "/api/run", data)[0], 400)
        self.assertEqual(self.request("POST", "/api/run", EXAMPLE, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/run", EXAMPLE, {"Content-Length": "100001"})[0], 413)

    def test_traversal_and_unknown_endpoints(self):
        for path in ("/../web.py", "/%2e%2e/web.py", "/api/jobs/no-such-job", "/api/not-a-route"):
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_async_job_and_one_active_limit(self):
        gate = threading.Event()
        outcome = {"run_id": "mock-1", "demo": True, "ideas": [], "archive": [], "trace": []}

        def fake_run(engine, config):
            gate.wait(2)
            return outcome

        with patch("creativity_lab.web.Engine.run", fake_run):
            status, _, body = self.request("POST", "/api/run", EXAMPLE)
            self.assertEqual(status, 202)
            job_id = json.loads(body)["job_id"]
            try:
                self.assertEqual(self.request("POST", "/api/run", EXAMPLE)[0], 409)
                self.assertEqual(self.request("GET", f"/api/jobs/{job_id}/export.json")[0], 409)
            finally:
                gate.set()
            job = self.wait_for_job(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"], outcome)

    def test_upstream_errors_sanitized(self):
        with patch("creativity_lab.web.Engine.run", side_effect=RuntimeError("Authorization sk-test-secret-do-not-leak prompt=private")):
            status, _, body = self.request("POST", "/api/run", EXAMPLE)
            self.assertEqual(status, 202)
            job = self.wait_for_job(json.loads(body)["job_id"])
        self.assertEqual(job["status"], "failed")
        encoded = json.dumps(job)
        self.assertNotIn("sk-test-secret", encoded)
        self.assertNotIn("prompt=private", encoded)

    def test_live_requires_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.request("POST", "/api/run", {**EXAMPLE, "demo": False})[0], 400)

    def test_keyless_loopback_is_ready_and_accepted(self):
        with patch.dict(os.environ, {"CREATIVITY_MODEL": "local-model", "CREATIVITY_BASE_URL": "http://127.0.0.1:12345/v1"}, clear=True):
            status, _, body = self.request("GET", "/api/config")
            config = json.loads(body)
            self.assertEqual(status, 200)
            self.assertTrue(config["live_ready"])
            self.assertFalse(config["key_present"])
            with patch("creativity_lab.web.Engine.run", return_value={"run_id": "local-test", "demo": False}):
                status, _, body = self.request("POST", "/api/run", {**EXAMPLE, "demo": False})
                self.assertEqual(status, 202)
                job = self.wait_for_job(json.loads(body)["job_id"])
            self.assertEqual(job["status"], "completed")

    def test_remote_keyless_configuration_is_not_ready(self):
        with patch.dict(os.environ, {"CREATIVITY_MODEL": "remote-model", "CREATIVITY_BASE_URL": "https://provider.example/v1"}, clear=True):
            status, _, body = self.request("GET", "/api/config")
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)["live_ready"])
            self.assertEqual(self.request("POST", "/api/run", {**EXAMPLE, "demo": False})[0], 400)

    def test_partial_artifact_preserves_candidates_and_budget_without_error_text(self):
        error = ProviderError("Authorization sk-test-secret-do-not-leak")
        error.partial_result = {"status": "provider_error", "budget": {"calls": 2}, "ideas": [{"id": "idea-001", "status": "proposed", "scores": {}}]}
        with patch("creativity_lab.web.Engine.run", side_effect=error):
            status, _, body = self.request("POST", "/api/run", EXAMPLE)
            self.assertEqual(status, 202)
            job = self.wait_for_job(json.loads(body)["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["status"], "provider_error")
        self.assertEqual(job["result"]["budget"]["calls"], 2)
        self.assertEqual(job["result"]["ideas"][0]["scores"], {})
        self.assertNotIn("sk-test-secret", json.dumps(job))

    def test_attachment_exports_exact_partial_json_and_uncertainty_markdown(self):
        result = {"run_id": "partial-001", "demo": True, "task": "用小实验验证降温方案", "status": "provider_error",
                  "config": {"mode": "lab"}, "budget": {"calls": 2, "max_calls": 30},
                  "ideas": [{"id": "idea-001", "title": "未评分候选", "mechanism": "transfer", "description": "待验证假设",
                             "status": "proposed", "scores": {}, "novelty_proxy": 0.2, "assumptions": ["尚不确定"], "risks": ["可能失败"], "test": "设置对照组"}],
                  "archive": [], "trace": []}
        with patch("creativity_lab.web.Engine.run", return_value=result):
            status, _, body = self.request("POST", "/api/run", EXAMPLE)
            self.assertEqual(status, 202)
            job_id = json.loads(body)["job_id"]
            self.wait_for_job(job_id)
        status, headers, body = self.request("GET", f"/api/jobs/{job_id}/export.json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), result)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(headers["Content-Disposition"], f'attachment; filename="creativity-{job_id}.json"')
        self.assertEqual(int(headers["Content-Length"]), len(body))
        status, headers, body = self.request("GET", f"/api/jobs/{job_id}/export.md")
        markdown = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Disposition"], f'attachment; filename="creativity-{job_id}.md"')
        self.assertIn("演示样例，不构成模型创造力证据", markdown)
        self.assertIn("运行状态：provider_error", markdown)
        self.assertIn("状态：proposed", markdown)
        self.assertIn("模型评分：未评分", markdown)
        self.assertIn("设置对照组", markdown)
        self.assertEqual(self.request("GET", f"/api/jobs/{job_id}/export.json", headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("GET", f"/api/jobs/{job_id}/export.exe")[0], 404)

    def test_demo_schema_defaults_are_valid(self):
        config, demo = validate_request(EXAMPLE)
        self.assertTrue(demo)
        self.assertEqual(config.max_calls, 30)
        self.assertEqual(config.mode, "lab")
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


class ExportRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is optional for frontend runtime checks")
    def test_real_export_handlers_preserve_partial_status_and_unreviewed_scores(self):
        """Execute shipped handlers and inspect real Blob bytes, not expected text fixtures."""
        script = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const nodes = new Map(), exports = [], blobs = new Map();
function makeNode() {
  return {listeners:{},value:"",textContent:"",children:[],dataset:{},style:{},
    classList:{toggle(){}},addEventListener(name,fn){this.listeners[name]=fn;},
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    setAttribute(){},remove(){},get childElementCount(){return this.children.length;},
    click(){exports.push({filename:this.download,href:this.href,blob:blobs.get(this.href)});}};
}
const document = {getElementById(id){if(!nodes.has(id))nodes.set(id,makeNode());return nodes.get(id);},
  createElement:makeNode,querySelectorAll(){return [];},body:makeNode()};
const fixture = {run_id:"partial/export",task:"任务：保留部分结果与引号 \"<script>\"",demo:false,status:"provider_error",
  config:{mode:"lab"},budget:{calls:2,max_calls:30,usage_complete:false},archive:[],trace:[],
  ideas:[{id:"idea-001",title:"未评分候选",status:"proposed",mechanism:"transfer",description:"这是待检验的假设",assumptions:["尚不确定"],risks:["可能失败"],test:"用对照实验验证",scores:{},novelty_proxy:0.21}]};
const sandbox = {document,Blob,fixture,setTimeout(){},fetch:async()=>({ok:true,json:async()=>({live_ready:true,model:"local"})}),
  URL:{createObjectURL(blob){const url=`blob:test/${blobs.size}`;blobs.set(url,blob);return url;},revokeObjectURL(){}}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),sandbox);
vm.runInContext("currentRun=fixture; render();",sandbox);
nodes.get("export-json").listeners.click();
nodes.get("export-md").listeners.click();
(async()=>{
  assert.equal(exports.length,2);
  const json=await exports[0].blob.text(), markdown=await exports[1].blob.text();
  assert.deepEqual(JSON.parse(json),fixture);
  assert.equal(exports[0].filename,"creativity-partial_export.json");
  assert.equal(exports[0].blob.type,"application/json");
  assert.match(markdown,/运行状态：provider_error/);
  assert.match(markdown,/状态：proposed/);
  assert.match(markdown,/模型评分：未评分/);
  assert.match(markdown,/待验证|未经过现实验证/);
  assert.equal(nodes.get("plot-points").children.length,1);
  assert.equal(nodes.get("plot-points").children[0].textContent,"尚无已评分候选，暂不绘制探索分布。");
  assert.match(nodes.get("result-mode").textContent,/上游失败/);
  assert.match(nodes.get("result-notice").textContent,/未评分/);
  exports.length=0;
  vm.runInContext("currentJobId='test-job-123';",sandbox);
  nodes.get("export-json").listeners.click();
  nodes.get("export-md").listeners.click();
  assert.equal(exports[0].href,"/api/jobs/test-job-123/export.json");
  assert.equal(exports[1].href,"/api/jobs/test-job-123/export.md");
  assert.equal(exports[0].blob,undefined);
  assert.equal(exports[0].filename,"creativity-test-job-123.json");
  console.log(JSON.stringify({json_bytes:Buffer.byteLength(json),markdown_bytes:Buffer.byteLength(markdown),unreviewed_preserved:true}));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        app = Path(__file__).resolve().parents[1] / "creativity_lab" / "static" / "app.js"
        result = subprocess.run([shutil.which("node"), "-e", script, str(app)], capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout)
        self.assertGreater(evidence["json_bytes"], 100)
        self.assertGreater(evidence["markdown_bytes"], 100)
        self.assertTrue(evidence["unreviewed_preserved"])


if __name__ == "__main__":
    unittest.main()
