"""Databricks foundation models as plain chat assistants, plus their cost.

Three facts about foundation-model endpoints drove this design, all verified on
this workspace rather than assumed:

1. **They have no endpoint `id`.** `GET /serving-endpoints/databricks-claude-sonnet-5`
   returns no id, so `/api/2.0/permissions/serving-endpoints/<id>` cannot be
   addressed at all - Databricks offers **no per-user ACL** for them.
2. **They cannot be tagged.** PATCH .../tags returns 404 RESOURCE_DOES_NOT_EXIST,
   so the tag-based capability mechanism used for real agents is unavailable.
3. **They cost money per token**, billed as DBUs against
   PREMIUM_ANTHROPIC_MODEL_SERVING and friends.

Because of (1) and (2) the switch cannot live in Databricks permissions or tags.
It lives in a **workspace group** instead: the group's existence is the on/off
state and its membership is the audience. That keeps the portal storing nothing
of its own, reuses the mechanism already used for agent sets, is visible and
auditable in the Databricks admin UI, and fails closed - no group, no models.

**This is curation, not security.** Any workspace user whose token carries the
model-serving scope can call these models directly outside the portal, and
Databricks provides no way to stop that per user. The switch decides what the
portal offers and therefore what casual users will actually spend; it is not an
access control. Said plainly in the admin UI too.
"""
from __future__ import annotations

import os

import httpx

from dbx import DbxError, call, host

# Existence = enabled. Membership = audience.
LLM_GROUP = os.environ.get("PORTAL_LLM_GROUP", "portal-llm-users")
CHAT_TASK = "llm/v1/chat"

# Anything cheap enough to hand a casual user without much thought. Purely a
# UI hint - the real bill depends on tokens, so the admin panel shows actual
# spend rather than relying on this.
#
# Matched with a leading dash on purpose: a bare "20b" also matches
# "gpt-oss-120b", which is emphatically not a small model.
LIGHT = ("haiku", "-8b", "-20b", "gemma", "-0-6b")


def _groups(app_tok: str) -> list:
    return call("GET", "/api/2.0/preview/scim/v2/Groups?count=200", app_tok).get("Resources", [])


def group_state(app_tok: str) -> dict:
    """Whether models are switched on, and who for.

    Must run as the app's service principal: reading groups needs a `scim`
    scope, and no such scope can be granted to a user's OBO token (`scim`,
    `iam.scim`, `iam.groups` and `iam.users` are all rejected as invalid).

    The catch is that the group listing can come back without its members, so
    an empty list does not prove the group is empty. `members_visible` says
    which case we are in, and the admin UI words itself accordingly rather than
    claiming nobody has been added.
    """
    for g in _groups(app_tok):
        if g.get("displayName") != LLM_GROUP:
            continue
        gid = g.get("id")
        members = [m.get("display") or m.get("value") for m in g.get("members") or []]
        if not members and gid:
            # The list endpoint often omits members; fetching the group
            # directly sometimes includes them.
            try:
                one = call("GET", "/api/2.0/preview/scim/v2/Groups/" + str(gid), app_tok)
                members = [m.get("display") or m.get("value") for m in one.get("members") or []]
            except DbxError:
                pass
        members = [m for m in members if m]
        return {
            "enabled": True,
            "group_id": gid,
            "members": members,
            "members_visible": bool(members),
        }
    return {"enabled": False, "group_id": None, "members": [], "members_visible": True}


def set_enabled(on: bool, app_tok: str) -> dict:
    """Flip the switch by creating or deleting the group.

    Deleting drops the membership with it, which is the intended behaviour: a
    re-enable should not silently restore an audience an admin last saw months
    ago.
    """
    from access import NOT_ADMIN

    state = group_state(app_tok)
    try:
        if on and not state["enabled"]:
            g = call(
                "POST", "/api/2.0/preview/scim/v2/Groups", app_tok, json={"displayName": LLM_GROUP}
            )
            return {"enabled": True, "group_id": g.get("id"), "members": [], "members_visible": True}
        if not on and state["enabled"]:
            call("DELETE", "/api/2.0/preview/scim/v2/Groups/" + state["group_id"], app_tok)
            return {"enabled": False, "group_id": None, "members": [], "members_visible": True}
    except DbxError as exc:
        # The switch is a group, so flipping it needs the same admin rights.
        if exc.status == 403 and "only accessible by admins" in str(exc):
            raise DbxError(NOT_ADMIN, 403) from exc
        raise
    return state


def _display(ep: dict) -> tuple:
    ents = (ep.get("config") or {}).get("served_entities") or []
    fm = (ents[0].get("foundation_model") or {}) if ents else {}
    name = ep.get("name", "")
    return (
        fm.get("display_name") or name.replace("databricks-", "").replace("-", " ").title(),
        fm.get("description") or "",
    )


def models(tok: str) -> list:
    """Every chat-capable foundation model, cheapest-looking first.

    Embedding endpoints are excluded - they are not conversational and would
    only confuse someone looking for a chatbot.
    """
    data = call("GET", "/api/2.0/serving-endpoints", tok)
    out = []
    for ep in data.get("endpoints", []):
        if ep.get("task") != CHAT_TASK:
            continue
        name = ep.get("name", "")
        label, desc = _display(ep)
        light = any(h in name.lower() for h in LIGHT)
        out.append(
            {
                "name": name,
                "id": None,
                "task": CHAT_TASK,
                "kind": "model",
                "kind_label": "Chat model",
                "kind_hint": "A general-purpose chat model. Usage is billed per token.",
                "display_name": label,
                # The provider blurb is long and full of legal text; a short,
                # honest line is more use to the person choosing.
                "blurb": ("Lower cost" if light else "Higher cost") + " · billed per token",
                "provider_note": desc[:400],
                "ready": (ep.get("state") or {}).get("ready") == "READY",
                "state": (ep.get("state") or {}).get("ready", "UNKNOWN"),
                "metered": True,
                "light": light,
                "supports_files": False,
                "accepts": [],
                "upload_volume": "",
            }
        )
    out.sort(key=lambda m: (not m["light"], m["display_name"]))
    return out


