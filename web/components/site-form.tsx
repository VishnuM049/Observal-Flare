"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { DeployType, SleepMode } from "@/lib/types";
import { sites } from "@/lib/api-client";
import { estimateDailyCost, formatDailyCost } from "@/lib/cost-estimate";
import { DeploySourcePicker } from "./deploy-source-picker";
import { EnvEditor } from "./env-editor";
import { SelectField } from "./select-field";

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
  const [idleTimeoutMinutes, setIdleTimeoutMinutes] = useState(120);
  const [sleepAtHour, setSleepAtHour] = useState(19);
  const [wakeAtHour, setWakeAtHour] = useState(7);
  const [ttlDays, setTtlDays] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (deployType === "pr" || deployType === "branch") {
      setTtlDays((prev) => prev ?? 1);
    }
  }, [deployType]);

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
        idle_timeout_minutes: sleepMode === "idle" ? idleTimeoutMinutes : undefined,
        sleep_at_hour: sleepMode === "nightly" ? sleepAtHour : undefined,
        wake_at_hour: sleepMode === "nightly" ? wakeAtHour : undefined,
        ttl_days: ttlDays ?? undefined,
      });
      router.push(`/sites/${site.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create site");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-2xl space-y-8">
      {error && (
        <div className="card px-4 py-3 text-sm" style={{ borderColor: "var(--color-danger)", backgroundColor: "var(--color-danger-light)", color: "var(--color-danger)" }}>
          {error}
        </div>
      )}

      <section>
        <h2 className="section-label mb-4">Basic Info</h2>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Site Name <span style={{ color: "var(--color-danger)" }}>*</span></label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
              placeholder="my-site"
              required
              className="input-field"
            />
            <p className="text-xs mt-1" style={{ color: "var(--color-ink-muted)" }}>
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
            <label className="block text-sm font-medium mb-1">Requestor Email <span style={{ color: "var(--color-danger)" }}>*</span></label>
            <input
              type="email"
              value={requestorEmail}
              onChange={(e) => setRequestorEmail(e.target.value)}
              required
              className="input-field"
            />
          </div>
        </div>
      </section>

      <section>
        <h2 className="section-label mb-4">Configuration</h2>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Instance Size</label>
            <SelectField
              value={instanceSize}
              onChange={setInstanceSize}
              options={[
                { value: "t3.medium", label: "t3.medium — 2 vCPU, 4 GB (~$1.00/day)" },
                { value: "t3.large", label: "t3.large — 2 vCPU, 8 GB (~$2.00/day)" },
                { value: "t3.xlarge", label: "t3.xlarge — 4 vCPU, 16 GB (~$3.80/day)" },
              ]}
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Sleep Mode</label>
            <SelectField
              value={sleepMode}
              onChange={(v) => setSleepMode(v as SleepMode)}
              options={[
                { value: "none", label: "None — always running" },
                { value: "nightly", label: "Nightly — scheduled sleep/wake" },
                { value: "idle", label: "Idle — stop after no traffic" },
              ]}
            />
          </div>

          {sleepMode === "nightly" && (
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Sleep At (UTC)</label>
                <SelectField
                  value={sleepAtHour.toString()}
                  onChange={(v) => setSleepAtHour(Number(v))}
                  options={Array.from({ length: 24 }, (_, i) => ({
                    value: i.toString(),
                    label: `${i.toString().padStart(2, "0")}:00 UTC`,
                  }))}
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Wake At (UTC)</label>
                <SelectField
                  value={wakeAtHour.toString()}
                  onChange={(v) => setWakeAtHour(Number(v))}
                  options={Array.from({ length: 24 }, (_, i) => ({
                    value: i.toString(),
                    label: `${i.toString().padStart(2, "0")}:00 UTC`,
                  }))}
                />
              </div>
            </div>
          )}

          {sleepMode === "idle" && (
            <div>
              <label className="block text-sm font-medium mb-1">Idle Timeout</label>
              <SelectField
                value={idleTimeoutMinutes.toString()}
                onChange={(v) => setIdleTimeoutMinutes(Number(v))}
                options={[
                  { value: "15", label: "15 minutes" },
                  { value: "30", label: "30 minutes" },
                  { value: "60", label: "1 hour" },
                  { value: "120", label: "2 hours (default)" },
                  { value: "240", label: "4 hours" },
                  { value: "480", label: "8 hours" },
                ]}
              />
              <p className="text-xs mt-1" style={{ color: "var(--color-ink-muted)" }}>
                Site will sleep after this period of no traffic.
              </p>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium mb-1">Time-to-Live</label>
            <SelectField
              value={ttlDays?.toString() ?? ""}
              onChange={(v) => setTtlDays(v ? Number(v) : null)}
              options={[
                { value: "", label: "No limit" },
                { value: "1", label: "1 day" },
                { value: "3", label: "3 days" },
                { value: "7", label: "7 days" },
                { value: "14", label: "14 days" },
                { value: "30", label: "30 days" },
              ]}
            />
            <p className="text-xs mt-1" style={{ color: "var(--color-ink-muted)" }}>
              After this period, a warning is sent and the site is destroyed 12 hours later.
            </p>
          </div>

          <div className="card px-4 py-3 text-sm">
            <span style={{ color: "var(--color-ink-muted)" }}>Estimated cost: </span>
            <span className="font-medium">
              {formatDailyCost(estimateDailyCost(instanceSize, sleepMode))}
            </span>
            {ttlDays && (
              <span className="ml-2" style={{ color: "var(--color-ink-muted)" }}>
                (~${(estimateDailyCost(instanceSize, sleepMode) * ttlDays).toFixed(2)} total for {ttlDays} day{ttlDays > 1 ? "s" : ""})
              </span>
            )}
          </div>
        </div>
      </section>

      <section>
        <h2 className="section-label mb-4">Automation</h2>
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={autoUpdate}
              onChange={(e) => setAutoUpdate(e.target.checked)}
              className="accent-[var(--color-accent)]"
            />
            Auto-update on push
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={autoWipeOnFailure}
              onChange={(e) => setAutoWipeOnFailure(e.target.checked)}
              className="accent-[var(--color-accent)]"
            />
            Auto-wipe data on failed redeploy
          </label>
        </div>
      </section>

      <section>
        <h2 className="section-label mb-2">Environment Variables</h2>
        <p className="text-xs mb-3" style={{ color: "var(--color-ink-muted)" }}>
          Custom variables merged into the Observal instance&apos;s .env file. Use these to configure model providers, API keys, or deployment-specific settings.
        </p>
        <EnvEditor value={envOverrides} onChange={setEnvOverrides} />
      </section>

      <div className="flex items-center gap-3 pt-2">
        <button type="submit" disabled={loading} className="btn-primary">
          {loading ? "Creating..." : "Create Site"}
        </button>
        <button type="button" onClick={() => router.back()} className="btn-primary" style={{ backgroundColor: "var(--color-ink-muted)" }}>
          Cancel
        </button>
      </div>
    </form>
  );
}
