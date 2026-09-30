"""Databricks access layer.

Two identities are used, deliberately:

* the **user** identity (OBO) - every agent invocation goes out under the
  signed-in user's own token, so Unity Catalog and the serving-endpoint ACL are
  enforced by Databricks itself. A bug in this app cannot widen access.
* the **app** identity (service principal) - used only to *read* endpoint ACLs
  and manage groups, i.e. the metadata plane. It never invokes an agent for a
  user.

Locally there is no Apps runtime, so both fall back to the developer's CLI
token; `auth_mode()` reports which world we are in and the UI shows it.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

import httpx

# Local dev only: which CLI profile to borrow a token from. Empty = the CLI default.
PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "")

# Anything under agent/* is an agent, whatever its schema - that prefix is what
# distinguishes a built agent from a raw model. An endpoint tagged
# portal=true is published too, which is how an admin exposes a plain chat
# model (or a task type invented after this code was written) as an agent.
AGENT_TASK_PREFIX = "agent/"
PORTAL_TAG = "portal"
QUERYABLE = {"CAN_QUERY", "CAN_MANAGE"}

_cli_token: tuple[str, float] | None = None
_sp_token: tuple[str, float] | None = None


class DbxError(RuntimeError):
    """Carries an upstream status code so routes can mirror it."""

    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


def in_apps() -> bool:
    return bool(os.environ.get("DATABRICKS_CLIENT_ID"))


def auth_mode() -> str:
    return "databricks-apps" if in_apps() else "local-dev"


def host() -> str:
    h = os.environ.get("DATABRICKS_HOST")
    if not h:
        h = _cli(["auth", "env", *_profile_args()]).get("env", {}).get("DATABRICKS_HOST", "")
    if not h:
        raise DbxError("DATABRICKS_HOST is not set and could not be read from the CLI profile")
    h = h.strip().rstrip("/")
    # The Apps runtime supplies a bare hostname; the CLI profile supplies a full
    # URL. Normalise, or httpx rejects the request for a missing scheme.
    if not h.startswith(("http://", "https://")):
        h = "https://" + h
    return h


def _profile_args() -> list[str]:
    return ["--profile", PROFILE] if PROFILE else []


def _cli(args: list[str]) -> dict:
    proc = subprocess.run(
        ["databricks", *args, "-o", "json"], capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise DbxError(f"databricks CLI failed: {proc.stderr.strip()[:300]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def _cli_user_token() -> str:
    """Developer's own token, used for both roles in local-dev mode.

    A PAT in DATABRICKS_TOKEN skips the CLI subprocess entirely - useful when
    the `databricks` binary isn't on this process's PATH, or OAuth login isn't
    available (e.g. workspace SSO only offers a provider you don't have).
    """
    env_tok = os.environ.get("DATABRICKS_TOKEN")
    if env_tok:
        return env_tok
    global _cli_token
    if _cli_token and _cli_token[1] > time.time() + 60:
        return _cli_token[0]
    data = _cli(["auth", "token", *_profile_args()])
    tok = data.get("access_token")
    if not tok:
        raise DbxError("could not obtain a CLI token; run `databricks auth login`")
    _cli_token = (tok, time.time() + 1800)
    return tok


def app_token() -> str:
    """Service-principal token (metadata plane only)."""
    if not in_apps():
        return _cli_user_token()
    global _sp_token
    if _sp_token and _sp_token[1] > time.time() + 60:
        return _sp_token[0]
    resp = httpx.post(
        f"{host()}/oidc/v1/token",
        data={"grant_type": "client_credentials", "scope": "all-apis"},
        auth=(os.environ["DATABRICKS_CLIENT_ID"], os.environ["DATABRICKS_CLIENT_SECRET"]),
        timeout=30,
    )
    if resp.status_code != 200:
        raise DbxError(f"service-principal token request failed: {resp.text[:200]}")
    payload = resp.json()
    _sp_token = (payload["access_token"], time.time() + payload.get("expires_in", 3600))
    return _sp_token[0]


def user_token(forwarded: str | None) -> str:
    """OBO token for the signed-in user.

    In Apps the runtime supplies it as X-Forwarded-Access-Token. Its absence in
    Apps is a misconfiguration (missing user_api_scopes) and must fail closed
    rather than silently falling back to the app identity - that fallback would
    turn every user into the service principal.
    """
    if forwarded:
        return forwarded
    if in_apps():
        raise DbxError(
            "no X-Forwarded-Access-Token from the Apps runtime. Apply user_api_scopes "
            "via the bundle (`databricks bundle deploy`) and restart the app.",
            403,
        )
    return _cli_user_token()


def call(method: str, path: str, token: str, **kw) -> Any:
    # Merge rather than overwrite: some APIs need extra headers (the Agent
    # Bricks endpoints want X-Databricks-Workspace-Id).
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(kw.pop("headers", None) or {})
    resp = httpx.request(method, f"{host()}{path}", headers=headers, timeout=120, **kw)
    if resp.status_code >= 400:
        raise DbxError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}", resp.status_code)
    return resp.json() if resp.content else {}
