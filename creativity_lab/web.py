"""Loopback-only, dependency-free web studio. Never accepts credentials in requests."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .engine import Engine, RunConfig
from .providers import ChatProvider, DemoProvider

STATIC_DIR = Path(__file__).parent / "static"
EXAMPLE = {
    "task": "为夏季城市里的户外工作者设计一种低成本降温方案。预算每人 100 元以内，无需电池，便于携带；请提出能用小实验验证的方案。",
    "references": ["蒸发能带走热量，但效果受空气湿度影响。", "户外工作者需要移动，方案不能依赖固定空调。"],
    "rounds": 2,
    "candidates_per_round": 4,
    "seed": 42,
    "max_calls": 30,
    "mode": "lab",
    "demo": True,
}


def live_ready() -> bool:
    """Use the provider's configuration rules, including keyless loopback models."""
    try:
        ChatProvider()
        judge_model = os.getenv("CREATIVITY_JUDGE_MODEL", "").strip()
        if judge_model:
            ChatProvider(model=judge_model)
        return True
    except Exception:
        return False


def export_markdown(result: dict) -> str:
    """Preserve run state and uncertainty in the human-readable attachment."""
    def text(value):
        if isinstance(value, list):
            return "\n".join(text(item) for item in value)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, indent=2)
        return str(value) if value is not None else "尚未提供"

    budget = result.get("budget", {})
    lines = ["# Creativity Lab 探索结果", "", "任务：" + text(result.get("task")), "",
             "> 演示样例，不构成模型创造力证据。" if result.get("demo") else "> 候选均未经过现实验证。模型评分与文本代理不构成原创性证明。",
             "", "运行状态：" + text(result.get("status")),
             f"策略：{result.get('config', {}).get('mode', 'lab')}；调用：{budget.get('calls', 0)} / {budget.get('max_calls', '—')}", ""]
    for idea in result.get("ideas", []):
        scores = idea.get("scores", {})
        reviewed = isinstance(scores.get("quality"), (int, float)) and not isinstance(scores.get("quality"), bool)
        lines.extend(["## " + text(idea.get("title")), "",
                      f"ID：{idea.get('id', '—')}；变换：{idea.get('operator', '生成')}；状态：{idea.get('status', 'proposed')}", "",
                      "机制：" + text(idea.get("mechanism")), "", text(idea.get("description")), "",
                      f"文本差异代理：{idea.get('novelty_proxy', '尚未提供')}；模型评分：{text(scores) if reviewed else '未评分'}", "",
                      "### 假设", "", text(idea.get("assumptions")), "", "### 风险", "", text(idea.get("risks")), "",
                      "### 验证计划", "", text(idea.get("test")), ""])
    return "\n".join(lines)


def validate_request(data: object) -> tuple[RunConfig, bool]:
    """A narrow input schema prevents arbitrary network destinations or credentials."""
    if not isinstance(data, dict):
        raise ValueError("请求必须是 JSON 对象。")
    allowed = {"task", "rounds", "candidates_per_round", "seed", "max_calls", "references", "mode", "demo"}
    if set(data) - allowed:
        raise ValueError("请求含不支持的字段。API 配置请通过服务器环境变量设置。")
    task = data.get("task", "")
    if not isinstance(task, str) or not 8 <= len(task.strip()) <= 12000:
        raise ValueError("请用 8 至 12000 个字符描述目标和限制。")
    values = {}
    for name, default, low, high in (
        ("rounds", 2, 1, 8), ("candidates_per_round", 4, 2, 12),
        ("seed", 42, 0, 2147483647), ("max_calls", 30, 1, 200),
    ):
        value = data.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{name} 必须是 {low} 至 {high} 的整数。")
        values[name] = value
    references = data.get("references", [])
    if not isinstance(references, list) or len(references) > 20 or any(
        not isinstance(ref, str) or not 1 <= len(ref.strip()) <= 3000 for ref in references
    ):
        raise ValueError("资料最多 20 条，每条 1 至 3000 个字符。")
    mode = data.get("mode", "lab")
    if mode not in ("lab", "baseline"):
        raise ValueError("mode 必须是 lab 或 baseline。")
    demo = data.get("demo", True)
    if not isinstance(demo, bool):
        raise ValueError("demo 必须是布尔值。")
    return RunConfig(task=task.strip(), references=[r.strip() for r in references], mode=mode, **values), demo


