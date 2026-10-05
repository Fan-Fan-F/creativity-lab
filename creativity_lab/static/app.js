"use strict";
const $ = (id) => document.getElementById(id);
let currentRun = null;
let currentJobId = null;
let selectedIdea = null;
let serverConfig = {};
let running = false;
const clamp = (value) => Math.max(0, Math.min(1, Number(value) || 0));
const hasScore = (idea, key) => typeof idea.scores?.[key] === "number" && Number.isFinite(idea.scores[key]);
const textValue = (value) => Array.isArray(value) ? value.map(textValue).join("\n") : value && typeof value === "object" ? JSON.stringify(value, null, 2) : String(value ?? "尚未提供");
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "本地服务请求失败。");
  return data;
}
function setStatus(message, error = false) {
  $("run-status").textContent = message;
  $("run-status").classList.toggle("error", error);
}
function selectedDemo() { return document.querySelector('input[name="provider"]:checked').value === "demo"; }
function modeChanged() {
  const demo = selectedDemo();
  $("mode-notice").textContent = demo ? "演示使用固定样例与任务适配，展示流程；分数不代表模型创造力。" : "任务与资料将发送至服务器环境变量配置的模型服务。评分是模型判断，想法仍需要实验验证。";
}
async function fillExample() {
  try {
    const example = await api("/api/example");
    $("task").value = example.task;
    $("references").value = example.references.join("\n");
    setStatus("示例已填入。可以直接探索，也可以改成你的问题。");
  } catch (error) { setStatus(error.message, true); }
}
function busy(value) {
  running = value;
  $("run-button").disabled = value;
  $("example").disabled = value;
  $("run-button").firstElementChild.textContent = value ? "探索进行中…" : "开始探索";
  $("loading-state").hidden = !value;
  $("empty-state").hidden = value || !!currentRun;
  $("run-output").hidden = value || !currentRun;
  $("exports").hidden = value || !currentRun;
}
async function run(event) {
  event.preventDefault();
  if (running) return;
  const demo = selectedDemo();
  if (!demo && !serverConfig.live_ready) {
    setStatus("模型配置未就绪。请设置 CREATIVITY_MODEL 和 API 地址；远程服务需要密钥，本机模型可不设密钥。修改环境变量后重启服务。", true);
    return;
  }
  const data = {
    task: $("task").value.trim(), references: $("references").value.split("\n").map((x) => x.trim()).filter(Boolean),
    rounds: Number($("rounds").value), candidates_per_round: Number($("candidates").value),
    max_calls: Number($("max-calls").value), seed: Number($("seed").value), mode: $("search-mode").value, demo,
  };
  busy(true);
  setStatus(demo ? "正在演示探索流程…" : "已开始真实模型探索；请保持页面打开。一次运行可能需要数分钟。");
  try {
    const job = await api("/api/run", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
    let elapsed = 0;
    while (true) {
      const state = await api(`/api/jobs/${encodeURIComponent(job.job_id)}`);
      if (state.status === "failed") throw new Error(state.error || "运行未完成。");
      if (state.status === "completed") {
        currentRun = state.result;
        currentJobId = job.job_id;
        selectedIdea = null;
        render();
        const partial = currentRun.status && currentRun.status !== "complete";
        setStatus(partial ? "本次探索提前结束，已保留候选和调用记录。请检查运行状态并导出部分结果。" : "探索完成。请选择一个想法，检查假设与验证计划，并导出结果。", partial);
        break;
      }
      elapsed += 1;
      $("loading-copy").textContent = demo ? "正在整理演示候选与档案。" : `生成候选并审查假设。已等待约 ${elapsed} 秒，真实模型运行可能需要数分钟。`;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } catch (error) {
    setStatus(error.message || "运行中断。请检查本地服务是否仍在运行。", true);
  } finally { busy(false); }
}
function archivedIds() {
  return new Set((currentRun.archive || []).map((cell) => typeof cell === "string" ? cell : cell.idea_id));
}
function render() {
  const demo = !!currentRun.demo;
  $("result-mode").textContent = demo ? "演示结果" : "真实模型 · 待验证";
  $("result-mode").classList.toggle("live", !demo);
  if (currentRun.status === "provider_error") $("result-mode").textContent += " · 上游失败 / 部分结果";
  else if (currentRun.status === "budget_exhausted") $("result-mode").textContent += " · 预算耗尽 / 部分结果";
  $("result-strategy").textContent = currentRun.config?.mode === "baseline" ? "直接生成基线" : "迭代探索";
  $("result-calls").textContent = `${currentRun.budget?.calls ?? 0} / ${currentRun.budget?.max_calls ?? "—"} 次调用`;
  $("result-tokens").textContent = `${((currentRun.budget?.input_tokens || 0) + (currentRun.budget?.output_tokens || 0)).toLocaleString()} tokens${currentRun.budget?.usage_complete === false ? "（部分或未报告）" : ""}`;
  $("result-notice").textContent = demo ? "以下是固定演示样例与任务适配，帮助理解工作流程。演示评分不能用于证明模型效果或超过人类。" : "以下均为待验证的候选想法。分数来自模型评价与文本代理指标，不能作为原创性或现实有效性的证明。";
  if (currentRun.status === "provider_error") $("result-notice").textContent = "上游模型调用或响应格式出现错误，探索提前结束；保留已产生的候选和消耗预算。未完成评审的候选显示“未评分”，仍可导出检查。" + $("result-notice").textContent;
  else if (currentRun.status === "budget_exhausted") $("result-notice").textContent = "调用预算已耗尽，以下为部分结果；未完成评审的候选显示“未评分”。" + $("result-notice").textContent;
  $("archive-count").textContent = `${archivedIds().size} 个档案位置 · ${(currentRun.ideas || []).length} 个候选`;
  renderScatter();
  renderIdeas();
  renderTrace();
}
function renderScatter() {
  const container = $("plot-points");
  container.replaceChildren();
  const archive = archivedIds();
  for (const idea of currentRun.ideas || []) {
    if (!hasScore(idea, "usefulness")) continue;
    const point = element("button", "plot-point" + (archive.has(idea.id) ? " archived" : ""));
    point.type = "button";
    point.style.left = `${clamp(idea.novelty_proxy) * 100}%`;
    point.style.bottom = `${clamp(idea.scores?.usefulness) * 100}%`;
    point.title = `${idea.title} · 文本差异 ${clamp(idea.novelty_proxy).toFixed(2)} · 有用性 ${clamp(idea.scores?.usefulness).toFixed(2)}`;
    point.setAttribute("aria-label", point.title);
    point.dataset.ideaId = idea.id;
    point.addEventListener("click", () => chooseIdea(idea.id));
    container.append(point);
  }
  if (!container.childElementCount) container.append(element("p", "chart-help", "尚无已评分候选，暂不绘制探索分布。"));
}
function chooseIdea(id) {
  selectedIdea = id;
  document.querySelectorAll(".idea-card, .plot-point").forEach((node) => node.classList.toggle("selected", node.dataset.ideaId === id));
  const card = [...document.querySelectorAll(".idea-card")].find((node) => node.dataset.ideaId === id);
  if (card) { card.querySelector("details").open = true; card.scrollIntoView({behavior: "smooth", block: "nearest"}); }
}
function renderIdeas() {
  const ideas = [...(currentRun.ideas || [])];
  const sort = $("sort-ideas").value;
  if (sort !== "original") ideas.sort((a, b) => (sort === "novelty" ? clamp(b.novelty_proxy) - clamp(a.novelty_proxy) : clamp(b.scores?.[sort]) - clamp(a.scores?.[sort])));
  $("ideas-count").textContent = `候选清单 · ${ideas.length}`;
  const archive = archivedIds();
  const container = $("ideas");
  container.replaceChildren();
  for (const [index, idea] of ideas.entries()) {
    const card = element("article", "idea-card" + (selectedIdea === idea.id ? " selected" : ""));
    card.dataset.ideaId = idea.id;
    const top = element("div", "idea-top");
    top.append(element("span", "idea-number", String(index + 1).padStart(2, "0")), element("span", "idea-status", archive.has(idea.id) ? "入选档案 · 待验证" : hasScore(idea, "quality") ? "候选 · 待验证" : "未评分 · 待验证"));
    card.append(top, element("h4", "", idea.title || "未命名想法"), element("p", "idea-mechanism", textValue(idea.mechanism)), element("p", "idea-description", textValue(idea.description)));
    const scores = element("div", "scores");
    for (const [key, label] of [["usefulness", "有用性"], ["feasibility", "可行性"], ["novelty", "新颖性"], ["quality", "综合质量"]]) {
      const wrapper = element("div");
      const score = clamp(idea.scores?.[key]);
      const labelNode = element("div", "score-label");
      labelNode.append(element("span", "", label), element("b", "", hasScore(idea, key) ? score.toFixed(2) : "未评分"));
      const track = element("div", "score-track");
      const fill = element("div", "score-fill");
      fill.style.width = `${score * 100}%`;
      track.append(fill); wrapper.append(labelNode, track); scores.append(wrapper);
    }
    card.append(scores);
    const details = element("details");
    const summary = element("summary", "", "检查假设、风险与实验");
    const detailBody = element("div", "idea-detail");
    for (const [title, content] of [["关键假设", idea.assumptions], ["风险与弱点", idea.risks], ["如何验证", idea.test], ["评审理由", idea.review?.rationale], ["下一步实验", idea.review?.next_test]]) {
      if (content === undefined || content === null || content === "") continue;
      detailBody.append(element("strong", "", title), element("p", "", textValue(content)));
    }
    details.append(summary, detailBody); card.append(details);
    card.append(element("p", "idea-lineage", `ID ${idea.id || "—"} · ${idea.operator || "生成"}${idea.parents?.length ? ` · 来源 ${idea.parents.join(", ")}` : ""}`));
    container.append(card);
  }
}
function renderTrace() {
  const container = $("trace");
  container.replaceChildren();
  const trace = currentRun.trace || [];
  $("trace-count").textContent = `${trace.length} 条记录`;
  trace.forEach((entry, index) => {
    const row = element("div", "trace-row");
    const body = element("div");
    if (entry && typeof entry === "object") {
      body.append(element("strong", "", entry.event || entry.phase || entry.action || entry.stage || "探索记录"));
      body.append(element("p", "", Object.entries(entry).filter(([key]) => !["event", "phase", "action", "stage"].includes(key)).map(([key, value]) => `${key}: ${textValue(value)}`).join(" · ")));
    } else { body.append(element("p", "", textValue(entry))); }
    row.append(element("span", "trace-index", String(index + 1).padStart(2, "0")), body); container.append(row);
  });
}
function download(text, ext, type) {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const anchor = element("a");
  anchor.href = url;
  anchor.download = `creativity-${String(currentRun.run_id || "run").replace(/[^A-Za-z0-9_-]/g, "_")}.${ext}`;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function downloadAttachment(ext) {
  if (!currentRun) return;
  if (!currentJobId) {
    download(ext === "json" ? JSON.stringify(currentRun, null, 2) : markdown(), ext, ext === "json" ? "application/json" : "text/markdown;charset=utf-8");
    return;
  }
  const anchor = element("a");
  anchor.href = `/api/jobs/${encodeURIComponent(currentJobId)}/export.${ext}`;
  anchor.download = `creativity-${currentJobId}.${ext}`;
  document.body.append(anchor); anchor.click(); anchor.remove();
}
function markdown() {
  const lines = ["# Creativity Lab 探索结果", "", `任务：${currentRun.task}`, "", currentRun.demo ? "> 演示样例，不构成模型创造力证据。" : "> 候选均未经过现实验证。模型评分与文本代理不构成原创性证明。", "", `运行状态：${currentRun.status || "未提供"}`, `策略：${currentRun.config?.mode || "lab"}；调用：${currentRun.budget?.calls || 0} / ${currentRun.budget?.max_calls || "—"}`, ""];
  for (const idea of currentRun.ideas || []) {
    lines.push(`## ${idea.title}`, "", `ID：${idea.id}；变换：${idea.operator || "生成"}；状态：${idea.status || "proposed"}`, "", `机制：${textValue(idea.mechanism)}`, "", textValue(idea.description), "", `文本差异代理：${clamp(idea.novelty_proxy).toFixed(2)}；模型评分：${hasScore(idea, "quality") ? JSON.stringify(idea.scores) : "未评分"}`, "", "### 假设", "", textValue(idea.assumptions), "", "### 风险", "", textValue(idea.risks), "", "### 验证计划", "", textValue(idea.test), "");
  }
  return lines.join("\n");
}
$("example").addEventListener("click", fillExample);
$("run-form").addEventListener("submit", run);
document.querySelectorAll('input[name="provider"]').forEach((input) => input.addEventListener("change", modeChanged));
$("sort-ideas").addEventListener("change", () => currentRun && renderIdeas());
$("export-json").addEventListener("click", () => downloadAttachment("json"));
$("export-md").addEventListener("click", () => downloadAttachment("md"));
api("/api/config").then((config) => {
  serverConfig = config;
  $("model-note").textContent = config.live_ready ? config.model : "需配置模型与 API 环境变量";
}).catch(() => { $("model-note").textContent = "本地服务连接失败"; });
