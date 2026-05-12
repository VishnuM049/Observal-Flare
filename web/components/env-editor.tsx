"use client";

import { useState } from "react";

interface EnvEditorProps {
  value: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
  disabled?: boolean;
}

export function EnvEditor({ value, onChange, disabled }: EnvEditorProps) {
  const entries = Object.entries(value);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");

  function addEntry() {
    if (!newKey.trim()) return;
    onChange({ ...value, [newKey.trim()]: newValue });
    setNewKey("");
    setNewValue("");
  }

  return (
    <div className="space-y-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex gap-2 items-center">
          <input
            value={k}
            disabled
            className="input-field flex-1 font-mono text-xs"
            style={{ backgroundColor: "var(--color-cream)" }}
          />
          <input
            value={v}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, [k]: e.target.value })}
            className="input-field flex-1 font-mono text-xs"
          />
          {!disabled && (
            <button
              type="button"
              onClick={() => {
                const next = { ...value };
                delete next[k];
                onChange(next);
              }}
              className="text-xs px-2 py-1 transition-colors"
              style={{ color: "var(--color-danger)" }}
            >
              Remove
            </button>
          )}
        </div>
      ))}
      {!disabled && (
        <div className="flex gap-2 items-center">
          <input
            placeholder="KEY"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            className="input-field flex-1 font-mono text-xs"
          />
          <input
            placeholder="value"
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            className="input-field flex-1 font-mono text-xs"
          />
          <button
            type="button"
            onClick={addEntry}
            className="text-xs px-2 py-1 font-medium transition-colors"
            style={{ color: "var(--color-accent)" }}
          >
            Add
          </button>
        </div>
      )}
      {entries.length === 0 && !disabled && (
        <p className="text-xs" style={{ color: "var(--color-ink-muted)" }}>
          No variables set. Examples: EVAL_MODEL_PROVIDER, EVAL_MODEL_API_KEY, DEPLOYMENT_MODE.
        </p>
      )}
    </div>
  );
}
