import { Agent } from "./api";

/** Three choices instead of twenty-one model names.
 *
 *  "Claude Opus 4.6" versus "Qwen3.5 122B A10B" is a meaningless choice to
 *  someone who just wants an answer, and a list of 21 of them is worse than no
 *  choice at all. So the models are collapsed into the only question a normal
 *  person actually has: how fast, how good, how much.
 *
 *  The exact model names are still reachable, one click away, for anyone who
 *  does care.
 */
export type Tier = {
  key: "fast" | "balanced" | "best";
  label: string;
  blurb: string;
  cost: string;
  model: Agent;
};

// Matched against the endpoint name. Ordered by preference within each tier, so
// the first hit wins and a workspace missing one model still gets a sensible
// pick rather than an empty tier.
const RULES: { key: Tier["key"]; patterns: string[] }[] = [
  { key: "fast", patterns: ["haiku", "gemma", "-8b", "-20b", "-0-6b"] },
  { key: "balanced", patterns: ["sonnet", "maverick", "qwen", "-120b", "-70b"] },
  { key: "best", patterns: ["opus", "fable"] },
];

const COPY: Record<Tier["key"], { label: string; blurb: string; cost: string }> = {
  fast: {
    label: "Quick",
    blurb: "Best for short questions, summaries and rewording. Answers fastest.",
    cost: "Cheapest to run",
  },
  balanced: {
    label: "Everyday",
    blurb: "A good all-rounder. Use this if you are not sure which to pick.",
    cost: "Moderate cost",
  },
  best: {
    label: "Most capable",
    blurb: "Best for long documents, careful reasoning and tricky problems.",
    cost: "Most expensive — use when it matters",
  },
};

/** Newer versions sort after older ones by name ("sonnet-5" after
 *  "sonnet-4-6"), so the last match in a family is the newest. Good enough for
 *  picking a default, and the full list is still available. */
function newest(candidates: Agent[]): Agent {
  return [...candidates].sort((a, b) => a.name.localeCompare(b.name)).pop() as Agent;
}

export function tiers(models: Agent[]): Tier[] {
  const out: Tier[] = [];
  for (const rule of RULES) {
    for (const pattern of rule.patterns) {
      const hits = models.filter((m) => m.name.toLowerCase().includes(pattern));
      if (hits.length) {
        out.push({ key: rule.key, ...COPY[rule.key], model: newest(hits) });
        break;
      }
    }
  }
  // Never offer the same model twice under two labels.
  const seen = new Set<string>();
  return out.filter((t) => (seen.has(t.model.name) ? false : (seen.add(t.model.name), true)));
}

export function tierOf(model: Agent, list: Tier[]): Tier | undefined {
  return list.find((t) => t.model.name === model.name);
}
