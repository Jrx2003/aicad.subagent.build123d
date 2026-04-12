import asyncio
import time
from enum import Enum
from typing import Callable, TypeVar, ParamSpec
from common.logging import get_logger

logger = get_logger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""

    def __init__(self, service_name: str, reset_at: float):
        self.service_name = service_name
        self.reset_at = reset_at
        retry_in = max(0, int(reset_at - time.monotonic()))
        super().__init__(
            f"Circuit breaker for {service_name} is open. Retry in {retry_in}s."
        )


class CircuitBreaker:
    """Circuit breaker for external service resilience.

    Implements the circuit breaker pattern to prevent cascading failures
    when external services are unhealthy.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Service is failing, requests are rejected immediately
    - HALF_OPEN: Testing if service has recovered

    Example:
        >>> breaker = CircuitBreaker("llm_service", failure_threshold=5)
        >>> try:
        ...     result = await breaker.call(llm_client.complete, messages)
        ... except CircuitBreakerError:
        ...     # Service is unavailable, use fallback
        ...     pass
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
    ):
        """Initialize circuit breaker.

        Args:
            name: Name of the service (for logging)
            failure_threshold: Consecutive failures before opening
            recovery_timeout: Seconds to wait before trying again
            half_open_max_calls: Max calls allowed in half-open state
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state

    async def call(
        self,
        func: Callable[P, T],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        """Execute function through circuit breaker.

        Args:
            func: Async function to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result of func

        Raises:
            CircuitBreakerError: If circuit is open
            Exception: If func raises and circuit opens
        """
        async with self._lock:
            self._check_state_transition()

            if self._state == CircuitState.OPEN:
                reset_at = self._last_failure_time + self.recovery_timeout
                raise CircuitBreakerError(self.name, reset_at)

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    reset_at = self._last_failure_time + self.recovery_timeout
                    raise CircuitBreakerError(self.name, reset_at)
                self._half_open_calls += 1

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure(e)
            raise

    async def _on_success(self) -> None:
        """Handle successful call."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info(
                    "circuit_breaker_recovered",
                    name=self.name,
                )
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._half_open_calls = 0

    async def _on_failure(self, error: Exception) -> None:
        """Handle failed call."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning(
                    "circuit_breaker_reopened",
                    name=self.name,
                    error=str(error),
                )
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "circuit_breaker_opened",
                    name=self.name,
                    failure_count=self._failure_count,
                    error=str(error),
                )

    def _check_state_transition(self) -> None:
        """Check if state should transition based on time."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info(
                    "circuit_breaker_half_open",
                    name=self.name,
                )

    def reset(self) -> None:
        """Manually reset circuit breaker to closed state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._half_open_calls = 0
        logger.info("circuit_breaker_reset", name=self.name)
