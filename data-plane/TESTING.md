# What we checked, and what we found

Plain-language notes on the Agent Portal. Written for someone who does not use
Databricks every day. Two useful words first, because they come up a lot:

- **Guest list** — every agent has a list of who is allowed to use it. Databricks
  calls this an ACL (access control list). Same thing.
- **"Acts as you"** — when you chat, the portal asks Databricks *using your own
  identity*, not its own. So Databricks can tell who is really asking.

---

## 1. How to run the checks yourself

Two sets of tests. Neither needs anything installed beyond the app itself.

```bash
# Set which workspace to talk to
export DATABRICKS_CONFIG_PROFILE=<your-profile>

# A) Offline checks - no workspace needed, instant.
#    Proves the portal understands every shape of reply an agent can send.
pytest                                  # offline: adapters + Control Plane reporter

# B) Live checks - talks to the real workspace.
python -m uvicorn app:app --port 8811   # in one terminal
SMOKE_AGENT=<an-agent-endpoint> ./scripts/smoke.sh   # in another; 24 checks

# C) The same live checks against the deployed app
SMOKE_AGENT=<an-agent-endpoint> ./scripts/smoke.sh https://<app-url>
```

One of the live checks sends a real question to a real agent, so it costs a
few pennies. Without `SMOKE_AGENT` the two checks that need an agent are skipped.

There is also a helper that copies agent names and settings out of Databricks:

```bash
python scripts/sync_agents.py            # show what it would change
python scripts/sync_agents.py --apply    # fill in anything missing
```

Run that after anyone builds a new agent.

---

## 2. Who can see and use which agent — this is now proven

The important question was: *is the portal actually stopping people, or is it
just hiding things?* Answer: **Databricks does the stopping.** We proved it.

We made a throwaway test identity that was **not** an administrator, gave it a
real login token, and tried three things:

| What we tried | What happened |
| --- | --- |
| Use the Weather agent, **not** on its guest list | **Refused.** "You do not have permission to query the endpoint" |
| Same agent, after adding it to the guest list | **Worked.** The agent answered |
| A different agent, still not on its list | **Refused** again |

So the guest list is a real lock, not a sign on a door.

We also checked the *seeing* side, using the real guest lists:

```
The portal itself can see:        every agent shared with it
Each person sees:                 exactly the agents whose list includes them
A plain user sees:                0 agents
```

An administrator who is not on an agent's list does not see that agent in the
portal either.

**Why this matters:** even if the portal had a bug and showed someone an agent
they shouldn't see, clicking it would still fail. The worst a bug can do is leak
an agent's *name*. It cannot hand out use of it.

**One exception:** administrators appear to bypass guest lists when calling an
agent directly, so these checks must be run with a non-administrator test
identity to prove anything.

---

## 3. Things you must not forget when setting this up

These are the steps that silently break everything if missed.

### Every new agent must be shared with the portal, once

An agent does not appear in the portal until its owner shares it. This is not a
bug — the portal refuses to guess.

**Just run the sync script.** It looks up the portal's identity and puts it on
each agent's permission list for you:

```bash
python scripts/sync_agents.py --apply
```

Why it is needed: the portal has to read the guest list to know who is allowed,
and only someone with "manage" rights can read one. Ordinary users can't.

### Agent Bricks agents have TWO guest lists, and they mean different things

This one caught us out badly, twice. There are two lists:

```
the serving endpoint list  <- decides who may USE the agent
the agent's own list       <- what the Databricks "Agents" build screen edits
```

We measured the difference with a throwaway non-administrator account:

| Put them on... | Can they use the agent? |
| --- | --- |
| the serving endpoint list | **Yes** |
| the agent's own list only | **No — refused** |

So **the serving endpoint list is the one that grants access.** The agent's own
list controls the agent as something you can open and edit, not the right to
talk to it.

A warning, because this is how we got it wrong the first time: an
**administrator** can use an agent without being on either list, so testing as
an admin makes the agent list look like it grants access. It does not. Only a
non-administrator test account shows the truth.

**Neither list copies to the other.** We granted on one and polled the other
repeatedly — it never appeared. They drift apart silently.

What the portal does now:

- **Reads** the serving endpoint list — the one that decides who may use an agent.
- **Give access** writes to **both** lists, so the portal and the Databricks
  Agents screen always show the same people. The endpoint grant is the one that
  matters and must succeed; if the Agent Bricks half fails, access is still
  granted and the console says the other screen will look out of date, with the
  fix (`sync_agents.py --apply`).
- **Revoke** clears **both**, so removal really means removal.

Verified end to end, on the live app:

```
Give access  ->  serving endpoint: PRESENT   agent bricks: PRESENT
Revoke       ->  serving endpoint: absent    agent bricks: absent
```

The failure path was tested too, by pointing an agent's id at something that
does not exist: the endpoint grant still applied and the warning appeared.

One exception: a `CAN_VIEW` grant is not mirrored, because the Agent Bricks list
has no view-only level. Promoting it to "can query" would hand out more access
than was asked for, so it is skipped instead.

The portal learns an agent's id from a tag written by `sync_agents.py`, because
the app is not allowed to look it up. So **after building a new agent, run the
sync script.**

### End users need the `workspace-access` permission

**`workspace-consume` on its own is not enough.** Without `workspace-access` a
person gets refused before their guest-list entry is even looked at:

> "This API is disabled for users without the workspace-access entitlement"

We got this wrong at first. Put it on your onboarding checklist.

### Four things must line up for a person to use an agent

Miss any one and it fails, often with a confusing message:

1. They can log in to Databricks, **and** have `workspace-access`
2. They are in the right group
3. That group is on the agent's guest list (the portal does this bit)
4. If the agent reads company data, they also have access to **that data**

