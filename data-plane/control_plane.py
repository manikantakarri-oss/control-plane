"""Outbound-only reporting to the provider's Control Plane.

The portal calls the Control Plane's heartbeat endpoint on its own schedule;
the Control Plane has no network path back in. A Control Plane that is slow,
unreachable or down must never affect the portal, so every failure here is
logged and swallowed, never raised.

Configuration is one JSON document, CONTROL_PLANE_CONFIG (app.yaml reads it
from the customer's secret scope; scripts/provision-credentials.sh writes it):

    {"url": "https://...", "deployment_id": "...", "token": "...",
     "oauth": {"host": "https://<provider-workspace>", "client_id": "...", "client_secret": "..."}}

"oauth" is present when the Control Plane is itself hosted as a Databricks App:
its proxy admits only Databricks OAuth tokens from the provider workspace, so
the portal first obtains one for a provider service principal that holds
CAN_USE on the Control Plane app. The deployment token then travels in
X-Deployment-Token.

With no configuration the reporter does not start - the normal local-dev state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time

import httpx

from dbx import DbxError, app_token, in_apps

log = logging.getLogger("agent_portal.control_plane")

VERSION = os.environ.get("APP_VERSION", "dev")

_oauth_lock = threading.Lock()
_oauth_token: tuple[str, float] | None = None


def _config() -> dict | None:
    raw = os.environ.get("CONTROL_PLANE_CONFIG", "").strip()
    if raw:
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError:
            log.error("CONTROL_PLANE_CONFIG is not valid JSON; control plane reporting disabled")
            return None
    else:
        # Individual variables: convenient for local testing.
        cfg = {
            "url": os.environ.get("CONTROL_PLANE_URL", ""),
            "deployment_id": os.environ.get("CONTROL_PLANE_DEPLOYMENT_ID", ""),
            "token": os.environ.get("CONTROL_PLANE_TOKEN", ""),
        }
    if not isinstance(cfg, dict):
        return None
    cfg["url"] = str(cfg.get("url") or "").strip().rstrip("/")
    if not (cfg["url"] and cfg.get("deployment_id") and cfg.get("token")):
        return None
    return cfg


def enabled() -> bool:
    return _config() is not None


def _provider_token(oauth: dict, timeout: float) -> str:
    """Databricks OAuth (client credentials) token for the provider workspace."""
    global _oauth_token
    with _oauth_lock:
        if _oauth_token and _oauth_token[1] - time.time() > 300:
            return _oauth_token[0]
        host = str(oauth["host"]).rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "https://" + host
        resp = httpx.post(
            f"{host}/oidc/v1/token",
            data={"grant_type": "client_credentials", "scope": "all-apis"},
            auth=(oauth["client_id"], oauth["client_secret"]),
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        _oauth_token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return _oauth_token[0]


def health_status() -> str:
    """What the portal reports about itself. Carries no customer data.

    degraded = the process is up but cannot get a service-principal token,
    which means every admin and catalog call is failing.
    """
    if not in_apps():
        return "healthy"
    try:
        app_token()
    except (DbxError, httpx.HTTPError, KeyError):
        return "degraded"
    return "healthy"


def send_heartbeat(timeout: float = 5.0) -> bool:
    """One best-effort report. Returns whether it was accepted; never raises."""
    cfg = _config()
    if cfg is None:
        return False
    try:
        headers = {"X-Deployment-Token": cfg["token"]}
        oauth = cfg.get("oauth")
        if oauth:
            headers["Authorization"] = f"Bearer {_provider_token(oauth, timeout)}"
        else:
            headers["Authorization"] = f"Bearer {cfg['token']}"
        resp = httpx.post(
            f"{cfg['url']}/api/v1/deployments/{cfg['deployment_id']}/heartbeat",
            json={"health_status": health_status(), "app_version": VERSION},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("control plane heartbeat failed (non-fatal): %s", exc)
        return False
    except Exception:  # noqa: BLE001 - reporting must never take the portal down
        log.exception("control plane heartbeat raised unexpectedly (non-fatal)")
        return False


async def run_forever() -> None:
    """Background loop started by the app's lifespan."""
    interval = max(15, int(os.environ.get("CONTROL_PLANE_HEARTBEAT_SECONDS", "60")))
    log.info("control plane reporting every %ss", interval)
    while True:
        await asyncio.to_thread(send_heartbeat)
        await asyncio.sleep(interval)
