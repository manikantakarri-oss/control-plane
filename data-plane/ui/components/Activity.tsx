"use client";

import { useEffect, useState } from "react";
import { ErrorBox, SectionHead, Spinner } from "./bits";

type Event = {
  at: string;
  actor: string;
  category: string;
  message: string;
  ok: boolean;
  status: number;
  error: string;
  kind: string;
  detail: { service: string; action: string; ip: string; params: Record<string, string> };
};

/** One choice, not a combination.
 *
 *  The first version of this had four checkboxes, which asks the reader to
 *  work out which combination answers their question before they can even
 *  look. There are really only three questions - who changed access, what was
 *  done to the portal, and show me everything - so it is one control with
 *  three positions.
 */
const VIEWS = [
  { key: "access", cats: "access", label: "Access changes", hint: "Who was given or lost access." },
  { key: "admin", cats: "admin", label: "Administration", hint: "Portal versions and restarts." },
  { key: "all", cats: "access,admin,system", label: "Everything", hint: "Includes sign-ins and internal checks." },
];

/** A glyph per kind of event, so the eye can skim a long day without reading
 *  every sentence. Never the only signal - the sentence always says it too. */
function glyph(e: Event): { mark: string; tone: string } {
  if (!e.ok) return { mark: "!", tone: "var(--err)" };
  // `kind` comes from the server, which knows whether an ACL change added or
  // removed access - the action name alone does not say.
  switch (e.kind) {
    case "grant":
      return { mark: "+", tone: "var(--brand-deep)" };
    case "revoke":
      return { mark: "−", tone: "var(--ink-faint)" };
    case "deploy":
      return { mark: "↻", tone: "var(--ink-faint)" };
    case "data":
      return { mark: "▤", tone: "var(--ink-faint)" };
    case "group":
      return { mark: "◍", tone: "var(--ink-faint)" };
    default:
      return { mark: "•", tone: "var(--ink-faint)" };
  }
}

export function Activity() {
  const [view, setView] = useState("access");
  const [days, setDays] = useState(7);
  const [verbose, setVerbose] = useState(false);
  const [data, setData] = useState<{ available: boolean; note: string; events: Event[] } | null>(
    null
  );
  const [err, setErr] = useState("");

  const cats = VIEWS.find((v) => v.key === view)?.cats || "access";

  useEffect(() => {
    setData(null);
    setErr("");
    fetch(`/api/admin/audit?days=${days}&cats=${cats}`)
      .then(async (r) => {
        const t = await r.text();
        const j = t ? JSON.parse(t) : {};
        if (!r.ok) throw new Error(j.error || j.detail || "Could not load activity");
        return j;
      })
      .then(setData)
      .catch((e) => setErr(e.message));
  }, [cats, days]);

  const groups = groupByDay(data?.events || []);

  return (
    <div>
      <SectionHead title="What has been happening">
        A plain-English record of changes in this workspace — including ones made directly in
        Databricks, not just through this portal.
      </SectionHead>

      <div className="card overflow-hidden">
        {/* Controls */}
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-3 px-5 py-4"
          style={{ borderBottom: "1px solid var(--line)" }}
        >
          <div
            className="inline-flex rounded-lg p-0.5"
            style={{ background: "var(--canvas)", border: "1px solid var(--line)" }}
            role="tablist"
            aria-label="Which activity to show"
          >
            {VIEWS.map((v) => {
              const active = v.key === view;
              return (
                <button
                  key={v.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  title={v.hint}
                  onClick={() => setView(v.key)}
                  className="rounded-md px-3 py-1.5 text-sm transition"
                  style={
                    active
                      ? { background: "var(--brand)", color: "var(--brand-ink)", fontWeight: 600 }
                      : { color: "var(--ink-dim)" }
                  }
                >
                  {v.label}
                </button>
              );
            })}
          </div>

          <span className="flex-1" />

          <select
            className="field w-auto text-sm"
            aria-label="Time period"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            <option value={1}>Last 24 hours</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>

          <label className="flex cursor-pointer items-center gap-2 text-sm muted">
            <input type="checkbox" checked={verbose} onChange={() => setVerbose(!verbose)} />
            Technical detail
          </label>
        </div>

        {/* Body */}
        <div className="px-5 py-4">
          <ErrorBox>{err}</ErrorBox>
          {data === null ? (
            <Spinner label="Reading the records…" />
          ) : !data.available ? (
            <p className="text-sm muted">Records are unavailable right now. {data.note}</p>
          ) : data.events.length === 0 ? (
            <p className="py-4 text-sm muted">
              Nothing to show. No {view === "access" ? "access changes" : "activity"} in this
              period.
            </p>
          ) : (
            <>
              {groups.map(([day, items]) => (
                <section key={day} className="mb-5 last:mb-0">
                  <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide faint">
                    {day}
                  </h4>
                  <ul>
                    {items.map((e, i) => (
                      <Row key={i} event={e} verbose={verbose} />
                    ))}
                  </ul>
                </section>
              ))}
              <p className="text-xs faint">
                {data.events.length} {data.events.length === 1 ? "entry" : "entries"} shown.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ event, verbose }: { event: Event; verbose: boolean }) {
  const g = glyph(event);
  return (
    <li className="flex gap-3 py-2" style={{ borderTop: "1px solid var(--line)" }}>
      <span
        aria-hidden
        className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
        style={{ background: "var(--canvas)", color: g.tone }}
      >
        {g.mark}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm">{event.message}</p>
        <p className="mt-0.5 text-xs faint">
          <time dateTime={event.at}>{clock(event.at)}</time>
          {!event.ok ? (
            <span style={{ color: "var(--err)" }}> · did not work{event.error ? ` — ${event.error}` : ""}</span>
          ) : null}
        </p>
        {verbose ? (
          <pre
            className="mt-1.5 overflow-x-auto rounded-md px-2.5 py-2 text-[11px] leading-relaxed"
            style={{ background: "var(--canvas)", border: "1px solid var(--line)" }}
          >
            {event.detail.service}.{event.detail.action}
            {event.detail.ip ? `  from ${event.detail.ip}` : ""}
            {"\n"}
            {JSON.stringify(event.detail.params, null, 1)}
          </pre>
        ) : null}
      </div>
    </li>
  );
}

/** "Today" and "Yesterday" are what people actually say; older days get a date. */
function groupByDay(events: Event[]): [string, Event[]][] {
  const buckets = new Map<string, Event[]>();
  for (const e of events) {
    const key = dayLabel(e.at);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key)!.push(e);
  }
  return [...buckets.entries()];
}

function dayLabel(iso: string) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "Earlier";
  const today = new Date();
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (same(d, today)) return "Today";
  if (same(d, yesterday)) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "long" });
}

function clock(iso: string) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
