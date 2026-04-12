from langchain_google_genai import ChatGoogleGenerativeAI
from llm.interface import LLMMessage, LLMResponse, extract_usage
from typing import AsyncIterator


class GoogleClient:
    """Google Gemini LLM client using LangChain."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash-exp",
        timeout: float | None = None,
    ):
        kwargs: dict = {
            "google_api_key": api_key,
            "model": model,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout

        self.client = ChatGoogleGenerativeAI(**kwargs)

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
            max_output_tokens=max_tokens,
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
            max_output_tokens=max_tokens,
        ):
            if chunk.content:
                yield str(chunk.content)
