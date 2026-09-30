"use client";

import { useEffect, useState } from "react";
import { AdminAgent, api } from "@/lib/api";
import { CostChart } from "./CostChart";
import { ErrorBox, Notice, SectionHead, Spinner, StatusDot } from "./bits";
import { Activity } from "./Activity";

type Overview = Awaited<ReturnType<typeof api.adminOverview>>;

export function Admin() {
  const [data, setData] = useState<Overview | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [openName, setOpenName] = useState<string | null>(null);

  async function load() {
    setErr("");
    try {
      setData(await api.adminOverview());
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  const open = data?.agents.find((a) => a.name === openName) || null;

  // Same shape as the agents tab: a grid of cards, then a detail view. One
  // interaction model to learn instead of two, and it still reads at twenty
  // agents where stacked full-width panels would not.
  if (open && data) {
    return (
      <AgentDetail agent={open} data={data} onBack={() => setOpenName(null)} reload={load} />
    );
  }

  return (
    <div className="space-y-10">
      <div>
        <SectionHead title="Who can use each agent">
          Pick an agent to see who has access and change it. Everything is saved straight into
          Databricks.
        </SectionHead>
        <ErrorBox>{err}</ErrorBox>
        {loading ? (
          <Spinner label="Loading…" />
        ) : !data?.agents.length ? (
          <p className="text-sm muted">No agents have been shared with the portal yet.</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {data.agents.map((a) => (
              <AgentTile key={a.name} agent={a} onOpen={() => setOpenName(a.name)} />
            ))}
          </div>
        )}
      </div>

      <Spending />
      <Activity />
    </div>
  );
}

function AgentTile({ agent, onOpen }: { agent: AdminAgent; onOpen: () => void }) {
  const people = agent.grants.filter(
    (g) => (g.level === "CAN_QUERY" || g.level === "CAN_MANAGE") && g.kind !== "service_principal"
  );
  const readOnly = agent.you_can_manage === false;

  return (
    <button
      type="button"
      onClick={onOpen}
      className="card w-full p-5 text-left transition hover:border-[var(--brand)]"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-[15px] font-semibold leading-snug">{agent.display_name}</h3>
        {readOnly ? <span className="tag shrink-0 whitespace-nowrap">View only</span> : null}
      </div>

      <p className="mt-1.5 text-sm muted">
        {agent.acl_error
          ? "Not shared with the portal yet"
          : people.length === 0
            ? "Nobody has access yet"
            : `${people.length} ${
                people.length === 1 ? "person or group has" : "people and groups have"
              } access`}
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs faint">
        <span className="inline-flex items-center gap-1.5">
          <StatusDot ok={agent.ready} />
          {agent.ready ? "Ready" : agent.state}
        </span>
        {agent.supports_files ? <span>Takes a file</span> : null}
      </div>
    </button>
  );
}

function AgentDetail({
  agent,
  data,
  onBack,
  reload,
}: {
  agent: AdminAgent;
  data: Overview;
  onBack: () => void;
  reload: () => void;
}) {
  const [kind, setKind] = useState<"group" | "user">("group");
  const [who, setWho] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const readOnly = agent.you_can_manage === false;
  const options = kind === "group" ? data.groups : data.users;

  useEffect(() => {
    const first = kind === "group" ? data.groups[0]?.name : data.users[0]?.name;
    setWho(first || "");
  }, [kind, data]);

  async function give() {
    if (!who || !agent.id) return;
    setBusy(true);
    setErr("");
    try {
      const res = await api.grant(agent.id, kind, who, "CAN_QUERY");
      if (res?.warning) setErr(res.warning);
      reload();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function take(principal: string, pKind: string) {
    if (!agent.id) return;
    setBusy(true);
    setErr("");
    try {
      await api.grant(agent.id, pKind, principal, null);
      reload();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const usable = agent.grants.filter((g) => g.level === "CAN_QUERY" || g.level === "CAN_MANAGE");

  return (
    <div>
      <div className="mb-5 flex items-start gap-3">
        <button type="button" onClick={onBack} className="btn btn-quiet shrink-0">
          ← All agents
        </button>
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold">{agent.display_name}</h2>
          <p className="text-sm muted">{agent.blurb || agent.name}</p>
        </div>
      </div>

      <ErrorBox>{err}</ErrorBox>

      {agent.acl_error ? (
        <ErrorBox>{agent.acl_error}</ErrorBox>
      ) : (
        <>
          {readOnly ? (
            <div className="mb-4">
              <Notice>
                You can see this list but not change it — you do not have Manage permission on
                this agent in Databricks. Its owner can give you that.
              </Notice>
            </div>
          ) : null}

          <div className="card p-5">
            <h3 className="text-sm font-semibold">Give someone access</h3>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <select
                className="field w-auto"
                aria-label="A group or one person"
                value={kind}
                onChange={(e) => setKind(e.target.value as "group" | "user")}
                disabled={readOnly}
              >
                <option value="group">A group</option>
                <option value="user">One person</option>
              </select>
              <select
                className="field w-auto min-w-[220px] flex-1"
                aria-label="Who to give access to"
                value={who}
                onChange={(e) => setWho(e.target.value)}
                disabled={readOnly}
              >
                {options.map((o: any) => (
                  <option key={o.name} value={o.name}>
                    {kind === "group" ? o.name : `${o.display} (${o.name})`}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-primary"
                onClick={give}
                disabled={readOnly || busy || !who}
              >
                Give access
              </button>
            </div>
          </div>

          <div className="card mt-4 p-5">
            <h3 className="text-sm font-semibold">Who has access now</h3>
            {usable.length === 0 ? (
              <p className="mt-3 text-sm muted">Nobody yet.</p>
            ) : (
              <ul className="mt-2">
                {usable.map((g) => (
                  <li
                    key={`${g.kind}:${g.principal}:${g.level}`}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5"
                    style={{ borderTop: "1px solid var(--line)" }}
                  >
                    <span className="min-w-0 flex-1 truncate text-sm">{g.principal}</span>
                    <span className="text-xs faint">
                      {g.kind === "service_principal" ? "app" : g.kind}
                    </span>
                    <span className="text-sm muted">
                      {g.level === "CAN_MANAGE" ? "Use and manage" : "Use"}
                      {g.inherited ? " (inherited)" : ""}
                    </span>
                    {g.kind !== "service_principal" && !g.inherited && !readOnly ? (
                      <button
                        type="button"
                        className="btn btn-quiet"
                        onClick={() => take(g.principal, g.kind)}
                        disabled={busy}
                      >
                        Remove
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <Appearance agent={agent} readOnly={readOnly} reload={reload} onError={setErr} />
        </>
      )}
    </div>
  );
}

function Appearance({
  agent,
  readOnly,
  reload,
  onError,
}: {
  agent: AdminAgent;
  readOnly: boolean;
  reload: () => void;
  onError: (m: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(agent.display_name);
  const [blurb, setBlurb] = useState(agent.blurb);
  const [vol, setVol] = useState(agent.upload_volume);
  const [accepts, setAccepts] = useState((agent.accepts || []).join(", "));
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    onError("");
    try {
      await api.meta({
        name: agent.name,
        display_name: name,
        blurb,
        upload_volume: vol,
        accepts,
      });
      reload();
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card mt-4 p-5">
      <button
        type="button"
        className="text-sm font-semibold"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        {open ? "▾" : "▸"} How this agent appears, and what it accepts
      </button>
      {open ? (
        <div className="mt-4 space-y-3">
          <Row label="Name people see">
            <input className="field" value={name} onChange={(e) => setName(e.target.value)} />
          </Row>
          <Row label="One-line description">
            <input className="field" value={blurb} onChange={(e) => setBlurb(e.target.value)} />
          </Row>
          <Row
            label="Folder for uploads"
            hint="Only if this agent reads a file people send it. Format: catalog.schema.volume"
          >
            <input
              className="field"
              placeholder="catalog.schema.volume"
              value={vol}
              onChange={(e) => setVol(e.target.value)}
            />
          </Row>
          <Row label="File types allowed" hint="Leave blank to allow any file.">
            <input
              className="field"
              placeholder="xlsx, csv"
              value={accepts}
              onChange={(e) => setAccepts(e.target.value)}
            />
          </Row>
          <button
            type="button"
            className="btn btn-primary"
            onClick={save}
            disabled={readOnly || busy}
          >
            Save
          </button>
        </div>
      ) : null}
    </div>
  );
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-sm">{label}</span>
      {hint ? <span className="ml-2 text-xs faint">{hint}</span> : null}
      <div className="mt-1">{children}</div>
    </label>
  );
}

function Spending() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Awaited<ReturnType<typeof api.cost>> | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setData(null);
    setErr("");
    api.cost(days).then(setData).catch((e) => setErr(e.message));
  }, [days]);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-[15px] font-semibold tracking-[-0.01em]">What the AI has cost</h2>
          <p className="mt-1 max-w-2xl text-sm muted">
            Per model, from your workspace&apos;s own billing records.
          </p>
        </div>
        <select
          className="field w-auto text-sm"
          aria-label="Time period"
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </div>

      <div className="card p-5">
        <ErrorBox>{err}</ErrorBox>
        {data === null ? (
          <Spinner label="Loading…" />
        ) : !data.available ? (
          <p className="text-sm muted">Cost figures are not available. {data.note}</p>
        ) : (
          <>
            <CostChart lines={data.lines} total={data.total_usd} days={data.days} />
            <p className="mt-4 text-xs faint">
              Your agents do not appear separately. An agent&apos;s thinking is charged to
              whichever model it runs on, so it is included above — Databricks does not break it
              out per agent.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
