from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from app.config import PrincipalConfig
from app.errors import AppError


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Principal:
    principal_id: str
    tenant: str
    role: str


def authenticate(request: Request, api_key: str | None = Depends(api_key_header)) -> Principal:
    if not api_key:
        raise AppError(401, "AUTH_REQUIRED", "X-API-Key is required")
    configured: dict[str, PrincipalConfig] = request.app.state.principals
    matched: PrincipalConfig | None = None
    # Compare every configured key to reduce key-existence timing leakage.
    for key, candidate in configured.items():
        if secrets.compare_digest(api_key, key):
            matched = candidate
    if matched is None:
        raise AppError(401, "INVALID_API_KEY", "API key is invalid")
    return Principal(matched.principal_id, matched.tenant, matched.role)


def require_roles(*roles: str):
    def dependency(principal: Principal = Depends(authenticate)) -> Principal:
        if principal.role not in roles:
            raise AppError(403, "FORBIDDEN", "principal role cannot perform this operation")
        return principal
    return dependency

