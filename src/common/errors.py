from typing import Any


class AiCADError(Exception):
    """Base exception for all AiCAD errors."""

    def __init__(self, message: str, **context: Any):
        super().__init__(message)
        self.message = message
        self.context = context


class ValidationError(AiCADError):
    """Raised when input validation fails."""

    pass


class NotFoundError(AiCADError):
    """Raised when a resource is not found."""

    pass


class ConflictError(AiCADError):
    """Raised when a conflict occurs."""

    pass


class AuthenticationError(AiCADError):
    """Raised when authentication fails."""

    pass


class AuthorizationError(AiCADError):
    """Raised when authorization fails."""

    pass


class ExternalServiceError(AiCADError):
    """Raised when an external service fails."""

    pass
