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
