import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_openai import ChatOpenAI
from llm.interface import (
    LLMImageContent,
    LLMMessage,
    LLMResponse,
    LLMTextContent,
    LLMToolCall,
    LLMToolDefinition,
    LLMToolResponse,
    extract_usage,
    normalize_response_content,
)


class OpenAICompatibleClient:
    """Base client for OpenAI-compatible LLM APIs via LangChain."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float | None = None,
    ):
        self._requested_model = model
        self._model = model
        self._api_model = self._resolve_api_model_name(model)
        kwargs: dict = {
            "api_key": api_key,
            "model": self._api_model,
        }
        if base_url:
            kwargs["base_url"] = base_url
        resolved_timeout = self._resolve_request_timeout(timeout)
        self._request_timeout_seconds = resolved_timeout
        if resolved_timeout is not None:
            kwargs["request_timeout"] = resolved_timeout

        self.client = ChatOpenAI(**kwargs)
        self.supports_multimodal = True
        self.supports_tool_calling = True

    async def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Generate a single completion."""
        lc_messages = [
            {
                "role": message.role,
                "content": self._to_langchain_content(message.content),
            }
            for message in messages
        ]

        invoke_kwargs: dict = {
            "temperature": self._effective_temperature(temperature),
            "max_tokens": max_tokens,
        }
        invoke_kwargs.update(self._provider_invoke_overrides())
        response = await self._invoke_with_timeout(
            lc_messages=lc_messages,
            invoke_kwargs=invoke_kwargs,
        )

        return LLMResponse(
            content=normalize_response_content(response.content),
            usage=extract_usage(response),
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream completion tokens."""
        lc_messages = [
            {
                "role": message.role,
                "content": self._to_langchain_content(message.content),
            }
            for message in messages
        ]

        async for chunk in self.client.astream(
            lc_messages,
            **{
                "temperature": self._effective_temperature(temperature),
                "max_tokens": max_tokens,
                **self._provider_invoke_overrides(),
            },
        ):
            if chunk.content:
                yield normalize_response_content(chunk.content)

    async def complete_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMToolResponse:
        """Generate one completion that may include tool calls."""
        lc_messages = [
            {
                "role": message.role,
                "content": self._to_langchain_content(message.content),
                **({"name": message.name} if message.name else {}),
            }
            for message in messages
        ]
        bound_client = self.client.bind_tools(
            [self._tool_to_openai_schema(tool) for tool in tools],
            tool_choice=self._normalize_tool_choice(tool_choice),
        )
        invoke_kwargs: dict[str, Any] = {
            "temperature": self._effective_temperature(temperature),
            "max_tokens": max_tokens,
        }
        invoke_kwargs.update(self._provider_invoke_overrides())
        response = await self._invoke_with_timeout(
            lc_messages=lc_messages,
            invoke_kwargs=invoke_kwargs,
            client=bound_client,
        )
        return LLMToolResponse(
            content=normalize_response_content(response.content),
            tool_calls=self._extract_tool_calls(response),
            usage=extract_usage(response),
            finish_reason=self._extract_finish_reason(response),
        )

    async def stream_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMToolResponse]:
        """Best-effort streaming interface for tool-calling models.

        LangChain's tool-call streaming support differs across providers. For now we
        expose a stable async iterator by yielding the final response once.
        """
        yield await self.complete_with_tools(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _to_langchain_content(
        self,
        content: str | list[LLMTextContent | LLMImageContent],
    ) -> str | list[dict]:
        if isinstance(content, str):
            return content

        blocks: list[dict] = []
        for part in content:
            if isinstance(part, LLMTextContent):
                blocks.append({"type": "text", "text": part.text})
                continue

            data_url = f"data:{part.mime_type};base64,{part.data_base64}"
            blocks.append({"type": "image_url", "image_url": {"url": data_url}})

        return blocks

    def _effective_temperature(self, requested_temperature: float) -> float:
        model_name = self._model.lower()
        if model_name.startswith("kimi-k2.5-thinking"):
            return 1.0
        # Kimi K2.5 instant mode requires temperature=0.6.
        if model_name.startswith("kimi-k2.5"):
            return 0.6
        # Other Kimi chat models currently require temperature=1.
        if model_name.startswith("kimi-"):
            return 1.0
        return requested_temperature

    def _provider_invoke_overrides(self) -> dict:
        model_name = self._model.lower()
        if model_name.startswith("kimi-k2.5-thinking"):
            return {"extra_body": {"thinking": {"type": "enabled"}}}
        if model_name.startswith("kimi-k2.5"):
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {}

    def _resolve_api_model_name(self, model: str) -> str:
        model_name = model.strip().lower()
        if model_name.startswith("kimi-k2.5-thinking"):
            return "kimi-k2.5"
        return model

    def _resolve_request_timeout(self, timeout: float | None) -> float | None:
        if timeout is None:
            if self._model.lower().startswith("kimi-k2.5-thinking"):
                return 180.0
            return None
        return max(0.1, float(timeout))

    async def _invoke_with_timeout(
        self,
        *,
        lc_messages: list[dict],
        invoke_kwargs: dict,
        client: Any | None = None,
    ) -> object:
        target_client = client or self.client
        timeout_seconds = getattr(self, "_request_timeout_seconds", None)
        ainvoke = getattr(target_client, "ainvoke", None)
        if callable(ainvoke):
            invoke_task = ainvoke(
                lc_messages,
                **invoke_kwargs,
            )
        else:
            invoke_task = asyncio.to_thread(
                target_client.invoke,
                lc_messages,
                **invoke_kwargs,
            )
        if timeout_seconds is None:
            return await invoke_task
        try:
            return await asyncio.wait_for(
                invoke_task,
                timeout=max(0.1, float(timeout_seconds)) + 1.0,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"openai_compatible_invoke_timeout_after_{float(timeout_seconds):.1f}s"
            ) from exc

    def _tool_to_openai_schema(self, tool: LLMToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            },
        }

    def _normalize_tool_choice(
        self,
        tool_choice: str | dict[str, Any] | None,
    ) -> str | dict[str, Any] | None:
        if tool_choice is None:
            return None
        if isinstance(tool_choice, dict):
            return tool_choice
        normalized = tool_choice.strip().lower()
        if normalized in {"auto", "none", "required", "any"}:
            return "required" if normalized == "any" else normalized
        return {
            "type": "function",
            "function": {"name": tool_choice},
        }

    def _extract_tool_calls(self, response: Any) -> list[LLMToolCall]:
        raw_calls = getattr(response, "tool_calls", None)
        if not isinstance(raw_calls, list):
            return []
        normalized_calls: list[LLMToolCall] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                continue
            name = raw_call.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            args = raw_call.get("args")
            if not isinstance(args, dict):
                args = {}
            raw_id = raw_call.get("id")
            call_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else None
            normalized_calls.append(
                LLMToolCall(
                    id=call_id,
                    name=name.strip(),
                    arguments=args,
                )
            )
        return normalized_calls

    def _extract_finish_reason(self, response: Any) -> str | None:
        raw = getattr(response, "response_metadata", None)
        if isinstance(raw, dict):
            finish_reason = raw.get("finish_reason")
            if isinstance(finish_reason, str) and finish_reason.strip():
                return finish_reason.strip()
        return None
