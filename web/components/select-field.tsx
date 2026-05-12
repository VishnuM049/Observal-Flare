"use client";

import { useEffect, useRef, useState } from "react";

interface Option {
  value: string;
  label: string;
}

interface SelectFieldProps {
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  className?: string;
  style?: React.CSSProperties;
}

export function SelectField({ value, onChange, options, className = "", style }: SelectFieldProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <div ref={ref} className={`relative ${className}`} style={style}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="input-field text-left flex items-center justify-between gap-2"
      >
        <span>{selected?.label || value}</span>
        <svg width="12" height="12" viewBox="0 0 12 12" className={`transition-transform ${open ? "rotate-180" : ""}`}>
          <path fill="var(--color-ink-muted)" d="M2 4l4 4 4-4" />
        </svg>
      </button>
      {open && (
        <div
          className="absolute z-50 mt-1 w-full card shadow-lg overflow-hidden"
          style={{ backgroundColor: "white" }}
        >
          {options.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
              className="w-full text-left px-3 py-2 text-sm transition-colors flex items-center gap-2"
              style={{
                backgroundColor: opt.value === value ? "var(--color-accent-light)" : "white",
                color: opt.value === value ? "var(--color-accent)" : "var(--color-ink)",
              }}
              onMouseEnter={(e) => {
                if (opt.value !== value) e.currentTarget.style.backgroundColor = "var(--color-cream)";
              }}
              onMouseLeave={(e) => {
                if (opt.value !== value) e.currentTarget.style.backgroundColor = "white";
              }}
            >
              {opt.value === value && <span className="text-xs">&#10003;</span>}
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
