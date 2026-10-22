import base64
from typing import Any

import anthropic

from app.ai.providers.base import (
    LLMError,
    LLMResult,
    Message,
    ProviderConfig,
    forced_tool_supported,
    is_tool_choice_error,
    json_instruction,
    mark_forced_tool_unsupported,
    must_call_tool,
    parse_json_text,
)


class AnthropicProvider:
    """Anthropic Messages 协议，兼容 Claude 及提供 Anthropic 兼容端点的服务。"""

    def __init__(self, config: ProviderConfig, http_client: Any = None):
        self.config = config
        self.client = anthropic.AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url or None,
            timeout=config.timeout,
            max_retries=1,
            http_client=http_client,  # 仅测试注入；生产使用 SDK 默认客户端
        )

    def _messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            if m.images and self.config.supports_vision and m.role == "user":
                content: list[dict[str, Any]] = [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img).decode()}}
                    for img in m.images
                ]
                content.append({"type": "text", "text": m.content})
                out.append({"role": m.role, "content": content})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    async def _create(self, **kwargs: Any) -> Any:
        try:
            return await self.client.messages.create(
                model=self.config.model, max_tokens=self.config.max_tokens, **kwargs
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(f"HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.APIError as exc:
            raise LLMError(str(exc)) from exc

    @staticmethod
    def _usage(resp: Any) -> tuple[int, int]:
        u = getattr(resp, "usage", None)
        return (getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0) if u else (0, 0)

    async def generate_structured(
        self, system: str, messages: list[Message], schema: dict[str, Any], tool_name: str, tool_description: str
    ) -> LLMResult:
        msgs = self._messages(messages)
        if self.config.supports_tools:
            tools = [{"name": tool_name, "description": tool_description, "input_schema": schema}]
            resp = None
            if forced_tool_supported(self.config):
                try:
                    resp = await self._create(
                        system=system, messages=msgs, tools=tools, tool_choice={"type": "tool", "name": tool_name}
                    )
                except LLMError as exc:
                    if not is_tool_choice_error(exc):
                        raise
                    mark_forced_tool_unsupported(self.config)
            if resp is None:
                # 扩展思考等模式不支持强制指定工具：改为 auto，并在提示中要求调用工具
                resp = await self._create(
                    system=system + must_call_tool(tool_name, schema), messages=msgs, tools=tools,
                    tool_choice={"type": "auto"},
                )
            texts = []
            for block in resp.content:
                if block.type == "tool_use" and block.name == tool_name:
                    return LLMResult(dict(block.input), "", *self._usage(resp))
                if block.type == "text":
                    texts.append(block.text)
            text = "\n".join(texts)
            return LLMResult(parse_json_text(text), text, *self._usage(resp))

        resp = await self._create(system=system + json_instruction(schema), messages=msgs)
        text = "\n".join(b.text for b in resp.content if b.type == "text")
        return LLMResult(parse_json_text(text), text, *self._usage(resp))
