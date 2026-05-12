"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { Site } from "@/lib/types";
import { sites as sitesApi } from "@/lib/api-client";
import { estimateDailyCost } from "@/lib/cost-estimate";
import { SiteTable } from "@/components/site-table";

export default function SitesPage() {
  const [siteList, setSiteList] = useState<Site[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    sitesApi
      .list()
      .then(setSiteList)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    if (!search.trim()) return siteList;
    const q = search.toLowerCase();
    return siteList.filter(
      (s) => s.name.toLowerCase().includes(q) || s.domain.toLowerCase().includes(q)
    );
  }, [siteList, search]);

  const runningCount = siteList.filter((s) => s.status === "running").length;
  const totalDaily = siteList.reduce((sum, s) => sum + estimateDailyCost(s.instance_size, s.sleep_mode), 0);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Sites</h1>
        <Link href="/sites/new" className="btn-primary">
          New Site &rarr;
        </Link>
      </div>

      {!loading && !error && siteList.length > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="card px-4 py-3">
            <div className="section-label">Total Sites</div>
            <div className="text-2xl font-bold mt-1">{siteList.length}</div>
          </div>
          <div className="card px-4 py-3">
            <div className="section-label">Running</div>
            <div className="text-2xl font-bold mt-1" style={{ color: "var(--color-accent)" }}>{runningCount}</div>
          </div>
          <div className="card px-4 py-3">
            <div className="section-label">Daily Cost</div>
            <div className="text-2xl font-bold mt-1">${totalDaily.toFixed(2)}</div>
          </div>
        </div>
      )}

      {!loading && !error && siteList.length > 0 && (
        <div className="mb-4">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or domain..."
            className="input-field"
            style={{ maxWidth: "24rem" }}
          />
        </div>
      )}

      {loading && (
        <div className="card overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-4 border-b" style={{ borderColor: "var(--color-border)" }}>
              <div className="skeleton h-4 w-32" />
              <div className="skeleton h-4 w-16" />
              <div className="skeleton h-4 w-24" />
              <div className="skeleton h-4 w-16" />
              <div className="skeleton h-4 w-12" />
              <div className="skeleton h-4 w-20 ml-auto" />
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="card px-4 py-3 flex items-center justify-between" style={{ borderColor: "var(--color-danger)", backgroundColor: "var(--color-danger-light)" }}>
          <span className="text-sm" style={{ color: "var(--color-danger)" }}>{error}</span>
          <button onClick={() => window.location.reload()} className="btn-secondary">
            Retry
          </button>
        </div>
      )}

      {!loading && !error && <SiteTable sites={filtered} />}
    </div>
  );
}
