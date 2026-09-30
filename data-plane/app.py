"""Agent Portal - a friendly front door to Databricks agents.

Two audiences, one workspace:

* an end user signs in and sees only the agents they hold CAN_QUERY on, and
  chats with them;
* a workspace admin grants that access from the UI, using Databricks groups as
  reusable "agent sets".

Nothing is stored here. Every read and write lands on Databricks-native state,
so the portal can be deleted and rebuilt without losing configuration.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import access
import audit
import chat
import control_plane
import files as filestore
import llm
from dbx import DbxError, app_token, auth_mode, user_token

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

HERE = os.path.dirname(os.path.abspath(__file__))
# Built by `npm run build` in ui/ (Next.js static export) and copied here.
# Databricks Apps runs one command, so there is no Node server: the export is
# plain files and FastAPI serves them.
WEB = os.path.join(HERE, "web")


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    # Outbound-only heartbeat to the provider Control Plane, if configured.
    task = asyncio.create_task(control_plane.run_forever()) if control_plane.enabled() else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Agent Portal", version=control_plane.VERSION, docs_url="/api/docs", lifespan=lifespan)


@app.exception_handler(DbxError)
async def _dbx_error(_: Request, exc: DbxError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def _who(forwarded):
    """Resolve the caller. Fails closed if the OBO token is missing in Apps."""
    tok = user_token(forwarded)
    return access.identity(tok), tok


def _require_admin(forwarded):
    who, tok = _who(forwarded)
    if not who["is_admin"]:
        raise HTTPException(403, "Access administration is limited to workspace admins.")
    return who, tok


@app.get("/api/session")
def session(x_forwarded_access_token: str = Header(None)):
    who, _ = _who(x_forwarded_access_token)
    return {**who, "auth_mode": auth_mode()}


@app.get("/api/agents")
def agents(x_forwarded_access_token: str = Header(None)):
    who, tok = _who(x_forwarded_access_token)
    return {"agents": access.visible_agents(who, app_token(), tok)}


def _allowed_agent(who: dict, endpoint: str, user_tok: str = "") -> dict:
    """The agent record, or 403. Needed because the task drives the wire format.

    Foundation models are checked too: they carry no ACL of their own, so the
    portal's group switch is the only thing gating them here.
    """
    for a in access.visible_agents(who, app_token(), user_tok):
        if a["name"] == endpoint:
            return a
    if user_tok:
        for m in llm.visible_models(who, app_token(), user_tok).get("models", []):
            if m["name"] == endpoint:
                return m
    raise HTTPException(403, "You do not have access to that agent.")


@app.post("/api/chat")
def send(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    endpoint = (payload.get("endpoint") or "").strip()
    history = payload.get("history") or []
    uploads = [p for p in (payload.get("files") or []) if isinstance(p, str) and p]
    if not endpoint:
        raise HTTPException(400, "endpoint is required")

    who, tok = _who(x_forwarded_access_token)
    # Confirm the agent is one this user may see before spending a call on it.
    # The invocation below still runs under the user's own token, so this check
    # is a courtesy for clearer errors, not the security boundary.
    agent = _allowed_agent(who, endpoint, tok)
    output_dir = filestore.output_dir_path(agent["output_volume"]) if agent.get("output_volume") else ""
    return chat.ask(endpoint, agent["task"], history, tok, files=uploads, output_dir=output_dir)


@app.post("/api/upload")
async def upload(
    endpoint: str = Form(...),
    file: UploadFile = File(...),
    x_forwarded_access_token: str = Header(None),
):
    """Put a file where a file-driven agent can read it, as the signed-in user."""
    who, tok = _who(x_forwarded_access_token)
    agent = _allowed_agent(who, endpoint.strip(), tok)
    if not agent.get("upload_volume"):
        raise HTTPException(400, "This agent does not accept file uploads.")

    accepts = agent.get("accepts") or []
    name = file.filename or "upload"
    if accepts and not any(name.lower().endswith("." + a.lstrip(".")) for a in accepts):
        raise HTTPException(
            400, "This agent accepts only: " + ", ".join("." + a.lstrip(".") for a in accepts)
        )

    blob = await file.read()
    return filestore.upload(agent["upload_volume"], name, blob, tok)


@app.get("/api/download")
def download(
    endpoint: str,
    path: str,
    x_forwarded_access_token: str = Header(None),
):
    """Fetch a file an agent generated, as the signed-in user."""
    who, tok = _who(x_forwarded_access_token)
    agent = _allowed_agent(who, endpoint.strip(), tok)
    if not agent.get("output_volume"):
        raise HTTPException(400, "This agent does not produce downloadable files.")
    if not filestore.in_volume(path, agent["output_volume"]):
        raise HTTPException(403, "That file is not in this agent's output location.")

    blob = filestore.download(path, tok)
    name = path.rsplit("/", 1)[-1] or "download"
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="' + name.replace('"', "") + '"'},
    )


@app.get("/api/admin/overview")
def overview(x_forwarded_access_token: str = Header(None)):
    who, user_tok = _require_admin(x_forwarded_access_token)
    tok = app_token()
    return {"agents": access.admin_agents(tok, user_tok, who), **access.principals(tok)}


@app.post("/api/admin/grant")
def grant(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    endpoint_id = (payload.get("endpoint_id") or "").strip()
    kind = (payload.get("kind") or "").strip()
    principal = (payload.get("principal") or "").strip()
    if not (endpoint_id and kind and principal):
        raise HTTPException(400, "endpoint_id, kind and principal are required")
    # level omitted or null means revoke
    level = payload.get("level")
    if level not in (None, "", "CAN_QUERY", "CAN_VIEW", "CAN_MANAGE"):
        raise HTTPException(400, "unsupported permission level")

    tok = app_token()
    # Resolve the whole agent: an Agent Bricks agent is governed by its own
    # permission list, not the serving endpoint's, and only the shaped record
    # knows which.
    target = next((a for a in access.all_agents(tok) if a["id"] == endpoint_id), None)
    if not target:
        target = {"id": endpoint_id}
    # The portal must not lend the app's CAN_MANAGE to its users.
    if not access.may_manage(target, who, tok):
        raise HTTPException(
            403,
            "You need Manage permission on this agent in Databricks to change who can use "
            "it. Ask its owner to grant you CAN_MANAGE.",
        )
    return access.set_grant(target, kind, principal, level or None, tok)


@app.post("/api/admin/group")
def new_group(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    # Also grants the group permission to open the portal, so setting up access
    # never needs a terminal.
    return access.create_group(name, app_token(), os.environ.get("DATABRICKS_APP_NAME") or "agent-portal")


@app.post("/api/admin/group-members")
def group_members(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    group_id = (payload.get("group_id") or "").strip()
    users = payload.get("users") or []
    if not group_id:
        raise HTTPException(400, "group_id is required")
    return access.set_group_members(group_id, [u for u in users if u], app_token())


@app.post("/api/admin/meta")
def meta(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    values = {k: payload[k] for k in access.META_TAGS if k in payload}
    if not values:
        raise HTTPException(400, "nothing to update")
    tok = app_token()
    target = next((a for a in access.all_agents(tok) if a["name"] == name), None)
    if not target:
        raise HTTPException(404, "no such agent")
    if not access.may_manage(target, who, tok):
        raise HTTPException(
            403,
            "You need Manage permission on this agent in Databricks to change how it "
            "appears. Ask its owner to grant you CAN_MANAGE.",
        )
    return access.set_meta(name, values, tok)


@app.get("/api/models")
def chat_models(x_forwarded_access_token: str = Header(None)):
    """Foundation models offered as plain chat assistants."""
    who, tok = _who(x_forwarded_access_token)
    return llm.visible_models(who, app_token(), tok)


@app.get("/api/admin/llm")
def llm_state(x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    state = llm.group_state(app_token())
    return {**state, "group": llm.LLM_GROUP}


@app.post("/api/admin/llm")
def llm_set(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Turn model access on or off, and set who gets it."""
    _require_admin(x_forwarded_access_token)
    tok = app_token()
    if "enabled" in payload:
        state = llm.set_enabled(bool(payload["enabled"]), tok)
    else:
        state = llm.group_state(tok)
    if state["enabled"] and isinstance(payload.get("users"), list):
        users = [u.strip() for u in payload["users"] if isinstance(u, str) and u.strip()]
        access.set_group_members(state["group_id"], users, tok)
    return {**llm.group_state(tok), "group": llm.LLM_GROUP}


@app.get("/api/admin/cost")
def llm_cost(days: int = 30, x_forwarded_access_token: str = Header(None)):
    """Actual model-serving spend, read from the workspace billing tables."""
    _who_admin, tok = _require_admin(x_forwarded_access_token)
    return llm.spend(tok, days)


@app.get("/api/admin/audit")
def activity(
    days: int = 7,
    cats: str = "access",
    x_forwarded_access_token: str = Header(None),
):
    """Recent activity, from Databricks' own audit records."""
    _who_admin, user_tok = _require_admin(x_forwarded_access_token)
    wanted = [c.strip() for c in (cats or "").split(",") if c.strip()]
    return audit.entries(
        user_tok,
        app_token(),
        days=days,
        cats=wanted,
        workspace_id=os.environ.get("DATABRICKS_WORKSPACE_ID", "")
        or str(access.identity(user_tok).get("workspace_id") or ""),
    )


@app.get("/api/health")
def health():
    return {"ok": True, "auth_mode": auth_mode(), "version": control_plane.VERSION}


# Mounted last on purpose: every /api route above is registered first and so
# still wins, while everything else falls through to the built UI.
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
