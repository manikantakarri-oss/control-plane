"""Agentic AI Platform - Control Plane.

Provider-owned registry of Data Plane deployments. It holds customer and
deployment metadata only, and it never initiates a connection into a customer
environment: every Data Plane reports in, outbound, on its own schedule.

Deploys are requested here and executed by GitHub Actions, which holds each
customer's Databricks credentials. The Control Plane's one outbound call is the
workflow dispatch to GitHub.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import __version__
from app.config import Settings, get_settings
from app.db import get_db
from app.github import DispatchError, dispatch_deploy
from app.models import Customer, Deployment, DeployRun, utcnow
from app.schemas import (
    Connectivity,
    CustomerResponse,
    DeploymentCredentials,
    DeploymentList,
    DeploymentRegisterRequest,
    DeploymentResponse,
    DeploymentUpdate,
    DeployRequest,
    DeployRunResponse,
    DeployRunStatus,
    DeployRunStatusUpdate,
    HeartbeatAck,
    HeartbeatRequest,
)
from app.security import deployment_credential, hash_token, new_token, require_admin, token_matches

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("control_plane")


def _migrate() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")
    log.info("database migrated to head")


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.auto_migrate:
        _migrate()
    yield


app = FastAPI(
    title="Agentic AI Platform - Control Plane",
    version=__version__,
    lifespan=lifespan,
    # Interactive docs are a development convenience, not a production surface.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

admin = [Depends(require_admin)]


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite drops tzinfo; Postgres keeps it. Everything stored is UTC.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _connectivity(d: Deployment, cfg: Settings) -> Connectivity:
    seen = _aware(d.last_heartbeat_at)
    if seen is None:
        return Connectivity.never_seen
    if utcnow() - seen > timedelta(seconds=cfg.heartbeat_stale_seconds):
        return Connectivity.stale
    return Connectivity.online


def _to_response(d: Deployment, cfg: Settings) -> DeploymentResponse:
    return DeploymentResponse(
        id=d.id,
        customer_id=d.customer_id,
        customer_name=d.customer.name,
        workspace_host=d.workspace_host,
        app_name=d.app_name,
        status=d.status,
        connectivity=_connectivity(d, cfg),
        last_heartbeat_at=_aware(d.last_heartbeat_at),
        last_health_status=d.last_health_status,
        app_version=d.app_version,
        github_environment=d.github_environment,
        created_at=_aware(d.created_at),
        updated_at=_aware(d.updated_at),
    )


def _run_response(r: DeployRun) -> DeployRunResponse:
    return DeployRunResponse(
        id=r.id,
        deployment_id=r.deployment_id,
        mode=r.mode,
        ref=r.ref,
        status=r.status,
        run_url=r.run_url,
        detail=r.detail,
        requested_at=_aware(r.requested_at),
        updated_at=_aware(r.updated_at),
    )


def _get_deployment(db: Session, deployment_id: str) -> Deployment:
    d = db.get(Deployment, deployment_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "deployment not found")
    return d


# --- probes ---------------------------------------------------------------


@app.get("/health", tags=["probes"])
def health():
    """Liveness: the process is up."""
    return {"status": "ok", "version": __version__}


@app.get("/ready", tags=["probes"])
def ready(response: Response, db: Session = Depends(get_db)):
    """Readiness: the database answers."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any DB failure means not ready
        log.exception("readiness check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ok"}


# --- management API (provider staff) --------------------------------------


@app.post(
    "/api/v1/deployments",
    response_model=DeploymentCredentials,
    status_code=status.HTTP_201_CREATED,
    dependencies=admin,
    tags=["deployments"],
)
def register_deployment(
    payload: DeploymentRegisterRequest,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
):
    """Register a Data Plane before it is deployed. The returned
    `deployment_token` goes into the customer's Databricks secret scope; it is
    shown only once."""
    customer = db.scalar(select(Customer).where(Customer.name == payload.customer_name))
    if customer is None:
        customer = Customer(name=payload.customer_name)
        db.add(customer)
        db.flush()

    token = new_token()
    d = Deployment(
        customer_id=customer.id,
        workspace_host=payload.workspace_host,
        app_name=payload.app_name,
        github_environment=payload.github_environment,
        token_hash=hash_token(token),
    )
    db.add(d)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a deployment with this workspace_host and app_name already exists"
        ) from None
    db.refresh(d)
    log.info("deployment registered id=%s customer=%s host=%s", d.id, customer.name, d.workspace_host)
    return DeploymentCredentials(**_to_response(d, cfg).model_dump(), deployment_token=token)


