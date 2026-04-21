from common.config import Settings
from llm.interface import TIERED_MODELS
from llm.providers.openai_compatible import OpenAICompatibleClient
from sandbox_mcp_server.contracts import CADActionInput

from llm.providers.openai_compatible import _sanitize_tool_input_schema


def test_kimi_tool_schema_sanitizer_removes_description_from_ref_properties() -> None:
    schema = CADActionInput.model_json_schema()

    sanitized = _sanitize_tool_input_schema(
        schema,
        model_name="kimi-k2.5-thinking",
    )

    assert "$ref" in sanitized["properties"]["action_type"]
    assert "description" not in sanitized["properties"]["action_type"]


def test_non_kimi_tool_schema_preserves_property_description() -> None:
    schema = CADActionInput.model_json_schema()

    sanitized = _sanitize_tool_input_schema(
        schema,
        model_name="glm-4.7",
    )

    assert sanitized["properties"]["action_type"]["description"] == "Type of CAD action to execute."


def test_kimi_tier_defaults_prefer_k2_6() -> None:
    assert TIERED_MODELS["kimi"]["standard"] == "kimi-k2.6"
    assert TIERED_MODELS["kimi"]["reasoning"] == "kimi-k2.6"


def test_settings_default_reasoning_model_uses_kimi_k2_6(monkeypatch) -> None:
    monkeypatch.delenv("LLM_REASONING_MODEL", raising=False)

    app_settings = Settings(_env_file=None)

    assert app_settings.llm_reasoning_model == "kimi-k2.6"


def test_kimi_k2_6_defaults_follow_non_thinking_k2_rules() -> None:
    client = object.__new__(OpenAICompatibleClient)
    client._model = "kimi-k2.6"

    assert client._effective_temperature(0.2) == 0.6
    assert client._provider_invoke_overrides() == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }
    assert client._resolve_api_model_name("kimi-k2.6") == "kimi-k2.6"
    assert client._resolve_request_timeout(None) is None


def test_kimi_k2_6_uses_same_provider_rules_as_k2_5() -> None:
    client = object.__new__(OpenAICompatibleClient)
    client._model = "kimi-k2.6-thinking"

    assert client._effective_temperature(0.2) == 1.0
    assert client._provider_invoke_overrides() == {
        "extra_body": {"thinking": {"type": "enabled"}}
    }
    assert client._resolve_api_model_name("kimi-k2.6-thinking") == "kimi-k2.6"
    assert client._resolve_request_timeout(None) == 180.0
