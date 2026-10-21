from typing import Any

from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.base import LLMError, LLMProvider, LLMResult, Message, ProviderConfig
from app.ai.providers.openai_provider import OpenAIProvider

PROTOCOLS = {"openai": OpenAIProvider, "anthropic": AnthropicProvider}


def build_provider(config: ProviderConfig, http_client: Any = None) -> LLMProvider:
    try:
        cls = PROTOCOLS[config.protocol]
    except KeyError as exc:
        raise LLMError(f"不支持的协议：{config.protocol}") from exc
    return cls(config, http_client)


__all__ = ["LLMError", "LLMProvider", "LLMResult", "Message", "ProviderConfig", "build_provider"]
