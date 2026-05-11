"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { Site } from "@/lib/types";
import { sites as sitesApi } from "@/lib/api-client";
import { SiteTable } from "@/components/site-table";

export default function SitesPage() {
  const [siteList, setSiteList] = useState<Site[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    sitesApi
      .list()
      .then(setSiteList)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Sites</h1>
        <Link
          href="/sites/new"
          className="bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          New Site
        </Link>
      </div>
      {loading && <p className="text-gray-500">Loading...</p>}
      {error && (
        <div className="bg-red-50 text-red-700 px-4 py-2 rounded-md text-sm">{error}</div>
      )}
      {!loading && !error && <SiteTable sites={siteList} />}
    </div>
  );
}
