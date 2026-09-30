"use client";

import { Agent } from "@/lib/api";
import { StatusDot, Tag } from "./bits";

/** One agent, as a card you click to start talking to it.
 *
 *  The old UI put chat models behind a collapsed row, so using one took two
 *  clicks and a discovery step. Cards are identical for agents and models now -
 *  the only difference is the label - so there is nothing extra to learn. */
export function AgentCard({ agent, onOpen }: { agent: Agent; onOpen: (a: Agent) => void }) {
  const disabled = !agent.ready;
  return (
    <button
      type="button"
      onClick={() => onOpen(agent)}
      disabled={disabled}
      className="card w-full p-5 text-left transition hover:border-[var(--brand)] disabled:cursor-not-allowed disabled:opacity-60"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-[15px] font-semibold leading-snug">{agent.display_name}</h3>
        {agent.metered ? (
          <span className="shrink-0 whitespace-nowrap">
            <Tag title="Each message is charged to the workspace.">
              {agent.light ? "Lower cost" : "Higher cost"}
            </Tag>
          </span>
        ) : null}
      </div>

      {/* A model's description only repeats the cost tag above, so skip it and
          keep the card quiet. Agents have real descriptions worth showing. */}
      {agent.metered ? null : (
        <p className="mt-1.5 text-sm muted line-clamp-3">
          {agent.blurb || "No description has been added for this one yet."}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs faint">
        <span className="inline-flex items-center gap-1.5">
          <StatusDot ok={agent.ready} />
          {agent.ready ? "Ready" : `Not ready (${agent.state})`}
        </span>
        <span>{agent.kind_label}</span>
        {agent.supports_files ? <span>Takes a file</span> : null}
      </div>
    </button>
  );
}
