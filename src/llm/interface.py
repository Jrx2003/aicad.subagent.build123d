from typing import Any, Protocol, AsyncIterator, Literal
from enum import Enum
from pydantic import BaseModel, Field

# Default base URLs for known OpenAI-compatible providers
DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "glm": "https://open.bigmodel.cn/api/paas/v4/",
    "kimi": "https://api.moonshot.cn/v1",
    "deepseek": "https://api.deepseek.com",
}

# Recommended models by provider and tier
# - rapid: Fast, cost-effective for simple tasks
# - standard: Balanced performance for normal work
# - reasoning: Most capable for complex generation
TIERED_MODELS: dict[str, dict[str, str]] = {
    "openai": {
        "rapid": "gpt-5.2-chat-latest",  # GPT-5.2 Instant
        "standard": "gpt-5.2",  # GPT-5.2 Thinking
        "reasoning": "gpt-5.2-pro",  # GPT-5.2 Pro
    },
    "anthropic": {
        "rapid": "claude-haiku-4-5-20251017",
        "standard": "claude-sonnet-4-5-20250929",
        "reasoning": "claude-opus-4-5-20251124",
    },
    "google": {
        "rapid": "gemini-3.0-flash",
        "standard": "gemini-3.0-pro",
        "reasoning": "gemini-3.0-deep-think",
    },
    "glm": {
        "rapid": "glm-4.7-flash",
        "standard": "glm-4.7",
        "reasoning": "glm-4.7",  # GLM-4.7 is their top model
    },
    "kimi": {
        "rapid": "kimi-k2-instruct",
        "standard": "kimi-k2.5",
        "reasoning": "kimi-k2-thinking",
    },
    "deepseek": {
        "rapid": "deepseek-v3.2",
        "standard": "deepseek-v3.2",
        "reasoning": "deepseek-v3.2-speciale",
    },
}

# Default models for known providers (uses standard tier)
DEFAULT_MODELS: dict[str, str] = {
    provider: tiers["standard"] for provider, tiers in TIERED_MODELS.items()
}


class LLMTier(str, Enum):
    """LLM capability tiers for explicit selection."""

    RAPID = "rapid"  # Fast, cost-effective for simple tasks
    STANDARD = "standard"  # Balanced for normal conversational work
    REASONING = "reasoning"  # Most capable for complex generation


class LLMTextContent(BaseModel):
    """Text content part for multimodal messages."""

    type: Literal["text"] = "text"
    text: str


class LLMImageContent(BaseModel):
    """Image content part for multimodal messages."""

    type: Literal["image"] = "image"
    mime_type: str
    data_base64: str


LLMContentPart = LLMTextContent | LLMImageContent


class LLMMessage(BaseModel):
    """LLM message model."""

    role: str
    content: str | list[LLMContentPart]
    name: str | None = None


class LLMResponse(BaseModel):
    """LLM response model."""

    content: str
    usage: dict | None = None


class LLMToolDefinition(BaseModel):
    """Model-visible tool schema."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class LLMToolCall(BaseModel):
    """One tool call emitted by the model."""

    id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMToolResponse(BaseModel):
    """Completion response that may include tool calls."""

    content: str
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    usage: dict | None = None
    finish_reason: str | None = None


def extract_usage(response: Any) -> dict | None:
    """Extract usage metadata from a LangChain response."""
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        return {
            "input_tokens": response.usage_metadata.get("input_tokens", 0),
            "output_tokens": response.usage_metadata.get("output_tokens", 0),
        }
    return None


def normalize_response_content(content: Any) -> str:
    """Normalize provider response content into a plain text string."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        normalized_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                normalized_parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    normalized_parts.append(text)
        return "".join(normalized_parts)

    return str(content)


class LLMClient(Protocol):
    """Protocol for LLM provider implementations."""

    supports_multimodal: bool
    supports_tool_calling: bool

    async def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a single completion."""
        ...

    def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream completion tokens (async generator)."""
        ...

    async def complete_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMToolResponse:
        """Generate a completion that may include structured tool calls."""
        ...

    def stream_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMToolResponse]:
        """Stream a tool-calling completion."""
        ...
