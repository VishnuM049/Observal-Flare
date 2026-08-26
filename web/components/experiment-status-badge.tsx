import type { ExperimentStatus } from "@/lib/types";

const COLORS: Record<ExperimentStatus, { background: string; color: string }> = {
  pending: { background: "var(--color-warning-light)", color: "var(--color-warning)" },
  provisioning: { background: "var(--color-accent-light)", color: "var(--color-accent)" },
  running: { background: "var(--color-accent-light)", color: "var(--color-accent)" },
  destroying: { background: "var(--color-warning-light)", color: "var(--color-warning)" },
  completed: { background: "var(--color-accent-light)", color: "var(--color-accent)" },
  failed: { background: "var(--color-danger-light)", color: "var(--color-danger)" },
  cleanup_failed: { background: "var(--color-danger-light)", color: "var(--color-danger)" },
  cancelled: { background: "var(--color-warning-light)", color: "var(--color-warning)" },
};

export function ExperimentStatusBadge({ status }: { status: ExperimentStatus }) {
  const colors = COLORS[status];
  return (
    <span
      className="inline-flex rounded-full px-2.5 py-1 text-xs font-medium"
      style={colors}
    >
      {status.replace("_", " ")}
    </span>
  );
}
