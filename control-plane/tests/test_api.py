from datetime import timedelta

import pytest
from conftest import ADMIN

from app.db import SessionLocal
from app.models import Deployment, utcnow


def beat(client, dep_id, token, **body):
    return client.post(
        f"/api/v1/deployments/{dep_id}/heartbeat",
        json=body or {"health_status": "healthy", "app_version": "1.0.0"},
        headers={"Authorization": f"Bearer {token}"},
    )


# --- probes ----------------------------------------------------------------


def test_health_and_ready_need_no_auth(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json() == {"status": "ok"}


# --- admin auth ------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_management_api_rejects_missing_or_wrong_key(client, headers):
    assert client.get("/api/v1/deployments", headers=headers).status_code == 401
    resp = client.post(
        "/api/v1/deployments", json={"customer_name": "X", "workspace_host": "https://h.example.com"}, headers=headers
    )
    assert resp.status_code == 401


def test_deployment_token_is_not_an_admin_credential(client, register):
    dep = register()
    resp = client.get("/api/v1/deployments", headers={"Authorization": f"Bearer {dep['deployment_token']}"})
    assert resp.status_code == 401


# --- registration ----------------------------------------------------------


def test_register_returns_token_once(client, register):
    dep = register()
    assert dep["deployment_token"]
    assert dep["status"] == "registered"
    assert dep["connectivity"] == "never_seen"

    fetched = client.get(f"/api/v1/deployments/{dep['id']}", headers=ADMIN).json()
    assert "deployment_token" not in fetched
    assert fetched["workspace_host"] == "https://dbc-acme.cloud.databricks.com"


def test_token_is_stored_hashed(register):
    dep = register()
    with SessionLocal() as db:
        row = db.get(Deployment, dep["id"])
    assert row.token_hash != dep["deployment_token"]
    assert len(row.token_hash) == 64


def test_workspace_host_is_normalised(register):
    dep = register(host="DBC-Acme.cloud.databricks.com/")
    assert dep["workspace_host"] == "https://dbc-acme.cloud.databricks.com"


@pytest.mark.parametrize("host", ["http://insecure.example.com", "https://h.example.com/some/path", "https://"])
def test_bad_workspace_host_rejected(client, host):
    resp = client.post("/api/v1/deployments", json={"customer_name": "X", "workspace_host": host}, headers=ADMIN)
    assert resp.status_code == 422


def test_duplicate_workspace_and_app_is_conflict(client, register):
    register()
    resp = client.post(
        "/api/v1/deployments",
        json={"customer_name": "Acme Corp", "workspace_host": "https://dbc-acme.cloud.databricks.com"},
        headers=ADMIN,
    )
    assert resp.status_code == 409


def test_same_customer_reused_across_deployments(client, register):
    a = register(host="https://dbc-1.example.com")
    b = register(host="https://dbc-2.example.com")
    assert a["customer_id"] == b["customer_id"]
    customers = client.get("/api/v1/customers", headers=ADMIN).json()
    assert customers == [
        {"id": a["customer_id"], "name": "Acme Corp", "created_at": customers[0]["created_at"], "deployment_count": 2}
    ]


def test_list_is_paginated_and_filterable(client, register):
    a = register(customer="A", host="https://a.example.com")
    register(customer="B", host="https://b.example.com")
    page = client.get("/api/v1/deployments?limit=1", headers=ADMIN).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    only_a = client.get(f"/api/v1/deployments?customer_id={a['customer_id']}", headers=ADMIN).json()
    assert [d["id"] for d in only_a["items"]] == [a["id"]]


def test_get_missing_deployment_404(client):
    assert client.get("/api/v1/deployments/nope", headers=ADMIN).status_code == 404


# --- heartbeat -------------------------------------------------------------


def test_heartbeat_updates_health_and_activates(client, register):
    dep = register()
    resp = beat(client, dep["id"], dep["deployment_token"], health_status="degraded", app_version="1.2.3")
    assert resp.status_code == 200
    # The ack must not echo deployment details back to the data plane.
    assert set(resp.json()) == {"received_at"}

    fetched = client.get(f"/api/v1/deployments/{dep['id']}", headers=ADMIN).json()
    assert fetched["status"] == "active"
    assert fetched["connectivity"] == "online"
    assert fetched["last_health_status"] == "degraded"
    assert fetched["app_version"] == "1.2.3"


def test_heartbeat_rejects_wrong_token_and_unknown_id_identically(client, register):
    dep = register()
    wrong = beat(client, dep["id"], "not-the-token")
    unknown = beat(client, "no-such-id", dep["deployment_token"])
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_heartbeat_token_is_scoped_to_its_deployment(client, register):
    a = register(host="https://a.example.com")
    b = register(host="https://b.example.com")
    assert beat(client, b["id"], a["deployment_token"]).status_code == 401


def test_heartbeat_rejects_unknown_health_status(client, register):
    dep = register()
    assert beat(client, dep["id"], dep["deployment_token"], health_status="fine").status_code == 422


def test_stale_heartbeat_reported(client, register):
    dep = register()
    beat(client, dep["id"], dep["deployment_token"])
    with SessionLocal() as db:
        row = db.get(Deployment, dep["id"])
        row.last_heartbeat_at = utcnow() - timedelta(hours=1)
        db.commit()
    assert client.get(f"/api/v1/deployments/{dep['id']}", headers=ADMIN).json()["connectivity"] == "stale"


# --- lifecycle -------------------------------------------------------------


def test_rotate_token_invalidates_old_one(client, register):
    dep = register()
    rotated = client.post(f"/api/v1/deployments/{dep['id']}/rotate-token", headers=ADMIN).json()
    assert rotated["deployment_token"] != dep["deployment_token"]
    assert beat(client, dep["id"], dep["deployment_token"]).status_code == 401
    assert beat(client, dep["id"], rotated["deployment_token"]).status_code == 200


def test_decommissioned_deployment_cannot_heartbeat(client, register):
    dep = register()
    resp = client.post(f"/api/v1/deployments/{dep['id']}/decommission", headers=ADMIN)
    assert resp.json()["status"] == "decommissioned"
    assert beat(client, dep["id"], dep["deployment_token"]).status_code == 403


# --- behind the Databricks Apps proxy --------------------------------------
# There, Authorization carries the caller's Databricks token, so our own
# credentials travel in dedicated headers.

DBX_TOKEN = {"Authorization": "Bearer databricks-oauth-token"}


def test_admin_key_in_dedicated_header(client, register):
    register()
    resp = client.get("/api/v1/deployments", headers={**DBX_TOKEN, "X-Admin-Key": "test-admin-key"})
    assert resp.status_code == 200
    resp = client.get("/api/v1/deployments", headers={**DBX_TOKEN, "X-Admin-Key": "wrong"})
    assert resp.status_code == 401


def test_deployment_token_in_dedicated_header(client, register):
    dep = register()
    resp = client.post(
        f"/api/v1/deployments/{dep['id']}/heartbeat",
        json={"health_status": "healthy"},
        headers={**DBX_TOKEN, "X-Deployment-Token": dep["deployment_token"]},
    )
    assert resp.status_code == 200


def test_databricks_token_alone_is_not_enough(client, register):
    dep = register()
    assert client.get("/api/v1/deployments", headers=DBX_TOKEN).status_code == 401
    resp = client.post(f"/api/v1/deployments/{dep['id']}/heartbeat", json={}, headers=DBX_TOKEN)
    assert resp.status_code == 401
