"""Activity log, built from Databricks' own audit records.

Reads `system.access.audit` - the workspace's real audit trail - rather than
keeping a log of its own. So it shows everything that happened, including
changes made directly in Databricks, not just changes made through the portal.

Two things to know about what is and is not in there, both checked:

* **Access changes are recorded in detail.** `changeInferenceEndpointAcl` carries
  the endpoint id, the permission set granted, and who it was granted to, so a
  proper sentence can be written about it.
* **Agent conversations are NOT recorded.** The only serving-inference action in
  the audit table is the ACL change; invocations do not appear. So this log
  answers "who changed access" and never "who asked what". Usage volume lives in
  the billing tables instead (see `llm.spend`).

Raw records are unreadable to anyone who does not work with Databricks daily -
`changeInferenceEndpointAcl / aclPermissionSet=View,Query,Manage /
targetUserId=145168881142316`. Everything here exists to turn that into
"Alice gave Bob permission to use and manage Weather Forecast Assistant".
"""
from __future__ import annotations

import json

import httpx

from dbx import DbxError, call, host

# What a viewer can ask to see. Ordered from "matters to everyone" down to
# "only matters when something is broken".
CATEGORIES = ("access", "admin", "system")

CATEGORY_LABEL = {
    "access": "Access changes",
    "admin": "Portal and workspace administration",
    "system": "Technical activity",
}

# service_name/action_name -> category. Anything unlisted falls into "system",
# which is why that category is off by default: it is mostly sign-ins, token
# minting and internal permission checks.
ACCESS_ACTIONS = {
    ("serverlessRealTimeInference", "changeInferenceEndpointAcl"),
    ("unityCatalog", "updatePermissions"),
    ("accounts", "createGroup"),
    ("accounts", "removeGroup"),
    ("accounts", "updateGroup"),
    ("accounts", "addPrincipalToGroup"),
    ("accounts", "removePrincipalFromGroup"),
    ("workspace", "changeIamAcl"),
    ("apps", "changeAppsAcl"),
}
ADMIN_ACTIONS = {
    ("apps", "createApp"),
    ("apps", "deployApp"),
    ("apps", "updateApp"),
    ("apps", "startApp"),
    ("apps", "stopApp"),
    ("apps", "deleteApp"),
    ("serverlessRealTimeInference", "createServingEndpoint"),
    ("serverlessRealTimeInference", "updateServingEndpoint"),
    ("serverlessRealTimeInference", "deleteServingEndpoint"),
}

# "View,Query,Manage" means nothing to a non-engineer.
PERMISSION_WORDS = {
    "manage": "use and manage",
    "query": "use",
    "view": "only see",
}


def _levels(acl_set: str) -> str:
    parts = [p.strip().lower() for p in (acl_set or "").split(",") if p.strip()]
    for key in ("manage", "query", "view"):
        if key in parts:
            return PERMISSION_WORDS[key]
    return "no access"


