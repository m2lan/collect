import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(Exception):
    pass


# 不支持强制指定工具（tool_choice 指定函数）的模型，如开启思考/推理模式的 DeepSeek、Kimi、Qwen、Claude 等。
# 按 (base_url, model) 记住，后续请求直接使用 tool_choice=auto，避免每次先失败一次。
_NO_FORCED_TOOL: set[tuple[str | None, str]] = set()


def is_tool_choice_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "tool_choice" in msg or "tool choice" in msg


def forced_tool_supported(config: "ProviderConfig") -> bool:
    return (config.base_url, config.model) not in _NO_FORCED_TOOL


def mark_forced_tool_unsupported(config: "ProviderConfig") -> None:
    _NO_FORCED_TOOL.add((config.base_url, config.model))


def must_call_tool(tool_name: str, schema: dict[str, Any]) -> str:
    """不能强制调用工具时追加到系统提示，同时说明 JSON 格式以便退回解析文本。"""
    return f"\n\n你必须调用工具 {tool_name} 提交结果；如果无法调用工具，" + json_instruction(schema).lstrip()


@dataclass
class Message:
    role: str  # user / assistant
    content: str
    images: list[bytes] = field(default_factory=list)  # PNG，仅 user 消息、支持视觉的模型使用


@dataclass
class LLMResult:
    data: dict[str, Any]
    raw_text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ProviderConfig:
    protocol: str
    model: str
    api_key: str
    base_url: str | None = None
    max_tokens: int = 8192
    temperature: float | None = None
    timeout: float = 120
    supports_tools: bool = True
    supports_vision: bool = False


class LLMProvider(Protocol):
    config: ProviderConfig

    async def generate_structured(
        self, system: str, messages: list[Message], schema: dict[str, Any], tool_name: str, tool_description: str
    ) -> LLMResult: ...


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_json_text(text: str) -> dict[str, Any]:
    """从模型的文本回复中提取 JSON 对象（兼容 ```json 代码块和裸 JSON）。"""
    candidates = [m.group(1) for m in _FENCE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            data = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise LLMError(f"模型未返回合法的 JSON：{text[:300]}")


def json_instruction(schema: dict[str, Any]) -> str:
    return (
        "\n\n请只输出一个 JSON 对象（可放在 ```json 代码块中），不要输出其他内容。JSON Schema 如下：\n"
        + json.dumps(schema, ensure_ascii=False)
    )