class StudioServer(ThreadingHTTPServer):
    """Jobs are ephemeral; prompts and credentials never enter access logs."""
    daemon_threads = True

    def __init__(self, port: int = 8765):
        super().__init__(("127.0.0.1", port), StudioHandler)
        self.jobs: dict[str, dict] = {}
        self.job_lock = threading.Lock()

    def start_job(self, config: RunConfig, demo: bool) -> str | None:
        with self.job_lock:
            if any(j["status"] in ("queued", "running") for j in self.jobs.values()):
                return None
            while len(self.jobs) >= 24:
                self.jobs.pop(next(iter(self.jobs)))
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"job_id": job_id, "status": "queued", "created_at": time.time()}
        threading.Thread(target=self._run_job, args=(job_id, config, demo), daemon=True).start()
        return job_id

    def _run_job(self, job_id: str, config: RunConfig, demo: bool) -> None:
        with self.job_lock:
            self.jobs[job_id]["status"] = "running"
        try:
            provider = DemoProvider() if demo else ChatProvider()
            judge_model = os.getenv("CREATIVITY_JUDGE_MODEL", "").strip()
            judge = ChatProvider(model=judge_model) if not demo and judge_model else None
            result = Engine(provider, judge=judge).run(config)
            # Serialize here so a provider result cannot leave an endlessly running job.
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            outcome = {"status": "completed", "result": result, "finished_at": time.time()}
        except Exception as exc:
            # Upstream exceptions may contain request bodies, authorization headers or keys.
            partial = getattr(exc, "partial_result", None)
            if isinstance(partial, dict):
                # Engine-generated partial artifacts contain sanitized status and consumed budget.
                try:
                    json.dumps(partial, ensure_ascii=False, allow_nan=False)
                    outcome = {"status": "completed", "result": partial, "finished_at": time.time()}
                except (ValueError, TypeError):
                    partial = None
            if not isinstance(partial, dict):
                outcome = {
                    "status": "failed", "finished_at": time.time(),
                    "error": "运行未完成。请检查模型名称、API 环境变量、服务可用性和调用预算；本地页面不会显示上游响应或密钥。",
                }
        with self.job_lock:
            self.jobs[job_id].update(outcome)


class StudioHandler(BaseHTTPRequestHandler):
    server: StudioServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _local_request(self) -> bool:
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "").lower() not in hosts:
            self._json(403, {"error": "仅接受本机页面请求。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in hosts}:
            self._json(403, {"error": "请求来源不受允许。"})
            return False
        if self.headers.get("Sec-Fetch-Site", "") == "cross-site":
            self._json(403, {"error": "拒绝跨站请求。"})
            return False
        return True

    def _headers(self, status: int, content_type: str, size: int, attachment: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()

    def _json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._local_request():
            return
        path = urlparse(self.path).path
        if path == "/api/config":
            self._json(200, {
                "model": os.getenv("CREATIVITY_MODEL", "").strip(),
                "judge_model": os.getenv("CREATIVITY_JUDGE_MODEL", "").strip(),
                "key_present": bool(os.getenv("CREATIVITY_API_KEY") or os.getenv("OPENAI_API_KEY")),
                "live_ready": live_ready(),
            })
        elif path == "/api/example":
            self._json(200, EXAMPLE)
        elif path.startswith("/api/jobs/"):
            parts = path.removeprefix("/api/jobs/").split("/")
            job_id = parts[0]
            with self.server.job_lock:
                job = self.server.jobs.get(job_id)
                snapshot = dict(job) if job else None
            if not snapshot:
                self._json(404, {"error": "找不到该运行。"})
            elif len(parts) == 1:
                self._json(200, snapshot)
            elif len(parts) == 2 and parts[1] in ("export.json", "export.md"):
                result = snapshot.get("result")
                if snapshot["status"] != "completed" or not isinstance(result, dict):
                    self._json(409, {"error": "此运行尚无可导出的结果。"})
                    return
                is_json = parts[1] == "export.json"
                body = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) if is_json else export_markdown(result)).encode("utf-8")
                filename = f"creativity-{job_id}.{'json' if is_json else 'md'}"
                content_type = "application/json; charset=utf-8" if is_json else "text/markdown; charset=utf-8"
                self._headers(200, content_type, len(body), attachment=filename)
                self.wfile.write(body)
            else:
                self._json(404, {"error": "接口不存在。"})
        elif path in ("/", "/index.html", "/app.js", "/style.css"):
            name = "index.html" if path == "/" else path[1:]
            target = STATIC_DIR / name
            if not target.is_file():
                self._json(404, {"error": "页面文件不存在。"})
                return
            types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
            body = target.read_bytes()
            self._headers(200, types[target.suffix] + "; charset=utf-8", len(body))
            self.wfile.write(body)
        else:
            self._json(404, {"error": "页面不存在。"})

    def do_POST(self) -> None:
        if not self._local_request():
            self.close_connection = True
            return
        if urlparse(self.path).path != "/api/run":
            self._json(404, {"error": "接口不存在。"})
            self.close_connection = True
            return
        if self.headers.get("Transfer-Encoding"):
            self._json(400, {"error": "不支持流式请求体。"})
            self.close_connection = True
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._json(415, {"error": "请发送 application/json。"})
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 0 < length <= 100000:
            self._json(413, {"error": "请求体不能为空或超过 100 KB。"})
            self.close_connection = True
            return
        self.connection.settimeout(10)
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("请求体不完整。")
            config, demo = validate_request(json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeError, TimeoutError) as exc:
            message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "JSON 格式无效。"
            self._json(400, {"error": message})
            self.close_connection = True
            return
        if not demo and not live_ready():
            self._json(400, {"error": "模型配置未就绪。请设置 CREATIVITY_MODEL、有效的 API 地址，以及远程服务所需的密钥环境变量。"})
            return
        job_id = self.server.start_job(config, demo)
        if job_id is None:
            self._json(409, {"error": "已有运行正在进行。请等待完成后再开始。"})
        else:
            self._json(202, {"job_id": job_id, "status": "queued"})


def serve_main(port: int = 8765, open_browser: bool = False) -> None:
    server = StudioServer(port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Creativity Lab: {url}\n按 Ctrl+C 停止。运行只保留在内存中，请及时导出。")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
