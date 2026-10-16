import uuid

from app.models.providers import ProviderErrorResponse


class ProviderError(RuntimeError):
    def __init__(
        self, provider: str, error: str, message: str, *, capability: str | None = None,
        retryable: bool = False, status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.response = ProviderErrorResponse(
            provider=provider, error=error, message=message, capability=capability,
            retryable=retryable, status_code=status_code, correlation_id=uuid.uuid4().hex,
        )

    def as_dict(self) -> dict:
        return self.response.model_dump(mode="json")
