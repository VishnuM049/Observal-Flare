"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { experiments as experimentsApi } from "@/lib/api-client";
import type { ExperimentConfig, ExperimentPreflight } from "@/lib/types";

function formatBytes(bytes: number): string {
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(2)} MB`;
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(2)} KB`;
  return `${bytes} B`;
}

export default function NewExperimentPage() {
  const router = useRouter();
  const [config, setConfig] = useState<ExperimentConfig | null>(null);
  const [targetRef, setTargetRef] = useState("");
  const [weightsByRef, setWeightsByRef] = useState<Record<string, number>>({});
  const [rate, setRate] = useState(48);
  const [duration, setDuration] = useState(5);
  const [concurrency, setConcurrency] = useState(4);
  const [instanceCount, setInstanceCount] = useState(1);
  const [preflight, setPreflight] = useState<ExperimentPreflight | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    experimentsApi
      .config()
      .then((value) => {
        setConfig(value);
        setTargetRef(value.target_ref);
        setConcurrency(Math.min(4, value.max_concurrency));
      })
      .catch((err) => setError(err.message));
  }, []);

  const expectedPerInstance = rate * duration;
  const expected = expectedPerInstance * instanceCount;
  const targetRefs = targetRef.split("\n").map((value) => value.trim()).filter(Boolean);
  const targetWeights = targetRefs.map((ref) => weightsByRef[ref] ?? 1);
  const weightsValid = targetWeights.every((weight) => Number.isInteger(weight) && weight > 0);
  const confirmationText = `RUN ${expected}`;
  const preflightCurrent = preflight?.targets.map((target) => target.requested_ref).join("\n") === targetRefs.join("\n")
    && preflight.targets.map((target) => target.weight).join(",") === targetWeights.join(",")
    && preflight.targets.reduce((sum, target) => sum + target.expected_pulls, 0) === expected;
  const valid = Boolean(config?.enabled && preflightCurrent && preflight?.within_transfer_limit)
    && weightsValid && confirmation === confirmationText;

  async function runPreflight() {
    setPreflightLoading(true);
    setError(null);
    setPreflight(null);
    try {
      setPreflight(await experimentsApi.preflight(
        targetRefs,
        expectedPerInstance,
        instanceCount,
        targetWeights,
      ));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Image preflight failed");
    } finally {
      setPreflightLoading(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const experiment = await experimentsApi.create({
        target_refs: targetRefs,
        resolved_target_refs: preflight?.targets.map((target) => target.target_ref) ?? [],
        target_weights: targetWeights,
        rate_per_minute: rate,
        duration_minutes: duration,
        concurrency_limit: concurrency,
        instance_count: instanceCount,
        confirmation,
      });
      router.push(`/experiments/${experiment.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create experiment");
      setLoading(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <Link href="/experiments" className="text-sm hover:underline" style={{ color: "var(--color-ink-muted)" }}>
          &larr; Experiments
        </Link>
        <h1 className="text-2xl font-bold mt-2">New GHCR Experiment</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-ink-muted)" }}>
          Creates a disposable, no-ingress EC2 fleet on the isolated experiment worker.
        </p>
      </div>

      {error && (
        <div className="card px-4 py-3" style={{ borderColor: "var(--color-danger)", color: "var(--color-danger)" }}>
          {error}
        </div>
      )}

      {config && !config.enabled && (
        <div className="card px-4 py-3" style={{ borderColor: "var(--color-warning)", color: "var(--color-warning)" }}>
          Experiments are disabled. Set <code>GHCR_EXPERIMENTS_ENABLED=true</code> and redeploy Flare.
        </div>
      )}

      <form onSubmit={submit} className="space-y-6">
        <section className="card p-5 space-y-3">
          <h2 className="section-label">Target container</h2>
          <label className="block text-sm font-medium">Public digest-pinned GHCR references</label>
          <textarea
            className="input-field font-mono text-xs min-h-28"
            value={targetRef}
            onChange={(event) => {
              setTargetRef(event.target.value);
              setPreflight(null);
              setConfirmation("");
            }}
            placeholder={"ghcr.io/owner/package-one:latest\nghcr.io/owner/package-two@sha256:..."}
            required
          />
          <p className="text-xs" style={{ color: "var(--color-ink-muted)" }}>
            Enter one image per line, up to {config?.max_images ?? 4}. Tags are resolved to immutable digests during preflight. Pulls use weighted round-robin independently on every fleet member.
          </p>
          {targetRefs.length > 0 && (
            <div className="space-y-2 rounded p-3" style={{ backgroundColor: "var(--color-cream)" }}>
              <div className="section-label">Image weights</div>
              {targetRefs.map((ref, index) => (
                <div key={`${ref}-${index}`} className="grid grid-cols-[1fr_6rem] items-center gap-3">
                  <code className="text-xs break-all">{ref}</code>
                  <input
                    className="input-field"
                    type="number"
                    min={1}
                    step={1}
                    aria-label={`Weight for ${ref}`}
                    value={targetWeights[index]}
                    onChange={(event) => {
                      setWeightsByRef((current) => ({
                        ...current,
                        [ref]: Number(event.target.value),
                      }));
                      setPreflight(null);
                      setConfirmation("");
                    }}
                  />
                </div>
              ))}
              <p className="text-xs" style={{ color: weightsValid ? "var(--color-ink-muted)" : "var(--color-danger)" }}>
                Positive integers only. For example, weights 2 and 1 allocate approximately two-thirds and one-third of each instance&apos;s pulls.
              </p>
            </div>
          )}
          <button type="button" className="btn-secondary" onClick={runPreflight} disabled={!targetRef.trim() || !weightsValid || preflightLoading}>
            {preflightLoading ? "Checking…" : "Validate Image & Estimate Transfer"}
          </button>
          {preflight && (
            <div className="space-y-3 rounded p-3" style={{ backgroundColor: "var(--color-cream)" }}>
              {preflight.targets.map((target) => (
                <div key={target.target_ref} className="grid grid-cols-[1fr_auto_auto_auto] gap-3 text-xs">
                  <div className="break-all">
                    <code>{target.requested_ref}</code>
                    {target.requested_ref !== target.target_ref && (
                      <div style={{ color: "var(--color-ink-muted)" }}>→ {target.target_ref.slice(-20)}</div>
                    )}
                  </div>
                  <span>weight {target.weight}</span>
                  <span>{target.platform}, {target.layer_count} layers</span>
                  <span>
                    {target.expected_pulls.toLocaleString()} pulls · {formatBytes(target.estimated_transfer_bytes)}
                  </span>
                </div>
              ))}
              <div className="border-t pt-2 text-sm flex justify-between" style={{ borderColor: "var(--color-border)" }}>
                <strong>Estimated total transfer</strong>
                <strong>{formatBytes(preflight.estimated_transfer_bytes)}</strong>
              </div>
              {!preflight.within_transfer_limit && (
                <p style={{ color: "var(--color-danger)" }}>
                  This exceeds the configured {formatBytes(preflight.max_transfer_bytes)} transfer limit.
                </p>
              )}
            </div>
          )}
        </section>

        <section className="card p-5 space-y-4">
          <h2 className="section-label">Bounded run</h2>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Pull starts per minute</label>
              <input
                className="input-field"
                type="number"
                min={1}
                max={config?.max_rate_per_minute ?? 48}
                value={rate}
                onChange={(event) => {
                  setRate(Number(event.target.value));
                  setPreflight(null);
                  setConfirmation("");
                }}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Duration in minutes</label>
              <input
                className="input-field"
                type="number"
                min={1}
                max={config?.max_duration_minutes ?? 1440}
                value={duration}
                onChange={(event) => {
                  setDuration(Number(event.target.value));
                  setPreflight(null);
                  setConfirmation("");
                }}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">EC2 instances</label>
              <input
                className="input-field"
                type="number"
                min={1}
                max={config?.max_instances ?? 10}
                value={instanceCount}
                onChange={(event) => {
                  setInstanceCount(Number(event.target.value));
                  setPreflight(null);
                  setConfirmation("");
                }}
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Maximum parallel pulls</label>
            <input
              className="input-field"
              type="number"
              min={1}
              max={config?.max_concurrency ?? 4}
              value={concurrency}
              onChange={(event) => setConcurrency(Number(event.target.value))}
            />
            <p className="text-xs mt-1" style={{ color: "var(--color-ink-muted)" }}>
              Applies per instance for both single- and multi-image runs. Concurrent pulls of the same image remain isolated in separate config, archive, log, and trial directories.
            </p>
          </div>
          <div className="grid grid-cols-4 gap-3 text-sm">
            <div><span className="section-label block">Fleet expected pulls</span><strong>{expected.toLocaleString()}</strong></div>
            <div><span className="section-label block">Per instance</span><strong>{expectedPerInstance.toLocaleString()}</strong></div>
            <div><span className="section-label block">Start interval / instance</span><strong>{rate > 0 ? (60 / rate).toFixed(2) : "—"}s</strong></div>
            <div><span className="section-label block">EC2 safety lifetime</span><strong>~{duration + 60}m max</strong></div>
          </div>
        </section>

        <section className="card p-5 space-y-3" style={{ borderColor: "var(--color-warning)" }}>
          <h2 className="section-label">Confirmation</h2>
          <p className="text-sm">
            This generates real anonymous download traffic and permanently changes the target package counter. Type <strong>{confirmationText}</strong> to continue.
          </p>
          {preflightCurrent && preflight && (
            <div className="text-xs space-y-1">
              {preflight.targets.map((target) => (
                <div key={target.target_ref} className="flex justify-between gap-4">
                  <span className="font-mono break-all">{target.requested_ref} (weight {target.weight})</span>
                  <strong className="whitespace-nowrap">
                    {target.expected_pulls.toLocaleString()} pulls · {formatBytes(target.estimated_transfer_bytes)}
                  </strong>
                </div>
              ))}
              <div className="flex justify-between border-t pt-1" style={{ borderColor: "var(--color-border)" }}>
                <span>Fleet total</span>
                <strong>{expected.toLocaleString()} pulls · {formatBytes(preflight.estimated_transfer_bytes)}</strong>
              </div>
            </div>
          )}
          <input
            className="input-field font-mono"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            placeholder={confirmationText}
            autoComplete="off"
          />
        </section>

        <div className="flex gap-3">
          <button type="submit" className="btn-primary" disabled={!valid || loading}>
            {loading ? "Starting…" : "Start Isolated Experiment"}
          </button>
          <Link href="/experiments" className="btn-secondary">Cancel</Link>
        </div>
      </form>
    </div>
  );
}
