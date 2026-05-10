"use client";

import { useState } from "react";
import type { DeployType } from "@/lib/types";
import { deploySources } from "@/lib/api-client";

interface DeploySourcePickerProps {
  deployType: DeployType;
  deployRef: string;
  onTypeChange: (type: DeployType) => void;
  onRefChange: (ref: string) => void;
}

const DEPLOY_TYPES: { value: DeployType; label: string }[] = [
  { value: "branch", label: "Branch" },
  { value: "pr", label: "Pull Request" },
  { value: "tag", label: "Tag" },
  { value: "release", label: "Release" },
  { value: "commit", label: "Commit SHA" },
];

export function DeploySourcePicker({
  deployType,
  deployRef,
  onTypeChange,
  onRefChange,
}: DeploySourcePickerProps) {
  const [validating, setValidating] = useState(false);
  const [validation, setValidation] = useState<{
    valid: boolean;
    sha: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function validate() {
    if (!deployRef.trim()) return;
    setValidating(true);
    setError(null);
    setValidation(null);
    try {
      const result = await deploySources.validate(deployType, deployRef);
      setValidation({ valid: result.valid, sha: result.resolved_sha });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Validation failed");
    } finally {
      setValidating(false);
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-sm font-medium mb-1">Deploy Source</label>
        <select
          value={deployType}
          onChange={(e) => {
            onTypeChange(e.target.value as DeployType);
            setValidation(null);
          }}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
        >
          {DEPLOY_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium mb-1">
          {deployType === "pr" ? "PR Number" : deployType === "commit" ? "Commit SHA" : "Ref"}
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            value={deployRef}
            onChange={(e) => {
              onRefChange(e.target.value);
              setValidation(null);
            }}
            placeholder={
              deployType === "branch"
                ? "main"
                : deployType === "pr"
                  ? "42"
                  : deployType === "tag"
                    ? "v0.4.0"
                    : deployType === "release"
                      ? "v0.4.0"
                      : "abc123"
            }
            className="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={validate}
            disabled={validating}
            className="px-3 py-2 text-sm border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
          >
            {validating ? "..." : "Validate"}
          </button>
        </div>
        {validation && (
          <p className="text-xs text-green-600 mt-1">
            Resolved to <span className="font-mono">{validation.sha.slice(0, 8)}</span>
          </p>
        )}
        {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
      </div>
    </div>
  );
}