Step 4 is the one that catches people. It is a completely separate permission
system (Unity Catalog) and the portal cannot set it. See section 5.

### After changing the portal's permissions, restart it

If you change what the app is allowed to do, you must stop and start it, or the
change is invisible:

```bash
databricks apps stop agent-portal -p <profile>
databricks apps start agent-portal -p <profile>
```

### Permission changes take a few seconds to spread

Twice we saw a change appear to do nothing, then work seconds later. If you
remove someone's access, do not expect it to be instant. To cut access
immediately, remove their guest-list entry rather than removing them from a
group.

---

## 4. What works

- **Chatting with agents.** Tested live against a real agent, including one that
  used its own web-search tool.
- **Any kind of agent.** Databricks agents do not all speak the same language.
  The portal handles the known ones, and for anything unfamiliar it tries one
  format, reads the complaint, switches, and remembers. We proved this by
  deliberately feeding an agent the wrong format — the answer still came back.
- **Giving and removing access**, from the admin screen. Removing access
  deliberately keeps the portal's own entry, or the portal would lock itself out.
- **Grouping agents.** Put agents on a group's list, then add people to the
  group. One action to onboard someone.
- **Real names.** Databricks already stores the name whoever built the agent
  typed, so nobody has to retype it. "Mid Campaign PPT Agent", not
  "Agent 1a2b3c4d".
- **Uploading a file**, where an agent needs one. The portal now works out
  *where* files should go by reading the agent's own settings.
- **Chat models** (Claude, Llama, Gemma — 21 of them), kept in a separate
  collapsed section so they do not bury the real agents.
- **Cost reporting.** Real numbers from the workspace's own billing records.

---

## 5. What does not work, honestly

### The Mid Campaign PPT agent cannot finish its job through the portal

Uploading works. The agent even says *"I'll generate the report from your
uploaded Excel file."* Then it does nothing.

It is waiting for a file handed over through a Databricks feature called
Conversation Files, which has no public interface. We tried naming the file's
location in the message, and two other methods. Every time the agent just
repeated "please upload your file".

**The fix is one line, on the agent, not the portal.** Its instructions need to
say: *if the user gives a file path starting with /Volumes/, pass that path to
the report tool.* The agent already has permission to read that location. Once
changed, the portal works with no code change.

### Data permissions are still manual

An agent that reads company data needs the *person* to have access to that data
too. Giving them the agent is not enough. The portal reports the failure clearly
but cannot fix it.

There is a real complication: the two permission systems do not accept the same
kinds of groups. Agent guest lists accept the groups the portal creates. The
data system does **not** — it only recognises individual people, or groups
created at the company level. So one group cannot currently cover both.

### Chat models cannot be truly restricted

The 21 chat models belong to Databricks, not to you, and Databricks gives every
user in the workspace permission to use them. We confirmed you cannot change
that.

The portal's on/off switch therefore **hides** them rather than blocking them.
Proven: the same person, same moment, was refused by the portal and allowed by
Databricks directly.

That is fine for its actual purpose — stopping people casually spending money on
expensive models. It is not a security control, and the admin screen says so.

### The admin screen cannot show you who has chat-model access

The switch works, and setting the list of people works. But the portal cannot
**read back** who is on that list, so it does not display them.

Why: reading group membership needs a permission called `scim`, and Databricks
does not allow that permission to be given to an app on a person's behalf. We
tried four different names for it; all were rejected. The app's own identity
cannot see members either, even asking for the group directly.

So the screen says *"membership cannot be listed from here; set it below to be
sure"* rather than pretending the group is empty — which is what it wrongly said
before we caught this. To see the real list:

```bash
databricks groups list -p <profile>          # find portal-llm-users and its id
databricks groups get <id> -p <profile>      # shows the members
```

### Why Databricks says "No Permissions" but the portal shows the full list

Not a contradiction. The Databricks permissions dialog only shows what **you**
are allowed to see; if you lack Manage on an agent it shows "No Permissions",
meaning *"I can't tell you"*, not *"there are none"*.

The portal shows the real list because it reads guest lists with the **app's**
identity, which does have Manage.

**This once caused a genuine security bug.** Because the portal also *wrote*
with the app's identity, anyone the portal treated as an admin could change the
guest list of an agent Databricks would refuse them. We reproduced it, then
fixed it: every write now checks that *you personally* hold Manage on that
agent. If you don't, the panel shows the list read-only with an explanation, and
the server refuses the write even if the buttons are bypassed. Two of the live
checks guard this.

### Conversations are not saved

Refresh the page and the chat is gone. Nothing is stored.

### Some things in Databricks' "Create Agent" menu will never appear

Information Extraction, Document Parsing and Text Classification are not agents
— they are database functions meant to run over a table, not to be talked to.
They produce nothing for the portal to show. To make one usable by a
non-technical person, wrap it inside a Supervisor Agent as a tool.

---

## 6. Surprises worth remembering

Things that cost us time and would cost you the same.

- **An agent can fail while reporting success.** One agent returns "OK" at the
  network level but hides an error inside the reply. Checking only the status
  code gives the user a blank answer and no explanation.
- **Being an administrator does not give you access to someone else's agent.**
  There is no "manage everything" setting to fall back on.
- **A deleted identity stays on guest lists.** Remove it by hand.
- **Databricks reports "you can manage this" on things you cannot manage** — a
  misleading field. Don't trust it; try the action.
- **The portal cannot read Databricks' own agent-name list from inside the app.**
  There is no permission available that allows it. That is why `sync_agents.py`
  exists and runs from a laptop instead.
- **A test account created in Databricks cannot log in** unless it also exists
  in Microsoft Entra ID, because that is what handles sign-in.

