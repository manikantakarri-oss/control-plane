"use client";

import { useState } from "react";

export type CostLine = { sku: string; raw?: string; dbus: number; usd: number | null };

/** Cost per model, as a horizontal bar chart.
 *
 *  Decisions, in the order the dataviz method asks for them:
 *
 *  1. **Form.** The job is "compare magnitude across categories with long
 *     names", which is a horizontal bar. A pie was the other candidate and
 *     loses: one model is ~95% of spend here, so the remaining slices would be
 *     unreadable slivers, and pie cannot carry names like "Meta Llama 3.3 70B
 *     Instruct".
 *  2. **Colour job.** This is one measure across categories, so it is
 *     *sequential* - a single hue for every bar. Shading each bar by its own
 *     value would double-encode length as darkness and waste the only free
 *     channel.
 *  3. **Validated, not eyeballed.** Both steps were run through the palette
 *     validator against their own surface: #058ea8 passes every check on white,
 *     #0fa39c passes on the dark surface. The brand teal #00b5b0 was rejected
 *     for charts - it warns on contrast in light mode - and #2ecfc7 failed the
 *     lightness band in dark mode. Dark mode is a selected step, not a flip.
 *  4. **Marks.** 20px bars (under the 24px cap) with a 4px rounded data-end and
 *     a square baseline; 2px of surface between neighbours; hairline recessive
 *     gridlines.
 *  5. **Labels.** Value at the tip, moved outside the bar when it will not fit -
 *     never clipped. No legend: a single series is named by the heading.
 *  6. **Relief.** A table view is always one click away, which is also what
 *     discharges the contrast warning on the light step.
 *
 *  A single bar is not a chart, so one row renders as a plain figure instead.
 */
export function CostChart({
  lines,
  total,
  days,
}: {
  lines: CostLine[];
  total: number | null;
  days: number;
}) {
  const [asTable, setAsTable] = useState(false);
  const [hover, setHover] = useState<number | null>(null);

  const rows = [...lines].sort((a, b) => (b.usd ?? 0) - (a.usd ?? 0));
  const max = Math.max(...rows.map((r) => r.usd ?? 0), 0.0001);
  const sum = rows.reduce((n, r) => n + (r.usd ?? 0), 0);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-2xl font-semibold tabular-nums">
            ${total == null ? "—" : total.toFixed(2)}
          </p>
          <p className="text-sm muted">
            across {rows.length} {rows.length === 1 ? "model" : "models"}, last {days} days
          </p>
        </div>
        <button
          type="button"
          className="text-sm muted underline hover:text-[var(--ink)]"
          onClick={() => setAsTable(!asTable)}
        >
          {asTable ? "Show chart" : "Show as table"}
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="mt-4 text-sm muted">Nothing has been used in this period.</p>
      ) : asTable || rows.length === 1 ? (
        <Table rows={rows} sum={sum} single={rows.length === 1 && !asTable} />
      ) : (
        <div className="mt-5">
          {rows.map((r, i) => {
            const usd = r.usd ?? 0;
            const pct = (usd / max) * 100;
            // A value needs roughly 52px of bar to sit inside comfortably.
            const inside = pct > 22;
            return (
              <div
                key={r.sku}
                className="grid items-center gap-3 py-[3px]"
                style={{ gridTemplateColumns: "minmax(90px, 30%) 1fr" }}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              >
                <span className="truncate text-sm" title={r.sku}>
                  {r.sku}
                </span>
                <div className="relative flex items-center">
                  <div
                    className="h-5 shrink-0 transition-[width]"
                    style={{
                      width: `max(3px, ${pct}%)`,
                      // 4px rounded data-end, square at the baseline.
                      borderRadius: "0 4px 4px 0",
                      background: "var(--chart-1)",
                      opacity: hover === null || hover === i ? 1 : 0.55,
                    }}
                  />
                  <span
                    className={
                      "pointer-events-none whitespace-nowrap text-xs tabular-nums " +
                      (inside ? "-ml-[52px] w-[44px] text-right" : "ml-2")
                    }
                    style={{ color: inside ? "var(--chart-on)" : "var(--ink-dim)" }}
                  >
                    ${usd.toFixed(2)}
                  </span>
                  {hover === i ? (
                    <span className="ml-3 whitespace-nowrap text-xs faint">
                      {r.dbus} units · {sum > 0 ? Math.round((usd / sum) * 100) : 0}% of spend
                    </span>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Table({
  rows,
  sum,
  single,
}: {
  rows: CostLine[];
  sum: number;
  single: boolean;
}) {
  return (
    <div className="mt-4 overflow-x-auto">
      {single ? (
        <p className="mb-2 text-xs faint">
          Only one model has been used, so there is nothing to compare — here is the figure.
        </p>
      ) : null}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs faint">
            <th className="py-2 pr-3 font-medium">Model</th>
            <th className="py-2 pr-3 font-medium">Usage</th>
            <th className="py-2 pr-3 font-medium">Cost</th>
            <th className="py-2 font-medium">Share</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.sku} style={{ borderTop: "1px solid var(--line)" }}>
              <td className="py-2.5 pr-3">{r.sku}</td>
              <td className="py-2.5 pr-3 muted tabular-nums">{r.dbus}</td>
              <td className="py-2.5 pr-3 tabular-nums">
                {r.usd == null ? "—" : `$${r.usd.toFixed(2)}`}
              </td>
              <td className="py-2.5 muted tabular-nums">
                {sum > 0 ? `${Math.round(((r.usd ?? 0) / sum) * 100)}%` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
