"use client";

import { useState } from "react";
import { invites } from "@/lib/api-client";

interface InviteFormProps {
  onCreated: () => void;
}

export function InviteForm({ onCreated }: InviteFormProps) {
  const [label, setLabel] = useState("");
  const [maxSites, setMaxSites] = useState(1);
  const [maxUses, setMaxUses] = useState<number | "">("");
  const [forcedTtlDays, setForcedTtlDays] = useState(7);
  const [expiresInDays, setExpiresInDays] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const expiresAt = new Date();
      expiresAt.setDate(expiresAt.getDate() + expiresInDays);
      await invites.create({
        label: label || undefined,
        max_sites: maxSites,
        forced_ttl_days: forcedTtlDays,
        expires_at: expiresAt.toISOString(),
        max_uses: maxUses === "" ? null : maxUses,
      });
      onCreated();
      setLabel("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create invite");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="bg-white border border-gray-200 rounded-lg p-6 space-y-4">
      <h2 className="font-semibold">Create Invite</h2>
      {error && (
        <div className="bg-red-50 text-red-700 px-3 py-1.5 rounded text-sm">{error}</div>
      )}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">Label</label>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Acme Corp demo"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Max Sites</label>
          <input
            type="number"
            min={1}
            value={maxSites}
            onChange={(e) => setMaxSites(Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Max Uses</label>
          <input
            type="number"
            min={1}
            value={maxUses}
            onChange={(e) => setMaxUses(e.target.value === "" ? "" : Number(e.target.value))}
            placeholder="Unlimited"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Site TTL (days)</label>
          <input
            type="number"
            min={1}
            value={forcedTtlDays}
            onChange={(e) => setForcedTtlDays(Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Link expires in (days)</label>
          <input
            type="number"
            min={1}
            value={expiresInDays}
            onChange={(e) => setExpiresInDays(Number(e.target.value))}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </div>
      </div>
      <button
        type="submit"
        disabled={loading}
        className="bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "Creating..." : "Create Invite"}
      </button>
    </form>
  );
}
