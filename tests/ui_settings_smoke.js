"use strict";
// Actual shipped handlers; browser affordances/API responses are controlled fixtures.
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const nodes = new Map(), requests = [];
let focusCount = 0, modelFailure = false, testFailure = false, jobFailures = 0, jobReads = 0, jobComplete = false;
function node() {
  return {value: "", textContent: "", checked: false, open: false, children: [], disabled: false,
    firstElementChild: {textContent: ""}, listeners: {}, classList: {toggle() {}}, setAttribute() {},
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    append(...values) { this.children.push(...values); }, replaceChildren(...values) { this.children = values; },
    focus() { focusCount++; }, reportValidity() { return true; },
    showModal() { this.open = true; }, close() { this.open = false; for (const fn of this.listeners.close || []) fn(); }};
}
function get(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); }
function fire(id, event, data = {}) { return Promise.all((get(id).listeners[event] || []).map((fn) => fn(data))); }
get("demo-provider").checked = true; get("demo-provider").value = "demo"; get("live-provider").value = "live";
let config = {base_url: "https://example.test/v1", model: "", judge_model: "", token_param: "auto", protocol: "openai_chat",
  json_mode: "json_object", auth_type: "auto", timeout: 120, max_output_tokens: 4096, temperature: null,
  key_present: false, live_ready: false, session_configured: false, settings_token: "token-1"};
const document = {getElementById: get, activeElement: node(),
  querySelector() { return get(get("live-provider").checked ? "live-provider" : "demo-provider"); },
  querySelectorAll(selector) { return selector.includes('name="provider"') ? [get("demo-provider"), get("live-provider")] : []; },
  createElement: node, body: {append() {}}};
