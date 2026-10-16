from contextvars import ContextVar, Token
from typing import Any


_current_claims: ContextVar[dict[str, Any] | None] = ContextVar(
    "current_workspace_claims", default=None
)


def set_current_claims(claims: dict[str, Any]) -> Token:
    return _current_claims.set(claims)


def reset_current_claims(token: Token) -> None:
    _current_claims.reset(token)


def get_current_claims() -> dict[str, Any] | None:
    return _current_claims.get()


def get_current_user_sub() -> str | None:
    claims = get_current_claims()
    subject = claims.get("sub") if claims else None
    return subject if isinstance(subject, str) and subject else None
