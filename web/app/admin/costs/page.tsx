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
            stroke="#e5e7eb" strokeWidth={1}
          />
          <text x={PAD.left - 8} y={y(val) + 4} textAnchor="end" fontSize={11} fill="#6b7280">
            ${val.toFixed(0)}
          </text>
        </g>
      ))}

      {all.map((d, i) =>
        i % labelInterval === 0 || i === all.length - 1 ? (
          <text
            key={d.date}
            x={x(i)} y={CHART_H - PAD.bottom + 18}
            textAnchor="middle" fontSize={10} fill="#6b7280"
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
          stroke="#9ca3af" strokeWidth={1} strokeDasharray="4 3"
        />
      )}

      <path d={historyPath} fill="none" stroke="#2563eb" strokeWidth={2} />

      {projection.length > 0 && (
        <path d={projPath} fill="none" stroke="#2563eb" strokeWidth={2} strokeDasharray="6 4" opacity={0.5} />
      )}

      {history.map((d, i) => (
        <circle key={`h-${d.date}`} cx={x(i)} cy={y(d.cost)} r={2.5} fill="#2563eb" />
      ))}
      {projection.map((d, i) => (
        <circle key={`p-${d.date}`} cx={x(projStart + 1 + i)} cy={y(d.cost)} r={2.5} fill="#2563eb" opacity={0.4} />
      ))}

      <text x={PAD.left + 4} y={PAD.top - 6} fontSize={11} fill="#6b7280">Daily cost ($)</text>
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

  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (error) return <div className="bg-red-50 text-red-700 px-4 py-2 rounded-md text-sm">{error}</div>;
  if (!data) return null;

  const historyTotal = data.history.reduce((sum, d) => sum + d.cost, 0);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Cost Overview</h1>

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="text-sm text-gray-500">Today</div>
          <div className="text-2xl font-bold">${data.today_daily.toFixed(2)}<span className="text-sm font-normal text-gray-400">/day</span></div>
          <div className="text-xs text-gray-400">{data.today_site_count} active site{data.today_site_count !== 1 ? "s" : ""}</div>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="text-sm text-gray-500">Last 30 days</div>
          <div className="text-2xl font-bold">${historyTotal.toFixed(2)}</div>
          <div className="text-xs text-gray-400">total incurred</div>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="text-sm text-gray-500">Next 14 days</div>
          <div className="text-2xl font-bold">${data.projection.reduce((sum, d) => sum + d.cost, 0).toFixed(2)}</div>
          <div className="text-xs text-gray-400">projected (accounts for TTLs)</div>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-6">
        <div className="flex items-center gap-4 mb-4 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <span className="inline-block w-4 h-0.5 bg-blue-600"></span> History
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-4 h-0.5 bg-blue-600 opacity-50" style={{ borderTop: "2px dashed" }}></span> Projection
          </span>
        </div>
        <CostChart history={data.history} projection={data.projection} />
      </div>
    </div>
  );
}