@app.get("/api/v1/deployments", response_model=DeploymentList, dependencies=admin, tags=["deployments"])
def list_deployments(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    customer_id: str | None = None,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
):
    q = select(Deployment)
    if customer_id:
        q = q.where(Deployment.customer_id == customer_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Deployment.created_at).limit(limit).offset(offset)).all()
    return DeploymentList(items=[_to_response(d, cfg) for d in rows], total=total, limit=limit, offset=offset)


@app.get(
    "/api/v1/deployments/{deployment_id}",
    response_model=DeploymentResponse,
    dependencies=admin,
    tags=["deployments"],
)
def get_deployment(deployment_id: str, db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)):
    return _to_response(_get_deployment(db, deployment_id), cfg)


@app.patch(
    "/api/v1/deployments/{deployment_id}",
    response_model=DeploymentResponse,
    dependencies=admin,
    tags=["deployments"],
)
def update_deployment(
    deployment_id: str,
    payload: DeploymentUpdate,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
):
    d = _get_deployment(db, deployment_id)
    if "github_environment" in payload.model_fields_set:
        d.github_environment = payload.github_environment
    db.commit()
    return _to_response(d, cfg)


@app.post(
    "/api/v1/deployments/{deployment_id}/rotate-token",
    response_model=DeploymentCredentials,
    dependencies=admin,
    tags=["deployments"],
)
def rotate_token(deployment_id: str, db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)):
    """Issue a new heartbeat token; the old one stops working immediately."""
    d = _get_deployment(db, deployment_id)
    token = new_token()
    d.token_hash = hash_token(token)
    db.commit()
    log.info("deployment token rotated id=%s", d.id)
    return DeploymentCredentials(**_to_response(d, cfg).model_dump(), deployment_token=token)


@app.post(
    "/api/v1/deployments/{deployment_id}/decommission",
    response_model=DeploymentResponse,
    dependencies=admin,
    tags=["deployments"],
)
def decommission(deployment_id: str, db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)):
    """Retire a deployment. Its heartbeats are refused from here on."""
    d = _get_deployment(db, deployment_id)
    d.status = "decommissioned"
    db.commit()
    log.info("deployment decommissioned id=%s", d.id)
    return _to_response(d, cfg)


# --- deploys (executed by GitHub Actions) ---------------------------------

ACTIVE = (DeployRunStatus.requested.value, DeployRunStatus.running.value)
TERMINAL = (DeployRunStatus.succeeded.value, DeployRunStatus.failed.value)


@app.post(
    "/api/v1/deployments/{deployment_id}/deploy",
    response_model=DeployRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=admin,
    tags=["deploys"],
)
def request_deploy(
    deployment_id: str,
    payload: DeployRequest,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
):
    """Deploy this Data Plane into its customer workspace (mode=onboard for the
    first install). Returns at once; follow progress on the returned run."""
    if not cfg.deploys_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "deploys are not configured (GITHUB_REPO/GITHUB_TOKEN)"
        )
    d = _get_deployment(db, deployment_id)
    if d.status == "decommissioned":
        raise HTTPException(status.HTTP_409_CONFLICT, "deployment is decommissioned")
    if not d.github_environment:
        raise HTTPException(status.HTTP_409_CONFLICT, "set github_environment on this deployment first (PATCH)")

    cutoff = utcnow() - timedelta(seconds=cfg.deploy_run_timeout_seconds)
    in_flight = db.scalar(
        select(DeployRun).where(
            DeployRun.deployment_id == d.id, DeployRun.status.in_(ACTIVE), DeployRun.requested_at > cutoff
        )
    )
    if in_flight is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"deploy run {in_flight.id} is still {in_flight.status}")

    run = DeployRun(deployment_id=d.id, mode=payload.mode.value, ref=payload.ref or cfg.github_ref)
    db.add(run)
    db.commit()

    inputs = {
        "deploy_run_id": run.id,
        "deployment_id": d.id,
        "mode": run.mode,
        "github_environment": d.github_environment,
        "app_name": d.app_name,
        "restart": "true" if payload.restart else "false",
    }
    try:
        dispatch_deploy(cfg, run.ref, inputs)
    except DispatchError as exc:
        run.status = DeployRunStatus.failed.value
        run.detail = str(exc)[:500]
        db.commit()
        log.error("deploy dispatch failed run=%s: %s", run.id, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, run.detail) from None

    log.info("deploy requested run=%s deployment=%s mode=%s ref=%s", run.id, d.id, run.mode, run.ref)
    return _run_response(run)


