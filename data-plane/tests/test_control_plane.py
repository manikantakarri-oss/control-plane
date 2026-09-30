"""The Control Plane reporter must be best-effort: never raise, never block
the portal, and send nothing when it is not configured."""
from __future__ import annotations

import httpx
import pytest

import control_plane

CFG = {
    "CONTROL_PLANE_URL": "https://cp.example.com/",
    "CONTROL_PLANE_DEPLOYMENT_ID": "dep-1",
    "CONTROL_PLANE_TOKEN": "tok-1",
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in [*CFG, "CONTROL_PLANE_CONFIG"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(control_plane, "_oauth_token", None)


@pytest.fixture
def configured(monkeypatch):
    for k, v in CFG.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)


def test_disabled_without_config():
    assert not control_plane.enabled()
    assert control_plane.send_heartbeat() is False


def test_invalid_json_config_disables_reporting(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_CONFIG", "{not json")
    assert not control_plane.enabled()


def test_json_config_with_oauth_gets_provider_token_first(monkeypatch):
    """Control Plane hosted as a Databricks App: OAuth token in Authorization,
    deployment token in its own header."""
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
    monkeypatch.setenv(
        "CONTROL_PLANE_CONFIG",
        '{"url": "https://cp.aws.databricksapps.com", "deployment_id": "dep-1", "token": "tok-1",'
        ' "oauth": {"host": "provider.cloud.databricks.com", "client_id": "cid", "client_secret": "sec"}}',
    )
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        req = httpx.Request("POST", url)
        if url.endswith("/oidc/v1/token"):
            return httpx.Response(200, json={"access_token": "dbx-tok", "expires_in": 3600}, request=req)
        return httpx.Response(200, request=req)

    monkeypatch.setattr(control_plane.httpx, "post", fake_post)
    assert control_plane.send_heartbeat() is True
    assert control_plane.send_heartbeat() is True

    token_calls = [c for c in calls if c[0].endswith("/oidc/v1/token")]
    assert len(token_calls) == 1, "provider token must be cached"
    assert token_calls[0][0] == "https://provider.cloud.databricks.com/oidc/v1/token"
    assert token_calls[0][1]["auth"] == ("cid", "sec")

    url, kw = calls[-1]
    assert url == "https://cp.aws.databricksapps.com/api/v1/deployments/dep-1/heartbeat"
    assert kw["headers"] == {"X-Deployment-Token": "tok-1", "Authorization": "Bearer dbx-tok"}


def test_sends_authenticated_heartbeat(configured, monkeypatch):
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(control_plane.httpx, "post", fake_post)
    assert control_plane.send_heartbeat() is True
    assert seen["url"] == "https://cp.example.com/api/v1/deployments/dep-1/heartbeat"
    assert seen["headers"] == {"X-Deployment-Token": "tok-1", "Authorization": "Bearer tok-1"}
    assert seen["json"]["health_status"] == "healthy"


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), RuntimeError("unexpected")],
)
def test_failures_never_raise(configured, monkeypatch, failure):
    def boom(*_, **__):
        raise failure

    monkeypatch.setattr(control_plane.httpx, "post", boom)
    assert control_plane.send_heartbeat() is False


def test_rejected_heartbeat_returns_false(configured, monkeypatch):
    monkeypatch.setattr(
        control_plane.httpx,
        "post",
        lambda url, **_: httpx.Response(401, request=httpx.Request("POST", url)),
    )
    assert control_plane.send_heartbeat() is False


def test_degraded_when_service_principal_token_fails(monkeypatch):
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp")

    def fail():
        raise control_plane.DbxError("no token")

    monkeypatch.setattr(control_plane, "app_token", fail)
    assert control_plane.health_status() == "degraded"
