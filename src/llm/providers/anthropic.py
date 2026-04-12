from langchain_anthropic import ChatAnthropic
from llm.interface import LLMMessage, LLMResponse, extract_usage
from typing import AsyncIterator


class AnthropicClient:
    """Anthropic LLM client using LangChain."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-20241022",
        base_url: str | None = None,
        timeout: float | None = None,
    ):
        # Note: ChatAnthropic uses anthropic_api_url for custom endpoints
        kwargs: dict = {
            "anthropic_api_key": api_key,
            "model": model,
        }
        if base_url:
            kwargs["anthropic_api_url"] = base_url
        if timeout is not None:
            kwargs["default_request_timeout"] = timeout

        self.client = ChatAnthropic(**kwargs)

    async def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a single completion."""
        lc_messages = [{"role": m.role, "content": m.content} for m in messages]

        response = await self.client.ainvoke(
            lc_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        return LLMResponse(content=str(response.content), usage=extract_usage(response))

    async def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream completion tokens."""
        lc_messages = [{"role": m.role, "content": m.content} for m in messages]

        async for chunk in self.client.astream(
            lc_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            if chunk.content:
                yield str(chunk.content)
