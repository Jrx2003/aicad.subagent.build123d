from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from enum import Enum
from pathlib import Path
import shlex
from llm.interface import LLMTier


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class SandboxType(str, Enum):
    DOCKER_LOCAL = "docker-local"
    DOCKER_HTTP = "docker-http"
    MCP = "mcp"
    LOCAL_PROCESS = "local-process"


def _discover_env_file(start_dir: Path | str | None = None) -> str | None:
    """Locate the nearest .env from the active workspace or source tree.

    This keeps copied repos and local worktree-style layouts usable without
    duplicating provider credentials into every checkout.
    """
    requested_root = Path(start_dir) if start_dir is not None else Path.cwd()
    search_roots = [requested_root, Path(__file__).resolve().parent]
    seen: set[Path] = set()

    for root in search_roots:
        resolved_root = root.resolve()
        for candidate_dir in (resolved_root, *resolved_root.parents):
            if candidate_dir in seen:
                continue
            seen.add(candidate_dir)
            env_path = candidate_dir / ".env"
            if env_path.is_file():
                return str(env_path)
    return None


def _discover_repo_root() -> str:
    """Locate monorepo root by searching upwards for .git first."""
    parents = Path(__file__).resolve().parents

    for parent in parents:
        if (parent / ".git").exists():
            return str(parent)

    for parent in parents:
        if (parent / "pyproject.toml").exists():
            return str(parent)
    return str(Path.cwd())


DEFAULT_REPO_ROOT = _discover_repo_root()


