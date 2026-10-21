import base64
import json
from typing import Any

import openai

from app.ai.providers.base import (
    LLMError,
    LLMResult,
    Message,
    ProviderConfig,
    json_instruction,
    parse_json_text,
)


class OpenAIProvider:
    """OpenAI Chat Completions 协议，兼容 DeepSeek、Gemini（OpenAI 兼容端点）、通义千问等。"""

    def __init__(self, config: ProviderConfig, http_client: Any = None):
        self.config = config
        self.client = openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url or None,
            timeout=config.timeout,
            max_retries=1,
            http_client=http_client,  # 仅测试注入；生产使用 SDK 默认客户端
        )

    def _messages(self, system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m.images and self.config.supports_vision and m.role == "user":
                parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
                for img in m.images:
                    url = "data:image/png;base64," + base64.b64encode(img).decode()
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                out.append({"role": m.role, "content": parts})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    async def _create(self, **kwargs: Any) -> Any:
        params: dict[str, Any] = {"model": self.config.model, "max_tokens": self.config.max_tokens}
        if self.config.temperature is not None:
            params["temperature"] = self.config.temperature
        try:
            return await self.client.chat.completions.create(**params, **kwargs)
        except openai.APIStatusError as exc:
            raise LLMError(f"HTTP {exc.status_code}: {exc.message}") from exc
        except openai.APIError as exc:
            raise LLMError(str(exc)) from exc

    @staticmethod
    def _usage(resp: Any) -> tuple[int, int]:
        u = getattr(resp, "usage", None)
        return (getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0) if u else (0, 0)

    async def generate_structured(
        self, system: str, messages: list[Message], schema: dict[str, Any], tool_name: str, tool_description: str
    ) -> LLMResult:
        msgs = self._messages(system, messages)
        if self.config.supports_tools:
            resp = await self._create(
                messages=msgs,
                tools=[{"type": "function", "function": {"name": tool_name, "description": tool_description, "parameters": schema}}],
                tool_choice={"type": "function", "function": {"name": tool_name}},
            )
            choice = resp.choices[0].message
            for call in choice.tool_calls or []:
                if call.function.name == tool_name:
                    try:
                        data = json.loads(call.function.arguments)
                    except json.JSONDecodeError as exc:
                        raise LLMError(f"工具参数不是合法 JSON：{call.function.arguments[:300]}") from exc
                    return LLMResult(data, call.function.arguments, *self._usage(resp))
            # 部分兼容服务会忽略 tool_choice，退回解析文本
            text = choice.content or ""
            return LLMResult(parse_json_text(text), text, *self._usage(resp))

        msgs[0]["content"] += json_instruction(schema)
        try:
            resp = await self._create(messages=msgs, response_format={"type": "json_object"})
        except LLMError:
            resp = await self._create(messages=msgs)  # 不支持 JSON 模式时再退回纯文本
        text = resp.choices[0].message.content or ""
        return LLMResult(parse_json_text(text), text, *self._usage(resp))
