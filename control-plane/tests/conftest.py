import os

# Configure before app.config is first imported, so no real Postgres is needed.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["ENVIRONMENT"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = {"Authorization": "Bearer test-admin-key"}


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def register(client):
    def _register(customer="Acme Corp", host="https://dbc-acme.cloud.databricks.com", app_name="agent-portal"):
        resp = client.post(
            "/api/v1/deployments",
            json={"customer_name": customer, "workspace_host": host, "app_name": app_name},
            headers=ADMIN,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _register
