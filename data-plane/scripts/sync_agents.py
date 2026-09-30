"""Copy what Agent Bricks already knows about each agent into endpoint tags.

Why this exists: Databricks already stores the name and description whoever
built an agent typed, at `/api/2.1/supervisor-agents` and
`/api/2.1/knowledge-assistants`. But the deployed portal **cannot read those
APIs** - there is no requestable Databricks Apps user scope for them
(`supervisor-agents`, `knowledge-assistants` and `agent-bricks` are all
rejected as invalid), so neither the signed-in user's OBO token nor the app's
service principal is authorised.

Endpoint tags, on the other hand, the portal reads fine - and tags take
precedence when the catalog is built. So run this from a workstation with
admin CLI auth to push the real values in.

It syncs two things:
  * display_name / blurb  - the name and description the builder typed
  * upload_volume         - discovered from the agent's own tools. An agent
    with a `volume` tool is one that reads uploaded files, so that volume is
    where the portal should put them. This is what makes "Attach a file"
    appear without anyone configuring it by hand.
  * agent_id              - the Agent Bricks id. Agent Bricks agents keep their
    real permission list at /api/2.0/permissions/supervisor-agents/<agent_id>,
    NOT on the serving endpoint, and that agent-level list is what grants
    access. Without this tag the portal would manage the wrong list and its
    "revoke" would not actually revoke.

    python scripts/sync_agents.py                 # show what would change
    python scripts/sync_agents.py --apply         # fill in anything missing
    python scripts/sync_agents.py --apply --force # also overwrite values set in the portal

Re-run it after building a new agent. By default it only fills gaps, so a name
an admin typed in the portal is never clobbered and repeat runs are harmless.
"""
from __future__ import annotations

import os
import sys

# Runs from a workstation, reusing the portal's own Databricks access layer.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from access import BRICKS_APIS, _tags  # noqa: E402
from dbx import DbxError, call, host  # noqa: E402

PROFILE_HINT = "set DATABRICKS_CONFIG_PROFILE if this is not the right workspace"


def bricks_agents(tok: str) -> dict:
    """endpoint_name -> {display_name, description} from the Agent Bricks APIs."""
    found: dict = {}
    headers = {"Accept": "application/json"}
    wid = os.environ.get("DATABRICKS_WORKSPACE_ID", "")
    if wid:
        headers["X-Databricks-Workspace-Id"] = wid
    for path, key in BRICKS_APIS:
        try:
            data = call("GET", path, tok, headers=headers)
        except DbxError as exc:
            print("  ! " + path + " unavailable: " + str(exc)[:120])
            continue
        for a in data.get(key) or []:
            ep = a.get("endpoint_name")
            if not ep:
                continue
            found[ep] = {
                "display_name": (a.get("display_name") or "").strip(),
                "description": (a.get("description") or "").strip(),
                "upload_volume": _volume_tool(a, path, tok, headers),
                # The agent's own id. Its permission list - not the serving
                # endpoint's - is what actually governs access, so the portal
                # needs this to manage the right thing.
                "agent_id": str(a.get("supervisor_agent_id") or a.get("id") or "").strip(),
            }
    return found


def _volume_tool(agent: dict, path: str, tok: str, headers: dict) -> str:
    """The Unity Catalog volume this agent reads uploads from, if it has one."""
    aid = agent.get("supervisor_agent_id") or agent.get("id") or ""
    if not aid or "supervisor-agents" not in path:
        return ""
    try:
        data = call("GET", path + "/" + str(aid) + "/tools", tok, headers=headers)
    except DbxError:
        return ""
    for t in data.get("tools") or []:
        if t.get("tool_type") == "volume":
            name = (t.get("volume") or {}).get("name") or ""
            if name:
                return name
    return ""


PORTAL_APP = os.environ.get("PORTAL_APP_NAME", "agent-portal")


