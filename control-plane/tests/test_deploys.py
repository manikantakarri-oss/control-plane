import pytest
from conftest import ADMIN

from app import main
from app.config import get_settings
from app.github import DispatchError


@pytest.fixture
def github(monkeypatch):
    """Enable deploys and capture dispatches instead of calling GitHub."""
    cfg = get_settings()
    monkeypatch.setattr(cfg, "github_repo", "acme-provider/platform")
    monkeypatch.setattr(cfg, "github_token", "gh-test")
    calls = []

    def fake_dispatch(_cfg, ref, inputs):
        calls.append({"ref": ref, "inputs": inputs})

    monkeypatch.setattr(main, "dispatch_deploy", fake_dispatch)
    return calls


def deploy(client, dep_id, **body):
    return client.post(f"/api/v1/deployments/{dep_id}/deploy", json=body, headers=ADMIN)


def test_deploy_dispatches_workflow_with_deployment_inputs(client, register, github):
    dep = register()
    client.patch(f"/api/v1/deployments/{dep['id']}", json={"github_environment": "acme"}, headers=ADMIN)

    resp = deploy(client, dep["id"], mode="onboard", restart=True)
    assert resp.status_code == 202, resp.text
    run = resp.json()
    assert run["status"] == "requested" and run["mode"] == "onboard" and run["ref"] == "main"

    assert github == [
        {
            "ref": "main",
            "inputs": {
                "deploy_run_id": run["id"],
                "deployment_id": dep["id"],
                "mode": "onboard",
                "github_environment": "acme",
                "app_name": "agent-portal",
                "restart": "true",
            },
        }
    ]


def test_deploy_needs_github_environment(client, register, github):
    dep = register()
    assert deploy(client, dep["id"]).status_code == 409
    assert github == []


def test_github_environment_can_be_set_at_registration(client, github):
    resp = client.post(
        "/api/v1/deployments",
        json={"customer_name": "B", "workspace_host": "https://b.example.com", "github_environment": "cust-b"},
        headers=ADMIN,
    )
    assert resp.json()["github_environment"] == "cust-b"
    assert deploy(client, resp.json()["id"]).status_code == 202


@pytest.mark.parametrize("bad", ["", "has space", "x;rm -rf", "../etc"])
def test_github_environment_is_validated(client, register, bad):
    dep = register()
    resp = client.patch(f"/api/v1/deployments/{dep['id']}", json={"github_environment": bad}, headers=ADMIN)
    assert resp.status_code == 422


def test_deploy_disabled_without_github_config(client, register):
    dep = register()
    client.patch(f"/api/v1/deployments/{dep['id']}", json={"github_environment": "acme"}, headers=ADMIN)
    assert deploy(client, dep["id"]).status_code == 503


def test_deploy_requires_admin(client, register, github):
    dep = register()
    resp = client.post(
        f"/api/v1/deployments/{dep['id']}/deploy",
        json={},
        headers={"Authorization": f"Bearer {dep['deployment_token']}"},
    )
    assert resp.status_code == 401


def test_only_one_deploy_in_flight(client, register, github):
    dep = register()
    client.patch(f"/api/v1/deployments/{dep['id']}", json={"github_environment": "acme"}, headers=ADMIN)
    first = deploy(client, dep["id"]).json()
    assert deploy(client, dep["id"]).status_code == 409

    client.post(f"/api/v1/deploy-runs/{first['id']}/status", json={"status": "succeeded"}, headers=ADMIN)
    assert deploy(client, dep["id"]).status_code == 202


def test_workflow_reports_progress(client, register, github):
    dep = register()
    client.patch(f"/api/v1/deployments/{dep['id']}", json={"github_environment": "acme"}, headers=ADMIN)
    run = deploy(client, dep["id"]).json()
    url = "https://github.com/acme-provider/platform/actions/runs/1"

    r = client.post(
        f"/api/v1/deploy-runs/{run['id']}/status", json={"status": "running", "run_url": url}, headers=ADMIN
    )
    assert r.json()["status"] == "running" and r.json()["run_url"] == url
    r = client.post(
        f"/api/v1/deploy-runs/{run['id']}/status", json={"status": "failed", "detail": "boom"}, headers=ADMIN
    )
    assert r.json()["status"] == "failed" and r.json()["run_url"] == url

    # terminal is final
    r = client.post(f"/api/v1/deploy-runs/{run['id']}/status", json={"status": "running"}, headers=ADMIN)
    assert r.status_code == 409

    history = client.get(f"/api/v1/deployments/{dep['id']}/deploy-runs", headers=ADMIN).json()
    assert [h["id"] for h in history] == [run["id"]]


def test_dispatch_failure_marks_run_failed(client, register, github, monkeypatch):
    def fail(*_):
        raise DispatchError("GitHub rejected the dispatch (404)")

    monkeypatch.setattr(main, "dispatch_deploy", fail)
    dep = register()
    client.patch(f"/api/v1/deployments/{dep['id']}", json={"github_environment": "acme"}, headers=ADMIN)
    assert deploy(client, dep["id"]).status_code == 502

    history = client.get(f"/api/v1/deployments/{dep['id']}/deploy-runs", headers=ADMIN).json()
    assert history[0]["status"] == "failed" and "404" in history[0]["detail"]


def test_decommissioned_deployment_cannot_be_deployed(client, register, github):
    dep = register()
    client.patch(f"/api/v1/deployments/{dep['id']}", json={"github_environment": "acme"}, headers=ADMIN)
    client.post(f"/api/v1/deployments/{dep['id']}/decommission", headers=ADMIN)
    assert deploy(client, dep["id"]).status_code == 409


def test_dispatch_calls_github_api(monkeypatch):
    """The real dispatcher, against a stubbed transport."""
    import httpx

    from app import github as gh

    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json, auth=headers["Authorization"])
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(gh.httpx, "post", fake_post)
    cfg = get_settings().model_copy(update={"github_repo": "o/r", "github_token": "t"})
    gh.dispatch_deploy(cfg, "main", {"mode": "upgrade"})
    assert seen["url"] == "https://api.github.com/repos/o/r/actions/workflows/deploy-customer.yml/dispatches"
    assert seen["json"] == {"ref": "main", "inputs": {"mode": "upgrade"}}
    assert seen["auth"] == "Bearer t"

    monkeypatch.setattr(gh.httpx, "post", lambda url, **_: httpx.Response(422, request=httpx.Request("POST", url)))
    with pytest.raises(DispatchError):
        gh.dispatch_deploy(cfg, "main", {})
