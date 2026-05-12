import type { SiteStatus } from "@/lib/types";

const STATUS_CONFIG: Record<SiteStatus, { bg: string; text: string; icon: string }> = {
  pending: { bg: "#F3F4F6", text: "#4B5563", icon: "○" },
  provisioning: { bg: "#DBEAFE", text: "#1D4ED8", icon: "◦" },
  deploying: { bg: "#DBEAFE", text: "#1D4ED8", icon: "◦" },
  running: { bg: "var(--color-accent-light)", text: "var(--color-accent)", icon: "✓" },
  stopping: { bg: "var(--color-warning-light)", text: "var(--color-warning)", icon: "●" },
  stopped: { bg: "#F3F4F6", text: "#6B7280", icon: "■" },
  sleeping: { bg: "#F3E8FF", text: "#7C3AED", icon: "☾" },
  destroying: { bg: "var(--color-danger-light)", text: "var(--color-danger)", icon: "✕" },
  destroyed: { bg: "#F3F4F6", text: "#9CA3AF", icon: "✕" },
  failed: { bg: "var(--color-danger-light)", text: "var(--color-danger)", icon: "!" },
};

export function StatusBadge({ status }: { status: SiteStatus }) {
  const config = STATUS_CONFIG[status];
  return (
    <span
      className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium"
      style={{ backgroundColor: config.bg, color: config.text }}
    >
      <span aria-hidden="true">{config.icon}</span>
      {status}
    </span>
  );
}