class Lookups:
    """Turns the ids in an audit record into names people recognise.

    Built once per request with the app identity, because resolving a numeric
    user id needs directory access an end user's token does not have.
    """

    def __init__(self, app_tok: str):
        self.people: dict = {}
        self.groups: dict = {}
        self.agents: dict = {}
        try:
            users = call(
                "GET",
                "/api/2.0/preview/scim/v2/Users?count=500&attributes=id,userName,displayName",
                app_tok,
            ).get("Resources", [])
            for u in users:
                label = u.get("displayName") or u.get("userName") or ""
                if u.get("id"):
                    self.people[str(u["id"])] = label
                # Audit records identify the actor by email, so index that too.
                if u.get("userName"):
                    self.people[u["userName"]] = label
        except DbxError:
            pass
        try:
            for g in call(
                "GET", "/api/2.0/preview/scim/v2/Groups?count=200", app_tok
            ).get("Resources", []):
                if g.get("id"):
                    self.groups[str(g["id"])] = g.get("displayName") or ""
        except DbxError:
            pass
        try:
            for sp in call(
                "GET",
                "/api/2.0/preview/scim/v2/ServicePrincipals?count=200",
                app_tok,
            ).get("Resources", []):
                label = sp.get("displayName") or ""
                if sp.get("id"):
                    self.people[str(sp["id"])] = label
                # A service principal appears in the audit trail as its bare
                # application id, which reads like line noise without this.
                if sp.get("applicationId"):
                    self.people[sp["applicationId"]] = label
        except DbxError:
            pass
        try:
            import access as access_mod

            for a in access_mod.all_agents(app_tok):
                if a.get("id"):
                    self.agents[str(a["id"])] = a["display_name"]
        except Exception:
            pass

    def person(self, pid: str) -> str:
        pid = str(pid or "")
        return self.people.get(pid) or self.groups.get(pid) or ("someone (id " + pid + ")" if pid else "someone")

    def agent(self, rid: str) -> str:
        rid = str(rid or "")
        return self.agents.get(rid) or ("an agent (id " + rid[:8] + "…)" if rid else "an agent")

    def actor(self, email: str) -> str:
        if not email:
            return "Someone"
        # Service principals show up as a bare UUID, which reads badly.
        if "@" not in email and len(email) >= 32:
            return self.people.get(email) or "An automated process"
        return self.people.get(email) or email


def _sentence(service: str, action: str, params: dict, who: str, look: Lookups) -> tuple:
    """One plain sentence, plus what kind of change it was.

    The kind is decided here rather than in the UI: whether an ACL change was a
    grant or a removal is only visible in the permission set, not in the action
    name, so guessing from `action_name` in the browser gets it wrong.
    """
    if service == "serverlessRealTimeInference" and action == "changeInferenceEndpointAcl":
        target = look.person(params.get("targetUserId") or params.get("targetGroupId") or "")
        agent = look.agent(params.get("resourceId") or "")
        level = _levels(params.get("aclPermissionSet") or "")
        if level == "no access":
            return f"{who} removed {target}'s access to {agent}.", "revoke"
        return f"{who} gave {target} permission to {level} {agent}.", "grant"

    if service == "unityCatalog" and action == "updatePermissions":
        what = params.get("securable_full_name") or params.get("full_name_arg") or "some data"
        return f"{who} changed who can read {what}.", "data"

    if service == "unityCatalog" and action == "getEffectivePermissions":
        return f"{who} checked data permissions.", "other"

    if service == "accounts":
        gname = look.groups.get(str(params.get("targetGroupId") or "")) or params.get("targetGroupName") or "a group"
        if action == "createGroup":
            return f"{who} created the group {gname}.", "grant"
        if action == "removeGroup":
            return f"{who} deleted the group {gname}.", "revoke"
        if action == "updateGroup":
            return f"{who} changed the group {gname}.", "group"
        if action == "addPrincipalToGroup":
            return f"{who} added {look.person(params.get('targetUserId') or '')} to the group {gname}.", "grant"
        if action == "removePrincipalFromGroup":
            return f"{who} removed {look.person(params.get('targetUserId') or '')} from the group {gname}.", "revoke"

    if service == "apps":
        app = params.get("app_name") or params.get("name") or "the portal"
        verbs = {
            "createApp": "created",
            "deployApp": "deployed a new version of",
            "updateApp": "changed the settings of",
            "startApp": "started",
            "stopApp": "stopped",
            "deleteApp": "deleted",
            "changeAppsAcl": "changed who can open",
        }
        if action in verbs:
            kind = "revoke" if action in ("stopApp", "deleteApp") else "deploy"
            return f"{who} {verbs[action]} {app}.", kind

    # Everything else: say what it was without pretending to understand it.
    return f"{who} performed {action} ({service}).", "other"


SQL = """
SELECT event_time,
       COALESCE(user_identity.email, '')            AS actor,
       service_name,
       action_name,
       to_json(request_params)                      AS params,
       COALESCE(response.status_code, 0)            AS code,
       COALESCE(response.error_message, '')         AS err,
       COALESCE(source_ip_address, '')              AS ip
FROM system.access.audit
WHERE event_date >= date_sub(current_date(), {days})
  AND workspace_id = '{ws}'
  {where}
ORDER BY event_time DESC
LIMIT {limit}
"""


