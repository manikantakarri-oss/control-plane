"use client";

import { useState } from "react";
import { Agent } from "@/lib/api";
import { Tier, tiers } from "@/lib/tiers";
import { SectionHead } from "./bits";

/** The "AI models" tab: three choices, not twenty-one.
 *
 *  A person who wants a quick answer cannot rank Claude Opus 4.6 against
 *  Qwen3.5 122B, and asking them to is the design failure - not their gap in
 *  knowledge. So the shelf shows Quick / Everyday / Most capable, and the real
 *  model names live behind a disclosure for whoever wants them.
 */
export function ModelChoice({
  models,
  onOpen,
}: {
  models: Agent[];
  onOpen: (a: Agent) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const list = tiers(models);

  return (
    <div>
      <SectionHead title="Ask a general assistant">
        These answer from general knowledge — they cannot see any of your company&apos;s data.
        Pick how much thinking you need; each message is charged to the workspace.
      </SectionHead>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {list.map((t) => (
          <TierCard key={t.key} tier={t} onOpen={onOpen} />
        ))}
      </div>

      <div className="mt-6">
        <button
          type="button"
          className="text-sm muted hover:text-[var(--ink)]"
          onClick={() => setShowAll(!showAll)}
          aria-expanded={showAll}
        >
          {showAll ? "▾" : "▸"} Choose a specific model instead ({models.length} available)
        </button>
        {showAll ? (
          <div className="mt-3 card divide-y" style={{ borderColor: "var(--line)" }}>
            {models.map((m) => (
              <button
                key={m.name}
                type="button"
                onClick={() => onOpen(m)}
                disabled={!m.ready}
                className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm transition hover:bg-[var(--brand-soft)] disabled:opacity-50"
                style={{ borderColor: "var(--line)" }}
              >
                <span>{m.display_name}</span>
                <span className="text-xs faint">{m.light ? "cheaper" : "costs more"}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function TierCard({ tier, onOpen }: { tier: Tier; onOpen: (a: Agent) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(tier.model)}
      disabled={!tier.model.ready}
      className="card flex w-full flex-col p-5 text-left transition hover:border-[var(--brand)] disabled:opacity-60"
    >
      <span
        className="mb-3 inline-flex h-9 w-9 items-center justify-center rounded-full text-base"
        style={{ background: "var(--brand-soft)", color: "var(--brand-deep)" }}
        aria-hidden
      >
        {tier.key === "fast" ? "⚡" : tier.key === "balanced" ? "◆" : "★"}
      </span>
      <span className="text-[15px] font-semibold">{tier.label}</span>
      <span className="mt-1 flex-1 text-sm muted">{tier.blurb}</span>
      <span className="mt-3 text-xs faint">{tier.cost}</span>
    </button>
  );
}

/** Compact switcher for the chat header: the same three choices, so changing
 *  your mind mid-conversation does not mean going back to a list. */
export function TierSwitch({
  models,
  current,
  onSwitch,
}: {
  models: Agent[];
  current: Agent;
  onSwitch: (a: Agent) => void;
}) {
  const list = tiers(models);
  if (list.length < 2) return null;
  return (
    <div className="shrink-0">
      <span className="mb-1 block text-xs faint">How much thinking?</span>
      <div className="inline-flex rounded-lg p-0.5" style={{ background: "var(--canvas)", border: "1px solid var(--line)" }}>
        {list.map((t) => {
          const active = t.model.name === current.name;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => !active && onSwitch(t.model)}
              aria-pressed={active}
              title={t.blurb}
              className="rounded-md px-3 py-1.5 text-sm transition"
              style={
                active
                  ? { background: "var(--brand)", color: "var(--brand-ink)", fontWeight: 600 }
                  : { color: "var(--ink-dim)" }
              }
            >
              {t.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
