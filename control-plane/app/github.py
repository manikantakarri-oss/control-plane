"""Dispatch the deploy workflow on GitHub Actions.

The Control Plane's only outbound call: to GitHub, never to a customer
workspace. GitHub holds each customer's Databricks credentials in a per-customer
Environment, and the runner uses them.
"""

from __future__ import annotations

import httpx

from app.config import Settings


class DispatchError(RuntimeError):
    pass


def dispatch_deploy(cfg: Settings, ref: str, inputs: dict[str, str]) -> None:
    url = f"{cfg.github_api_url.rstrip('/')}/repos/{cfg.github_repo}/actions/workflows/{cfg.github_workflow}/dispatches"
    try:
        resp = httpx.post(
            url,
            json={"ref": ref, "inputs": inputs},
            headers={
                "Authorization": f"Bearer {cfg.github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise DispatchError(f"could not reach GitHub: {exc}") from exc
    # 204 No Content is success; GitHub newer API versions may return 200.
    if resp.status_code not in (200, 204):
        raise DispatchError(f"GitHub rejected the dispatch ({resp.status_code}): {resp.text[:300]}")