const sandbox = {document, Blob, URL, setTimeout(callback) { queueMicrotask(callback); }, fetch: async (path, options) => {
  if (path.startsWith("/api/jobs/")) {
    jobReads++;
    if (jobFailures-- > 0) throw new TypeError("Temporary network failure");
    return {ok: true, status: 200, json: async () => ({status: jobComplete ? "completed" : "running", result: {run_id: "recovered-run", task: "Existing task", status: "complete", demo: false, config: {}, ideas: [], archive: [], trace: [], budget: {calls: 2, max_calls: 30}}})};
  }
  if (options) {
    const body = JSON.parse(options.body); requests.push({path, headers: options.headers, body});
    if (path.endsWith("/models")) {
      if (modelFailure) return {ok: false, status: 503, json: async () => ({error: "模型列表暂时不可用", code: "upstream_unavailable", retryable: true, http_status: 503})};
      return {ok: true, json: async () => ({models: [{id: "model-a", name: "Model A"}, {id: "model-b", name: "Model B"}, {id: "model-a", name: "Duplicate"}], count: 3, elapsed_ms: 180, protocol: body.protocol})};
    }
    if (path.endsWith("/test")) {
      if (testFailure === "output") return {ok: false, status: 502, json: async () => ({error: "输出预算不足", code: "output_limit", retryable: false, http_status: null, connection_ok: true, elapsed_ms: 420, budget: {calls: 1, input_tokens: 34, output_tokens: 55, usage_complete: true}})};
      if (testFailure) return {ok: false, status: 401, json: async () => ({error: "鉴权失败", code: "authentication_failed", retryable: false, http_status: 401})};
      return {ok: true, json: async () => ({ok: true, message: "连接成功", model: body.model, elapsed_ms: 230, budget: {calls: 1, input_tokens: 10, output_tokens: 3, usage_complete: true}})};
    }
    if (path.endsWith("/reset")) config = {...config, model: "", key_present: false, live_ready: false, session_configured: false, settings_token: "token-3"};
    else config = {...config, ...Object.fromEntries(Object.entries(body).filter(([key]) => key !== "api_key")), key_present: true, live_ready: true, session_configured: true, settings_token: "token-2"};
  }
  return {ok: true, status: 200, json: async () => ({...config})};
}};
vm.createContext(sandbox); vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const exec = (code) => vm.runInContext(code, sandbox);
async function key(value) { get("settings-api-key").value = value; await fire("settings-api-key", "input"); }
async function address(value) { get("settings-base-url").value = value; await fire("settings-base-url", "input"); }
(async () => {
  await new Promise((resolve) => setImmediate(resolve)); await exec("openModelSettings()");
  assert.equal(get("settings-protocol").value, "openai_chat"); assert.equal(get("settings-token-param").value, "auto");
  get("settings-model").value = "manual-model"; await fire("settings-model", "input"); await key("fake-A-key");
  await address("HTTPS://EXAMPLE.TEST/v1/chat/completions///"); assert.equal(get("settings-api-key").value, "fake-A-key");
  await address("https://example.test/v1/models"); assert.equal(get("settings-api-key").value, "fake-A-key");
  await address("https://new.example/v1"); assert.equal(get("settings-api-key").value, ""); await key("fake-B-key");
  await exec("fetchModels()"); assert.equal(requests.at(-1).path, "/api/settings/models");
  assert.equal(requests.at(-1).headers["X-Settings-Token"], "token-1"); assert.equal(get("settings-model").value, "manual-model");
  assert.equal(get("settings-model-options").children.length, 2); assert.match(get("settings-models-status").textContent, /2 个模型/);
  assert.equal(get("settings-model-options").children[0].value, "model-a");
  get("model-dialog").close(); await exec("openModelSettings()"); assert.equal(get("settings-model").value, "manual-model");
  assert.equal(get("settings-base-url").value, "https://new.example/v1"); assert.equal(get("settings-api-key").value, "");
  get("settings-protocol").value = "anthropic"; await fire("settings-protocol", "change");
  assert.equal(get("settings-json-mode").value, "prompt"); assert.equal(get("settings-json-strict").disabled, true);
  assert.equal(get("settings-token-param").value, "auto"); assert.match(get("settings-request-path").textContent, /\/messages$/);
  assert.equal(exec("normalizeDraftAddress('https://api.anthropic.com/')"), "https://api.anthropic.com/v1");
  get("settings-protocol").value = "openai_responses"; await fire("settings-protocol", "change");
  assert.equal(get("settings-json-strict").disabled, false); assert.equal(get("settings-token-legacy").disabled, true);
  assert.match(get("settings-request-path").textContent, /\/responses$/);
  get("settings-temperature").value = "0"; get("settings-timeout").value = "240"; get("settings-max-output").value = "8192";
  await key("fresh-key"); await exec("submitSettings('test')");
  const test = requests.at(-1); assert.equal(test.body.protocol, "openai_responses"); assert.equal(test.body.temperature, 0);
  assert.equal(test.body.timeout, 240); assert.equal(test.body.max_output_tokens, 8192); assert.equal(test.body.auth_type, "auto");
  assert.match(get("settings-status").textContent, /0.23 秒/); assert.match(get("settings-status").textContent, /13 tokens/);
  assert.equal(get("live-provider").checked, false);
  testFailure = true; const before = requests.length; await exec("submitSettings('test')");
  assert.equal(requests.length, before + 1, "paid test POST must not retry");
  assert.match(get("settings-status").textContent, /authentication_failed/); assert.match(get("settings-status").textContent, /HTTP 401/);
  testFailure = "output"; const beforeOutput = requests.length; await exec("submitSettings('test')");
  assert.equal(requests.length, beforeOutput + 1, "output-limit failure must not repeat paid POST");
  assert.match(get("settings-status").textContent, /提高.*最大输出/); assert.match(get("settings-status").textContent, /0.42 秒/);
  assert.match(get("settings-status").textContent, /89 tokens/); assert.doesNotMatch(get("settings-status").textContent, /鉴权|密钥/);
  assert.doesNotMatch(get("settings-status").textContent, /HTTP 502/, "local forwarding status must not be shown as an upstream HTTP failure");
  testFailure = false; modelFailure = true; await exec("fetchModels()"); assert.match(get("settings-models-status").textContent, /upstream_unavailable/);
  assert.match(get("settings-models-status").textContent, /手动重试/); modelFailure = false;
  await exec("submitSettings('apply')"); assert.equal(get("model-dialog").open, false); assert.equal(get("settings-api-key").value, "");
  assert.equal(get("live-provider").checked, true); await exec("openModelSettings()"); await exec("submitSettings('reset')");
  assert.deepEqual(requests.at(-1).body, {}); assert.equal(requests.at(-1).headers["X-Settings-Token"], "token-2");
  get("model-dialog").close(); assert.ok(focusCount > 0);
  jobFailures = 2; jobReads = 0; await exec("jobStatusWithRetry('existing-job')"); assert.equal(jobReads, 3);
  jobFailures = 9; jobReads = 0; exec("pendingJobId='paused-job'; pendingDemo=false;"); await exec("pollPendingRun()");
  assert.equal(jobReads, 3); assert.equal(exec("pendingJobId"), "paused-job"); assert.equal(get("resume-run-wrapper").hidden, false);
  assert.equal(get("run-button").disabled, true); assert.equal(requests.filter((r) => r.path === "/api/run").length, 0);
  jobFailures = 0; jobComplete = true; await fire("resume-run", "click");
  assert.equal(exec("pendingJobId"), null); assert.equal(exec("currentJobId"), "paused-job");
  assert.equal(get("resume-run-wrapper").hidden, true); assert.equal(get("run-button").disabled, false);
  assert.equal(requests.filter((r) => r.path === "/api/run").length, 0, "resume button must query the existing job, never POST a new run");
  console.log("0.3 settings QA passed: protocol paths/defaults, models/datalist/manual preservation, reopen draft preservation, key/address guard, advanced payload, success/failure latency/budget, structured diagnostics, no paid retries, apply/reset, GET3 retries and actual paused-job resume handler.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
