from sandbox_mcp_server.contracts import RenderViewInput
from sandbox_mcp_server.service import SandboxMCPService


class _DummyRunner:
    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("not used in this test")


def test_render_view_generated_xyz_helper_supports_build123d_vector_accessors() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    code = service._build_render_view_code(
        action_history=[],
        request=RenderViewInput(session_id="render-session"),
        focus_bbox=None,
    )
    code_text = code if isinstance(code, str) else "\n".join(code)

    assert "float(point.X)" in code_text or "float(point.X())" in code_text
