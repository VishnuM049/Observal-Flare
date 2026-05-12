"use client";

import { useState } from "react";
import type { DeployType } from "@/lib/types";
import { deploySources } from "@/lib/api-client";
import { SelectField } from "./select-field";

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
    message: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function validate() {
    if (!deployRef.trim()) return;
    setValidating(true);
    setError(null);
    setValidation(null);
    try {
      const result = await deploySources.validate(deployType, deployRef);
      setValidation({ valid: result.valid, sha: result.resolved_sha, message: result.commit_message });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Validation failed");
    } finally {
      setValidating(false);
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-sm font-medium mb-1">Deploy Source <span style={{ color: "var(--color-danger)" }}>*</span></label>
        <SelectField
          value={deployType}
          onChange={(v) => {
            onTypeChange(v as DeployType);
            setValidation(null);
          }}
          options={DEPLOY_TYPES.map((t) => ({ value: t.value, label: t.label }))}
        />
      </div>
      <div>
        <label className="block text-sm font-medium mb-1">
          {deployType === "pr" ? "PR Number" : deployType === "commit" ? "Commit SHA" : "Ref"} <span style={{ color: "var(--color-danger)" }}>*</span>
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
                ? "e.g. main, feat/new-ui"
                : deployType === "pr"
                  ? "e.g. 42 (PR number)"
                  : deployType === "tag"
                    ? "e.g. v0.4.0"
                    : deployType === "release"
                      ? "e.g. v0.4.0 (release tag)"
                      : "e.g. a1b2c3d (full or short SHA)"
            }
            className="input-field flex-1"
          />
          <button
            type="button"
            onClick={validate}
            disabled={validating}
            className="btn-primary"
          >
            {validating ? "..." : "Validate"}
          </button>
        </div>
        {validation && (
          <p className="text-xs mt-1" style={{ color: "var(--color-accent)" }}>
            Resolved to <span className="font-mono">{validation.sha.slice(0, 8)}</span> — {validation.message}
          </p>
        )}
        {error && <p className="text-xs mt-1" style={{ color: "var(--color-danger)" }}>{error}</p>}
      </div>
    </div>
  );
}
