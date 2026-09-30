# Control Plane

The provider-owned registry of Data Plane deployments. It stores **customer and deployment
metadata only** — never customer business data — and it **never calls into a customer
environment**. Each Data Plane reports in, outbound, on its own schedule; a deployment that
stops reporting shows as `stale`.

It also **requests deploys**: it dispatches `.github/workflows/deploy-customer.yml` on GitHub,
which holds each customer's Databricks credentials, and records each run's progress. See the
[repository README](../README.md) for the end-to-end onboarding flow.

It runs either way:

- **As a Databricks App** in the provider's own workspace, with Lakebase as its database:
  [`databricks.yml`](databricks.yml), set up end to end by `scripts/setup-provider.sh`.
  There is no database password: the app connects as its own identity with short-lived tokens.
- **As a container** anywhere, with any Postgres ([`Dockerfile`](Dockerfile), `docker-compose.yml`).

## API

| Method & path | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | none | liveness |
| `GET /ready` | none | readiness (database answers) |
| `POST /api/v1/deployments` | admin | register a deployment; returns `deployment_token` **once** |
| `GET /api/v1/deployments?limit=&offset=&customer_id=` | admin | list, paginated |
| `GET /api/v1/deployments/{id}` | admin | one deployment, with derived `connectivity` |
| `PATCH /api/v1/deployments/{id}` | admin | set `github_environment` |
| `POST /api/v1/deployments/{id}/rotate-token` | admin | issue a new token; the old one stops working |
| `POST /api/v1/deployments/{id}/decommission` | admin | retire; further heartbeats get 403 |
| `GET /api/v1/customers` | admin | customers with deployment counts |
| `POST /api/v1/deployments/{id}/deploy` | admin | `{mode: onboard\|upgrade, ref?, restart?}` → 202 with a deploy run |
| `GET /api/v1/deployments/{id}/deploy-runs` | admin | deploy history, newest first |
| `GET /api/v1/deploy-runs/{run_id}` | admin | one run: `requested`, `running`, `succeeded`, `failed` + GitHub run link |
| `POST /api/v1/deploy-runs/{run_id}/status` | admin | progress report, called by the deploy workflow |
| `POST /api/v1/deployments/{id}/heartbeat` | deployment token | called by the Data Plane |

Each credential goes in its own header, `X-Admin-Key` or `X-Deployment-Token`. As a fallback,
either one is also accepted as `Authorization: Bearer <token>`.

The dedicated headers are required when hosted as a Databricks App. There, the Apps proxy uses
`Authorization` for the caller's Databricks OAuth token, and rejects any request without one
before it reaches this service.

- **Admin API key** (`ADMIN_API_KEY`) — provider staff and automation.
- **Deployment token** — one per deployment, stored only as a SHA-256 hash, valid only for
  that deployment's own heartbeat. The heartbeat response echoes nothing back, and an unknown
  id and a wrong token get the same 401, so a token cannot be used to read or probe anything.

`connectivity` is derived at read time: `never_seen`, `online`, or `stale` (no heartbeat for
`HEARTBEAT_STALE_SECONDS`, default 300).

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `production` disables `/docs` and enforces the checks below |
| `DATABASE_URL` | *(required)* unless `LAKEBASE_INSTANCE` | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host:5432/db`; SQLite is refused in production |
| `LAKEBASE_INSTANCE` | *(empty)* | use this Lakebase instance instead of `DATABASE_URL` (Databricks App hosting) |
| `LAKEBASE_DATABASE` | `databricks_postgres` | |
| `AUTO_MIGRATE` | `false` | run Alembic migrations at startup (set by the Databricks bundle) |
| `ADMIN_API_KEY` | *(empty)* | required, ≥ 32 chars, in production; empty means the admin API rejects everything |
| `HEARTBEAT_STALE_SECONDS` | `300` | |
| `LOG_LEVEL` | `INFO` | |
| `WEB_CONCURRENCY` | `2` | uvicorn workers (container only) |
| `SKIP_MIGRATIONS` | `0` | set `1` if migrations run as a separate release step |
| `GITHUB_REPO` | *(empty)* | `owner/name` of this repository; with `GITHUB_TOKEN`, enables the deploy API |
| `GITHUB_TOKEN` | *(empty)* | fine-grained token, this repo only, **Actions: Read and write** only |
| `GITHUB_WORKFLOW` | `deploy-customer.yml` | |
| `GITHUB_REF` | `main` | default ref to deploy |
| `DEPLOY_RUN_TIMEOUT_SECONDS` | `3600` | an unfinished run stops blocking new deploys after this |

Generate an admin key with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

## Run locally

```bash
cp .env.example .env            # set ADMIN_API_KEY
docker compose up --build       # Postgres + service on http://localhost:8001 (docs at /docs)
```

Without Docker:

```bash
pip install -r requirements-dev.txt
export DATABASE_URL=sqlite:///./local.db ADMIN_API_KEY=dev-key
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```

## Test

```bash
pip install -r requirements-dev.txt
pytest          # in-memory SQLite, no Docker needed
ruff check . && ruff format --check .
```

`tests/test_migrations.py` fails if the Alembic history and the models drift apart.

## Schema changes

Production only ever runs migrations. After changing `app/models.py`:

```bash
alembic revision --autogenerate -m "describe the change"   # review the generated file
alembic upgrade head
```

## Production checklist

As a Databricks App, TLS, network exposure and secrets are handled by the platform. The
Lakebase instance is protected from bundle deletion (`prevent_destroy`). As a container:

- Run behind TLS termination (load balancer / ingress); the container speaks plain HTTP on 8000
  and honours `X-Forwarded-*`.
- Use a managed Postgres with backups; supply `DATABASE_URL` and `ADMIN_API_KEY` from a secret
  manager, not from `.env`.
- The heartbeat endpoint must be reachable from customer workspaces (outbound from them); the
  management endpoints need only be reachable from provider networks — restrict them at the
  ingress if possible.

## Layout

```
app/main.py       routes
app/config.py     settings from environment
app/db.py         engine and session (Postgres URL or Lakebase)
app/models.py     customers, deployments, deploy runs
app/schemas.py    request/response models and validation
app/security.py   admin key and deployment-token checks
app/github.py     workflow dispatch
migrations/       Alembic history
tests/            pytest suite
databricks.yml    Databricks App + Lakebase bundle
```
