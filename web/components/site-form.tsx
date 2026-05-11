"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { DeployType, SleepMode } from "@/lib/types";
import { sites } from "@/lib/api-client";
import { estimateDailyCost, formatDailyCost } from "@/lib/cost-estimate";
import { DeploySourcePicker } from "./deploy-source-picker";
import { EnvEditor } from "./env-editor";

export function SiteForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [deployType, setDeployType] = useState<DeployType>("branch");
  const [deployRef, setDeployRef] = useState("");
  const [requestorEmail, setRequestorEmail] = useState("");
  const [instanceSize, setInstanceSize] = useState("t3.large");
  const [envOverrides, setEnvOverrides] = useState<Record<string, string>>({});
  const [autoUpdate, setAutoUpdate] = useState(false);
  const [autoWipeOnFailure, setAutoWipeOnFailure] = useState(true);
  const [sleepMode, setSleepMode] = useState<SleepMode>("none");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const site = await sites.create({
        name,
        deploy_type: deployType,
        deploy_ref: deployRef,
        requestor_email: requestorEmail,
        instance_size: instanceSize,
        env_overrides: Object.keys(envOverrides).length > 0 ? envOverrides : undefined,
        auto_update: autoUpdate,
        auto_wipe_on_failure: autoWipeOnFailure,
        sleep_mode: sleepMode,
      });
      router.push(`/sites/${site.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create site");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-2xl space-y-6">
      {error && (
        <div className="bg-red-50 text-red-700 px-4 py-2 rounded-md text-sm">{error}</div>
      )}

      <div>
        <label className="block text-sm font-medium mb-1">Site Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
          placeholder="my-site"
          required
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
        />
        <p className="text-xs text-gray-400 mt-1">
          Becomes the subdomain: {name || "my-site"}.observal.io
        </p>
      </div>

      <DeploySourcePicker
        deployType={deployType}
        deployRef={deployRef}
        onTypeChange={setDeployType}
        onRefChange={setDeployRef}
      />

      <div>
        <label className="block text-sm font-medium mb-1">Requestor Email</label>
        <input
          type="email"
          value={requestorEmail}
          onChange={(e) => setRequestorEmail(e.target.value)}
          required
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
        />
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">Instance Size</label>
        <select
          value={instanceSize}
          onChange={(e) => setInstanceSize(e.target.value)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
        >
          <option value="t3.medium">t3.medium (4 GB)</option>
          <option value="t3.large">t3.large (8 GB)</option>
          <option value="t3.xlarge">t3.xlarge (16 GB)</option>
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">Sleep Mode</label>
        <select
          value={sleepMode}
          onChange={(e) => setSleepMode(e.target.value as SleepMode)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
        >
          <option value="none">None — always running</option>
          <option value="nightly">Nightly — stop at 7 PM daily</option>
          <option value="idle">Idle — stop after 2h no traffic</option>
        </select>
      </div>

      <div className="bg-gray-50 border border-gray-200 rounded-md px-4 py-3 text-sm">
        <span className="text-gray-500">Estimated cost: </span>
        <span className="font-medium">
          {formatDailyCost(estimateDailyCost(instanceSize, sleepMode))}
        </span>
      </div>

      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={autoUpdate}
            onChange={(e) => setAutoUpdate(e.target.checked)}
          />
          Auto-update on push
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={autoWipeOnFailure}
            onChange={(e) => setAutoWipeOnFailure(e.target.checked)}
          />
          Auto-wipe data on failed redeploy
        </label>
      </div>

      <div>
        <label className="block text-sm font-medium mb-2">Environment Overrides</label>
        <EnvEditor value={envOverrides} onChange={setEnvOverrides} />
      </div>

      <button
        type="submit"
        disabled={loading}
        className="bg-blue-600 text-white px-6 py-2 rounded-md font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
      >
        {loading ? "Creating..." : "Create Site"}
      </button>
    </form>
  );
}