@app.get(
    "/api/v1/deployments/{deployment_id}/deploy-runs",
    response_model=list[DeployRunResponse],
    dependencies=admin,
    tags=["deploys"],
)
def list_deploy_runs(deployment_id: str, limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)):
    _get_deployment(db, deployment_id)
    rows = db.scalars(
        select(DeployRun)
        .where(DeployRun.deployment_id == deployment_id)
        .order_by(DeployRun.requested_at.desc())
        .limit(limit)
    ).all()
    return [_run_response(r) for r in rows]


@app.get("/api/v1/deploy-runs/{run_id}", response_model=DeployRunResponse, dependencies=admin, tags=["deploys"])
def get_deploy_run(run_id: str, db: Session = Depends(get_db)):
    run = db.get(DeployRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "deploy run not found")
    return _run_response(run)


@app.post(
    "/api/v1/deploy-runs/{run_id}/status",
    response_model=DeployRunResponse,
    dependencies=admin,
    tags=["deploys"],
)
def update_deploy_run(run_id: str, payload: DeployRunStatusUpdate, db: Session = Depends(get_db)):
    """Progress report from the deploy workflow."""
    run = db.get(DeployRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "deploy run not found")
    if run.status in TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, f"deploy run already {run.status}")
    if payload.status == DeployRunStatus.requested:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "status must be running, succeeded or failed")
    run.status = payload.status.value
    if payload.run_url:
        run.run_url = payload.run_url
    if payload.detail is not None:
        run.detail = payload.detail
    db.commit()
    log.info("deploy run=%s -> %s", run.id, run.status)
    return _run_response(run)


@app.get("/api/v1/customers", response_model=list[CustomerResponse], dependencies=admin, tags=["customers"])
def list_customers(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Customer, func.count(Deployment.id)).outerjoin(Deployment).group_by(Customer.id).order_by(Customer.name)
    ).all()
    return [
        CustomerResponse(id=c.id, name=c.name, created_at=_aware(c.created_at), deployment_count=n) for c, n in rows
    ]


# --- Data Plane API (outbound calls from customer environments) -----------


@app.post(
    "/api/v1/deployments/{deployment_id}/heartbeat",
    response_model=HeartbeatAck,
    tags=["data-plane"],
)
def heartbeat(
    deployment_id: str,
    payload: HeartbeatRequest,
    token: str = Depends(deployment_credential),
    db: Session = Depends(get_db),
):
    """Called by the Data Plane on its own schedule. The response carries no
    deployment details, so the endpoint cannot be used to read anything."""
    d = db.get(Deployment, deployment_id)
    # Same answer for unknown id and wrong token: do not confirm which ids exist.
    if d is None or not token_matches(token, d.token_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing credentials")
    if d.status == "decommissioned":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "deployment is decommissioned")

    now = utcnow()
    d.last_heartbeat_at = now
    d.last_health_status = payload.health_status.value
    d.app_version = payload.app_version
    if d.status == "registered":
        d.status = "active"
    db.commit()
    return HeartbeatAck(received_at=now)
