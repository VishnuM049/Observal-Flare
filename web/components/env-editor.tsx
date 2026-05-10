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

  function removeEntry(key: string) {
    const next = { ...value };
    delete next[key];
    onChange(next);
  }

  return (
    <div className="space-y-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex gap-2 items-center">
          <input
            value={k}
            disabled
            className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm bg-gray-50 font-mono"
          />
          <input
            value={v}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, [k]: e.target.value })}
            className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm font-mono"
          />
          {!disabled && (
            <button
              type="button"
              onClick={() => removeEntry(k)}
              className="text-red-500 hover:text-red-700 text-sm px-2"
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
            className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm font-mono"
          />
          <input
            placeholder="value"
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm font-mono"
          />
          <button
            type="button"
            onClick={addEntry}
            className="text-blue-600 hover:text-blue-800 text-sm px-2 font-medium"
          >
            Add
          </button>
        </div>
      )}
    </div>
  );
}
