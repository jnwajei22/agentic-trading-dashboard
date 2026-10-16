from __future__ import annotations

from typing import Protocol

from app.config.settings import Settings, settings


class SecretProvider(Protocol):
    def get(self, name: str) -> str | None: ...


class EnvironmentSecretProvider:
    """Compatibility bootstrap provider; business services never call os.getenv directly."""
    def __init__(self, source: Settings | None = None) -> None:
        self.source = source or settings

    def get(self, name: str) -> str | None:
        value = getattr(self.source, name.lower(), None)
        return str(value) if value is not None else None


class ManagedSecretProvider:
    """Production hosting boundary. A deployment adapter must implement retrieval."""
    def get(self, name: str) -> str | None:
        raise NotImplementedError("A managed secret provider is not configured.")
