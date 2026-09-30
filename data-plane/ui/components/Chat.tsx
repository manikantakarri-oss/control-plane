"use client";

import { useEffect, useRef, useState } from "react";
import { Agent, api, downloadUrl, Reply, upload } from "@/lib/api";
import { ErrorBox, Spinner } from "./bits";
import { TierSwitch } from "./ModelChoice";

type Turn = {
  role: "user" | "assistant" | "error";
  text: string;
  tools?: string[];
  citations?: { label: string; url: string }[];
  attachments?: { name: string; path: string }[];
  files?: string[];
};

export function Chat({
  agent,
  models,
  agents,
  onBack,
  onSwitch,
}: {
  agent: Agent;
  models: Agent[];
  agents: Agent[];
  onBack: () => void;
  onSwitch: (a: Agent) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<{ name: string; path: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState("");
  const [err, setErr] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns, busy]);

  // A different assistant means a different conversation. Carrying the old
  // transcript across would send one model's words to another as if it had
  // said them.
  useEffect(() => {
    setTurns([]);
    setPending([]);
    setDraft("");
    setErr("");
  }, [agent.name]);

  // Only the real back-and-forth goes to the agent; errors stay local so a
  // failed attempt never poisons the next one.
  const history = () =>
    turns
      .filter((t) => t.role !== "error")
      .map((t) => ({ role: t.role, content: t.text }));

  async function send() {
    const text = draft.trim();
    if (!text && pending.length === 0) return;
    setErr("");
    setDraft("");
    const files = pending.map((f) => f.path);
    const shown = pending.map((f) => f.name);
    setPending([]);

    const asked: Turn = {
      role: "user",
      text: text || "Please process the attached file.",
      files: shown,
    };
    setTurns((t) => [...t, asked]);
    setBusy(true);
    try {
      const out: Reply = await api.chat(
        agent.name,
        [...history(), { role: "user", content: asked.text }],
        files
      );
      setTurns((t) => [
        ...t,
        {
          role: "assistant",
          text: out.reply,
          tools: out.tools,
          citations: out.citations,
          attachments: out.attachments,
        },
      ]);
    } catch (e: any) {
      setTurns((t) => [...t, { role: "error", text: e.message }]);
    } finally {
      setBusy(false);
    }
  }

  async function pick(f: File | undefined) {
    if (!f) return;
    setErr("");
    setUploading(f.name);
    try {
      const done = await upload(agent.name, f);
      setPending((p) => [...p, { name: done.name, path: done.path }]);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setUploading("");
    }
  }

  const accepts = (agent.accepts || []).map((a) => "." + a.replace(/^\./, ""));

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <button type="button" onClick={onBack} className="btn btn-quiet shrink-0">
            ← Back
          </button>
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold">{agent.display_name}</h2>
            <p className="text-sm muted">{agent.blurb || agent.kind_hint}</p>
          </div>
        </div>

        {agent.metered ? (
          <TierSwitch models={models} current={agent} onSwitch={onSwitch} />
        ) : models.length ? (
          <button
            type="button"
            className="shrink-0 text-sm muted underline hover:text-[var(--ink)]"
            onClick={() => onSwitch(models[0])}
          >
            Ask a general assistant instead
          </button>
        ) : null}
      </div>

      <ErrorBox>{err}</ErrorBox>

      <div className="card p-5">
        {turns.length === 0 ? (
          <p className="py-6 text-center text-sm muted">
            {agent.supports_files
              ? "Attach a file below, or just type your question to get started."
              : "Type a question below to get started."}
          </p>
        ) : (
          <div className="flex flex-col gap-4">
            {turns.map((t, i) => (
              <Bubble key={i} turn={t} agent={agent} />
            ))}
          </div>
        )}

        {busy ? (
          <div className="mt-4">
            <Spinner label="Working on it — this can take a moment." />
          </div>
        ) : null}
        <div ref={endRef} />
      </div>

      {agent.supports_files ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => fileRef.current?.click()}
            disabled={!!uploading}
          >
            {uploading ? `Uploading ${uploading}…` : "Attach a file"}
          </button>
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            accept={accepts.join(",") || undefined}
            aria-label="Choose a file to send to this agent"
            onChange={(e) => {
              pick(e.target.files?.[0]);
              e.currentTarget.value = "";
            }}
          />
          <span className="text-xs faint">
            {accepts.length ? `Accepts ${accepts.join(", ")}` : "Any file type"}
          </span>
          {pending.map((f, i) => (
            <span key={i} className="tag gap-1.5">
              {f.name}
              <button
                type="button"
                aria-label={`Remove ${f.name}`}
                className="opacity-70 hover:opacity-100"
                onClick={() => setPending((p) => p.filter((_, j) => j !== i))}
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <div className="mt-3 flex items-end gap-2">
        <textarea
          className="field min-h-[46px] resize-y"
          rows={1}
          value={draft}
          aria-label="Your message"
          placeholder="Ask a question…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          type="button"
          className="btn btn-primary shrink-0"
          onClick={send}
          disabled={busy || (!draft.trim() && pending.length === 0)}
        >
          Send
        </button>
      </div>
      <p className="mt-1.5 text-xs faint">Enter sends. Shift+Enter starts a new line.</p>
    </div>
  );
}

function Bubble({ turn, agent }: { turn: Turn; agent: Agent }) {
  if (turn.role === "user") {
    return (
      <div className="self-end max-w-[80%]">
        <div
          className="rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap break-words"
          style={{ background: "var(--brand)", color: "var(--brand-ink)" }}
        >
          {turn.text}
        </div>
        {turn.files?.length ? (
          <p className="mt-1 text-right text-xs faint">Sent: {turn.files.join(", ")}</p>
        ) : null}
      </div>
    );
  }

  if (turn.role === "error") {
    return (
      <div className="self-start max-w-[85%]">
        <div className="error whitespace-pre-wrap">{turn.text}</div>
      </div>
    );
  }

  return (
    <div className="self-start max-w-[85%]">
      <div
        className="rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap break-words"
        style={{ background: "var(--canvas)", border: "1px solid var(--line)" }}
      >
        {turn.text}
      </div>

      {turn.citations?.length ? (
        <div className="mt-2">
          <p className="text-xs faint">Based on:</p>
          <ul className="mt-0.5 space-y-0.5">
            {turn.citations.map((c, i) => (
              <li key={i} className="text-xs">
                {c.url ? (
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="muted underline hover:text-[var(--brand)]"
                  >
                    {c.label}
                  </a>
                ) : (
                  <span className="muted">{c.label}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {turn.attachments?.length ? (
        <div className="mt-2">
          <p className="text-xs faint">
            {turn.attachments.length > 1 ? "Files created:" : "File created:"}
          </p>
          {turn.attachments.map((f, i) =>
            agent.output_volume && f.path ? (
              <a
                key={i}
                href={downloadUrl(agent.name, f.path)}
                className="mt-1 flex items-center gap-1.5 rounded-md px-2 py-1 text-xs underline hover:no-underline"
                style={{ background: "var(--canvas)", border: "1px solid var(--line)" }}
              >
                ⬇ {f.name || f.path}
              </a>
            ) : (
              <code
                key={i}
                className="mt-1 block break-all rounded-md px-2 py-1 text-xs"
                style={{ background: "var(--canvas)", border: "1px solid var(--line)" }}
              >
                {f.path || f.name}
              </code>
            )
          )}
        </div>
      ) : null}

      {turn.tools?.length ? (
        <p className="mt-1.5 text-xs faint">Used: {turn.tools.join(", ")}</p>
      ) : null}
    </div>
  );
}
