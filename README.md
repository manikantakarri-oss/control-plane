# Agentic AI Platform

Two deployables, one repository:

| | What | Where it runs | Owner |
| --- | --- | --- | --- |
| [`data-plane/`](data-plane/) | **Agent Portal** — a front door to a workspace's Databricks agents (FastAPI + Next.js static UI) | a Databricks App **inside each customer's workspace** | deployed per customer |
| [`control-plane/`](control-plane/) | registry of customers and deployments; triggers deploys; tracks health and versions | provider infrastructure (container + Postgres) | provider |

```
 PROVIDER                                                 CUSTOMER WORKSPACE (security boundary)
 ┌───────────────┐  1. dispatch   ┌──────────────────┐    ┌──────────────────────────────────┐
 │ Control Plane │ ─────────────▶ │ GitHub Actions   │ 2. │  Agent Portal (Databricks App)   │
 │ customers,    │ ◀───────────── │ per-customer     │ ──▶│   ├─ user's OBO token → agents   │
 │ deployments,  │  3. run status │ Environment with │ deploy └─ app SP → ACLs, groups     │
 │ deploy runs,  │                │ Databricks creds │    │                                  │
 │ health        │ ◀──────────────────────────────────────│  4. heartbeat (outbound only)    │
 └───────────────┘                                        └──────────────────────────────────┘
```

- The Control Plane holds **metadata only**. It never holds customer credentials and never
  connects to a customer workspace; its one outbound call is the workflow dispatch to GitHub.
- Each customer's Databricks deploy credentials live in a **GitHub Environment** named for that
  customer. Add required reviewers on the environment to make every deploy need approval.
- Before touching anything, a deploy checks that the environment's credentials reach the
  workspace the Control Plane has on record, so a customer's release or token can never land
  in another customer's workspace.
- The portal stores nothing of its own; all state is Databricks-native and every agent call runs
  under the signed-in user's own token. See [data-plane/README.md](data-plane/README.md).
- The portal's only link back is a best-effort heartbeat using a per-deployment token kept in
  the customer's own secret scope. If the Control Plane is down, the portal is unaffected.

## Layout

```
databricks.yml                     Asset Bundle: deploys data-plane/ into a customer workspace
.github/workflows/
  deploy-customer.yml              the deploy, triggered by the Control Plane (or by hand)
  ci.yml                           lint + tests for both, UI build, optional bundle validate
scripts/
  setup-provider.sh                host the Control Plane as a Databricks App (one-time / updates)
  onboard-customer.sh              register a customer (and, from a laptop, deploy it)
  provision-credentials.sh         issue heartbeat credentials into the customer's secret scope
  deploy.sh                        deploy / upgrade the bundle
  lib.sh                           shared helpers
control-plane/                     provider service (see its README), with its own databricks.yml
data-plane/                        Agent Portal app (see its README)
```

## One-time setup

1. **Push this repository to GitHub.**
2. **Host the Control Plane.** The simplest home is your own (provider) Databricks workspace,
   as a Databricks App backed by Lakebase. With a CLI profile for that workspace:
   ```bash
   export GITHUB_DISPATCH_TOKEN=<fine-grained token: this repo, "Actions: Read and write" only>
   scripts/setup-provider.sh --profile <provider-profile> --github-repo <owner>/<repo> --github-secrets
   ```
   This creates:
   - the Lakebase database and the app
   - an admin key
   - two service principals: one that GitHub Actions calls the Control Plane as, and one that
     customer portals send heartbeats as. Each holds only `CAN_USE` on the app.

   It then writes every secret the deploy workflow needs into the GitHub repo, without printing
   any of them. Re-running it is safe and updates the app.

   Alternatively, run the container anywhere else (see [control-plane/README.md](control-plane/README.md))
   and set the `CONTROL_PLANE_URL` / `CONTROL_PLANE_ADMIN_KEY` repository secrets yourself.

## Onboarding a customer

1. **Customer side:** they create a service principal in their workspace with rights to create
   an app and a secret scope, and give you its OAuth client id and secret.
2. **GitHub:** Settings → Environments → **New environment**, e.g. `acme`, with secrets
   `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`. Optionally add
   required reviewers.
3. **Register** it in the Control Plane (`cp_api` handles auth; see
   [Calling the Control Plane](#calling-the-control-plane)):
   ```bash
   source scripts/lib.sh
   cp_api POST /api/v1/deployments \
     '{"customer_name":"Acme Corp","workspace_host":"https://<customer-workspace>","github_environment":"acme"}'
   ```
4. **Deploy** from the Control Plane — first time with `mode=onboard`:
   ```bash
   cp_api POST /api/v1/deployments/<id>/deploy '{"mode":"onboard"}'
   ```
   This starts **Deploy customer** in GitHub Actions. It issues heartbeat credentials into the
   customer's secret scope, deploys the bundle and starts the app. Follow it with
   `GET /api/v1/deploy-runs/<run-id>` (status `requested → running → succeeded | failed`, with a
   link to the GitHub run). Within a minute the deployment reports `connectivity: online`.
5. **Customer side:** share each agent endpoint with the app's service principal
   (`data-plane/scripts/sync_agents.py --apply`) — see "Onboarding an agent" in the portal README.

**Upgrades:** `POST /api/v1/deployments/<id>/deploy` with `{"mode":"upgrade"}`. Add
`"ref":"v1.2.0"` to deploy a tag instead of `main`, and `"restart":true` after a scope change.
Only one deploy per deployment runs at a time.

**Rotate a heartbeat token:** deploy with `{"mode":"onboard","restart":true}` — it issues a new
token and writes it into the customer's scope.

A deploy waiting on environment approval that is rejected never reports back; the run stays
`requested` and stops blocking new deploys after `DEPLOY_RUN_TIMEOUT_SECONDS` (default 1 hour).

### Calling the Control Plane

Hosted as a Databricks App, every request must first get through the Apps proxy with a Databricks
login for the provider workspace; the admin key then goes in `X-Admin-Key`. `cp_api` does both:

```bash
export CONTROL_PLANE_URL=<app-url> CONTROL_PLANE_PROFILE=<provider-profile>
export CONTROL_PLANE_ADMIN_KEY=$(databricks secrets get-secret control-plane admin-api-key \
  -p <provider-profile> -o json | python -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['value']).decode())")
source scripts/lib.sh
cp_api GET /api/v1/deployments
```

### Without GitHub (from a laptop)

With a Databricks CLI profile for the customer workspace:

```bash
export CONTROL_PLANE_URL=... CONTROL_PLANE_ADMIN_KEY=...
scripts/onboard-customer.sh --profile acme --customer "Acme Corp"   # register + credentials + deploy
scripts/deploy.sh --profile acme [--build-ui] [--restart]           # upgrades
```

## Development

```bash
# Control Plane
cd control-plane && pip install -r requirements-dev.txt && pytest

# Agent Portal
cd data-plane && pip install -r requirements-dev.txt && pytest
cd data-plane/ui && npm ci && npm run build
```

To deploy the portal to your own workspace for testing, the `control-plane` secret must
exist in the scope first (register against a dev Control Plane and run
`scripts/provision-credentials.sh`), then `databricks bundle deploy -t dev -p <profile>`.