def portal_sp(tok: str) -> str:
    """The portal app's service-principal client id, looked up not hard-coded."""
    try:
        app = call("GET", "/api/2.0/apps/" + PORTAL_APP, tok)
    except DbxError as exc:
        print("  ! could not read app " + PORTAL_APP + ": " + str(exc)[:100])
        return ""
    return app.get("service_principal_client_id") or ""


def ensure_portal_on_agent(agent_id: str, sp: str, tok: str, apply: bool) -> str:
    """Put the portal on the agent-level permission list.

    This is the list that actually governs an Agent Bricks agent, so the portal
    must be able to read and write it - otherwise granting access through the
    portal fails with "You do not have read access to the agent."
    """
    path = "/api/2.0/permissions/supervisor-agents/" + agent_id
    try:
        acl = call("GET", path, tok).get("access_control_list", [])
    except DbxError as exc:
        return "cannot read agent permissions: " + str(exc)[:80]
    for e in acl:
        if e.get("service_principal_name") == sp:
            return ""
    if not apply:
        return "would add portal to agent permissions"
    try:
        call("PATCH", path, tok, json={
            "access_control_list": [
                {"service_principal_name": sp, "permission_level": "CAN_MANAGE"}
            ]
        })
    except DbxError as exc:
        return "could not add portal: " + str(exc)[:80]
    return "added portal to agent permissions"


def endpoint_tags(tok: str) -> dict:
    data = call("GET", "/api/2.0/serving-endpoints", tok)
    return {e.get("name"): _tags(e) for e in data.get("endpoints", []) if e.get("name")}


def main(argv: list) -> int:
    apply = "--apply" in argv
    force = "--force" in argv
    from dbx import _cli_user_token

    tok = _cli_user_token()
    print("workspace: " + host())
    print(PROFILE_HINT if not os.environ.get("DATABRICKS_CONFIG_PROFILE") else "")

    agents = bricks_agents(tok)
    if not agents:
        print("no Agent Bricks agents found - nothing to sync")
        return 0
    existing = endpoint_tags(tok)

    sp = portal_sp(tok)
    if sp:
        print("portal service principal: " + sp)
        for ep, meta in sorted(agents.items()):
            aid = meta.get("agent_id") or ""
            if not aid:
                continue
            note = ensure_portal_on_agent(aid, sp, tok, apply)
            if note:
                print("  " + ep + ": " + note)
    print()

    planned = []
    for ep, meta in sorted(agents.items()):
        if ep not in existing:
            print("  - " + ep + ": no such serving endpoint, skipping")
            continue
        tags = existing[ep]
        want = {}
        # Fill gaps only. A name an admin typed in the portal is a deliberate
        # choice and outranks the builder's - clobbering it would make this
        # script destructive to re-run. Use --force to push Databricks' names
        # over the top.
        if meta["display_name"] and (force or not tags.get("display_name")):
            if tags.get("display_name") != meta["display_name"]:
                want["display_name"] = meta["display_name"]
        if meta["description"] and (force or not tags.get("blurb")):
            if tags.get("blurb") != meta["description"]:
                want["blurb"] = meta["description"]
        vol = meta.get("upload_volume") or ""
        if vol and (force or not tags.get("upload_volume")):
            if tags.get("upload_volume") != vol:
                want["upload_volume"] = vol
        aid = meta.get("agent_id") or ""
        if aid and tags.get("agent_id") != aid:
            want["agent_id"] = aid
        if want:
            planned.append((ep, want))
            print("  * " + ep)
            for k, v in want.items():
                print("      " + k + " = " + v[:80])
        else:
            print("  = " + ep + " (already current)")

    if not planned:
        print("nothing to change")
        return 0
    if not apply:
        print("\n" + str(len(planned)) + " endpoint(s) would change. Re-run with --apply.")
        return 0

    failed = 0
    for ep, want in planned:
        body = {"add_tags": [{"key": k, "value": v} for k, v in want.items()]}
        try:
            call("PATCH", "/api/2.0/serving-endpoints/" + ep + "/tags", tok, json=body)
            print("  written: " + ep)
        except DbxError as exc:
            failed += 1
            print("  FAILED " + ep + ": " + str(exc)[:160])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
