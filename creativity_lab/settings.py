"""Validated, ephemeral model settings. Secrets are never serialized or persisted."""
from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlsplit

from .providers import ChatProvider, ProviderError


def normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.endswith("/chat/completions"):
        value = value[:-len("/chat/completions")].rstrip("/")
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
    # Canonical host/scheme avoids treating equivalent endpoints as a key change.
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()).geturl()


@dataclass(frozen=True, repr=False)
class ModelSettings:
    base_url: str
    model: str
    api_key: str
    judge_model: str = ""
    token_param: str = "max_completion_tokens"

    @classmethod
    def from_environment(cls) -> ModelSettings:
        return cls(os.getenv("CREATIVITY_BASE_URL", "https://api.openai.com/v1").strip(),
                   os.getenv("CREATIVITY_MODEL", "").strip(),
                   os.getenv("CREATIVITY_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
                   os.getenv("CREATIVITY_JUDGE_MODEL", "").strip(),
                   os.getenv("CREATIVITY_TOKEN_PARAM", "max_completion_tokens"))

    @classmethod
    def from_request(cls, data: object, current: ModelSettings) -> ModelSettings:
        if not isinstance(data, dict):
            raise ValueError("设置必须是 JSON 对象。")
        allowed = {"base_url", "model", "api_key", "judge_model", "token_param"}
        if set(data) - allowed:
            raise ValueError("设置含不支持的字段。")
        values = {}
        for name, limit in (("base_url", 2048), ("model", 200), ("api_key", 8192),
                            ("judge_model", 200), ("token_param", 32)):
            value = data.get(name, "" if name == "api_key" else getattr(current, name))
            if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("设置字段格式或长度无效。")
            values[name] = value.strip()
        values["base_url"] = normalize_url(values["base_url"])
        if not values["model"]:
            raise ValueError("请填写模型名称。")
        if values["token_param"] not in ("max_completion_tokens", "max_tokens"):
            raise ValueError("请选择支持的输出长度参数。")
        if not values["api_key"]:
            try:
                same_endpoint = values["base_url"] == normalize_url(current.base_url)
            except ValueError:
                same_endpoint = False
            if same_endpoint:
                values["api_key"] = current.api_key
            elif urlsplit(values["base_url"]).hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("更换接口地址时，请重新填写该接口的密钥。")
        settings = cls(**values)
        try:
            settings.providers()
        except ProviderError:
            raise ValueError("模型设置未就绪，请检查接口、模型名和密钥。") from None
        return settings

    def providers(self, timeout: int = 60) -> tuple[ChatProvider, ChatProvider | None]:
        options = {"base_url": self.base_url, "api_key": self.api_key,
                   "token_param": self.token_param, "timeout": timeout}
        provider = ChatProvider(model=self.model, **options)
        judge = ChatProvider(model=self.judge_model, **options) if self.judge_model else None
        return provider, judge

    def public(self) -> dict:
        try:
            base_url = normalize_url(self.base_url)
        except ValueError:
            base_url = ""  # Never echo embedded credentials from an invalid environment URL.
        try:
            self.providers()
            ready = True
        except (ProviderError, ValueError, TypeError):
            ready = False
        return {"base_url": base_url, "model": self.model, "judge_model": self.judge_model,
                "token_param": self.token_param, "key_present": bool(self.api_key), "live_ready": ready}