def visible_models(who: dict, app_tok: str, user_tok: str) -> dict:
    """Models this user may use in the portal, plus why not, if not."""
    state = group_state(app_tok)
    if not state["enabled"]:
        return {"enabled": False, "allowed": False, "models": [], "reason": "switched off"}
    if LLM_GROUP not in (who.get("groups") or []):
        return {
            "enabled": True,
            "allowed": False,
            "models": [],
            "reason": "you are not in the " + LLM_GROUP + " group",
        }
    return {
        "enabled": True,
        "allowed": True,
        "reason": "",
        "models": [dict(m, access_reason="via group " + LLM_GROUP) for m in models(user_tok)],
    }


# ------------------------------------------------------------------------ cost


def _warehouse(user_tok: str) -> str:
    data = call("GET", "/api/2.0/sql/warehouses", user_tok)
    whs = data.get("warehouses", []) or []
    if not whs:
        raise DbxError("no SQL warehouse is available to read billing data", 503)
    for w in whs:
        if w.get("state") == "RUNNING":
            return w["id"]
    return whs[0]["id"]


def _sql(query: str, user_tok: str, warehouse: str) -> list:
    """Run one statement as the signed-in user; they need system.billing access."""
    try:
        resp = httpx.post(
            host() + "/api/2.0/sql/statements",
            headers={"Authorization": "Bearer " + user_tok},
            json={"statement": query, "warehouse_id": warehouse, "wait_timeout": "50s"},
            timeout=120,
        )
    except httpx.RequestError as exc:
        raise DbxError("could not reach SQL: " + str(exc), 504) from exc
    if resp.status_code >= 400:
        raise DbxError((resp.text or "")[:300], resp.status_code)
    body = resp.json()
    state = (body.get("status") or {}).get("state")
    if state != "SUCCEEDED":
        msg = ((body.get("status") or {}).get("error") or {}).get("message") or state
        raise DbxError("billing query failed: " + str(msg), 502)
    return (body.get("result") or {}).get("data_array") or []


# Grouped by endpoint, not by SKU: `usage_metadata.endpoint_name` says which
# model was actually paid for, which is the question people ask. Note that
# agent endpoints (mas-*) never appear here - an agent's thinking is billed
# against whichever foundation model it runs on, so cost cannot be attributed
# to an individual agent. Verified: no mas-* endpoint has any billing rows.
SPEND_SQL = """
SELECT COALESCE(u.usage_metadata.endpoint_name, u.sku_name) AS item,
       SUM(u.usage_quantity)                                AS dbus,
       SUM(u.usage_quantity * p.pricing.default)            AS usd
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices p
       ON p.sku_name = u.sku_name AND p.price_end_time IS NULL
WHERE u.billing_origin_product = 'MODEL_SERVING'
  AND u.usage_date >= date_sub(current_date(), {days})
GROUP BY 1
HAVING SUM(u.usage_quantity) > 0
ORDER BY usd DESC NULLS LAST
LIMIT 25
"""


def _pretty_model(name: str) -> str:
    """`databricks-claude-fable-5` -> `Claude Fable 5`."""
    if not name.startswith("databricks-"):
        return name
    stem = name[len("databricks-") :]
    words = []
    for part in stem.split("-"):
        if part.isdigit():
            words.append(part)
        elif part in ("oss", "bge", "gte"):
            words.append(part.upper())
        else:
            words.append(part.capitalize())
    # "Claude Fable 5 1" reads worse than "Claude Fable 5.1"
    out = " ".join(words)
    return out.replace(" 5 1", " 5.1").replace(" 4 5", " 4.5").replace(" 4 6", " 4.6").replace(
        " 4 7", " 4.7"
    ).replace(" 4 8", " 4.8").replace(" 4 1", " 4.1").replace(" 3 1", " 3.1").replace(
        " 3 3", " 3.3"
    )


def spend(user_tok: str, days: int = 30) -> dict:
    """Real model-serving spend, from the workspace's own billing tables.

    Read as the signed-in admin, because system.billing is granted to people
    rather than to the app. Returns a `note` instead of raising when billing is
    unreadable - a cost panel that cannot load must not break the console.
    """
    days = 7 if days <= 7 else (90 if days >= 90 else 30)
    try:
        wh = _warehouse(user_tok)
        rows = _sql(SPEND_SQL.format(days=days), user_tok, wh)
    except DbxError as exc:
        return {"days": days, "available": False, "note": str(exc), "lines": [], "total_usd": None}

    lines, total = [], 0.0
    for r in rows:
        item = r[0] or ""
        dbus = float(r[1]) if r[1] is not None else 0.0
        usd = float(r[2]) if r[2] is not None else None
        if usd:
            total += usd
        lines.append(
            {
                "sku": _pretty_model(item),
                "raw": item,
                "dbus": round(dbus, 2),
                "usd": round(usd, 2) if usd else None,
            }
        )
    return {
        "days": days,
        "available": True,
        "note": "",
        "lines": lines,
        "total_usd": round(total, 2),
        "currency": "USD",
    }
