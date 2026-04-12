from llm.providers.anthropic import AnthropicClient
from llm.providers.openai_compatible import OpenAICompatibleClient
from llm.providers.google import GoogleClient
from llm.interface import LLMClient, LLMTier, DEFAULT_BASE_URLS, DEFAULT_MODELS
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from common.config import Settings


# Mapping of tiers to settings attributes
_TIER_CONFIG_MAP = {
    LLMTier.RAPID: ("llm_rapid_provider", "llm_rapid_model"),
    LLMTier.STANDARD: ("llm_standard_provider", "llm_standard_model"),
    LLMTier.REASONING: ("llm_reasoning_provider", "llm_reasoning_model"),
}


def _get_tier_config(tier: LLMTier, settings: "Settings") -> tuple[str, str]:
    """Extract provider and model for a specific tier.

    Args:
        tier: The capability tier (rapid, standard, or reasoning)
        settings: Application settings

    Returns:
        Tuple of (provider, model) for the tier

    Raises:
        ValueError: If tier is invalid
    """
    if tier not in _TIER_CONFIG_MAP:
        raise ValueError(f"Invalid LLM tier: {tier}")

    provider_attr, model_attr = _TIER_CONFIG_MAP[tier]
    provider = getattr(settings, provider_attr)
    model = getattr(settings, model_attr)

    return provider, model


def _get_provider_config(
    provider: str, settings: "Settings"
) -> tuple[str | None, str | None, str | None]:
    """Get API key, base URL, and model for a provider.

    Lookup priority:
    1. Settings attribute (for explicitly defined providers)
    2. Settings model_extra (for dynamic providers loaded from .env via extra="allow")
    3. Default values from interface.py

    This allows new OpenAI-compatible providers to work by just adding
    {PROVIDER}_API_KEY to .env, without code changes to Settings.

    Args:
        provider: Provider name (e.g., 'openai', 'kimi', 'deepseek')
        settings: Application settings

    Returns:
        Tuple of (api_key, base_url, default_model)
    """
    provider_lower = provider.lower()

    # Try settings attribute first (for explicitly defined providers)
    api_key = getattr(settings, f"{provider_lower}_api_key", None)
    base_url = getattr(settings, f"{provider_lower}_base_url", None)
    model = getattr(settings, f"{provider_lower}_model", None)

    # Fall back to model_extra (for dynamic providers via extra="allow" from .env file)
    extra = getattr(settings, "model_extra", None) or {}
    if not api_key:
        api_key = extra.get(f"{provider_lower}_api_key")
    if not base_url:
        base_url = extra.get(f"{provider_lower}_base_url")

    # Fall back to defaults for known providers
    if not base_url and provider_lower in DEFAULT_BASE_URLS:
        base_url = DEFAULT_BASE_URLS[provider_lower]

    if not model and provider_lower in DEFAULT_MODELS:
        model = DEFAULT_MODELS[provider_lower]

    return api_key, base_url, model


def _create_provider_client(
    provider: str, model: str, settings: "Settings"
) -> LLMClient:
    """Create LLM client for a specific provider and model.

    Supports:
    - Native providers (anthropic, google) with custom client implementations
    - Any OpenAI-compatible provider via dynamic config lookup

    Args:
        provider: Provider name (e.g., 'openai', 'anthropic', 'kimi', 'deepseek')
        model: Model name
        settings: Application settings

    Returns:
        LLM client instance

    Raises:
        ValueError: If provider is unknown or API key not configured
    """
    provider_lower = provider.lower()
    timeout = settings.llm_timeout_seconds

    # Get provider config
    api_key, base_url, _ = _get_provider_config(provider_lower, settings)

    if not api_key:
        raise ValueError(f"{provider.upper()}_API_KEY not configured")

    # Handle native providers with custom implementations
    if provider_lower == "anthropic":
        return AnthropicClient(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
        )

    if provider_lower == "google":
        return GoogleClient(
            api_key=api_key,
            model=model,
            timeout=timeout,
        )

    # All other providers use OpenAI-compatible client
    if not base_url:
        raise ValueError(
            f"{provider.upper()}_BASE_URL not configured and no default URL known. "
            f"Please set {provider.upper()}_BASE_URL in your environment."
        )

    return OpenAICompatibleClient(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )


def create_tiered_llm_client(tier: LLMTier, settings: "Settings") -> LLMClient:
    """Create LLM client for specific capability tier.

    Args:
        tier: Capability tier (rapid, standard, or reasoning)
        settings: Application settings with tier configurations

    Returns:
        LLM client configured for the specified tier

    Raises:
        ValueError: If tier configuration is invalid or provider not supported

    Example:
        >>> from llm.interface import LLMTier
        >>> from common.config import settings
        >>> client = create_tiered_llm_client(LLMTier.STANDARD, settings)
        >>> response = await client.complete(messages)
    """
    try:
        provider, model = _get_tier_config(tier, settings)
        return _create_provider_client(provider, model, settings)
    except ValueError as e:
        # Re-raise with tier context for better error messages
        raise ValueError(
            f"Invalid LLM configuration for '{tier.value}' tier: {e}"
        ) from e


def create_provider_client(
    provider: str,
    model: str,
    settings: "Settings",
) -> LLMClient:
    """Create an explicit provider/model client outside tier defaults."""
    return _create_provider_client(provider, model, settings)


def create_llm_client(settings: "Settings") -> LLMClient:
    """Create LLM client based on settings (legacy).

    Note: This function is maintained for backward compatibility.
    For new code, prefer using create_tiered_llm_client() with explicit tier selection.

    Uses the STANDARD tier configuration as the default.

    Args:
        settings: Application settings

    Returns:
        LLM client configured with standard tier provider

    Raises:
        ValueError: If provider is unknown or API key not configured
    """
    return create_tiered_llm_client(LLMTier.STANDARD, settings)
