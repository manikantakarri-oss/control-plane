"""Two credentials, two audiences.

* **Admin API key** - provider staff / automation managing deployments.
* **Deployment token** - one per Data Plane, only good for that deployment's
  own heartbeat. A leaked token lets someone fake one customer's health
  reports; it cannot read or change anything else.

Each is read from its own header (``X-Admin-Key`` / ``X-Deployment-Token``),
falling back to ``Authorization: Bearer``. The dedicated headers are what make
hosting behind the Databricks Apps proxy work: there, ``Authorization`` carries
the caller's Databricks OAuth token, which the proxy checks before any request
reaches this code.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, "invalid or missing credentials", headers={"WWW-Authenticate": "Bearer"}
    )


def _credential(header_value: str | None, creds: HTTPAuthorizationCredentials | None) -> str:
    if header_value:
        return header_value
    if creds is not None and creds.credentials:
        return creds.credentials
    raise _unauthorized()


def admin_credential(
    x_admin_key: str | None = Header(None), creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> str:
    return _credential(x_admin_key, creds)


def deployment_credential(
    x_deployment_token: str | None = Header(None), creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> str:
    return _credential(x_deployment_token, creds)


def require_admin(token: str = Depends(admin_credential), settings: Settings = Depends(get_settings)) -> None:
    # An unset key locks the admin API rather than opening it.
    if not settings.admin_api_key or not hmac.compare_digest(token, settings.admin_api_key):
        raise _unauthorized()
