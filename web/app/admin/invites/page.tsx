"use client";

import { useCallback, useEffect, useState } from "react";
import type { Invite } from "@/lib/types";
import { invites as invitesApi } from "@/lib/api-client";
import { InviteForm } from "@/components/invite-form";

export default function InvitesPage() {
  const [inviteList, setInviteList] = useState<Invite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    invitesApi
      .list()
      .then(setInviteList)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function revoke(id: string) {
    try {
      await invitesApi.revoke(id);
      load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to revoke");
    }
  }

  function copyLink(token: string) {
    navigator.clipboard.writeText(`${window.location.origin}/invite/${token}`);
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Invite Management</h1>

      <InviteForm onCreated={load} />

      {loading && <p className="text-gray-500">Loading...</p>}
      {error && (
        <div className="bg-red-50 text-red-700 px-4 py-2 rounded-md text-sm">{error}</div>
      )}

      {!loading && inviteList.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-4 py-3 font-medium">Label</th>
                <th className="text-left px-4 py-3 font-medium">Token</th>
                <th className="text-left px-4 py-3 font-medium">Uses</th>
                <th className="text-left px-4 py-3 font-medium">Expires</th>
                <th className="text-left px-4 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {inviteList.map((invite) => (
                <tr key={invite.id}>
                  <td className="px-4 py-3">{invite.label || "—"}</td>
                  <td className="px-4 py-3 font-mono text-xs">{invite.token}</td>
                  <td className="px-4 py-3">
                    {invite.use_count}
                    {invite.max_uses !== null && ` / ${invite.max_uses}`}
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {new Date(invite.expires_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-3 flex gap-2">
                    <button
                      onClick={() => copyLink(invite.token)}
                      className="text-blue-600 hover:underline text-xs"
                    >
                      Copy Link
                    </button>
                    <button
                      onClick={() => revoke(invite.id)}
                      className="text-red-600 hover:underline text-xs"
                    >
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
