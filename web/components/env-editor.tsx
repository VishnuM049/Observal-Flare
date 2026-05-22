"use client";

import { useEffect, useRef, useState } from "react";
import { envVars } from "@/lib/api-client";

interface KnownVar {
  key: string;
  default: string;
  description: string;
  section: string;
}

interface EnvEditorProps {
  value: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
  disabled?: boolean;
}

export function EnvEditor({ value, onChange, disabled }: EnvEditorProps) {
  const entries = Object.entries(value);
  const [knownVars, setKnownVars] = useState<KnownVar[]>([]);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [showDropdown, setShowDropdown] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    envVars.known().then(setKnownVars).catch(() => {});
  }, []);

  const filtered = knownVars.filter(
    (v) =>
      !value[v.key] &&
      (v.key.toLowerCase().includes(newKey.toLowerCase()) ||
        v.description.toLowerCase().includes(newKey.toLowerCase()))
  );

  function selectVar(v: KnownVar) {
    setNewKey(v.key);
    setNewValue(v.default);
    setShowDropdown(false);
  }

  function addEntry() {
    if (!newKey.trim()) return;
    onChange({ ...value, [newKey.trim()]: newValue });
    setNewKey("");
    setNewValue("");
    setShowDropdown(false);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (!showDropdown || filtered.length === 0) {
      if (e.key === "Enter") {
        e.preventDefault();
        addEntry();
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      selectVar(filtered[selectedIndex]);
    } else if (e.key === "Escape") {
      setShowDropdown(false);
    }
  }

  useEffect(() => {
    setSelectedIndex(0);
  }, [newKey]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node) &&
          inputRef.current && !inputRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="space-y-2">
      {entries.map(([k, v]) => {
        const known = knownVars.find((kv) => kv.key === k);
        return (
          <div key={k}>
            <div className="flex gap-2 items-center">
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
            {known?.description && (
              <p className="text-xs mt-0.5 ml-1" style={{ color: "var(--color-ink-muted)" }}>
                {known.description}
              </p>
            )}
          </div>
        );
      })}
      {!disabled && (
        <div className="relative">
          <div className="flex gap-2 items-center">
            <div className="flex-1 relative">
              <input
                ref={inputRef}
                placeholder="Search or type KEY..."
                value={newKey}
                onChange={(e) => {
                  setNewKey(e.target.value);
                  setShowDropdown(true);
                }}
                onFocus={() => setShowDropdown(true)}
                onKeyDown={handleKeyDown}
                className="input-field w-full font-mono text-xs"
              />
              {showDropdown && filtered.length > 0 && (
                <div
                  ref={dropdownRef}
                  className="absolute z-50 left-0 right-0 top-full mt-1 max-h-48 overflow-y-auto border rounded shadow-lg"
                  style={{ backgroundColor: "var(--color-surface)", borderColor: "var(--color-border)" }}
                >
                  {filtered.slice(0, 15).map((v, i) => (
                    <button
                      key={v.key}
                      type="button"
                      className="w-full text-left px-3 py-2 text-xs transition-colors"
                      style={{
                        backgroundColor: i === selectedIndex ? "var(--color-accent-light)" : "transparent",
                      }}
                      onMouseEnter={() => setSelectedIndex(i)}
                      onClick={() => selectVar(v)}
                    >
                      <span className="font-mono font-medium">{v.key}</span>
                      {v.description && (
                        <span className="ml-2" style={{ color: "var(--color-ink-muted)" }}>
                          {v.description.length > 60 ? v.description.slice(0, 60) + "..." : v.description}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <input
              placeholder="value"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addEntry(); } }}
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
        </div>
      )}
      {entries.length === 0 && !disabled && (
        <p className="text-xs" style={{ color: "var(--color-ink-muted)" }}>
          No variables set. Start typing to search available variables, or enter a custom key.
        </p>
      )}
    </div>
  );
}
