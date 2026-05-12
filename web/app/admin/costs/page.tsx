"use client";

import { useEffect, useState } from "react";
import type { CostSummary, DayCost } from "@/lib/types";
import { costs } from "@/lib/api-client";

const CHART_W = 900;
const CHART_H = 300;
const PAD = { top: 20, right: 20, bottom: 50, left: 60 };
const INNER_W = CHART_W - PAD.left - PAD.right;
const INNER_H = CHART_H - PAD.top - PAD.bottom;

function CostChart({ history, projection }: { history: DayCost[]; projection: DayCost[] }) {
  const all = [...history, ...projection];
  if (all.length === 0) return null;

  const maxCost = Math.max(...all.map((d) => d.cost), 1);
  const yMax = Math.ceil(maxCost * 1.2);

  function x(i: number) {
    return PAD.left + (i / Math.max(all.length - 1, 1)) * INNER_W;
  }
  function y(cost: number) {
    return PAD.top + INNER_H - (cost / yMax) * INNER_H;
  }

  const historyPath = history
    .map((d, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(d.cost).toFixed(1)}`)
    .join(" ");

  const projStart = history.length - 1;
  const projPoints = [history[history.length - 1], ...projection];
  const projPath = projPoints
    .map((d, i) => `${i === 0 ? "M" : "L"}${x(projStart + i).toFixed(1)},${y(d.cost).toFixed(1)}`)
    .join(" ");

  const yTicks = 5;
  const yLines = Array.from({ length: yTicks + 1 }, (_, i) => (yMax / yTicks) * i);

  const labelInterval = Math.max(1, Math.floor(all.length / 8));

  return (
    <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full max-w-4xl">
      {yLines.map((val) => (
        <g key={val}>
          <line
            x1={PAD.left} y1={y(val)} x2={CHART_W - PAD.right} y2={y(val)}
            stroke="#D9D4C9" strokeWidth={1}
          />
          <text x={PAD.left - 8} y={y(val) + 4} textAnchor="end" fontSize={11} fill="#8A8A7A">
            ${val.toFixed(0)}
          </text>
        </g>
      ))}

      {all.map((d, i) =>
        i % labelInterval === 0 || i === all.length - 1 ? (
          <text
            key={d.date}
            x={x(i)} y={CHART_H - PAD.bottom + 18}
            textAnchor="middle" fontSize={10} fill="#8A8A7A"
            transform={`rotate(-35, ${x(i)}, ${CHART_H - PAD.bottom + 18})`}
          >
            {d.date.slice(5)}
          </text>
        ) : null
      )}

      {history.length > 0 && (
        <line
          x1={x(history.length - 1)} y1={PAD.top}
          x2={x(history.length - 1)} y2={PAD.top + INNER_H}
          stroke="#D9D4C9" strokeWidth={1} strokeDasharray="4 3"
        />
      )}

      <path d={historyPath} fill="none" stroke="#2D6A4F" strokeWidth={2} />

      {projection.length > 0 && (
        <path d={projPath} fill="none" stroke="#2D6A4F" strokeWidth={2} strokeDasharray="6 4" opacity={0.5} />
      )}

      {history.map((d, i) => (
        <circle key={`h-${d.date}`} cx={x(i)} cy={y(d.cost)} r={2.5} fill="#2D6A4F" />
      ))}
      {projection.map((d, i) => (
        <circle key={`p-${d.date}`} cx={x(projStart + 1 + i)} cy={y(d.cost)} r={2.5} fill="#2D6A4F" opacity={0.4} />
      ))}

      <text x={PAD.left + 4} y={PAD.top - 6} fontSize={11} fill="#8A8A7A">Daily cost ($)</text>
    </svg>
  );
}

export default function CostsPage() {
  const [data, setData] = useState<CostSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    costs
      .summary()
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="skeleton h-8 w-40" />
        <div className="grid grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="card p-4"><div className="skeleton h-3 w-16 mb-2" /><div className="skeleton h-6 w-24" /></div>
          ))}
        </div>
        <div className="card p-6"><div className="skeleton h-48 w-full" /></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card px-4 py-3 flex items-center justify-between" style={{ borderColor: "var(--color-danger)", backgroundColor: "var(--color-danger-light)" }}>
        <span className="text-sm" style={{ color: "var(--color-danger)" }}>{error}</span>
        <button onClick={() => window.location.reload()} className="btn-secondary text-xs">Retry</button>
      </div>
    );
  }

  if (!data) return null;

  const historyTotal = data.history.reduce((sum, d) => sum + d.cost, 0);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Cost Overview</h1>

      <div className="grid grid-cols-3 gap-4">
        <div className="card p-4">
          <div className="section-label">Today</div>
          <div className="text-2xl font-bold mt-1">${data.today_daily.toFixed(2)}<span className="text-sm font-normal ml-1" style={{ color: "var(--color-ink-muted)" }}>/day</span></div>
          <div className="text-xs mt-1" style={{ color: "var(--color-ink-muted)" }}>{data.today_site_count} active site{data.today_site_count !== 1 ? "s" : ""}</div>
        </div>
        <div className="card p-4">
          <div className="section-label">Last 30 days</div>
          <div className="text-2xl font-bold mt-1">${historyTotal.toFixed(2)}</div>
          <div className="text-xs mt-1" style={{ color: "var(--color-ink-muted)" }}>total incurred</div>
        </div>
        <div className="card p-4">
          <div className="section-label">Next 14 days</div>
          <div className="text-2xl font-bold mt-1">${data.projection.reduce((sum, d) => sum + d.cost, 0).toFixed(2)}</div>
          <div className="text-xs mt-1" style={{ color: "var(--color-ink-muted)" }}>projected (accounts for TTLs)</div>
        </div>
      </div>

      <div className="card p-6">
        <div className="flex items-center gap-4 mb-4 text-xs" style={{ color: "var(--color-ink-muted)" }}>
          <span className="flex items-center gap-1">
            <span className="inline-block w-4 h-0.5" style={{ backgroundColor: "var(--color-accent)" }}></span> History
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-4 h-0.5 opacity-50" style={{ backgroundColor: "var(--color-accent)", borderTop: "2px dashed" }}></span> Projection
          </span>
        </div>
        <CostChart history={data.history} projection={data.projection} />
      </div>
    </div>
  );
}