class Settings(BaseSettings):
    # extra="allow" permits dynamic provider credentials (e.g., NEWPROVIDER_API_KEY)
    # to be loaded from .env without explicit field definitions.
    # Access via settings.model_extra["newprovider_api_key"]
    model_config = SettingsConfigDict(
        env_file=_discover_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow",
    )

    # Environment
    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"

    # Database
    # Use database_url as override, otherwise computed from local/remote based on storage_type
    database_url: str | None = None  # Override: takes precedence if set
    local_database_url: str = (
        "postgresql://cad_user:cad_password@localhost:5433/cad_agent"
    )
    remote_database_url: str | None = None
    db_pool_min_size: int = 5
    db_pool_max_size: int = 20

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # ============================================================================
    # LLM Provider Credentials
    # ============================================================================
    # Anthropic
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None

    # OpenAI
    openai_api_key: str | None = None
    openai_base_url: str | None = None

    # Google
    google_api_key: str | None = None

    # GLM
    glm_api_key: str | None = None
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"

    # Kimi (Moonshot AI)
    kimi_api_key: str | None = None
    kimi_base_url: str = "https://api.moonshot.cn/v1"

    # ============================================================================
    # Web Search
    # ============================================================================
    # Tavily (free tier: 1,000 searches/month)
    tavily_api_key: str | None = None

    # # DeepSeek
    # deepseek_api_key: str | None = None
    # deepseek_base_url: str = "https://api.deepseek.com"

    # ============================================================================
    # LLM Tier Configuration
    # ============================================================================
    # Rapid Tier - Fast, cost-effective for simple tasks
    llm_rapid_provider: str = "google"
    llm_rapid_model: str = "gemini-3.0-flash"

    # Standard Tier - Balanced for normal conversational work
    llm_standard_provider: str = "glm"
    llm_standard_model: str = "glm-4.7"

    # Reasoning Tier - Most capable for complex generation
    llm_reasoning_provider: str = "kimi"
    llm_reasoning_model: str = "kimi-k2.5-thinking"

    # Storage
    storage_type: str = "local"  # local | remote
    local_storage_path: str = "./storage/cad_files"
    signed_url_ttl_seconds: int = 1800

    # Supabase Storage (required when storage_type = "remote")
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    supabase_storage_bucket: str = "cad-files"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8100
    api_tokens_current: str = ""
    api_tokens_next: str = ""

    # CORS
    cors_allowed_origins: str = "*"  # Comma-separated for production
    cors_allow_credentials: bool = True
    cors_max_age: int = 600

    # Rate Limiting
    rate_limit_requests_per_minute: int = 60
    rate_limit_burst_size: int = 10
    rate_limit_enabled: bool = True

    # Timeouts
    llm_timeout_seconds: float = 180.0
    db_command_timeout: float = 30.0

    # Sandbox
    sandbox_type: SandboxType = SandboxType.DOCKER_LOCAL
    sandbox_timeout: int = 180
    sandbox_image: str = "build123d-runtime:latest"
    sandbox_memory_limit: str = "512m"
    sandbox_cpu_quota: int = 100000  # 1 CPU (100000 microseconds per 100ms period)
    sandbox_docker_socket: str | None = None  # Auto-detect if None
    sandbox_use_mock: bool = False  # Use MockSandboxRunner for testing
    sandbox_runner_url: str = "http://localhost:8200"
    sandbox_runner_auth_token: str | None = None
    sandbox_mcp_server_command: str = "uv"
    sandbox_mcp_server_args: str = "run,python,-m,sandbox_mcp_server"
    sandbox_mcp_server_cwd: str | None = None
    sandbox_mcp_timeout_buffer_seconds: int = 30
    sandbox_mcp_benchmark_index_path: str | None = None
    sandbox_mcp_benchmark_default_name: str = "Text2CAD-Bench"
    sandbox_mcp_benchmark_pass_threshold: float = 0.85
    sandbox_mcp_llm_judge_enabled: bool = False
    sandbox_mcp_llm_judge_rubric_path: str = "configs/eval/cad_llm_judge_v1.yaml"
    sandbox_mcp_llm_judge_max_prompt_chars: int = 12000
    sandbox_mcp_llm_judge_max_code_chars: int = 6000

    # Sub Agent
    sub_agent_max_retries: int = 5
    sub_agent_concurrency: int = 5
    sub_agent_dequeue_timeout: int = 5
    sub_agent_aci_max_iterations: int = 20
    sub_agent_runtime_mode: str = "v2"  # legacy | v2
    sub_agent_runtime_hooks_json: str = ""
    sub_agent_runtime_hook_timeout_seconds: float = 8.0

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        if self.cors_allowed_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def current_tokens(self) -> list[str]:
        """Parse current API tokens from comma-separated string."""
        return [t.strip() for t in self.api_tokens_current.split(",") if t.strip()]

    @property
    def next_tokens(self) -> list[str]:
        """Parse next API tokens from comma-separated string."""
        if not self.api_tokens_next:
            return []
        return [t.strip() for t in self.api_tokens_next.split(",") if t.strip()]

    @property
    def sandbox_mcp_server_args_list(self) -> list[str]:
        """Parse MCP server args from comma-separated or shell-style string."""
        return self._parse_sandbox_mcp_server_args(self.sandbox_mcp_server_args)

    @property
    def sandbox_mcp_server_cwd_effective(self) -> str:
        """Return effective MCP server working directory.

        Empty/omitted config falls back to repo root to keep module resolution stable.
        """
        configured_cwd = self.sandbox_mcp_server_cwd
        if configured_cwd and configured_cwd.strip():
            return configured_cwd.strip()
        return DEFAULT_REPO_ROOT

    @property
    def effective_database_url(self) -> str:
        """Get the effective database URL based on configuration.

        Priority:
        1. DATABASE_URL if explicitly set (override/legacy support)
        2. LOCAL_DATABASE_URL if storage_type is "local"
        3. REMOTE_DATABASE_URL if storage_type is "remote"
        """
        if self.database_url:
            return self.database_url
        if self.storage_type == "local":
            return self.local_database_url
        if self.storage_type == "remote":
            if not self.remote_database_url:
                raise ValueError(
                    "REMOTE_DATABASE_URL required when STORAGE_TYPE=remote"
                )
            return self.remote_database_url
        raise ValueError(f"Unknown storage type: {self.storage_type}")

    @model_validator(mode="after")
    def validate_tier_configs(self) -> "Settings":
        """Validate all tier configurations have provider and model specified."""
        tiers = [t.value for t in LLMTier]

        for tier in tiers:
            provider = getattr(self, f"llm_{tier}_provider")
            model = getattr(self, f"llm_{tier}_model")

            if not provider or not provider.strip():
                raise ValueError(f"Empty {tier} tier provider")

            if not model or not model.strip():
                raise ValueError(f"Empty {tier} tier model")

        return self

    @model_validator(mode="after")
    def validate_storage_config(self) -> "Settings":
        """Validate storage configuration."""
        if self.storage_type == "remote":
            # Check remote database URL (unless DATABASE_URL override is set)
            if not self.database_url and not self.remote_database_url:
                raise ValueError(
                    "REMOTE_DATABASE_URL required when STORAGE_TYPE=remote "
                    "(or set DATABASE_URL as override)"
                )
            # Check Supabase storage credentials
            if not self.supabase_url:
                raise ValueError("SUPABASE_URL required when STORAGE_TYPE=remote")
            if not self.supabase_service_role_key:
                raise ValueError(
                    "SUPABASE_SERVICE_ROLE_KEY required when STORAGE_TYPE=remote"
                )
        return self

    @model_validator(mode="after")
    def validate_sandbox_config(self) -> "Settings":
        if self.sandbox_type == SandboxType.DOCKER_HTTP and not self.sandbox_runner_url:
            raise ValueError(
                "SANDBOX_RUNNER_URL required when SANDBOX_TYPE=docker-http"
            )
        if self.sandbox_type == SandboxType.MCP:
            if self.sandbox_mcp_server_cwd is not None:
                normalized_cwd = self.sandbox_mcp_server_cwd.strip()
                self.sandbox_mcp_server_cwd = normalized_cwd or None
            if not self.sandbox_mcp_server_command.strip():
                raise ValueError(
                    "SANDBOX_MCP_SERVER_COMMAND required when SANDBOX_TYPE=mcp"
                )
            if self.sandbox_mcp_timeout_buffer_seconds < 0:
                raise ValueError("SANDBOX_MCP_TIMEOUT_BUFFER_SECONDS must be >= 0")
            if not (0.0 <= self.sandbox_mcp_benchmark_pass_threshold <= 1.0):
                raise ValueError(
                    "SANDBOX_MCP_BENCHMARK_PASS_THRESHOLD must be in [0, 1]"
                )
            if self.sandbox_mcp_llm_judge_max_prompt_chars <= 0:
                raise ValueError("SANDBOX_MCP_LLM_JUDGE_MAX_PROMPT_CHARS must be > 0")
            if self.sandbox_mcp_llm_judge_max_code_chars <= 0:
                raise ValueError("SANDBOX_MCP_LLM_JUDGE_MAX_CODE_CHARS must be > 0")
            try:
                parsed_args = self._parse_sandbox_mcp_server_args(
                    self.sandbox_mcp_server_args
                )
            except ValueError as exc:
                raise ValueError(
                    "SANDBOX_MCP_SERVER_ARGS must be valid shell-style or comma-separated arguments"
                ) from exc
            if not parsed_args:
                raise ValueError(
                    "SANDBOX_MCP_SERVER_ARGS required when SANDBOX_TYPE=mcp"
                )
        return self

    @model_validator(mode="after")
    def validate_sub_agent_config(self) -> "Settings":
        if self.sub_agent_max_retries <= 0:
            raise ValueError("SUB_AGENT_MAX_RETRIES must be > 0")
        if self.sub_agent_concurrency <= 0:
            raise ValueError("SUB_AGENT_CONCURRENCY must be > 0")
        if self.sub_agent_dequeue_timeout <= 0:
            raise ValueError("SUB_AGENT_DEQUEUE_TIMEOUT must be > 0")
        if self.sub_agent_aci_max_iterations <= 0:
            raise ValueError("SUB_AGENT_ACI_MAX_ITERATIONS must be > 0")
        if self.sub_agent_runtime_hook_timeout_seconds <= 0:
            raise ValueError("SUB_AGENT_RUNTIME_HOOK_TIMEOUT_SECONDS must be > 0")
        return self

    def _parse_sandbox_mcp_server_args(self, raw_args: str) -> list[str]:
        """Parse MCP server args with backward compatibility.

        Supported formats:
        1) Comma-separated (preferred): run,python,-m,sandbox_mcp_server
        2) Shell-style (legacy): run python -m sandbox_mcp_server
        """
        normalized = raw_args.strip()
        if not normalized:
            return []

        if (
            "," in normalized
            and " " not in normalized
            and "\t" not in normalized
            and "'" not in normalized
            and '"' not in normalized
        ):
            return [part for part in (p.strip() for p in normalized.split(",")) if part]

        return shlex.split(normalized)

    @model_validator(mode="after")
    def filter_extra_to_llm_credentials(self) -> "Settings":
        """Filter model_extra to only keep LLM credential patterns.

        Defense-in-depth: limits what extra env vars are retained to prevent
        accidental exposure of unrelated secrets if Settings is ever serialized.
        Only keeps patterns matching dynamic LLM provider credentials.
        """
        if self.__pydantic_extra__:
            self.__pydantic_extra__ = {
                k: v
                for k, v in self.__pydantic_extra__.items()
                if k.endswith(("_api_key", "_base_url"))
            }
        return self


# Singleton instance
settings = Settings()
