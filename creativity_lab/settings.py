"""Validated, ephemeral model settings. Secrets are never serialized or persisted."""
from __future__ import annotations

from dataclasses import dataclass
import os
import math
from urllib.parse import urlsplit

from .providers import ChatProvider, ProviderError


def normalize_url(value: str, protocol: str = "openai_chat") -> str:
    value = value.strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages", "/models"):
        if value.endswith(suffix):
            value = value[:-len(suffix)].rstrip("/")
            break
    if not value or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("请输入有效的接口地址。")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise ValueError("接口地址格式无效。") from None
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError("接口地址须使用 HTTP/HTTPS，不能含账号、密钥参数或片段。")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("远程接口请使用 HTTPS；本机接口可以使用 HTTP。")
    if protocol == "anthropic" and not parsed.path:
        parsed = parsed._replace(path="/v1")
    # Canonical host/scheme avoids treating equivalent endpoints as a key change.
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()).geturl()


@dataclass(frozen=True, repr=False)
class ModelSettings:
    base_url: str
    model: str
    api_key: str
    judge_model: str = ""
    token_param: str = "auto"
    protocol: str = "openai_chat"
    json_mode: str = "json_object"
    auth_type: str = "auto"
    timeout: int = 120
    max_output_tokens: int = 4096
    temperature: float | None = None

    @classmethod
    def from_environment(cls) -> ModelSettings:
        def number(name, default, converter):
            try:
                result = converter(os.getenv(name, ""))
                return result if math.isfinite(result) else default
            except (TypeError, ValueError, OverflowError):
                return default
        protocol = os.getenv("CREATIVITY_PROTOCOL", "openai_chat")
        return cls(os.getenv("CREATIVITY_BASE_URL", "https://api.openai.com/v1").strip(),
                   os.getenv("CREATIVITY_MODEL", "").strip(),
                   os.getenv("CREATIVITY_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
                   os.getenv("CREATIVITY_JUDGE_MODEL", "").strip(),
                   os.getenv("CREATIVITY_TOKEN_PARAM", "auto"), protocol,
                   os.getenv("CREATIVITY_JSON_MODE", "prompt" if protocol == "anthropic" else "json_object"),
                   os.getenv("CREATIVITY_AUTH_TYPE", "auto"),
                   number("CREATIVITY_TIMEOUT", 120, int),
                   number("CREATIVITY_MAX_OUTPUT_TOKENS", 4096, int),
                   number("CREATIVITY_TEMPERATURE", None, float))

    @classmethod
    def from_request(cls, data: object, current: ModelSettings, *, require_model=True) -> ModelSettings:
        if not isinstance(data, dict):
            raise ValueError("设置必须是 JSON 对象。")
        allowed = {"base_url", "model", "api_key", "judge_model", "token_param", "protocol",
                   "json_mode", "auth_type", "timeout", "max_output_tokens", "temperature"}
        if set(data) - allowed:
            raise ValueError("设置含不支持的字段。")
        values = {}
        for name, limit in (("base_url", 2048), ("model", 200), ("api_key", 8192),
                            ("judge_model", 200), ("token_param", 32), ("protocol", 32),
                            ("json_mode", 32), ("auth_type", 32)):
            value = data.get(name, "" if name == "api_key" else getattr(current, name))
            if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("设置字段格式或长度无效。")
            values[name] = value.strip()
        values["base_url"] = normalize_url(values["base_url"], values["protocol"])
        if require_model and not values["model"]:
            raise ValueError("请填写模型名称。")
        if values["token_param"] not in ("auto", "max_completion_tokens", "max_tokens"):
            raise ValueError("请选择支持的输出长度参数。")
        if values["protocol"] not in ("openai_chat", "openai_responses", "anthropic"):
            raise ValueError("请选择支持的接口协议。")
        if values["json_mode"] not in ("json_object", "prompt") or (values["protocol"] == "anthropic" and values["json_mode"] != "prompt"):
            raise ValueError("此协议请选择提示词 JSON 输出。")
        if values["auth_type"] not in ("auto", "bearer", "x_api_key"):
            raise ValueError("请选择支持的鉴权方式。")
        for name, low, high in (("timeout", 5, 300), ("max_output_tokens", 256, 32768)):
            value = data.get(name, getattr(current, name))
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} 必须是 {low} 至 {high} 的整数。")
            values[name] = value
        temperature = data.get("temperature", current.temperature)
        if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2 or not math.isfinite(temperature)):
            raise ValueError("温度须留空或填写 0 至 2 的数字。")
        values["temperature"] = temperature
        if not values["api_key"]:
            try:
                same_endpoint = values["base_url"] == normalize_url(current.base_url, current.protocol)
            except ValueError:
                same_endpoint = False
            if same_endpoint:
                values["api_key"] = current.api_key
            elif urlsplit(values["base_url"]).hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("更换接口地址时，请重新填写该接口的密钥。")
        settings = cls(**values)
        try:
            settings.providers(require_model=require_model)
        except ProviderError:
            raise ValueError("模型设置未就绪，请检查接口、模型名和密钥。") from None
        return settings

    def providers(self, timeout: int | None = None, *, require_model=True) -> tuple[ChatProvider, ChatProvider | None]:
        options = {"base_url": normalize_url(self.base_url, self.protocol), "api_key": self.api_key,
                   "token_param": self.token_param, "timeout": self.timeout if timeout is None else timeout,
                   "protocol": self.protocol, "json_mode": self.json_mode, "auth_type": self.auth_type,
                   "max_output_tokens": self.max_output_tokens, "temperature": self.temperature}
        provider = ChatProvider(model=self.model, require_model=require_model, **options)
        judge = ChatProvider(model=self.judge_model, **options) if self.judge_model else None
        return provider, judge

    def public(self) -> dict:
        try:
            base_url = normalize_url(self.base_url, self.protocol)
        except ValueError:
            base_url = ""  # Never echo embedded credentials from an invalid environment URL.
        try:
            self.providers()
            ready = True
        except (ProviderError, ValueError, TypeError):
            ready = False
        return {"base_url": base_url, "model": self.model, "judge_model": self.judge_model,
                "token_param": self.token_param, "protocol": self.protocol, "json_mode": self.json_mode,
                "auth_type": self.auth_type, "timeout": self.timeout,
                "max_output_tokens": self.max_output_tokens, "temperature": self.temperature,
                "key_present": bool(self.api_key), "live_ready": ready}
