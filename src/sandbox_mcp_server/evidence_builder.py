import hashlib
import base64
import re
from uuid import uuid4

from sandbox.interface import SandboxResult
from sandbox_mcp_server.contracts import ExecuteBuild123dInput, SandboxErrorCode
from sandbox_mcp_server.llm_judge_models import (
    EvidenceBundle,
    EvidenceImage,
    LLMJudgeRubric,
)

_STEP_EXTENSIONS = (".step", ".stp")
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
_PREVIEW_IMAGE_PREFERRED_ORDER = (
    "preview_iso.png",
    "preview_front.png",
    "preview_right.png",
    "preview_top.png",
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


class EvidenceBuilder:
    def __init__(
        self,
        max_prompt_chars: int,
        max_code_chars: int,
        max_preview_images: int = 4,
        max_preview_image_bytes: int = 600_000,
        evaluator_version: str = "llm_eval_v1",
    ) -> None:
        self._max_prompt_chars = max_prompt_chars
        self._max_code_chars = max_code_chars
        self._max_preview_images = max(0, max_preview_images)
        self._max_preview_image_bytes = max(0, max_preview_image_bytes)
        self._evaluator_version = evaluator_version

    def build(
        self,
        request: ExecuteBuild123dInput,
        sandbox_result: SandboxResult,
        error_code: SandboxErrorCode,
        rubric: LLMJudgeRubric,
    ) -> EvidenceBundle:
        step_filename, step_content = self._pick_step_artifact(
            sandbox_result.output_file_contents
        )

        step_sha256 = (
            hashlib.sha256(step_content).hexdigest()
            if step_content is not None
            else None
        )
        preview_images = self._collect_preview_images(
            sandbox_result.output_file_contents
        )

        requirement_text = self._truncate(
            (request.requirement_text or "").strip(),
            self._max_prompt_chars,
        )
        requirement_text_sha256 = (
            hashlib.sha256(requirement_text.encode("utf-8")).hexdigest()
            if requirement_text
            else None
        )
        normalized_requirement = self._normalize_prompt(requirement_text)
        request_prompt = self._truncate(request.code.strip(), self._max_prompt_chars)
        normalized_prompt = self._normalize_prompt(request_prompt)

        preview_hashes = ",".join(image.sha256 for image in preview_images)
        replay_material = (
            f"{normalized_requirement}:{normalized_prompt}:{step_sha256 or 'missing'}:{preview_hashes or 'no_preview'}"
        ).encode("utf-8")
        replay_key = hashlib.sha256(replay_material).hexdigest()

        return EvidenceBundle(
            requirement_text=requirement_text,
            requirement_text_sha256=requirement_text_sha256,
            request_prompt=request_prompt,
            execution_success=sandbox_result.success,
            execution_error_code=error_code.value,
            stderr_excerpt=self._truncate(sandbox_result.stderr or "", 2000),
            has_step=step_content is not None,
            step_filename=step_filename,
            step_size_bytes=(len(step_content) if step_content is not None else None),
            code_excerpt=self._truncate(request.code.strip(), self._max_code_chars),
            step_sha256=step_sha256,
            rubric_version=rubric.rubric_version,
            prompt_version=rubric.prompt_version,
            judge_model=rubric.judge_model,
            evaluator_version=self._evaluator_version,
            evaluation_trace_id=str(uuid4()),
            replay_key=replay_key,
            preview_images=preview_images,
        )

    def _pick_step_artifact(
        self,
        output_file_contents: dict[str, bytes],
    ) -> tuple[str | None, bytes | None]:
        if "model.step" in output_file_contents:
            return "model.step", output_file_contents["model.step"]

        for filename, content in output_file_contents.items():
            if filename.lower().endswith(_STEP_EXTENSIONS):
                return filename, content

        return None, None

    def _collect_preview_images(
        self,
        output_file_contents: dict[str, bytes],
    ) -> list[EvidenceImage]:
        if self._max_preview_images <= 0 or self._max_preview_image_bytes <= 0:
            return []

        image_candidates: list[tuple[str, bytes]] = [
            (filename, content)
            for filename, content in output_file_contents.items()
            if filename.lower().endswith(_IMAGE_EXTENSIONS)
        ]

        sorted_candidates = sorted(
            image_candidates,
            key=lambda pair: self._image_sort_key(pair[0]),
        )

        preview_images: list[EvidenceImage] = []
        for filename, content in sorted_candidates:
            if len(preview_images) >= self._max_preview_images:
                break

            if len(content) > self._max_preview_image_bytes:
                continue

            preview_images.append(
                EvidenceImage(
                    filename=filename,
                    mime_type=self._resolve_image_mime_type(filename),
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    content_base64=base64.b64encode(content).decode("ascii"),
                )
            )

        return preview_images

    def _image_sort_key(self, filename: str) -> tuple[int, str]:
        normalized = filename.lower()
        if normalized in _PREVIEW_IMAGE_PREFERRED_ORDER:
            return (_PREVIEW_IMAGE_PREFERRED_ORDER.index(normalized), normalized)
        return (len(_PREVIEW_IMAGE_PREFERRED_ORDER), normalized)

    def _resolve_image_mime_type(self, filename: str) -> str:
        normalized = filename.lower()
        if normalized.endswith(".png"):
            return "image/png"
        if normalized.endswith(".jpg") or normalized.endswith(".jpeg"):
            return "image/jpeg"
        return "application/octet-stream"

    def _normalize_prompt(self, prompt: str) -> str:
        compact = _WHITESPACE_PATTERN.sub(" ", prompt).strip().lower()
        return compact

    def _truncate(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars]