def _in_clause(pairs) -> str:
    ors = [
        "(service_name = '%s' AND action_name = '%s')" % (s, a)
        for s, a in sorted(pairs)
    ]
    return "(" + " OR ".join(ors) + ")"


def _sql(days: int, cats: list, limit: int, ws: str) -> str:
    wanted = set()
    if "access" in cats:
        wanted |= ACCESS_ACTIONS
    if "admin" in cats:
        wanted |= ADMIN_ACTIONS
    if "system" in cats:
        # Everything, including actions this module has never heard of.
        where = ""
    elif wanted:
        where = "AND " + _in_clause(wanted)
    else:
        where = "AND 1 = 0"
    return SQL.format(days=days, where=where, limit=limit, ws=ws)


def _run(query: str, user_tok: str, warehouse: str) -> list:
    try:
        resp = httpx.post(
            host() + "/api/2.0/sql/statements",
            headers={"Authorization": "Bearer " + user_tok},
            json={"statement": query, "warehouse_id": warehouse, "wait_timeout": "50s"},
            timeout=140,
        )
    except httpx.RequestError as exc:
        raise DbxError("could not reach SQL: " + str(exc), 504) from exc
    if resp.status_code >= 400:
        raise DbxError((resp.text or "")[:300], resp.status_code)
    body = resp.json()
    state = (body.get("status") or {}).get("state")
    if state != "SUCCEEDED":
        msg = ((body.get("status") or {}).get("error") or {}).get("message") or state
        raise DbxError("audit query failed: " + str(msg), 502)
    return (body.get("result") or {}).get("data_array") or []


def entries(
    user_tok: str,
    app_tok: str,
    days: int = 7,
    cats: list | None = None,
    limit: int = 200,
    workspace_id: str = "",
) -> dict:
    """Recent activity as readable sentences.

    Read as the signed-in admin: `system.access` is granted to people, not to
    the app. Returns a note instead of raising when the table is unreadable, so
    a missing audit schema cannot take the whole console down.
    """
    days = 1 if days < 1 else (90 if days > 90 else days)
    cats = [c for c in (cats or ["access"]) if c in CATEGORIES] or ["access"]
    limit = 50 if limit < 50 else (500 if limit > 500 else limit)

    from llm import _warehouse

    try:
        wh = _warehouse(user_tok)
        rows = _run(_sql(days, cats, limit, workspace_id), user_tok, wh)
    except DbxError as exc:
        return {"available": False, "note": str(exc), "days": days, "cats": cats, "events": []}

    look = Lookups(app_tok)
    out = []
    for r in rows:
        try:
            params = json.loads(r[4]) if r[4] else {}
        except (json.JSONDecodeError, TypeError):
            params = {}
        service, action = r[2] or "", r[3] or ""
        actor = look.actor(r[1] or "")
        cat = (
            "access"
            if (service, action) in ACCESS_ACTIONS
            else "admin"
            if (service, action) in ADMIN_ACTIONS
            else "system"
        )
        # Databricks maintains its own internal catalogs; permission churn on
        # those is plumbing, not something an admin needs to review.
        if cat == "access" and "__databricks_internal" in json.dumps(params):
            cat = "system"
        # The SQL filters by action; the category is only known once params are
        # read, so re-categorised rows have to be dropped here or internal
        # plumbing leaks into the Access view.
        if cat not in cats:
            continue
        code = int(r[5] or 0)
        message, kind = _sentence(service, action, params, actor, look)
        out.append(
            {
                "at": str(r[0]),
                "actor": actor,
                "category": cat,
                "kind": kind,
                "message": message,
                "ok": code == 0 or 200 <= code < 400,
                "status": code,
                "error": r[6] or "",
                # Kept for the "show technical detail" switch. Nobody should
                # need this, but when something is wrong it is the only thing
                # that helps.
                "detail": {
                    "service": service,
                    "action": action,
                    "ip": r[7] or "",
                    "params": params,
                },
            }
        )
    return {"available": True, "note": "", "days": days, "cats": cats, "events": out}
