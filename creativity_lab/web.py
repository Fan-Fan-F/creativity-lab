"""Loopback-only web studio with ephemeral settings and sanitized results."""
from __future__ import annotations

import json
import hashlib
import http.client
import os
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .engine import Engine, RunConfig
from . import __version__
from .providers import CallBudget, ChatProvider, DemoProvider, ProviderError
from .settings import ModelSettings

STATIC_DIR = Path(__file__).parent / "static"
INSTALLATION_ID = hashlib.sha256(str(STATIC_DIR.resolve()).encode()).hexdigest()[:24]
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
        ModelSettings.from_environment().providers()
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
        raise ValueError("请求含不支持的字段。请通过模型设置配置接口。")
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
    allow_reuse_address = False  # Windows must not share a live app's listening port.

    def __init__(self, port: int = 8765):
        super().__init__(("127.0.0.1", port), StudioHandler)
        self.jobs: dict[str, dict] = {}
        self.job_lock = threading.Lock()
        self.settings_lock = threading.Lock()
        self.settings: ModelSettings | None = None
        self.settings_token = secrets.token_urlsafe(32)

    def settings_snapshot(self) -> ModelSettings:
        with self.settings_lock:
            return self.settings if self.settings is not None else ModelSettings.from_environment()

    def public_config(self) -> dict:
        with self.settings_lock:
            snapshot = self.settings if self.settings is not None else ModelSettings.from_environment()
            configured = self.settings is not None
        return {**snapshot.public(), "session_configured": configured,
                "settings_token": self.settings_token, "version": __version__,
                "app": "creativity-lab", "installation_id": INSTALLATION_ID}

    def start_job(self, config: RunConfig, demo: bool) -> str | None:
        # Construct providers at acceptance, so a later settings change cannot
        # redirect an in-flight run or send its key to a different endpoint.
        provider, judge = (DemoProvider(), None) if demo else self.settings_snapshot().providers()
        with self.job_lock:
            if any(j["status"] in ("queued", "running") for j in self.jobs.values()):
                return None
            while len(self.jobs) >= 24:
                self.jobs.pop(next(iter(self.jobs)))
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"job_id": job_id, "status": "queued", "created_at": time.time()}
        threading.Thread(target=self._run_job, args=(job_id, config, provider, judge), daemon=True).start()
        return job_id

    def _run_job(self, job_id: str, config: RunConfig, provider, judge) -> None:
        with self.job_lock:
            self.jobs[job_id]["status"] = "running"
        try:
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
                    "error": "运行未完成。请打开模型设置，检查模型名称、连接和调用预算。",
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
            self._json(200, self.server.public_config())
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
        path = urlparse(self.path).path
        if path not in ("/api/run", "/api/settings", "/api/settings/test", "/api/settings/reset", "/api/settings/models"):
            self._json(404, {"error": "接口不存在。"})
            self.close_connection = True
            return
        if path.startswith("/api/settings") and not secrets.compare_digest(
                self.headers.get("X-Settings-Token", "").encode("utf-8"), self.server.settings_token.encode("ascii")):
            self._json(403, {"error": "设置请求已失效，请刷新页面后重试。"})
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
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError, TimeoutError, RecursionError) as exc:
            message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "JSON 格式无效。"
            self._json(400, {"error": message})
            self.close_connection = True
            return
        if path.startswith("/api/settings"):
            self._settings_request(path, data)
            return
        try:
            config, demo = validate_request(data)
            job_id = self.server.start_job(config, demo)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        except (ProviderError, TypeError):
            self._json(400, {"error": "模型配置未就绪，请点击右上角的模型设置。"})
            return
        if job_id is None:
            self._json(409, {"error": "已有运行正在进行。请等待完成后再开始。"})
        else:
            self._json(202, {"job_id": job_id, "status": "queued"})

    def _settings_request(self, path: str, data: object) -> None:
        if path == "/api/settings/reset":
            if data != {}:
                self._json(400, {"error": "恢复设置请求必须是空对象。"})
                return
            with self.server.settings_lock:
                self.server.settings = None
            self._json(200, self.server.public_config())
            return
        try:
            settings = ModelSettings.from_request(data, self.server.settings_snapshot(),
                                                  require_model=path != "/api/settings/models")
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        if path == "/api/settings":
            with self.server.settings_lock:
                self.server.settings = settings
            self._json(200, self.server.public_config())
            return
        budget = CallBudget(1)
        started = time.monotonic()
        try:
            provider, _ = settings.providers(require_model=path != "/api/settings/models")
            if path == "/api/settings/models":
                provider.timeout = min(settings.timeout, 30)
                models = provider.list_models()
                self._json(200, {"models": models, "count": len(models), "protocol": settings.protocol,
                                 "elapsed_ms": round((time.monotonic() - started) * 1000),
                                 "message": "模型列表已读取（最多 500 个）；请选择模型后测试生成连接。"})
                return
            response = provider.complete({"purpose": "connection_test", "instruction": 'Return only {"ok": true} as JSON.'}, budget)
            if response.get("ok") is not True:
                raise ProviderError("Unexpected probe JSON", code="invalid_json", connection_ok=True)
        except ProviderError as exc:
            hints = {"auth_failed": "密钥无效或已过期，请检查鉴权方式和密钥。",
                     "access_denied": "接口拒绝访问，请检查账号权限及模型授权。",
                     "endpoint_not_found": "接口或模型不存在，请检查协议、地址和模型名称。列表不受支持时可手填模型。",
                     "invalid_request": "接口拒绝此参数组合。请检查协议及输出长度参数；兼容接口可试提示词 JSON，温度留空。",
                     "rate_limited": "接口限流或额度不足，请检查额度并稍后手动重试。",
                     "provider_unavailable": "模型服务暂时不可用，请稍后手动重试。",
                     "timeout": "等待模型响应超时。慢响应模型可在高级设置中提高超时，再手动测试。",
                     "tls_error": "HTTPS 证书验证失败，请检查服务证书及系统时间。",
                     "dns_error": "无法解析接口域名，请检查地址和网络。",
                     "connection_failed": "无法连接接口，请检查服务是否运行、地址和网络。",
                     "redirect_blocked": "接口要求跳转，请直接填写最终接口地址。",
                     "output_limit": "已收到模型响应，但输出被截断。请提高最大输出 token（推理也可能占用预算）。",
                     "refused": "已连接模型，但模型拒绝此次请求。",
                     "invalid_json": "已连接模型，但没有收到要求的 JSON 对象。请检查模型输出能力或切换 JSON 输出方式。",
                     "invalid_response": "已收到接口响应，但其格式与所选协议不一致。请检查协议和接口地址。",
                     "incomplete_response": "已收到模型响应，但文本生成未完成。"}
            message = hints.get(exc.code, "请求未完成，请检查协议、接口和模型设置。")
            if exc.http_status is not None:
                message = f"HTTP {exc.http_status}：{message}"
            self._json(502, {"error": message, "code": exc.code, "retryable": exc.retryable,
                             "http_status": exc.http_status, "connection_ok": exc.connection_ok,
                             "elapsed_ms": round((time.monotonic() - started) * 1000),
                             "budget": budget.as_dict()})
            return
        except Exception:
            self._json(502, {"error": "连接测试未完成，请检查服务可用性。", "budget": budget.as_dict()})
            return
        self._json(200, {"ok": True, "model": settings.model, "protocol": settings.protocol,
                         "elapsed_ms": round((time.monotonic() - started) * 1000), "budget": budget.as_dict(),
                         "message": "生成模型连接成功，已收到 JSON 响应。请点击应用设置。"})


def _existing_installation(port: int) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/api/config")
        response = connection.getresponse()
        raw = response.read(8193)
        if response.status != 200 or len(raw) > 8192:
            return False
        data = json.loads(raw)
        return isinstance(data, dict) and data.get("app") == "creativity-lab" and data.get("version") == __version__ and data.get("installation_id") == INSTALLATION_ID
    except (OSError, ValueError, RecursionError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def serve_main(port: int = 8765, open_browser: bool = False) -> None:
    # An old desktop window may still use the default port during an upgrade.
    # Leave its jobs intact and open the updated app on the next free port.
    server = None
    for candidate in range(port, min(port + 10, 65535) + 1):
        try:
            server = StudioServer(candidate)
            break
        except OSError:
            if open_browser and _existing_installation(candidate):
                webbrowser.open(f"http://127.0.0.1:{candidate}")
                print("已打开正在运行的 Creativity Lab；沿用其设置与任务。")
                return
            continue
    if server is None:
        raise RuntimeError("本地端口均被占用，请关闭旧实验室窗口后重试。")
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
